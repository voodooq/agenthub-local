"""
会话仓储层 (Conversation Repository)
封装会话创建、列表置顶查询、更新与级联删除
"""
from pathlib import Path
import shutil
from typing import Any
from ..model.models import ConversationModel
from .db import db_manager


class ConversationRepository:
    """会话数据访问对象"""
    
    @staticmethod
    async def get_all(workspace_id: str | None = None) -> list[dict[str, Any]]:
        """获取所有会话列表（置顶优先，时间降序），附带消息条数统计，支持按工作区过滤"""
        async with db_manager.get_connection() as conn:
            where_clause = "WHERE c.workspace_id = ?" if workspace_id else ""
            params = (workspace_id,) if workspace_id else ()
            sql = f"""
            SELECT c.*, COUNT(m.id) as message_count
            FROM conversations c
            LEFT JOIN messages m ON c.id = m.conversation_id
            {where_clause}
            GROUP BY c.id
            ORDER BY c.is_pinned DESC, c.updated_at DESC
            """
            async with conn.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
                results: list[dict[str, Any]] = []
                for row in rows:
                    item = dict(row)
                    # NOTE: SQLite 无原生布尔类型，返回前规范化为 bool，避免前端 JSX 条件渲染数字 0
                    item["is_pinned"] = bool(item.get("is_pinned", 0))
                    item["incognito"] = bool(item.get("incognito", 0))
                    results.append(item)
                return results

    @staticmethod
    async def get_by_id(conv_id: str) -> ConversationModel | None:
        """通过 UUID 查询指定会话"""
        async with db_manager.get_connection() as conn:
            async with conn.execute("SELECT * FROM conversations WHERE id = ?", (conv_id,)) as cursor:
                row = await cursor.fetchone()
                return ConversationModel.from_row(row) if row else None

    @staticmethod
    async def create(data: dict[str, Any]) -> None:
        """创建新会话记录，支持指定工作区与工作目录"""
        import os
        default_cwd = data.get("current_cwd") or os.getcwd()
        workspace_id = data.get("workspace_id", "default")
        async with db_manager.get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO conversations (
                    id, title, source_type, source_account, model_name,
                    is_pinned, scene_mode, incognito, working_memory_json, current_cwd, workspace_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["id"],
                    data["title"],
                    data.get("source_type", "local"),
                    data.get("source_account"),
                    data.get("model_name", "4 Argon"),
                    1 if data.get("is_pinned") else 0,
                    data.get("scene_mode", "agent"),
                    1 if data.get("incognito") else 0,
                    data.get("working_memory_json", "{}"),
                    default_cwd,
                    workspace_id,
                )
            )
            await conn.commit()

    @staticmethod
    async def update_agent_state(
        conv_id: str,
        scene_mode: str | None = None,
        incognito: bool | None = None,
        working_memory_json: str | None = None,
        rolling_summary: str | None = None,
        current_cwd: str | None = None,
    ) -> None:
        """更新会话的微内核 Agent 运行时状态（工作草稿、场景模式、无痕模式、CWD、早期摘要）"""
        updates = []
        params: list[Any] = []
        if scene_mode is not None:
            updates.append("scene_mode = ?")
            params.append(scene_mode)
        if incognito is not None:
            updates.append("incognito = ?")
            params.append(1 if incognito else 0)
        if working_memory_json is not None:
            updates.append("working_memory_json = ?")
            params.append(working_memory_json)
        if rolling_summary is not None:
            updates.append("rolling_summary = ?")
            params.append(rolling_summary)
        if current_cwd is not None:
            updates.append("current_cwd = ?")
            params.append(current_cwd)

        if not updates:
            return

        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(conv_id)

        async with db_manager.get_connection() as conn:
            await conn.execute(
                f"UPDATE conversations SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            await conn.commit()

    @staticmethod
    async def update_collapse_anchor(
        conv_id: str,
        anchor_turn_id: int,
        anchored_summary: str,
        added_saved_tokens: int = 0,
    ) -> None:
        """原子推进分块坍缩锚点水位线、固化前缀摘要并累加省下的 Token 总数"""
        safe_added = max(0, int(added_saved_tokens or 0))
        async with db_manager.get_connection() as conn:
            await conn.execute(
                """
                UPDATE conversations
                SET collapse_anchor_turn_id = MAX(COALESCE(collapse_anchor_turn_id, 0), ?),
                    anchored_summary = ?,
                    collapsed_tokens_saved = COALESCE(collapsed_tokens_saved, 0) + ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (int(anchor_turn_id), anchored_summary, safe_added, conv_id),
            )
            await conn.commit()

    @staticmethod
    async def update(conv_id: str, title: str | None = None, is_pinned: bool | None = None) -> None:
        """更新会话标题或置顶状态"""
        updates = []
        params = []
        if title is not None:
            updates.append("title = ?")
            params.append(title)
        if is_pinned is not None:
            updates.append("is_pinned = ?")
            params.append(1 if is_pinned else 0)
            
        if not updates:
            return
            
        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(conv_id)
        
        async with db_manager.get_connection() as conn:
            await conn.execute(
                f"UPDATE conversations SET {', '.join(updates)} WHERE id = ?",
                params
            )
            await conn.commit()

    @staticmethod
    async def get_last_extracted_turn_id(conv_id: str) -> int:
        """获取指定会话长期记忆已提炼的水位线 turn_id"""
        async with db_manager.get_connection() as conn:
            async with conn.execute(
                "SELECT last_extracted_turn_id FROM conversations WHERE id = ?",
                (conv_id,),
            ) as cursor:
                row = await cursor.fetchone()
                if row and row[0] is not None:
                    return int(row[0])
                return 0

    @staticmethod
    async def update_last_extracted_turn_id(conv_id: str, turn_id: int) -> None:
        """推进长期记忆已提炼的水位线游标"""
        async with db_manager.get_connection() as conn:
            await conn.execute(
                "UPDATE conversations SET last_extracted_turn_id = ? WHERE id = ?",
                (turn_id, conv_id),
            )
            await conn.commit()

    @staticmethod
    async def touch(conv_id: str) -> None:
        """更新最后活动时间"""
        async with db_manager.get_connection() as conn:
            await conn.execute(
                "UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (conv_id,)
            )
            await conn.commit()

    @staticmethod
    async def cleanup_duplicates() -> int:
        """
        智能清理重复标题的空闲会话以及消息数为 0 的废弃会话。
        针对相同标题的会话，保留最新的一条，自动安全清理历史冗余副本。
        :returns: 被清理的会话条数
        """
        async with db_manager.get_connection() as conn:
            # 1. 查找所有会话及其消息数
            sql = """
            SELECT c.id, c.title, c.is_pinned, c.updated_at, COUNT(m.id) as msg_count
            FROM conversations c
            LEFT JOIN messages m ON c.id = m.conversation_id
            GROUP BY c.id
            ORDER BY c.updated_at DESC
            """
            async with conn.execute(sql) as cursor:
                rows = await cursor.fetchall()

            seen_titles = set()
            to_delete_ids = []

            for row in rows:
                c_id = row["id"]
                title = row["title"]
                is_pinned = bool(row["is_pinned"])
                msg_count = row["msg_count"]

                # 若置顶，绝不自动清理
                if is_pinned:
                    seen_titles.add(title)
                    continue

                # 若消息数为 0，直接列入清理列表
                if msg_count == 0:
                    to_delete_ids.append(c_id)
                    continue

                # 若相同标题已存在最新的一个，且本会话不是唯一的，列入重复清理
                if title in seen_titles:
                    to_delete_ids.append(c_id)
                else:
                    seen_titles.add(title)

            # 批量安全删除
            deleted_count = 0
            for del_id in to_delete_ids:
                await conn.execute("DELETE FROM messages WHERE conversation_id = ?", (del_id,))
                await conn.execute("DELETE FROM conversations WHERE id = ?", (del_id,))
                deleted_count += 1

            await conn.commit()

        # NOTE: 在数据库事务提交后同步清理对应会话的磁盘 tool_refs 目录，避免孤儿大文件残留
        for del_id in to_delete_ids:
            ConversationRepository.cleanup_tool_refs_dir(del_id)

        return deleted_count

    @staticmethod
    def cleanup_tool_refs_dir(conv_id: str) -> None:
        """清理指定会话留在磁盘上的 tool_refs 大文件目录，防止磁盘泄漏"""
        if not conv_id:
            return
        safe_conv_id = Path(conv_id).name
        if not safe_conv_id or safe_conv_id in {".", ".."}:
            return
        refs_dir = Path(db_manager.db_path).resolve().parent / "tool_refs" / safe_conv_id
        if refs_dir.exists():
            shutil.rmtree(refs_dir, ignore_errors=True)

    @staticmethod
    async def delete(conv_id: str) -> bool:
        """级联删除会话及其所有关联消息，并同步回收磁盘 tool_refs 大文件目录"""
        async with db_manager.get_connection() as conn:
            cursor = await conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
            await conn.commit()
            deleted = cursor.rowcount > 0
        if deleted:
            ConversationRepository.cleanup_tool_refs_dir(conv_id)
        return deleted

    @staticmethod
    async def move_to_workspace(conv_id: str, new_workspace_id: str) -> None:
        """将已有会话迁移到指定的用户工作区"""
        async with db_manager.get_connection() as conn:
            await conn.execute(
                "UPDATE conversations SET workspace_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (new_workspace_id, conv_id),
            )
            await conn.commit()
