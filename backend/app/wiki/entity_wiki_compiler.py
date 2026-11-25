"""
知识库核心实体维基自动编纂器 (Entity Wiki Compiler)
核心功能：
1. 从知识库当前活跃版本 index.db 发现高频且高信息密度（谓词种类 >= 3）的核心实体，自动过滤泛化停用主体；
2. 严格按 (entity, doc_year) 隔离分桶，兼容无年份的常青财务/制度文档（无 None-/0- 前缀）；
3. 计算聚合资产 source_hash 实现幂等跳过；已发布词条更新时仅写 pending_diff_markdown 挂起待审，不覆盖线上内容；
4. 所有自动编纂产物默认写入 Wiki Inbox，经管理员审核批准后才触发 sync_page_to_knowledge_base 获取 1.25x 权威权重。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
from collections import defaultdict
from typing import Any

from ..core.logging_config import logger
from ..kb.manager import KBLifecycleManager
from ..kb.storage import KBStorageManager
from ..providers.light_llm_router import light_llm_router
from ..repository.db import db_manager
from ..search.multi_kb_router import normalize_entity_key, to_like_wildcard_pattern
from .storage_manager import WikiStorageManager


# 跨行业泛化停用主体（避免生成空洞废词条）
_GENERIC_STOP_SUBJECTS: frozenset[str] = frozenset({
    "公司", "本企业", "企业", "单位", "本单位", "部门", "各部门", "员工", "人员", "个人",
    "选手", "运动员", "参赛者", "参赛人员", "参赛单位", "骑手", "教练", "裁判",
    "主办方", "主办单位", "承办方", "承办单位", "协办方", "协办单位", "指导单位", "支持单位",
    "组委会", "协会", "赛事", "比赛", "活动", "项目", "费用", "标准", "收费标准",
    "规定", "要求", "通知", "办法", "规程", "制度", "本办法", "本规程", "本制度",
    "附件", "备注", "说明", "其他", "以上", "以下", "相关人员", "工作人员",
})

_COMPILE_SYSTEM_PROMPT = (
    "你是严谨的企业与业务知识库百科编纂助手。请严格基于提供的【图谱事实三元组】与【权威原文片段】，"
    "为目标实体编写一篇结构清晰、可直接查阅的 Markdown 百科词条。\n"
    "必须严格遵守以下防幻觉铁律：\n"
    "1. 所有数字、日期、金额、比例、人数、地点、发票/审批条件必须**逐字照抄**证据中的原文，严禁推算、四舍五入或自行补全；\n"
    "2. 若证据中缺少某个维度的信息（例如没有提到费用或没有提到时间），**直接省略该栏目**，绝对不要写“暂无”凑数或猜测；\n"
    "3. 使用清晰的二级标题（## 核心概况、## 关键指标与标准、## 执行流程与规则）组织，总字数控制在 300~900 字。"
)


def _normalize_slug(title: str) -> str:
    """将标题转为安全可读的唯一 slug。"""
    s = re.sub(r"[^\w\u4e00-\u9fa5-]+", "-", (title or "").strip()).strip("-").lower()
    return s[:80] or "entity-wiki"


class EntityWikiCompiler:
    """知识库到百科工作台 (Inbox) 的自动实体维基编纂引擎。"""

    @classmethod
    async def _ensure_kb_wiki_space(cls, kb_id: str, kb_name: str, kb_desc: str = "") -> dict[str, Any]:
        """查找或自动创建关联该知识库的专属 Wiki 空间。"""
        spaces = await WikiStorageManager.list_spaces()
        for sp in spaces:
            if str(sp.get("default_kb_id") or "") == kb_id:
                return sp

        space_name = f"{kb_name} · 实体百科" if kb_name else f"知识库 {kb_id} 百科"
        return await WikiStorageManager.create_space(
            name=space_name,
            description=kb_desc or f"由知识库 [{kb_name}] 自动编纂沉淀的实体百科空间（Inbox 审核后反哺检索）",
            default_kb_id=kb_id,
            auto_ingest_chat=1,
        )

    @classmethod
    def _discover_entity_buckets(
        cls,
        db_path: Any,
        max_entities: int = 15,
        min_predicates: int = 3,
    ) -> list[dict[str, Any]]:
        """从活跃版本 index.db 发现高信息密度的 (entity, doc_year) 聚合桶。"""
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            has_triples = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge_triples'"
            ).fetchone()
            if not has_triples:
                return []

            # 读取非拒绝三元组，并尝试通过 source_chunk_id 关联切片年份
            cols = [c[1] for c in conn.execute("PRAGMA table_info(knowledge_triples)").fetchall()]
            chunk_id_col = "t.source_chunk_id" if "source_chunk_id" in cols else "'' AS source_chunk_id"

            sql = f"""
                SELECT t.subject, t.predicate, t.object, {chunk_id_col},
                       COALESCE(c.doc_year, 0) AS chunk_year
                FROM knowledge_triples t
                LEFT JOIN hybrid_chunks c ON t.source_chunk_id = c.id
                WHERE (t.review_status IS NULL OR t.review_status != 'REJECTED')
            """ if "source_chunk_id" in cols else """
                SELECT subject, predicate, object, '' AS source_chunk_id, 0 AS chunk_year
                FROM knowledge_triples
                WHERE (review_status IS NULL OR review_status != 'REJECTED')
            """
            rows = conn.execute(sql).fetchall()
            if not rows:
                return []

            # 若三元组未直接带年份，按标题前缀或子查询兜底年份；同时汇总 (subject, doc_year) 桶
            buckets: dict[tuple[str, int], dict[str, Any]] = {}
            for r in rows:
                subj = str(r["subject"] or "").strip()
                pred = str(r["predicate"] or "").strip()
                obj = str(r["object"] or "").strip()
                if len(subj) < 2 or subj.isdigit() or subj in _GENERIC_STOP_SUBJECTS:
                    continue
                # 过滤过长句子型伪实体
                if len(subj) > 40:
                    continue

                doc_year = int(r["chunk_year"] or 0)
                if doc_year <= 0:
                    m_yr = re.match(r"^(20\d\d)\b", subj)
                    if m_yr:
                        doc_year = int(m_yr.group(1))

                bkey = (normalize_entity_key(subj), doc_year)
                if bkey not in buckets:
                    buckets[bkey] = {
                        "entity": subj,
                        "doc_year": doc_year,
                        "triples": [],
                        "predicates": set(),
                        "chunk_ids": set(),
                    }
                buckets[bkey]["triples"].append((subj, pred, obj))
                if pred:
                    buckets[bkey]["predicates"].add(pred)
                if r["source_chunk_id"]:
                    buckets[bkey]["chunk_ids"].add(str(r["source_chunk_id"]))

            # 过滤：同一 (实体, 年份) 桶至少包含 >= min_predicates 种不同谓词
            qualified: list[dict[str, Any]] = []
            for b in buckets.values():
                if len(b["predicates"]) < min_predicates:
                    continue
                # 补充抓取同年份命中该实体的 Top 5 权威正文切片
                subj = b["entity"]
                pat_subj = to_like_wildcard_pattern(subj)
                yr = b["doc_year"]
                if yr > 0:
                    c_rows = conn.execute(
                        """
                        SELECT id, title, content, authority_tier, effective_date
                        FROM hybrid_chunks
                        WHERE (chunk_role IN ('parent', 'child') OR chunk_role IS NULL)
                          AND doc_year = ?
                          AND (content LIKE ? OR title LIKE ?)
                        ORDER BY authority_tier ASC, length(content) DESC
                        LIMIT 5
                        """,
                        (yr, pat_subj, pat_subj),
                    ).fetchall()
                else:
                    c_rows = conn.execute(
                        """
                        SELECT id, title, content, authority_tier, effective_date
                        FROM hybrid_chunks
                        WHERE (chunk_role IN ('parent', 'child') OR chunk_role IS NULL)
                          AND (doc_year IS NULL OR doc_year = 0)
                          AND (content LIKE ? OR title LIKE ?)
                        ORDER BY authority_tier ASC, length(content) DESC
                        LIMIT 5
                        """,
                        (pat_subj, pat_subj),
                    ).fetchall()

                # 若按年份严格匹配未捞到，放宽从全部切片兜底但仍记录实体名匹配
                if not c_rows:
                    c_rows = conn.execute(
                        """
                        SELECT id, title, content, authority_tier, effective_date
                        FROM hybrid_chunks
                        WHERE (chunk_role IN ('parent', 'child') OR chunk_role IS NULL)
                          AND (content LIKE ? OR title LIKE ?)
                        ORDER BY authority_tier ASC, length(content) DESC
                        LIMIT 3
                        """,
                        (pat_subj, pat_subj),
                    ).fetchall()

                chunks = [dict(cr) for cr in c_rows]
                b["chunks"] = chunks
                # 计算幂等 source_hash（有序三元组 + 切片 ID）
                triple_sig = sorted(set(b["triples"]))
                chunk_sig = sorted([str(ch["id"]) for ch in chunks])
                sig_str = json.dumps({"t": triple_sig, "c": chunk_sig}, ensure_ascii=False)
                b["source_hash"] = hashlib.md5(sig_str.encode("utf-8")).hexdigest()
                b["score"] = len(b["predicates"]) * 2 + len(b["triples"])
                qualified.append(b)

            qualified.sort(key=lambda x: x["score"], reverse=True)
            return qualified[:max_entities]
        finally:
            conn.close()

    @classmethod
    async def _compile_bucket_markdown(
        cls,
        bucket: dict[str, Any],
        accounts: list[dict[str, Any]] | None = None,
    ) -> str:
        """调用轻量模型将聚合的三元组与切片编纂为结构化百科 Markdown（不可用时确定性降级）。"""
        entity = bucket["entity"]
        doc_year = int(bucket["doc_year"] or 0)
        year_label = f"{doc_year}年" if doc_year > 0 else "常青制度/通用"

        triples_text = "\n".join(
            f"- {s} | {p} | {o}" for s, p, o in bucket["triples"][:25]
        )
        chunks_text = "\n\n".join(
            f"[片段 {i}] 来源: {c.get('title', '')}\n{str(c.get('content') or '')[:600]}"
            for i, c in enumerate(bucket.get("chunks", [])[:4], start=1)
        )

        prompt = (
            f"目标实体：{entity}（适用年份/周期：{year_label}）\n\n"
            f"【图谱事实三元组】\n{triples_text}\n\n"
            f"【权威原文片段】\n{chunks_text or '无额外切片'}\n\n"
            "请按规则编写该实体的结构化百科词条正文（直接输出 Markdown 正文，不要包含顶层 # 标题）："
        )

        try:
            raw = await light_llm_router.complete(
                prompt=prompt,
                accounts=accounts,
                system_prompt=_COMPILE_SYSTEM_PROMPT,
                temperature=0.1,
                max_tokens=900,
            )
            if raw and raw.strip():
                cleaned = re.sub(
                    r"<think>.*?(</think>|$)", "", raw, flags=re.DOTALL | re.IGNORECASE
                ).strip()
                if len(cleaned) >= 40:
                    return cleaned
        except Exception as err:
            logger.debug(f"[EntityWiki] 轻量模型编纂异常，使用确定性模板兜底: {err}")

        # 确定性无幻觉降级模板
        lines = ["## 核心事实属性", "", "| 属性 | 事实内容 |", "| :--- | :--- |"]
        seen_po = set()
        for _, p, o in bucket["triples"][:20]:
            if (p, o) not in seen_po:
                seen_po.add((p, o))
                lines.append(f"| {p} | {o} |")
        sources = sorted({str(c.get("title") or "") for c in bucket.get("chunks", []) if c.get("title")})
        if sources:
            lines.extend(["", "## 参考文档来源", ""] + [f"- {src}" for src in sources])
        return "\n".join(lines)

    @classmethod
    async def compile_kb_entities_to_inbox(
        cls,
        kb_id: str,
        max_entities: int = 15,
        min_predicates: int = 3,
    ) -> dict[str, Any]:
        """扫描指定知识库活跃版本，为高频实体编纂百科词条并写入 Wiki Inbox。"""
        kbs = await KBLifecycleManager.list_knowledge_bases()
        target_kb = next((k for k in kbs if k["kb_id"] == kb_id), None)
        if not target_kb:
            raise ValueError(f"知识库不存在: {kb_id}")

        kb_name = str(target_kb.get("name") or kb_id)
        active_ver = str(target_kb.get("active_version") or "v1.0.0")
        db_path = KBStorageManager.get_version_db_path(kb_id, active_ver)
        if not db_path.exists():
            raise ValueError(f"知识库 [{kb_name}] 版本 [{active_ver}] 数据文件不存在")

        buckets = await asyncio.to_thread(
            cls._discover_entity_buckets, db_path, max_entities, min_predicates
        )
        if not buckets:
            return {
                "kb_id": kb_id,
                "kb_name": kb_name,
                "version": active_ver,
                "total_candidates": 0,
                "created_inbox": 0,
                "updated_inbox": 0,
                "skipped_unchanged": 0,
                "wiki_space_id": "",
            }

        space = await cls._ensure_kb_wiki_space(
            kb_id=kb_id, kb_name=kb_name, kb_desc=str(target_kb.get("description") or "")
        )
        space_id = space["wiki_space_id"]

        # 查询该空间已有词条（按 slug 索引，用于幂等性与保护已发布内容）
        existing_by_slug: dict[str, dict[str, Any]] = {}
        async with db_manager.get_connection() as conn:
            async with conn.execute(
                "SELECT * FROM wiki_pages WHERE wiki_space_id = ? AND review_status != 'discarded'",
                (space_id,),
            ) as cur:
                for r in await cur.fetchall():
                    rd = dict(r)
                    existing_by_slug[str(rd.get("slug") or "")] = rd

        pool_accounts = await light_llm_router.fetch_pool_accounts()
        sem = asyncio.Semaphore(4)

        created_cnt = 0
        updated_cnt = 0
        skipped_cnt = 0

        async def _process_bucket(b: dict[str, Any]) -> str:
            entity = b["entity"]
            doc_year = int(b["doc_year"] or 0)
            # 无年份的常青制度文档省略年份前缀，避免 None-/0-
            if doc_year > 0:
                title = f"{doc_year} {entity}"
                slug = _normalize_slug(f"{doc_year}-{entity}")
            else:
                title = entity
                slug = _normalize_slug(entity)

            existing = existing_by_slug.get(slug)
            if existing:
                old_refs_raw = existing.get("source_refs_json") or existing.get("source_refs") or "[]"
                try:
                    old_refs = json.loads(old_refs_raw) if isinstance(old_refs_raw, str) else old_refs_raw
                except Exception:
                    old_refs = []
                old_hash = next(
                    (ref.get("source_hash") for ref in (old_refs or []) if isinstance(ref, dict) and ref.get("source_hash")),
                    "",
                )
                if old_hash == b["source_hash"]:
                    return "skipped"

            async with sem:
                compiled_md = await cls._compile_bucket_markdown(b, accounts=pool_accounts)

            source_refs = [
                {
                    "source_hash": b["source_hash"],
                    "kb_id": kb_id,
                    "kb_name": kb_name,
                    "version": active_ver,
                    "doc_year": doc_year,
                    "triple_count": len(b["triples"]),
                }
            ]
            sources = sorted({str(c.get("title") or "") for c in b.get("chunks", []) if c.get("title")})
            if sources:
                source_refs[0]["sources"] = sources

            eff_date = f"{doc_year}-01-01" if doc_year > 0 else ""
            cat_path = f"{kb_name}/{doc_year}年" if doc_year > 0 else f"{kb_name}/常青制度"

            if existing and existing.get("review_status") == "published":
                # 保护已发布词条：从物理 .md 读取既有正文并保留不变，仅写入 pending_diff_markdown 挂起待审
                detail = await WikiStorageManager.get_page_detail(existing["page_id"])
                existing_body = str((detail or {}).get("markdown_content") or "")
                await WikiStorageManager.upsert_page(
                    wiki_space_id=space_id,
                    slug=slug,
                    title=existing.get("title") or title,
                    markdown_content=existing_body,
                    category_path=existing.get("category_path") or cat_path,
                    authority_level=existing.get("authority_level") or "official",
                    review_status="inbox",
                    effective_date=existing.get("effective_date") or eff_date,
                    kb_id=kb_id,
                    summary_text=existing.get("summary_text") or compiled_md[:140],
                    source_refs=source_refs,
                    pending_diff_markdown=compiled_md,
                    page_id=existing["page_id"],
                    stitch_links=False,
                )
                return "updated"
            else:
                # 新建或尚在 inbox 的草稿：写入 pending_diff_markdown，等待工作台人工批准后才入库拿 1.25x
                await WikiStorageManager.upsert_page(
                    wiki_space_id=space_id,
                    slug=slug,
                    title=title,
                    markdown_content="",
                    category_path=cat_path,
                    authority_level="official",
                    review_status="inbox",
                    effective_date=eff_date,
                    kb_id=kb_id,
                    summary_text=compiled_md[:140].replace("\n", " "),
                    source_refs=source_refs,
                    pending_diff_markdown=compiled_md,
                    page_id=existing["page_id"] if existing else None,
                    stitch_links=False,
                )
                return "updated" if existing else "created"

        outcomes = await asyncio.gather(*[_process_bucket(b) for b in buckets])
        for o in outcomes:
            if o == "created":
                created_cnt += 1
            elif o == "updated":
                updated_cnt += 1
            elif o == "skipped":
                skipped_cnt += 1

        logger.info(
            f"[EntityWiki] 知识库 [{kb_name}] ({active_ver}) 实体百科编纂完成: "
            f"新建Inbox={created_cnt}, 更新挂起={updated_cnt}, 幂等跳过={skipped_cnt}"
        )
        return {
            "kb_id": kb_id,
            "kb_name": kb_name,
            "version": active_ver,
            "wiki_space_id": space_id,
            "wiki_space_name": space.get("name", ""),
            "total_candidates": len(buckets),
            "created_inbox": created_cnt,
            "updated_inbox": updated_cnt,
            "skipped_unchanged": skipped_cnt,
        }
