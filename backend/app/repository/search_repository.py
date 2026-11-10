"""
FTS5 全文检索仓储层 (Search Repository)
基于 SQLite FTS5 (trigram) 实现毫秒级中英混合、代码符号及子串关键词检索
支持短词自动降级与关键词高亮提取
"""
import re
from typing import Any
from .db import db_manager
from ..service.attachment_service import attachment_service
from ..memory.policy_guard import MemoryPolicyGuard

_UNCLOSED_ANCHOR_RE = re.compile(r"<agenthub_attachments>[\s\S]*$", re.IGNORECASE)


def _clean_search_snippet(text: str) -> str:
    """清洗搜索片段末尾的 <agenthub_attachments> 原始锚点及被截断的尾部片段"""
    cleaned = attachment_service.strip_attachments_for_display(text or "")
    return _UNCLOSED_ANCHOR_RE.sub("", cleaned).strip()


class SearchRepository:
    """全文搜索数据访问对象"""
    
    @staticmethod
    async def search_messages(query: str, limit: int = 50) -> list[dict[str, Any]]:
        """
        检索消息全文
        # NOTE: 长度 >= 3 使用 FTS5 trigram 毫秒级倒排索引；
        # 短词（1~2字）自动平滑降级为 LIKE 模式，兼顾极致性能与中文短词体验
        """
        clean_query = query.strip()
        if not clean_query:
            return []

        async with db_manager.get_connection() as conn:
            if len(clean_query) >= 3:
                fts_sql = """
                SELECT 
                    m.id as message_id,
                    m.conversation_id,
                    c.workspace_id as workspace_id,
                    m.role,
                    m.content,
                    m.executed_by_account,
                    m.created_at,
                    c.title as conversation_title,
                    snippet(messages_fts, 0, '<mark class="bg-amber-500/30 text-amber-200 px-1 rounded">', '</mark>', '...', 15) as highlighted_snippet
                FROM messages_fts f
                JOIN messages m ON f.rowid = m.id
                JOIN conversations c ON m.conversation_id = c.id
                WHERE messages_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """
                try:
                    # trigram 支持带双引号或裸词检索
                    search_term = f'"{clean_query.replace(chr(34), "")}"'
                    async with conn.execute(fts_sql, (search_term, limit)) as cursor:
                        rows = await cursor.fetchall()
                        if rows:
                            fts_results: list[dict[str, Any]] = []
                            for row in rows:
                                item = dict(row)
                                item["content"] = attachment_service.strip_attachments_for_display(item.get("content", ""))
                                item["highlighted_snippet"] = _clean_search_snippet(item.get("highlighted_snippet", ""))
                                fts_results.append(item)
                            return fts_results
                except Exception:
                    pass

            # 短词或降级检索
            like_sql = """
            SELECT 
                m.id as message_id,
                m.conversation_id,
                c.workspace_id as workspace_id,
                m.role,
                m.content,
                m.executed_by_account,
                m.created_at,
                c.title as conversation_title,
                m.content as highlighted_snippet
            FROM messages m
            JOIN conversations c ON m.conversation_id = c.id
            WHERE m.content LIKE ?
            ORDER BY m.created_at DESC
            LIMIT ?
            """
            async with conn.execute(like_sql, (f'%{clean_query}%', limit)) as cursor:
                rows = await cursor.fetchall()
                results = []
                for row in rows:
                    item = dict(row)
                    # 内存高亮（先剥离原始附件 JSON 锚点再切片高亮）
                    raw_content = attachment_service.strip_attachments_for_display(item.get("content", ""))
                    item["content"] = raw_content
                    highlighted = raw_content.replace(
                        clean_query,
                        f'<mark class="bg-amber-500/30 text-amber-200 px-1 rounded">{clean_query}</mark>'
                    )
                    item["highlighted_snippet"] = highlighted[:180]
                    results.append(item)
                return results

    @staticmethod
    async def search_conversation_cold_history(
        conversation_id: str,
        query: str,
        max_turn_id: int,
        limit: int = 2,
    ) -> list[dict[str, Any]]:
        """检索当前会话冷区（已坍缩移出热窗口：turn_id <= max_turn_id）中最相关的历史消息片段。

        NOTE: 严格限制 turn_id <= max_turn_id，避免与 Zone 2 热区对话重复召回造成 Token 浪费；
        结果全部经附件剥离与 MemoryPolicyGuard 隐私脱敏净化后方可注入 Zone 3 尾注。
        """
        if not conversation_id or max_turn_id <= 0:
            return []
        clean_query = attachment_service.strip_attachments_for_display(query or "").strip()
        if not clean_query:
            return []

        # 提取核心检索词（支持中英文词组及 trigram 子串）
        tokens = [
            tok.strip()
            for tok in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_.-]{3,}", clean_query)
            if len(tok.strip()) >= 2
        ]
        # 去重保持出现顺序，最多取前 5 个高信息密度关键词
        unique_keywords: list[str] = []
        seen_kw: set[str] = set()
        for tok in tokens:
            low = tok.lower()
            if low not in seen_kw:
                seen_kw.add(low)
                unique_keywords.append(tok)
            if len(unique_keywords) >= 5:
                break

        if not unique_keywords and len(clean_query) >= 2:
            unique_keywords = [clean_query[:24]]
        if not unique_keywords:
            return []

        safe_limit = max(1, min(5, int(limit or 2)))
        matched_rows: list[dict[str, Any]] = []

        async with db_manager.get_connection() as conn:
            # 1. 优先尝试 FTS5 trigram 对长度 >= 3 的关键词做 OR 检索（严格限定在冷区 turn_id <= max_turn_id）
            fts_terms = [
                f'"{kw.replace(chr(34), "")}"'
                for kw in unique_keywords
                if len(kw) >= 3
            ]
            if fts_terms:
                fts_match_expr = " OR ".join(fts_terms[:4])
                fts_sql = """
                SELECT
                    m.id AS message_id,
                    COALESCE(m.turn_id, 1) AS turn_id,
                    m.role,
                    m.content,
                    m.compressed_content,
                    m.created_at
                FROM messages_fts f
                JOIN messages m ON f.rowid = m.id
                WHERE m.conversation_id = ?
                  AND COALESCE(m.turn_id, 1) <= ?
                  AND messages_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """
                try:
                    async with conn.execute(
                        fts_sql,
                        (conversation_id, int(max_turn_id), fts_match_expr, safe_limit * 2),
                    ) as cursor:
                        matched_rows = [dict(r) for r in await cursor.fetchall()]
                except Exception:
                    matched_rows = []

            # 2. FTS 未命中或仅含 2 字中文短词时，降级至冷区 LIKE 多词匹配
            if not matched_rows:
                like_clauses = ["m.content LIKE ?" for _ in unique_keywords[:4]]
                like_params: list[Any] = [conversation_id, int(max_turn_id)]
                like_params.extend([f"%{kw}%" for kw in unique_keywords[:4]])
                like_params.append(safe_limit * 2)
                like_sql = f"""
                SELECT
                    m.id AS message_id,
                    COALESCE(m.turn_id, 1) AS turn_id,
                    m.role,
                    m.content,
                    m.compressed_content,
                    m.created_at
                FROM messages m
                WHERE m.conversation_id = ?
                  AND COALESCE(m.turn_id, 1) <= ?
                  AND ({" OR ".join(like_clauses)})
                ORDER BY COALESCE(m.turn_id, 1) DESC, m.id DESC
                LIMIT ?
                """
                async with conn.execute(like_sql, like_params) as cursor:
                    matched_rows = [dict(r) for r in await cursor.fetchall()]

        results: list[dict[str, Any]] = []
        seen_turn_roles: set[tuple[int, str]] = set()
        for row in matched_rows:
            t_id = int(row.get("turn_id") or 1)
            role = str(row.get("role") or "user")
            if (t_id, role) in seen_turn_roles:
                continue
            seen_turn_roles.add((t_id, role))

            raw_text = (
                row.get("compressed_content") or row.get("content") or ""
                if role == "tool"
                else (row.get("content") or "")
            )
            cleaned = _clean_search_snippet(raw_text)
            if not cleaned:
                continue
            sanitized, _ = MemoryPolicyGuard.sanitize_text(cleaned)
            snippet = sanitized[:280] + ("..." if len(sanitized) > 280 else "")
            results.append(
                {
                    "turn_id": t_id,
                    "role": role,
                    "snippet": snippet,
                    "created_at": row.get("created_at"),
                }
            )
            if len(results) >= safe_limit:
                break

        return results
