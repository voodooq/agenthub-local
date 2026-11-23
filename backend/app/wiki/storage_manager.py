"""
LLM Wiki 物理 Markdown + SQLite 双写仓储管理器
核心职责：
1. 管理 wiki_spaces 与 ~/.agenthub/wikis/{wiki_space_id}/ 物理目录；
2. 词条页双写（YAML Frontmatter + Markdown 正文落盘 + SQLite 元数据）；
3. Auto-WikiLink 自动双链缝合（保护代码块、行内代码、URL 与已有双链）与 wiki_links 索引维护；
4. 1-hop 局部图谱与 Backlinks 反向引用查询；
5. 发布/编辑保存后双向反哺至关联知识库 index.db（hybrid_chunks + kb_structured_rules）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

from ..core.logging_config import logger
from ..kb.storage import KBStorageManager
from ..repository.db import db_manager
from ..search.embedding_provider import HAS_NUMPY, embedding_router, np
from ..search.rule_extractor import StructuredRuleExtractor, UniversalRuleExtractor

# 保护 Markdown 中不可做自动双链替换的片段：围栏代码块、行内代码、已有双链、Markdown 链接/图片、裸 URL
_PROTECTED_MD_RE = re.compile(
    r"(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]+`|\[\[[^\[\]\n]+\]\]|!?\[[^\]]*\]\([^)]+\)|https?://[^\s<>()]+)"
)
_WIKILINK_EXTRACT_RE = re.compile(r"\[\[([^\[\]\n]+)\]\]")
_FRONTMATTER_RE = re.compile(r"^---\s*\n[\s\S]*?\n---\s*\n?", re.MULTILINE)
_INVALID_PATH_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def get_wikis_root_dir() -> Path:
    """获取物理 Wiki 存储根目录，支持通过环境变量 AGENTHUB_WIKIS_DIR 覆盖。"""
    custom = os.environ.get("AGENTHUB_WIKIS_DIR", "").strip()
    root = Path(custom) if custom else (Path.home() / ".agenthub" / "wikis")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _sanitize_segment(raw: str, fallback: str = "未分类") -> str:
    """清洗单级目录或文件名，阻断路径穿越与非法字符。"""
    cleaned = _INVALID_PATH_CHARS_RE.sub("_", str(raw or "").strip())
    cleaned = cleaned.replace("..", "_").strip(". ")
    return cleaned or fallback


def _extract_summary(body: str, max_chars: int = 150) -> str:
    """从 Markdown 正文提炼纯文本预览摘要。"""
    plain = re.sub(r"```[\s\S]*?```", " ", body or "")
    plain = re.sub(r"[#>*`\[\]]", "", plain)
    plain = re.sub(r"\s+", " ", plain).strip()
    return plain[:max_chars]


def _compose_markdown_file(meta: dict[str, Any], body: str) -> str:
    """生成带标准 YAML Frontmatter 的物理 Markdown 文本。"""
    safe_title = str(meta.get("title") or meta.get("slug") or "").replace('"', '\\"')
    lines = [
        "---",
        f"page_id: {meta.get('page_id', '')}",
        f"slug: {meta.get('slug', '')}",
        f'title: "{safe_title}"',
        f"authority_level: {meta.get('authority_level', 'decision')}",
        f"effective_date: {meta.get('effective_date', '')}",
        f"kb_id: {meta.get('kb_id', '')}",
        "---",
        "",
    ]
    clean_body = _strip_frontmatter(body).lstrip("\n")
    return "\n".join(lines) + clean_body


def _strip_frontmatter(raw_text: str) -> str:
    """剥离 Markdown 文件头部的 YAML Frontmatter。"""
    if not raw_text:
        return ""
    if raw_text.startswith("---"):
        return _FRONTMATTER_RE.sub("", raw_text, count=1)
    return raw_text


def _extract_context_snippet(text: str, start: int, end: int, max_len: int = 120) -> str:
    """提取双向链接所在行或邻近上下文片段（最长 120 字）。"""
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end == -1:
        line_end = len(text)
    line = text[line_start:line_end].strip()
    if len(line) <= max_len:
        return line
    rel_pos = start - line_start
    half = max_len // 2
    s_idx = max(0, rel_pos - half)
    e_idx = min(len(line), s_idx + max_len)
    return line[s_idx:e_idx]


def _to_float32_bytes(vec: Any) -> bytes:
    """将向量数组安全转换为 float32 字节流（兼容 numpy 与纯 Python 回退）。"""
    if HAS_NUMPY and np is not None and hasattr(vec, "astype"):
        return vec.astype(np.float32).tobytes()
    import array
    return array.array("f", (float(x) for x in vec)).tobytes()


class WikiStorageManager:
    """LLM Wiki 物理文件与 SQLite 双写仓储管理器。"""

    @classmethod
    def get_space_root(cls, wiki_space_id: str, root_dir_path: str | None = None) -> Path:
        """解析并确保 Wiki 空间物理根目录存在。"""
        if root_dir_path:
            space_root = Path(root_dir_path)
        else:
            safe_id = _sanitize_segment(wiki_space_id, fallback="default_space")
            space_root = get_wikis_root_dir() / safe_id
        space_root.mkdir(parents=True, exist_ok=True)
        return space_root

    @classmethod
    def resolve_safe_page_path(
        cls,
        space_root: Path,
        category_path: str,
        slug: str,
    ) -> tuple[str, Path]:
        """根据分类目录与 slug 生成经过安全校验的相对与绝对路径。"""
        raw_parts = [p for p in re.split(r"[/\\]+", str(category_path or "未分类")) if p.strip()]
        safe_parts = [_sanitize_segment(p, fallback="未分类") for p in raw_parts] or ["未分类"]
        safe_slug = _sanitize_segment(slug, fallback=f"page_{uuid.uuid4().hex[:8]}")
        rel_path = Path(*safe_parts) / f"{safe_slug}.md"

        resolved_root = space_root.resolve()
        abs_path = (resolved_root / rel_path).resolve()
        if not abs_path.is_relative_to(resolved_root):
            raise ValueError(f"非法 Wiki 文件路径，拒绝越界写入: {abs_path}")
        return rel_path.as_posix(), abs_path

    @classmethod
    async def create_space(
        cls,
        name: str,
        description: str = "",
        default_kb_id: str = "",
        auto_ingest_chat: int = 1,
        wiki_space_id: str | None = None,
    ) -> dict[str, Any]:
        """创建新的 Wiki 知识空间并初始化物理目录。"""
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Wiki 空间名称不能为空")

        space_id = (wiki_space_id or f"ws_{uuid.uuid4().hex[:10]}").strip()
        space_root = cls.get_space_root(space_id)

        async with db_manager.get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO wiki_spaces (
                    wiki_space_id, name, description, root_dir_path, default_kb_id, auto_ingest_chat
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (space_id, clean_name, description.strip(), str(space_root), default_kb_id.strip(), int(auto_ingest_chat)),
            )
            await conn.commit()

        return await cls.get_space(space_id) or {}

    @classmethod
    async def get_space(cls, wiki_space_id: str) -> dict[str, Any] | None:
        """获取单个 Wiki 空间元信息。"""
        async with db_manager.get_connection() as conn:
            await cls._ensure_snapshot_columns(conn)
            async with conn.execute(
                """
                SELECT s.*,
                       COALESCE(SUM(CASE WHEN p.review_status = 'published' THEN 1 ELSE 0 END), 0) AS published_count,
                       COALESCE(SUM(CASE WHEN p.review_status = 'inbox' THEN 1 ELSE 0 END), 0) AS inbox_count
                FROM wiki_spaces s
                LEFT JOIN wiki_pages p ON s.wiki_space_id = p.wiki_space_id
                WHERE s.wiki_space_id = ?
                GROUP BY s.wiki_space_id
                """,
                (wiki_space_id,),
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    @classmethod
    async def list_spaces(cls) -> list[dict[str, Any]]:
        """获取全部 Wiki 空间列表及已发布/待审阅词条计数。"""
        async with db_manager.get_connection() as conn:
            await cls._ensure_snapshot_columns(conn)
            async with conn.execute(
                """
                SELECT s.*,
                       COALESCE(SUM(CASE WHEN p.review_status = 'published' THEN 1 ELSE 0 END), 0) AS published_count,
                       COALESCE(SUM(CASE WHEN p.review_status = 'inbox' THEN 1 ELSE 0 END), 0) AS inbox_count
                FROM wiki_spaces s
                LEFT JOIN wiki_pages p ON s.wiki_space_id = p.wiki_space_id
                GROUP BY s.wiki_space_id
                ORDER BY s.created_at DESC, s.wiki_space_id ASC
                """
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    @classmethod
    def write_markdown_file(cls, abs_path: Path, meta: dict[str, Any], body: str) -> str:
        """将词条元信息与正文写入物理 .md 文件，返回正文的 MD5 摘要。"""
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        clean_body = _strip_frontmatter(body)
        full_text = _compose_markdown_file(meta, clean_body)
        abs_path.write_text(full_text, encoding="utf-8")
        return hashlib.md5(clean_body.encode("utf-8")).hexdigest()

    @classmethod
    def read_markdown_body(cls, abs_path: Path) -> str:
        """读取物理 .md 文件并剥离 Frontmatter 返回正文。"""
        if not abs_path.is_file():
            return ""
        return _strip_frontmatter(abs_path.read_text(encoding="utf-8"))

    @classmethod
    async def _ensure_snapshot_columns(cls, conn: Any) -> None:
        """幂等迁移 wiki_pages 的 doc_type 与 snapshot_meta_json 列，实现统计快照与普通规程隔离。"""
        async with conn.execute("PRAGMA table_info(wiki_pages)") as cur:
            cols = {row["name"] for row in await cur.fetchall()}
        if "doc_type" not in cols:
            await conn.execute(
                "ALTER TABLE wiki_pages ADD COLUMN doc_type TEXT NOT NULL DEFAULT 'guide'"
            )
        if "snapshot_meta_json" not in cols:
            await conn.execute(
                "ALTER TABLE wiki_pages ADD COLUMN snapshot_meta_json TEXT NOT NULL DEFAULT '{}'"
            )

    @classmethod
    async def ensure_expert_wiki_space(cls, agent_id: str) -> dict[str, Any]:
        """根据数字专家自动获取或创建专属百科空间，并自动写回 bound_wiki_spaces 保障数据一致性。"""
        from ..repository.agent_repository import AgentProfileRepository

        agent = await AgentProfileRepository.get_agent(agent_id)
        if not agent:
            raise ValueError(f"数字专家不存在: {agent_id}")

        bound_spaces = agent.get("bound_wiki_spaces") or []
        for sp_id in bound_spaces:
            sp = await cls.get_space(str(sp_id))
            if sp:
                return sp

        # 未绑定任何有效百科空间 → 自动按专家名称创建专属空间并自动绑定
        expert_name = str(agent.get("name") or agent_id).strip()
        auto_space_id = f"wiki_expert_{agent_id}"
        existing = await cls.get_space(auto_space_id)
        if existing:
            space = existing
        else:
            space = await cls.create_space(
                name=f"{expert_name} · 百科空间",
                description=f"由数字专家【{expert_name}】自动创建并绑定的专属百科与统计快照空间",
                wiki_space_id=auto_space_id,
            )

        new_bound = list(bound_spaces)
        if space["wiki_space_id"] not in new_bound:
            new_bound.append(space["wiki_space_id"])
            await AgentProfileRepository.update_agent(agent_id, {"bound_wiki_spaces": new_bound})

        return space

    @classmethod
    async def ensure_workspace_wiki_space(
        cls,
        *,
        workspace_id: str = "",
        workspace_name: str = "",
        agent_id: str = "",
    ) -> dict[str, Any]:
        """自动解析或创建当前沙盒/专家对应的 Wiki 空间，免去用户手动创建。
        优先级：绑定数字专家的专属空间 > 当前沙盒专属空间 > 默认工作区百科空间。
        """
        clean_agent = (agent_id or "").strip()
        if clean_agent:
            try:
                return await cls.ensure_expert_wiki_space(clean_agent)
            except ValueError:
                pass

        clean_ws_id = (workspace_id or "default").strip() or "default"
        clean_ws_name = (workspace_name or "").strip() or (
            "默认工作区" if clean_ws_id == "default" else clean_ws_id
        )
        safe_ws_key = re.sub(r"[^a-zA-Z0-9_-]", "_", clean_ws_id)[:32]
        ws_space_id = f"wiki_ws_{safe_ws_key}"
        existing = await cls.get_space(ws_space_id)
        if existing:
            return existing
        return await cls.create_space(
            name=f"{clean_ws_name} · 百科空间",
            description=f"由沙盒工作区【{clean_ws_name}】自动创建并关联的专属百科空间",
            wiki_space_id=ws_space_id,
        )

    @staticmethod
    def infer_wiki_source_type(content: str, *, has_sql: bool = False) -> tuple[str, str]:
        """根据对话内容自动判断最合适的 Wiki 来源类型，返回 (source_type, 中文标签)。"""
        text = (content or "").strip()
        if (
            has_sql
            or "```sql" in text.lower()
            or "溯源查询信息" in text
            or (
                ("|---" in text or "| ---" in text)
                and any(k in text for k in ("汇总", "统计", "合计", "小计", "人数", "团体数", "占比", "排名"))
            )
        ):
            return "sql_snapshot", "数据快照"
        if any(k in text for k in ("会议纪要", "参会人", "会议时间", "决议事项", "待办事项", "行动项")):
            return "meeting_note", "会议纪要"
        return "chat_decision", "对话决策"

    @classmethod
    async def upsert_page(
        cls,
        wiki_space_id: str,
        slug: str,
        title: str,
        markdown_content: str,
        category_path: str = "未分类",
        authority_level: str = "decision",
        review_status: str = "published",
        effective_date: str = "",
        kb_id: str = "",
        summary_text: str = "",
        source_refs: list[dict[str, Any]] | None = None,
        pending_diff_markdown: str = "",
        page_id: str | None = None,
        stitch_links: bool = True,
        doc_type: str = "guide",
        snapshot_meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """双写或更新 Wiki 词条页面，并按需执行自动双链缝合。"""
        space = await cls.get_space(wiki_space_id)
        if not space:
            raise ValueError(f"Wiki 空间不存在: {wiki_space_id}")

        clean_slug = slug.strip()
        if not clean_slug:
            raise ValueError("词条 slug 不能为空")
        clean_title = title.strip() or clean_slug
        space_root = cls.get_space_root(wiki_space_id, space.get("root_dir_path"))
        rel_path, abs_path = cls.resolve_safe_page_path(space_root, category_path, clean_slug)

        async with db_manager.get_connection() as conn:
            await cls._ensure_snapshot_columns(conn)
            async with conn.execute(
                "SELECT page_id, file_path, source_refs_json FROM wiki_pages WHERE wiki_space_id = ? AND slug = ?",
                (wiki_space_id, clean_slug),
            ) as cursor:
                existing = await cursor.fetchone()

            final_page_id = page_id or (existing["page_id"] if existing else f"wp_{uuid.uuid4().hex[:10]}")
            if existing and existing["file_path"] and existing["file_path"] != rel_path:
                old_abs = (space_root.resolve() / existing["file_path"]).resolve()
                if old_abs.is_file() and old_abs != abs_path and old_abs.is_relative_to(space_root.resolve()):
                    old_abs.unlink(missing_ok=True)

            merged_refs = source_refs
            if merged_refs is None and existing:
                try:
                    merged_refs = json.loads(existing["source_refs_json"] or "[]")
                except Exception:
                    merged_refs = []
            refs_json = json.dumps(merged_refs or [], ensure_ascii=False)

            clean_body = _strip_frontmatter(markdown_content)
            final_summary = summary_text.strip() or _extract_summary(clean_body)
            clean_doc_type = (doc_type or "guide").strip() or "guide"
            meta_payload: dict[str, Any] = {
                "page_id": final_page_id,
                "slug": clean_slug,
                "title": clean_title,
                "doc_type": clean_doc_type,
                "authority_level": authority_level,
                "effective_date": effective_date,
                "kb_id": kb_id,
            }
            if snapshot_meta:
                for mk in ("captured_at", "query_scope", "profile_id", "expert_id"):
                    if snapshot_meta.get(mk):
                        meta_payload[mk] = snapshot_meta[mk]
            content_md5 = cls.write_markdown_file(abs_path, meta_payload, clean_body)
            snapshot_meta_json = json.dumps(snapshot_meta or {}, ensure_ascii=False)

            await conn.execute(
                """
                INSERT INTO wiki_pages (
                    page_id, wiki_space_id, kb_id, slug, title, category_path, file_path,
                    authority_level, review_status, effective_date, summary_text,
                    source_refs_json, pending_diff_markdown, content_md5, doc_type, snapshot_meta_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(page_id) DO UPDATE SET
                    kb_id = excluded.kb_id,
                    slug = excluded.slug,
                    title = excluded.title,
                    category_path = excluded.category_path,
                    file_path = excluded.file_path,
                    authority_level = excluded.authority_level,
                    review_status = excluded.review_status,
                    effective_date = excluded.effective_date,
                    summary_text = excluded.summary_text,
                    source_refs_json = excluded.source_refs_json,
                    pending_diff_markdown = excluded.pending_diff_markdown,
                    content_md5 = excluded.content_md5,
                    doc_type = excluded.doc_type,
                    snapshot_meta_json = excluded.snapshot_meta_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    final_page_id,
                    wiki_space_id,
                    kb_id.strip(),
                    clean_slug,
                    clean_title,
                    category_path.strip() or "未分类",
                    rel_path,
                    authority_level,
                    review_status,
                    effective_date.strip(),
                    final_summary,
                    refs_json,
                    pending_diff_markdown,
                    content_md5,
                    clean_doc_type,
                    snapshot_meta_json,
                ),
            )
            await conn.commit()

        if stitch_links:
            await cls.auto_stitch_wikilinks(wiki_space_id, target_page_id=final_page_id if review_status == "published" else None)

        return await cls.get_page_detail(final_page_id) or {}

    @classmethod
    async def auto_stitch_wikilinks(
        cls,
        wiki_space_id: str,
        target_page_id: str | None = None,
    ) -> int:
        """为指定空间（或单个页面）自动缝合 [[WikiLink]] 并同步更新 wiki_links 索引表。"""
        space = await cls.get_space(wiki_space_id)
        if not space:
            return 0
        space_root = cls.get_space_root(wiki_space_id, space.get("root_dir_path"))

        async with db_manager.get_connection() as conn:
            await cls._ensure_snapshot_columns(conn)
            async with conn.execute(
                "SELECT * FROM wiki_pages WHERE wiki_space_id = ? AND review_status = 'published'",
                (wiki_space_id,),
            ) as cursor:
                published_rows = [dict(r) for r in await cursor.fetchall()]

            all_slugs = sorted(
                {r["slug"] for r in published_rows if len(str(r.get("slug") or "")) >= 2},
                key=len,
                reverse=True,
            )

            pages_to_process = (
                [r for r in published_rows if r["page_id"] == target_page_id]
                if target_page_id
                else published_rows
            )

            total_links = 0
            for page in pages_to_process:
                pid = page["page_id"]
                own_slug = page["slug"]
                abs_path = (space_root.resolve() / page["file_path"]).resolve()
                body = cls.read_markdown_body(abs_path)
                if not body:
                    continue

                candidate_slugs = [s for s in all_slugs if s != own_slug]
                stitched_body = cls._stitch_body_links(body, candidate_slugs)

                if stitched_body != body:
                    new_md5 = cls.write_markdown_file(abs_path, page, stitched_body)
                    await conn.execute(
                        "UPDATE wiki_pages SET content_md5 = ?, updated_at = CURRENT_TIMESTAMP WHERE page_id = ?",
                        (new_md5, pid),
                    )

                await conn.execute("DELETE FROM wiki_links WHERE source_page_id = ?", (pid,))
                seen_targets: set[str] = set()
                for m in _WIKILINK_EXTRACT_RE.finditer(stitched_body):
                    target = m.group(1).strip()
                    if not target or target == own_slug or target in seen_targets:
                        continue
                    seen_targets.add(target)
                    snippet = _extract_context_snippet(stitched_body, m.start(), m.end())
                    await conn.execute(
                        "INSERT OR REPLACE INTO wiki_links (source_page_id, target_slug, context_snippet) VALUES (?, ?, ?)",
                        (pid, target, snippet),
                    )
                    total_links += 1

            await conn.commit()
        return total_links

    @classmethod
    def _stitch_body_links(cls, body: str, candidate_slugs: list[str]) -> str:
        """在跳过代码块、行内代码、URL 与已有双链的普通文本中首次命中处缝合 [[slug]]。"""
        if not candidate_slugs:
            return body

        already_linked = {m.group(1).strip() for m in _WIKILINK_EXTRACT_RE.finditer(body)}
        pending_slugs = [s for s in candidate_slugs if s not in already_linked]
        if not pending_slugs:
            return body

        segments = _PROTECTED_MD_RE.split(body)
        linked_now: set[str] = set()

        for idx in range(0, len(segments), 2):
            seg = segments[idx]
            if not seg:
                continue
            for slug in pending_slugs:
                if slug in linked_now or slug not in seg:
                    continue
                seg = seg.replace(slug, f"[[{slug}]]", 1)
                linked_now.add(slug)
            segments[idx] = seg

        return "".join(segments)

    @classmethod
    async def list_pages(
        cls,
        wiki_space_id: str,
        status: str | None = None,
        q: str = "",
    ) -> list[dict[str, Any]]:
        """获取空间下词条列表，支持状态筛选与关键词搜索。"""
        sql = "SELECT * FROM wiki_pages WHERE wiki_space_id = ?"
        params: list[Any] = [wiki_space_id]

        if status:
            sql += " AND review_status = ?"
            params.append(status)
        if q and q.strip():
            like_q = f"%{q.strip()}%"
            sql += " AND (title LIKE ? OR slug LIKE ? OR summary_text LIKE ? OR category_path LIKE ?)"
            params.extend([like_q, like_q, like_q, like_q])

        sql += " ORDER BY category_path ASC, updated_at DESC"
        async with db_manager.get_connection() as conn:
            await cls._ensure_snapshot_columns(conn)
            async with conn.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    @classmethod
    async def preview_by_slug(cls, wiki_space_id: str, slug: str) -> dict[str, Any] | None:
        """双链极速预览接口，返回词条摘要卡片字段。"""
        async with db_manager.get_connection() as conn:
            await cls._ensure_snapshot_columns(conn)
            async with conn.execute(
                """
                SELECT page_id, slug, title, authority_level, review_status, effective_date, updated_at, summary_text
                FROM wiki_pages
                WHERE wiki_space_id = ? AND slug = ? AND review_status != 'discarded'
                LIMIT 1
                """,
                (wiki_space_id, slug.strip()),
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    @classmethod
    async def get_page_detail(cls, page_id: str) -> dict[str, Any] | None:
        """获取词条完整详情、正反向双链、溯源记录及 1-hop 局部图谱。"""
        async with db_manager.get_connection() as conn:
            await cls._ensure_snapshot_columns(conn)
            async with conn.execute("SELECT * FROM wiki_pages WHERE page_id = ?", (page_id,)) as cursor:
                row = await cursor.fetchone()
            if not row:
                return None
            page = dict(row)

            space = await cls.get_space(page["wiki_space_id"])
            space_root = cls.get_space_root(page["wiki_space_id"], space.get("root_dir_path") if space else None)
            abs_path = (space_root.resolve() / page["file_path"]).resolve()
            markdown_content = cls.read_markdown_body(abs_path)

            async with conn.execute(
                """
                SELECT l.target_slug, l.context_snippet, p.page_id AS target_page_id, p.title AS target_title
                FROM wiki_links l
                LEFT JOIN wiki_pages p ON p.wiki_space_id = ? AND p.slug = l.target_slug
                WHERE l.source_page_id = ?
                """,
                (page["wiki_space_id"], page_id),
            ) as cursor:
                outgoing_links = [dict(r) for r in await cursor.fetchall()]

            async with conn.execute(
                """
                SELECT l.source_page_id, l.context_snippet, p.slug AS source_slug, p.title AS source_title, p.authority_level
                FROM wiki_links l
                JOIN wiki_pages p ON p.page_id = l.source_page_id
                WHERE p.wiki_space_id = ? AND l.target_slug = ? AND p.review_status = 'published'
                """,
                (page["wiki_space_id"], page["slug"]),
            ) as cursor:
                backlinks = [dict(r) for r in await cursor.fetchall()]

        try:
            source_refs = json.loads(page.get("source_refs_json") or "[]")
        except Exception:
            source_refs = []

        local_graph = await cls.get_local_graph(page["wiki_space_id"], page_id)
        page.update({
            "markdown_content": markdown_content,
            "outgoing_links": outgoing_links,
            "backlinks": backlinks,
            "source_refs": source_refs,
            "local_graph": local_graph,
        })
        return page

    @classmethod
    async def get_local_graph(cls, wiki_space_id: str, page_id: str) -> dict[str, Any]:
        """构建以当前词条为中心的 1-hop 双向局部关系图谱。"""
        async with db_manager.get_connection() as conn:
            await cls._ensure_snapshot_columns(conn)
            async with conn.execute(
                "SELECT page_id, slug, title, authority_level FROM wiki_pages WHERE page_id = ?",
                (page_id,),
            ) as cursor:
                center = await cursor.fetchone()
            if not center:
                return {"nodes": [], "edges": []}

            nodes_map: dict[str, dict[str, Any]] = {
                center["slug"]: {
                    "id": center["page_id"],
                    "slug": center["slug"],
                    "title": center["title"],
                    "authority_level": center["authority_level"],
                    "is_center": True,
                }
            }
            edges: list[dict[str, str]] = []

            async with conn.execute(
                """
                SELECT l.target_slug, p.page_id, p.title, p.authority_level
                FROM wiki_links l
                JOIN wiki_pages p ON p.wiki_space_id = ? AND p.slug = l.target_slug
                WHERE l.source_page_id = ? AND p.review_status = 'published'
                """,
                (wiki_space_id, page_id),
            ) as cursor:
                for r in await cursor.fetchall():
                    nodes_map.setdefault(
                        r["target_slug"],
                        {
                            "id": r["page_id"],
                            "slug": r["target_slug"],
                            "title": r["title"],
                            "authority_level": r["authority_level"],
                            "is_center": False,
                        },
                    )
                    edges.append({"source": center["slug"], "target": r["target_slug"]})

            async with conn.execute(
                """
                SELECT p.page_id, p.slug, p.title, p.authority_level
                FROM wiki_links l
                JOIN wiki_pages p ON p.page_id = l.source_page_id
                WHERE p.wiki_space_id = ? AND l.target_slug = ? AND p.review_status = 'published'
                """,
                (wiki_space_id, center["slug"]),
            ) as cursor:
                for r in await cursor.fetchall():
                    nodes_map.setdefault(
                        r["slug"],
                        {
                            "id": r["page_id"],
                            "slug": r["slug"],
                            "title": r["title"],
                            "authority_level": r["authority_level"],
                            "is_center": False,
                        },
                    )
                    edges.append({"source": r["slug"], "target": center["slug"]})

        return {"nodes": list(nodes_map.values()), "edges": edges}

    @classmethod
    async def sync_page_to_knowledge_base(cls, page: dict[str, Any]) -> dict[str, int]:
        """将已发布的 Wiki 词条反哺至关联知识库 index.db（wiki_page 切片 + 规则卡片）。"""
        kb_id = str(page.get("kb_id") or "").strip()
        if not kb_id or page.get("review_status") != "published":
            return {"chunks_written": 0, "rules_written": 0}

        async with db_manager.get_connection() as conn:
            await cls._ensure_snapshot_columns(conn)
            async with conn.execute(
                """
                SELECT active_version, current_version, domain_type, rule_profile_json,
                       embedding_model, embedding_dim
                FROM knowledge_bases
                WHERE kb_id = ?
                """,
                (kb_id,),
            ) as cursor:
                kb_row = await cursor.fetchone()

        if not kb_row:
            return {"chunks_written": 0, "rules_written": 0}

        version = kb_row["active_version"] or kb_row["current_version"] or "v1.0.0"
        domain_type = kb_row["domain_type"] or "general"
        rule_profile_json = kb_row["rule_profile_json"] or ""
        emb_model = kb_row["embedding_model"] or "null_offline_lexical"
        emb_dim = int(kb_row["embedding_dim"] or 768)

        body = str(page.get("markdown_content") or "").strip()
        if not body:
            return {"chunks_written": 0, "rules_written": 0}

        # 计算 wiki_page 切片向量（遵循算力隔离，仅走 embedding_router）
        embedding_blob: bytes | None = None
        provider = await embedding_router.get_provider()
        if provider.is_available and provider.dimension > 0:
            try:
                vectors = await provider.encode_batch([body[:2000]])
                if vectors is None:
                    vectors = await provider.embed_texts([body[:2000]])
                if vectors is not None and len(vectors) > 0:
                    embedding_blob = _to_float32_bytes(vectors[0])
                    emb_model = getattr(provider, "model_name", emb_model)
                    emb_dim = int(provider.dimension)
            except Exception as err:
                logger.warning(f"[WikiSync] 计算 wiki_page 向量失败，降级为全文检索: {err}")

        chunk_id = f"wiki_{page['page_id']}"
        eff_date = str(page.get("effective_date") or "").strip()
        doc_year = int(eff_date[:4]) if len(eff_date) >= 4 and eff_date[:4].isdigit() else 0
        eff_ts = 0
        if len(eff_date) == 10:
            try:
                eff_ts = int(time.mktime(time.strptime(eff_date, "%Y-%m-%d")))
            except Exception:
                eff_ts = 0

        chunk_dict: dict[str, Any] = {
            "id": chunk_id,
            "remote_uri": f"wiki://{page['wiki_space_id']}/{page['slug']}",
            "title": page.get("title") or page["slug"],
            "content": body,
            "authority_tier": 1 if page.get("authority_level") == "official" else 2,
            "embedding_blob": embedding_blob,
            "doc_year": doc_year,
            "effective_date": eff_date,
            "effective_ts": eff_ts,
            "doc_priority_type": "official_wiki" if page.get("authority_level") == "official" else "normal",
            "chunk_role": "wiki_page",
            "parent_chunk_id": chunk_id,
            "section_path": page.get("category_path") or "Wiki",
            "skip_hype": 1,
        }

        total_chunks, _ = await asyncio.to_thread(
            KBStorageManager.upsert_chunks_to_version_db,
            kb_id=kb_id,
            version=version,
            chunks=[chunk_dict],
            embedding_model=emb_model,
            embedding_dim=emb_dim,
        )

        # 刷新关联知识库结构化规则卡片
        db_path = KBStorageManager.get_version_db_path(kb_id, version)
        profile = UniversalRuleExtractor.resolve_profile(domain_type, rule_profile_json)
        extracted_rules = UniversalRuleExtractor.extract_rules_from_parent_chunk(
            chunk=chunk_dict,
            kb_id=kb_id,
            profile=profile,
        )

        def _refresh_rules() -> int:
            if not db_path.exists():
                return 0
            conn = sqlite3.connect(str(db_path))
            try:
                with conn:
                    conn.execute("DELETE FROM kb_structured_rules WHERE source_chunk_id = ?", (chunk_id,))
            except Exception:
                pass
            finally:
                conn.close()
            if extracted_rules:
                return StructuredRuleExtractor.batch_save_rules(db_path=db_path, rules=extracted_rules)
            return 0

        rules_written = await asyncio.to_thread(_refresh_rules)

        async with db_manager.get_connection() as conn:
            await conn.execute(
                "UPDATE knowledge_bases SET chunk_count = ?, updated_at = ? WHERE kb_id = ?",
                (total_chunks, time.time(), kb_id),
            )
            await conn.commit()

        logger.info(
            f"[WikiSync] 词条 [{page['slug']}] 已反哺知识库 [{kb_id}@{version}] "
            f"(wiki_page=1, rules={rules_written})"
        )
        return {"chunks_written": 1, "rules_written": rules_written}

    @staticmethod
    def _classify_markdown_section(heading: str) -> str:
        """根据二级标题关键词自动归类至标准 Wiki 目录。"""
        h = (heading or "").strip()
        if any(k in h for k in ("表", "映射", "清理", "对照")):
            return "01-物理表与模块映射"
        if any(k in h for k in ("口径", "人次", "金额", "代码", "术语", "人数")):
            return "02-业务术语与统计口径"
        if any(k in h for k in ("红线", "防坑", "关联", "血缘")):
            return "03-数据库关联与红线"
        if any(k in h for k in ("SQL", "sql", "范例", "校准", "提问")):
            return "04-标准SQL校准范例"
        if any(k in h for k in ("对账", "流水", "快照")):
            return "05-确定性对账与快照"
        return "00-总纲概览"

    @classmethod
    def _append_audit_log(
        cls, space_root: Path, action: str, target_path: str, detail: str
    ) -> None:
        """向空间 log.md 追加变更审计流水。"""
        log_file = space_root / "log.md"
        ts = _now_str()
        if not log_file.exists():
            log_file.write_text(
                "# Wiki 变更审计流水\n\n| 时间 | 操作 | 词条路径 | 变更说明 |\n|---|---|---|---|\n",
                encoding="utf-8",
            )
        safe_detail = (detail or "").replace("\n", " ").replace("|", "/")[:120]
        with log_file.open("a", encoding="utf-8") as fh:
            fh.write(f"| {ts} | **{action}** | `{target_path}` | {safe_detail} |\n")

    @classmethod
    async def _rebuild_space_index(cls, wiki_space_id: str, space_root: Path) -> None:
        """根据已发布词条重建空间根目录 index.md 总导航。"""
        pages = await cls.list_pages(wiki_space_id, status="published")
        grouped: dict[str, list[dict[str, Any]]] = {}
        for p in pages:
            cat = p.get("category_path") or "未分类"
            grouped.setdefault(cat, []).append(p)

        lines = [
            f"# Wiki 总导航目录 (Index)",
            f"> 最后同步时间：`{_now_str()}`",
            "",
        ]
        for cat in sorted(grouped.keys()):
            lines.append(f"## {cat}")
            for p in grouped[cat]:
                lines.append(f"- [{p['title']}]({p['file_path']})")
            lines.append("")

        index_file = space_root / "index.md"
        tmp_file = space_root / "index.md.tmp"
        tmp_file.write_text("\n".join(lines), encoding="utf-8")
        os.replace(tmp_file, index_file)

    @classmethod
    async def import_markdown_split(
        cls,
        *,
        wiki_space_id: str,
        filename: str,
        content: str,
        auto_split: bool = True,
        auto_publish: bool = True,
        kb_id: str = "",
    ) -> dict[str, Any]:
        """导入总纲 Markdown：原件备份至 raw/，按 ## 二级标题自动拆解并双写落盘。"""
        space = await cls.get_space(wiki_space_id)
        if not space:
            raise ValueError(f"Wiki 空间不存在: {wiki_space_id}")

        space_root = cls.get_space_root(wiki_space_id, space.get("root_dir_path"))
        raw_dir = space_root / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        safe_name = _sanitize_segment(Path(filename).name, "imported_guide.md")
        if not safe_name.lower().endswith(".md"):
            safe_name += ".md"
        raw_path = raw_dir / safe_name
        raw_path.write_text(content or "", encoding="utf-8")

        sections: list[tuple[str, str]] = []
        if auto_split:
            parts = re.split(r"(?m)^##\s+", content or "")
            preamble = parts[0].strip()
            for idx, part in enumerate(parts[1:], start=1):
                lines = part.splitlines()
                if not lines:
                    continue
                heading = lines[0].strip()
                body = f"## {heading}\n" + "\n".join(lines[1:]).strip()
                sections.append((heading, body))
            if not sections and preamble:
                sections.append((Path(safe_name).stem, preamble))
        else:
            sections.append((Path(safe_name).stem, (content or "").strip()))

        review_status = "published" if auto_publish else "inbox"
        created_pages: list[dict[str, Any]] = []
        for idx, (heading, sec_body) in enumerate(sections, start=1):
            category_path = cls._classify_markdown_section(heading)
            clean_slug_title = re.sub(r"^[一二三四五六七八九十\d]+[、.\s]+", "", heading).strip() or heading
            slug = _sanitize_segment(f"{idx:02d}_{clean_slug_title}", f"sec_{idx}")[:60]
            page = await cls.upsert_page(
                wiki_space_id=wiki_space_id,
                slug=slug,
                title=heading,
                markdown_content=sec_body,
                category_path=category_path,
                authority_level="official" if auto_publish else "decision",
                review_status=review_status,
                kb_id=kb_id or space.get("default_kb_id") or "",
                source_refs=[
                    {
                        "type": "kb_doc",
                        "ref_id": safe_name,
                        "title": safe_name,
                        "timestamp": _now_str(),
                    }
                ],
                stitch_links=True,
            )
            created_pages.append(page)

        cls._append_audit_log(
            space_root,
            "IMPORT_SPLIT",
            f"raw/{safe_name}",
            f"自动拆解生成 {len(created_pages)} 篇结构化词条",
        )
        await cls._rebuild_space_index(wiki_space_id, space_root)
        return {
            "raw_file": f"raw/{safe_name}",
            "imported_count": len(created_pages),
            "pages": created_pages,
        }

    @classmethod
    async def delete_page(
        cls,
        page_id: str,
        *,
        hard_delete: bool = False,
        reason: str = "",
    ) -> dict[str, Any]:
        """删除 Wiki 词条（支持软删除移入 .trash/ 或物理硬删除）。"""
        detail = await cls.get_page_detail(page_id)
        if not detail:
            raise ValueError(f"Wiki 词条不存在: {page_id}")

        space_id = detail["wiki_space_id"]
        space = await cls.get_space(space_id)
        space_root = cls.get_space_root(space_id, space.get("root_dir_path") if space else None)
        rel_path = detail["file_path"]
        abs_path = (space_root / rel_path).resolve()

        if hard_delete:
            if abs_path.exists() and abs_path.is_file():
                abs_path.unlink()
            async with db_manager.get_connection() as conn:
                await conn.execute("DELETE FROM wiki_links WHERE source_page_id = ?", (page_id,))
                await conn.execute("DELETE FROM wiki_pages WHERE page_id = ?", (page_id,))
                await conn.commit()
        else:
            if abs_path.exists() and abs_path.is_file():
                trash_dir = space_root / ".trash"
                trash_dir.mkdir(parents=True, exist_ok=True)
                trash_target = trash_dir / f"{int(time.time())}_{abs_path.name}"
                os.replace(abs_path, trash_target)
            async with db_manager.get_connection() as conn:
                await conn.execute("DELETE FROM wiki_links WHERE source_page_id = ?", (page_id,))
                await conn.execute(
                    "UPDATE wiki_pages SET review_status = 'discarded', updated_at = ? WHERE page_id = ?",
                    (_now_str(), page_id),
                )
                await conn.commit()

        cls._append_audit_log(
            space_root,
            "HARD_DELETE" if hard_delete else "SOFT_DELETE",
            rel_path,
            reason or f"删除词条 {detail['title']}",
        )
        await cls._rebuild_space_index(space_id, space_root)
        return {"page_id": page_id, "hard_delete": hard_delete, "status": "deleted"}

    @classmethod
    async def export_ontology_bundle(
        cls,
        *,
        wiki_space_id: str = "",
        query: str = "",
        limit: int = 12,
    ) -> dict[str, Any]:
        """导出已发布业务口径与 SQL 红线精简包，供数字专家查数前直接阅读（严格排除 stats_snapshot 统计大表）。"""
        sql_conds = ["review_status = 'published'", "COALESCE(doc_type, 'guide') != 'stats_snapshot'"]
        params: list[Any] = []
        if wiki_space_id:
            sql_conds.append("wiki_space_id = ?")
            params.append(wiki_space_id)
        kw = (query or "").strip().lower()
        if kw:
            sql_conds.append("(LOWER(title) LIKE ? OR LOWER(summary_text) LIKE ? OR LOWER(category_path) LIKE ?)")
            like = f"%{kw}%"
            params.extend([like, like, like])

        async with db_manager.get_connection() as conn:
            await cls._ensure_snapshot_columns(conn)
            async with conn.execute(
                f"""
                SELECT page_id, wiki_space_id, slug, title, category_path, file_path, updated_at
                FROM wiki_pages
                WHERE {' AND '.join(sql_conds)}
                ORDER BY category_path ASC, updated_at DESC
                LIMIT ?
                """,
                (*params, max(1, limit)),
            ) as cur:
                rows = [dict(r) for r in await cur.fetchall()]

        sections: list[str] = []
        for r in rows:
            space = await cls.get_space(r["wiki_space_id"])
            space_root = cls.get_space_root(
                r["wiki_space_id"], space.get("root_dir_path") if space else None
            )
            abs_p = space_root / r["file_path"]
            body = cls.read_markdown_body(abs_p) if abs_p.exists() else ""
            if body:
                sections.append(f"### [{r['category_path']}] {r['title']}\n{body[:2400]}")

        return {
            "count": len(sections),
            "pages": rows,
            "bundle_markdown": "\n\n---\n\n".join(sections),
        }

    @classmethod
    async def save_stats_snapshot(
        cls,
        *,
        agent_id: str,
        title: str,
        markdown_content: str,
        query_scope: str,
        verified_sqls: list[str],
        profile_id: str = "",
        captured_at: str = "",
        visibility_role: str = "hq_admin",
        visibility_scope_hash: str = "hq_global",
    ) -> dict[str, Any]:
        """将已通过红线审计的统计结果沉淀为百科统计快照（自动按专家创建并绑定百科空间，写入权限可见性标签）。"""
        space = await cls.ensure_expert_wiki_space(agent_id)
        ts_str = captured_at.strip() or _now_str()
        clean_title = title.strip()[:80] or f"统计快照_{ts_str[:10]}"
        slug_hash = hashlib.md5(f"{agent_id}:{visibility_role}:{visibility_scope_hash}:{clean_title}".encode("utf-8")).hexdigest()[:8]
        slug = f"stats_{slug_hash}"

        sql_block = ""
        clean_sqls = [s.strip() for s in verified_sqls if s and s.strip()]
        if clean_sqls:
            joined = "\n\n-- next query --\n\n".join(clean_sqls)
            sql_block = f"\n\n### 已验证 SQL (`verified_sql`)\n```sql\n{joined}\n```\n"

        full_body = (
            f"> ⏱️ **统计时间**：`{ts_str}`  \n"
            f"> 🎯 **统计主题与口径范围**：{query_scope.strip() or clean_title}\n\n"
            f"{markdown_content.strip()}"
            f"{sql_block}"
        )

        snapshot_meta = {
            "captured_at": ts_str,
            "query_scope": query_scope.strip() or clean_title,
            "verified_sql": clean_sqls[0] if len(clean_sqls) == 1 else clean_sqls,
            "verified_sqls": clean_sqls,
            "profile_id": profile_id,
            "expert_id": agent_id,
            "visibility_role": visibility_role or "hq_admin",
            "visibility_scope_hash": visibility_scope_hash or "hq_global",
        }
        page = await cls.upsert_page(
            wiki_space_id=space["wiki_space_id"],
            slug=slug,
            title=clean_title,
            markdown_content=full_body,
            category_path="统计数据快照",
            authority_level="fact",
            review_status="published",
            effective_date=ts_str[:10],
            summary_text=f"统计时间 {ts_str} | {query_scope.strip()[:120]}",
            doc_type="stats_snapshot",
            snapshot_meta=snapshot_meta,
            stitch_links=False,
        )
        return {
            "page": page,
            "wiki_space_id": space["wiki_space_id"],
            "wiki_space_name": space.get("name", ""),
            "captured_at": ts_str,
        }

    @classmethod
    async def search_stats_snapshots(
        cls,
        *,
        agent_id: str,
        user_query: str,
        limit: int = 3,
        caller: Any | None = None,
    ) -> list[dict[str, Any]]:
        """在专家绑定的百科空间内检索 doc_type='stats_snapshot' 的历史统计快照（严格校验权限可见性）。"""
        from ..repository.agent_repository import AgentProfileRepository
        from ..schema.access_control import is_snapshot_visible_to_caller

        agent = await AgentProfileRepository.get_agent(agent_id)
        if not agent:
            return []
        bound_spaces = [str(s) for s in (agent.get("bound_wiki_spaces") or []) if s]
        if not bound_spaces:
            return []

        placeholders = ",".join("?" for _ in bound_spaces)
        async with db_manager.get_connection() as conn:
            await cls._ensure_snapshot_columns(conn)
            async with conn.execute(
                f"""
                SELECT page_id, wiki_space_id, slug, title, category_path, file_path,
                       summary_text, snapshot_meta_json, updated_at
                FROM wiki_pages
                WHERE wiki_space_id IN ({placeholders})
                  AND review_status = 'published'
                  AND doc_type = 'stats_snapshot'
                ORDER BY updated_at DESC
                LIMIT 30
                """,
                bound_spaces,
            ) as cur:
                rows = [dict(r) for r in await cur.fetchall()]

        if not rows:
            return []

        # 基于字符二元组 + 关键词重叠度做轻量打分，跨行业通用且零外部依赖
        q_clean = re.sub(r"[\s，。！？、,.!?()（）\[\]【】]+", "", user_query.lower())
        q_bigrams = {q_clean[i : i + 2] for i in range(max(0, len(q_clean) - 1))}
        if not q_bigrams:
            return []

        scored: list[tuple[float, dict[str, Any]]] = []
        for r in rows:
            try:
                meta = json.loads(r.get("snapshot_meta_json") or "{}")
            except Exception:
                meta = {}
            if not is_snapshot_visible_to_caller(meta, caller):
                continue
            hay = re.sub(
                r"[\s，。！？、,.!?()（）\[\]【】]+",
                "",
                f"{r.get('title', '')}{meta.get('query_scope', '')}{r.get('summary_text', '')}".lower(),
            )
            h_bigrams = {hay[i : i + 2] for i in range(max(0, len(hay) - 1))}
            if not h_bigrams:
                continue
            overlap = len(q_bigrams & h_bigrams) / max(1, min(len(q_bigrams), len(h_bigrams)))
            if overlap < 0.35:
                continue

            space = await cls.get_space(r["wiki_space_id"])
            space_root = cls.get_space_root(
                r["wiki_space_id"], space.get("root_dir_path") if space else None
            )
            abs_p = space_root / r["file_path"]
            body = cls.read_markdown_body(abs_p) if abs_p.exists() else ""
            r["body_markdown"] = body
            r["captured_at"] = meta.get("captured_at") or str(r.get("updated_at") or "")
            r["query_scope"] = meta.get("query_scope") or r.get("title") or ""
            r["verified_sqls"] = meta.get("verified_sqls") or []
            r["match_score"] = round(overlap, 3)
            scored.append((overlap, r))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[:limit]]

    @classmethod
    async def search_published_wiki_guides(
        cls,
        *,
        agent_id: str = "",
        workspace_id: str = "",
        user_query: str = "",
        limit: int = 3,
        stale_days: int = 30,
    ) -> list[dict[str, Any]]:
        """Level 1 百科优先检索：在专家绑定空间 + 当前沙盒空间内检索已审核发布的百科词条（匹配标题+摘要+正文，并评估时效）。"""
        from datetime import datetime, timezone
        from ..repository.agent_repository import AgentProfileRepository

        space_ids: list[str] = []
        if agent_id:
            agent = await AgentProfileRepository.get_agent(agent_id)
            if agent:
                for sp in agent.get("bound_wiki_spaces") or []:
                    s = str(sp).strip()
                    if s and s not in space_ids:
                        space_ids.append(s)

        clean_ws = (workspace_id or "").strip()
        if clean_ws:
            safe_ws_key = re.sub(r"[^a-zA-Z0-9_-]", "_", clean_ws)[:32]
            ws_sp = f"wiki_ws_{safe_ws_key}"
            if ws_sp not in space_ids:
                space_ids.append(ws_sp)

        if not space_ids or not (user_query or "").strip():
            return []

        placeholders = ",".join("?" for _ in space_ids)
        async with db_manager.get_connection() as conn:
            await cls._ensure_snapshot_columns(conn)
            async with conn.execute(
                f"""
                SELECT page_id, wiki_space_id, slug, title, category_path, file_path,
                       summary_text, effective_date, updated_at
                FROM wiki_pages
                WHERE wiki_space_id IN ({placeholders})
                  AND review_status = 'published'
                  AND COALESCE(doc_type, 'guide') != 'stats_snapshot'
                ORDER BY updated_at DESC
                LIMIT 40
                """,
                space_ids,
            ) as cur:
                rows = [dict(r) for r in await cur.fetchall()]

        if not rows:
            return []

        # 提取查询关键词与字符二元组（去除口语虚词，提高“有联系电话吗 -> 官方联系方式/电话”召回率）
        q_norm = re.sub(r"[\s，。！？、,.!?()（）\[\]【】吗呢吧啊的有是在和与请问帮我查一下]+", "", user_query.lower())
        q_bigrams = {q_norm[i : i + 2] for i in range(max(0, len(q_norm) - 1))}
        if not q_bigrams:
            return []

        now_utc = datetime.now(timezone.utc)
        scored: list[tuple[float, dict[str, Any]]] = []
        for r in rows:
            space = await cls.get_space(r["wiki_space_id"])
            space_root = cls.get_space_root(
                r["wiki_space_id"], space.get("root_dir_path") if space else None
            )
            abs_p = space_root / r["file_path"]
            body = cls.read_markdown_body(abs_p) if abs_p.exists() else ""

            title_hay = re.sub(r"[\s，。！？、,.!?()（）\[\]【】]+", "", str(r.get("title") or "").lower())
            body_hay = re.sub(r"[\s，。！？、,.!?()（）\[\]【】]+", "", f"{r.get('summary_text', '')}{body[:4000]}".lower())
            full_hay = f"{title_hay}{body_hay}"
            if not full_hay:
                continue

            h_bigrams = {full_hay[i : i + 2] for i in range(max(0, len(full_hay) - 1))}
            hit_bigrams = q_bigrams & h_bigrams
            if not hit_bigrams:
                continue

            # 标题命中额外加权 + 正文二元组覆盖率
            t_bigrams = {title_hay[i : i + 2] for i in range(max(0, len(title_hay) - 1))}
            title_hits = len(q_bigrams & t_bigrams)
            coverage = len(hit_bigrams) / max(1, len(q_bigrams))
            score = coverage + (0.25 if title_hits >= 1 else 0.0)
            if score < 0.25:
                continue

            # 计算词条存储时长（天），判断是否过长需结合后续知识库/实时库核实
            age_days = 0
            upd_str = str(r.get("updated_at") or "").strip()
            if upd_str:
                try:
                    dt = datetime.fromisoformat(upd_str.replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    age_days = max(0, int((now_utc - dt).total_seconds() // 86400))
                except Exception:
                    age_days = 0

            r["wiki_space_name"] = (space or {}).get("name") or r["wiki_space_id"]
            r["body_markdown"] = body
            r["match_score"] = round(score, 3)
            r["age_days"] = age_days
            r["is_stale"] = age_days > stale_days
            scored.append((score, r))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[:limit]]

    @classmethod
    async def delete_space(cls, wiki_space_id: str) -> dict[str, Any]:
        """删除整个 Wiki 空间，并清理其下所有词条、双链索引与物理目录。"""
        import shutil

        space = await cls.get_space(wiki_space_id)
        if not space:
            raise ValueError(f"Wiki 空间不存在: {wiki_space_id}")

        space_root = cls.get_space_root(wiki_space_id, space.get("root_dir_path"))
        async with db_manager.get_connection() as conn:
            await conn.execute(
                "DELETE FROM wiki_links WHERE source_page_id IN (SELECT page_id FROM wiki_pages WHERE wiki_space_id = ?)",
                (wiki_space_id,),
            )
            cur_pages = await conn.execute(
                "DELETE FROM wiki_pages WHERE wiki_space_id = ?",
                (wiki_space_id,),
            )
            await conn.execute(
                "DELETE FROM wiki_spaces WHERE wiki_space_id = ?",
                (wiki_space_id,),
            )
            await conn.commit()

        if space_root.exists() and space_root.is_dir():
            try:
                shutil.rmtree(space_root, ignore_errors=True)
            except OSError as rm_err:
                logger.warning(f"清理 Wiki 空间物理目录失败 ({space_root}): {rm_err}")

        return {
            "wiki_space_id": wiki_space_id,
            "deleted_pages_count": cur_pages.rowcount or 0,
        }

