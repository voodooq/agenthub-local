"""
LLM Wiki 多源增量编译器与权威防污染 Diff Merge 引擎
支持五类来源：
- kb_doc: 规程/知识库文档初始化（official / published）
- meeting_note: 会议纪要（decision / inbox）
- chat_decision: 对话结论沉淀（decision / inbox）
- sql_snapshot: Text-to-SQL 统计快照（data_snapshot / inbox）
- camera_ocr: 移动端拍照 OCR / 白板识别（decision / inbox）
"""
from __future__ import annotations

import json
import math
import re
import time
import uuid
from datetime import datetime
from typing import Any, Literal

from ..core.logging_config import logger
from ..providers.light_llm_router import light_llm_router
from ..repository.db import db_manager
from ..search.embedding_provider import embedding_router
from .storage_manager import WikiStorageManager

SourceType = Literal["kb_doc", "meeting_note", "chat_decision", "sql_snapshot", "data_snapshot", "camera_ocr"]

_COMPILE_SYSTEM_PROMPT = (
    "你是企业知识百科（LLM Wiki）整理专家。"
    "请将输入的多源业务内容整理为一个结构清晰的 JSON 对象（不要输出 Markdown 围栏以外的多余解释）：\n"
    '{"title": "简明词条标题", "slug": "唯一短词条名", '
    '"category_path": "分类路径(如 01-竞赛规程/报名与退费)", '
    '"effective_date": "YYYY-MM-DD或空字符串", '
    '"summary_text": "不超过120字的要点摘要", '
    '"markdown_delta": "结构化Markdown正文或补充条款"}'
)

_SOURCE_LABEL_MAP: dict[str, str] = {
    "kb_doc": "规程初始化",
    "meeting_note": "会议纪要",
    "chat_decision": "对话沉淀",
    "sql_snapshot": "数据快照",
    "data_snapshot": "数据快照",
    "camera_ocr": "拍照识别",
}


def _cosine_similarity(vec_a: Any, vec_b: Any) -> float:
    """计算两个向量的余弦相似度（兼容 list 与 numpy 数组）。"""
    try:
        dot = sum(float(a) * float(b) for a, b in zip(vec_a, vec_b))
        norm_a = math.sqrt(sum(float(a) * float(a) for a in vec_a))
        norm_b = math.sqrt(sum(float(b) * float(b) for b in vec_b))
        if norm_a <= 0 or norm_b <= 0:
            return 0.0
        return dot / (norm_a * norm_b)
    except Exception:
        return 0.0


def _parse_compiled_json(raw_text: str | None, fallback_title: str, raw_content: str) -> dict[str, str]:
    """安全解析轻量模型输出的结构化 JSON，解析失败时平滑降级为原文保真整理。"""
    default_title = fallback_title.strip() or re.sub(r"[#*\s]+", " ", raw_content[:24]).strip() or f"未命名词条_{uuid.uuid4().hex[:6]}"
    default_res = {
        "title": default_title,
        "slug": default_title,
        "category_path": "未分类",
        "effective_date": "",
        "summary_text": raw_content.strip().replace("\n", " ")[:120],
        "markdown_delta": raw_content.strip(),
    }
    if not raw_text or not raw_text.strip():
        return default_res

    txt = raw_text.strip()
    json_match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", txt)
    if json_match:
        txt = json_match.group(1).strip()
    elif "{" in txt and "}" in txt:
        txt = txt[txt.find("{") : txt.rfind("}") + 1]

    try:
        parsed = json.loads(txt)
        if isinstance(parsed, dict):
            title = str(parsed.get("title") or default_title).strip()
            slug = str(parsed.get("slug") or title).strip()
            return {
                "title": title,
                "slug": slug,
                "category_path": str(parsed.get("category_path") or "未分类").strip(),
                "effective_date": str(parsed.get("effective_date") or "").strip(),
                "summary_text": str(parsed.get("summary_text") or default_res["summary_text"]).strip()[:150],
                "markdown_delta": str(parsed.get("markdown_delta") or raw_content).strip(),
            }
    except Exception:
        pass
    return default_res


