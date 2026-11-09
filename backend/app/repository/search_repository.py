"""
FTS5 全文检索仓储层 (Search Repository)
基于 SQLite FTS5 (trigram) 实现毫秒级中英混合、代码符号及子串关键词检索
支持短词自动降级与关键词高亮提取
"""
import re
from typing import Any
from .db import db_manager
from ..service.attachment_service import attachment_service

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
