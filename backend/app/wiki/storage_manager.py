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
from pathlib import Path
from typing import Any

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
            content_md5 = cls.write_markdown_file(
                abs_path,
                {
                    "page_id": final_page_id,
                    "slug": clean_slug,
                    "title": clean_title,
                    "authority_level": authority_level,
                    "effective_date": effective_date,
                    "kb_id": kb_id,
                },
                clean_body,
            )

            await conn.execute(
                """
                INSERT INTO wiki_pages (
                    page_id, wiki_space_id, kb_id, slug, title, category_path, file_path,
                    authority_level, review_status, effective_date, summary_text,
                    source_refs_json, pending_diff_markdown, content_md5, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
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
            async with conn.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    @classmethod
    async def preview_by_slug(cls, wiki_space_id: str, slug: str) -> dict[str, Any] | None:
        """双链极速预览接口，返回词条摘要卡片字段。"""
        async with db_manager.get_connection() as conn:
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

