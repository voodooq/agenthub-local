"""
消息明细仓储层 (Message Repository)
"""
from typing import Any
from ..model.models import MessageModel
from .db import db_manager


class MessageRepository:
    """消息数据访问对象"""
    
    @staticmethod
    async def get_by_conversation(
        conv_id: str,
        limit: int | None = None,
        from_latest: bool = True,
    ) -> list[MessageModel]:
        """
        获取指定会话的历史消息数组（始终按创建时间升序返回）。

        NOTE: 必须区分全量读取（limit=None，供前端完整回显超长会话）与滑窗截取
        （from_latest=True 取最近 N 条再恢复正序），防止超过 100 条的长会话截断最新对话。
        """
        async with db_manager.get_connection() as conn:
            if limit is None or limit <= 0:
                sql = "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC, id ASC"
                params: tuple[Any, ...] = (conv_id,)
            elif from_latest:
                sql = """
                SELECT * FROM (
                    SELECT * FROM messages
                    WHERE conversation_id = ?
                    ORDER BY created_at DESC, id DESC
                    LIMIT ?
                )
                ORDER BY created_at ASC, id ASC
                """
                params = (conv_id, limit)
            else:
                sql = "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC, id ASC LIMIT ?"
                params = (conv_id, limit)

            async with conn.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
                return [MessageModel.from_row(row) for row in rows]

    @staticmethod
    async def get_by_turn_window(conv_id: str, min_turn_id: int) -> list[MessageModel]:
        """按轮次号 (turn_id) 窗口获取历史消息"""
        async with db_manager.get_connection() as conn:
            async with conn.execute(
                """
                SELECT * FROM messages
                WHERE conversation_id = ? AND COALESCE(turn_id, 1) >= ?
                ORDER BY created_at ASC, id ASC
                """,
                (conv_id, min_turn_id)
            ) as cursor:
                rows = await cursor.fetchall()
                return [MessageModel.from_row(row) for row in rows]

    @staticmethod
    async def get_max_turn_id(conv_id: str) -> int:
        """获取指定会话当前最大的 turn_id"""
        async with db_manager.get_connection() as conn:
            async with conn.execute(
                "SELECT COALESCE(MAX(turn_id), 0) as max_turn FROM messages WHERE conversation_id = ?",
                (conv_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return int(row["max_turn"]) if row and row["max_turn"] is not None else 0

    @staticmethod
    async def save_message(
        conversation_id: str,
        role: str,
        content: str,
        executed_by_account: str | None = None,
        token_count: int = 0,
        turn_id: int = 1,
        compressed_content: str | None = None,
        tool_calls_json: str | None = None,
        tool_call_id: str | None = None,
    ) -> int:
        """
        持久化单条消息（支持双层循环 turn_id、历史命令折叠摘要与工具调用参数）
        # NOTE: 插入 messages 表时会由底层触发器自动同步建立 FTS5 全文索引
        """
        async with db_manager.get_connection() as conn:
            cursor = await conn.execute(
                """
                INSERT INTO messages (
                    conversation_id, turn_id, role, content,
                    compressed_content, tool_calls_json, tool_call_id,
                    executed_by_account, token_count
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    turn_id,
                    role,
                    content,
                    compressed_content,
                    tool_calls_json,
                    tool_call_id,
                    executed_by_account,
                    token_count,
                )
            )
            await conn.commit()
            return cursor.lastrowid or 0

    @staticmethod
    async def update_message_content(
        msg_id: int,
        content: str,
        token_count: int | None = None,
    ) -> None:
        """原地更新已落盘消息正文（用于工具过程步骤与最终交付结果的拆分落盘）。"""
        if not msg_id:
            return
        async with db_manager.get_connection() as conn:
            if token_count is not None:
                await conn.execute(
                    "UPDATE messages SET content = ?, token_count = ? WHERE id = ?",
                    (content, token_count, msg_id),
                )
            else:
                await conn.execute(
                    "UPDATE messages SET content = ? WHERE id = ?",
                    (content, msg_id),
                )
            await conn.commit()