class WikiIncrementalCompiler:
    """多源知识增量编译器与防污染 Diff Merge 处理器。"""

    SIMILARITY_THRESHOLD = 0.78

    @classmethod
    async def _find_matching_page(
        cls,
        wiki_space_id: str,
        target_slug: str | None,
        candidate_title: str,
        query_text: str,
    ) -> dict[str, Any] | None:
        """按显式 slug、精确标题或向量语义相似度 (>= 0.78) 定位同空间既有词条。"""
        async with db_manager.get_connection() as conn:
            if target_slug and target_slug.strip():
                async with conn.execute(
                    "SELECT * FROM wiki_pages WHERE wiki_space_id = ? AND slug = ? AND review_status != 'discarded'",
                    (wiki_space_id, target_slug.strip()),
                ) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        return dict(row)

            async with conn.execute(
                "SELECT * FROM wiki_pages WHERE wiki_space_id = ? AND review_status = 'published'",
                (wiki_space_id,),
            ) as cursor:
                published_pages = [dict(r) for r in await cursor.fetchall()]

        if not published_pages:
            return None

        # 精确标题或 slug 匹配优先
        for p in published_pages:
            if candidate_title and (p["title"] == candidate_title or p["slug"] == candidate_title):
                return p

        # 若未指定 target_slug，使用 embedding_router 匹配 Top-3 词条 (>= 0.78)
        if not target_slug:
            provider = await embedding_router.get_provider()
            if provider.is_available and provider.dimension > 0:
                try:
                    texts = [query_text[:1000]] + [
                        f"{p['title']}\n{p.get('summary_text', '')}" for p in published_pages
                    ]
                    vectors = await provider.encode_batch(texts)
                    if vectors is None:
                        vectors = await provider.embed_texts(texts)
                    if vectors is not None and len(vectors) == len(texts):
                        query_vec = vectors[0]
                        best_page = None
                        best_score = 0.0
                        for idx, p in enumerate(published_pages, start=1):
                            score = _cosine_similarity(query_vec, vectors[idx])
                            if score > best_score:
                                best_score = score
                                best_page = p
                        if best_page is not None and best_score >= cls.SIMILARITY_THRESHOLD:
                            logger.info(
                                f"[WikiCompiler] 语义匹配命中既有词条 [{best_page['slug']}] (score={best_score:.3f})"
                            )
                            return best_page
                except Exception as err:
                    logger.debug(f"[WikiCompiler] 语义匹配失败，回退为新建词条: {err}")

        return None

    @classmethod
    async def ingest_and_compile(
        cls,
        wiki_space_id: str,
        source_type: SourceType,
        raw_content: str,
        source_meta: dict[str, Any] | None = None,
        target_slug: str | None = None,
        auto_publish: bool = False,
    ) -> dict[str, Any]:
        """多源增量编译入口：严禁覆写 official 正文，命中既有词条写入 pending_diff_markdown。"""
        meta = source_meta or {}
        clean_raw = (raw_content or "").strip()
        if not clean_raw:
            raise ValueError("待摄入内容不能为空")

        space = await WikiStorageManager.get_space(wiki_space_id)
        if not space:
            raise ValueError(f"Wiki 空间不存在: {wiki_space_id}")

        kb_id = str(meta.get("kb_id") or space.get("default_kb_id") or "").strip()
        hint_title = str(meta.get("title") or "").strip()
        source_label = _SOURCE_LABEL_MAP.get(source_type, source_type)

        # 仅当内容未提供显式标题或需要结构化抽取时调用轻量模型（严禁触碰 primary）
        llm_prompt = (
            f"来源类型: {source_label}\n"
            f"参考标题: {hint_title or '无'}\n"
            f"原始正文:\n{clean_raw[:3000]}"
        )
        compiled_raw = await light_llm_router.complete(
            prompt=llm_prompt,
            system_prompt=_COMPILE_SYSTEM_PROMPT,
            temperature=0.2,
            max_tokens=1024,
        )
        compiled = _parse_compiled_json(compiled_raw, hint_title, clean_raw)
        if hint_title:
            compiled["title"] = hint_title
            if not compiled.get("slug"):
                compiled["slug"] = hint_title

        matched_page = await cls._find_matching_page(
            wiki_space_id=wiki_space_id,
            target_slug=target_slug,
            candidate_title=compiled["title"],
            query_text=f"{compiled['title']}\n{compiled['summary_text']}\n{clean_raw[:500]}",
        )

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        new_ref = {
            "type": source_type,
            "ref_id": str(meta.get("ref_id") or f"src_{int(time.time())}"),
            "title": str(meta.get("source_title") or compiled["title"]),
            "timestamp": now_str,
        }

        # 1. 命中已有词条：执行防污染 Diff Merge（严禁修改原正文，写入 pending_diff_markdown 进入 Inbox）
        if matched_page is not None:
            pid = matched_page["page_id"]
            try:
                refs = json.loads(matched_page.get("source_refs_json") or "[]")
            except Exception:
                refs = []
            refs.append(new_ref)

            diff_header = f"### [{now_str}] 来自「{source_label}」的补充记录"
            if meta.get("source_title"):
                diff_header += f"（{meta['source_title']}）"
            diff_block = f"{diff_header}\n\n{compiled['markdown_delta'].strip()}"

            existing_pending = str(matched_page.get("pending_diff_markdown") or "").strip()
            combined_diff = f"{existing_pending}\n\n{diff_block}".strip() if existing_pending else diff_block

            async with db_manager.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE wiki_pages
                    SET pending_diff_markdown = ?,
                        review_status = 'inbox',
                        source_refs_json = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE page_id = ?
                    """,
                    (combined_diff, json.dumps(refs, ensure_ascii=False), pid),
                )
                await conn.commit()

            logger.info(
                f"[WikiCompiler] 命中既有词条 [{matched_page['slug']}] (authority={matched_page['authority_level']})，"
                f"增量已写入 Inbox 待审阅"
            )
            detail = await WikiStorageManager.get_page_detail(pid) or {}
            detail["merge_mode"] = "diff_inbox"
            return detail

        # 2. 未命中已有词条
        slug_val = (target_slug or compiled["slug"] or compiled["title"]).strip()
        authority_level = str(meta.get("authority_level") or "").strip()
        if not authority_level:
            if source_type == "kb_doc":
                authority_level = "official"
            elif source_type in ("sql_snapshot", "data_snapshot"):
                authority_level = "data_snapshot"
            else:
                authority_level = "decision"

        category_path = str(meta.get("category_path") or compiled["category_path"] or "未分类").strip()
        effective_date = str(meta.get("effective_date") or compiled["effective_date"] or "").strip()

        if source_type == "kb_doc" or auto_publish:
            # 规程初始化或用户主动收藏的个人查询结论直接发布并落盘（无需二次审阅）
            page = await WikiStorageManager.upsert_page(
                wiki_space_id=wiki_space_id,
                slug=slug_val,
                title=compiled["title"],
                markdown_content=compiled["markdown_delta"],
                category_path=category_path,
                authority_level=authority_level,
                review_status="published",
                effective_date=effective_date,
                kb_id=kb_id,
                summary_text=compiled["summary_text"],
                source_refs=[new_ref],
                pending_diff_markdown="",
                stitch_links=True,
            )
            if kb_id:
                await WikiStorageManager.sync_page_to_knowledge_base(page)
            page["merge_mode"] = "new_published"
            return page

        # 其余多源输入新建为 Inbox 草稿，正文先置入 pending_diff_markdown 待批准后正式发布
        draft = await WikiStorageManager.upsert_page(
            wiki_space_id=wiki_space_id,
            slug=slug_val,
            title=compiled["title"],
            markdown_content="",
            category_path=category_path,
            authority_level=authority_level,
            review_status="inbox",
            effective_date=effective_date,
            kb_id=kb_id,
            summary_text=compiled["summary_text"],
            source_refs=[new_ref],
            pending_diff_markdown=compiled["markdown_delta"],
            stitch_links=False,
        )
        draft["merge_mode"] = "new_inbox"
        return draft

    @classmethod
    async def approve_inbox_page(cls, page_id: str) -> dict[str, Any]:
        """一键批准归档 Inbox 增量或草稿：原子合并至物理 .md 并反哺知识库。"""
        page = await WikiStorageManager.get_page_detail(page_id)
        if not page:
            raise ValueError(f"Wiki 词条不存在: {page_id}")

        existing_body = str(page.get("markdown_content") or "")
        pending_diff = str(page.get("pending_diff_markdown") or "").strip()

        if existing_body.strip() and pending_diff:
            # 保留 official / 既有原正文完整不变，原子追加到末尾的修订与补充记录章节
            revision_heading = "## 修订与补充记录"
            if revision_heading in existing_body:
                merged_body = f"{existing_body.rstrip()}\n\n{pending_diff}\n"
            else:
                merged_body = f"{existing_body.rstrip()}\n\n{revision_heading}\n\n{pending_diff}\n"
        elif pending_diff:
            merged_body = f"{pending_diff}\n"
        else:
            merged_body = existing_body

        updated = await WikiStorageManager.upsert_page(
            wiki_space_id=page["wiki_space_id"],
            slug=page["slug"],
            title=page["title"],
            markdown_content=merged_body,
            category_path=page.get("category_path") or "未分类",
            authority_level=page.get("authority_level") or "decision",
            review_status="published",
            effective_date=page.get("effective_date") or "",
            kb_id=page.get("kb_id") or "",
            summary_text=page.get("summary_text") or "",
            source_refs=page.get("source_refs") or [],
            pending_diff_markdown="",
            page_id=page_id,
            stitch_links=True,
        )

        if updated.get("kb_id"):
            sync_res = await WikiStorageManager.sync_page_to_knowledge_base(updated)
            updated["sync_result"] = sync_res

        logger.info(f"[WikiCompiler] Inbox 词条 [{updated.get('slug')}] 已批准归档并发布")
        return updated

    @classmethod
    async def discard_inbox_page(cls, page_id: str) -> dict[str, Any]:
        """丢弃 Inbox 待审阅增量：既有词条恢复为 published，纯新建草稿标记为 discarded。"""
        page = await WikiStorageManager.get_page_detail(page_id)
        if not page:
            raise ValueError(f"Wiki 词条不存在: {page_id}")

        has_published_body = bool(str(page.get("markdown_content") or "").strip())
        next_status = "published" if has_published_body else "discarded"

        async with db_manager.get_connection() as conn:
            await conn.execute(
                """
                UPDATE wiki_pages
                SET pending_diff_markdown = '',
                    review_status = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE page_id = ?
                """,
                (next_status, page_id),
            )
            await conn.commit()

        return await WikiStorageManager.get_page_detail(page_id) or {"page_id": page_id, "review_status": next_status}

