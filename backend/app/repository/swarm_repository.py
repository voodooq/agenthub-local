"""
Swarm 原生蜂群持久化仓库
管理 swarms、swarm_members、swarm_messages 三张核心表，
提供原子递增 seq 发帖、since 增量拉取与成员状态机持久化。
"""
import json
import uuid
from typing import Any

from .db import db_manager


class SwarmRepository:
    """Swarm 蜂群数据访问层"""

    @staticmethod
    def _row_to_swarm(row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "title": row["title"],
            "status": row["status"],
            "head_seq": int(row["head_seq"] or 0),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _row_to_member(row: Any) -> dict[str, Any]:
        def _parse_json_list(raw: str | None) -> list[str]:
            if not raw:
                return []
            try:
                val = json.loads(raw)
                return [str(x) for x in val] if isinstance(val, list) else []
            except Exception:
                return []

        return {
            "id": row["id"],
            "swarm_id": row["swarm_id"],
            "name": row["name"],
            "role": row["role"],
            "agent_id": row["agent_id"],
            "conv_id": row["conv_id"],
            "model": row["model"],
            "state": row["state"],
            "allowed_tools": _parse_json_list(row["allowed_tools_json"]) if row["allowed_tools_json"] is not None else None,
            "allowed_write_dirs": _parse_json_list(row["allowed_write_dirs_json"]) if row["allowed_write_dirs_json"] is not None else None,
            "skill_ids": _parse_json_list(row["skill_ids_json"]),
            "system_prompt": row["system_prompt"] or "",
            "last_seq": int(row["last_seq"] or 0),
            "brief": row["brief"] or "",
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _row_to_message(row: Any) -> dict[str, Any]:
        mentions: list[str] = []
        try:
            raw_m = json.loads(row["mentions_json"] or "[]")
            if isinstance(raw_m, list):
                mentions = [str(x) for x in raw_m]
        except Exception:
            mentions = []
        return {
            "id": int(row["id"]),
            "swarm_id": row["swarm_id"],
            "seq": int(row["seq"]),
            "sender": row["sender"],
            "text": row["text"],
            "kind": row["kind"],
            "mentions": mentions,
            "created_at": row["created_at"],
        }

    @classmethod
    async def create_swarm(
        cls,
        session_id: str,
        title: str = "Swarm 协作",
        swarm_id: str | None = None,
    ) -> dict[str, Any]:
        """创建新的 Swarm 实例。"""
        sid = swarm_id or f"swarm_{uuid.uuid4().hex[:12]}"
        async with db_manager.get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO swarms (id, session_id, title, status, head_seq)
                VALUES (?, ?, ?, 'active', 0)
                """,
                (sid, session_id, title),
            )
            await conn.commit()
            async with conn.execute("SELECT * FROM swarms WHERE id = ?", (sid,)) as cursor:
                row = await cursor.fetchone()
                return cls._row_to_swarm(row)

    @classmethod
    async def get_swarm(cls, swarm_id: str) -> dict[str, Any] | None:
        """按 swarm_id 获取 Swarm 元信息。"""
        async with db_manager.get_connection() as conn:
            async with conn.execute("SELECT * FROM swarms WHERE id = ?", (swarm_id,)) as cursor:
                row = await cursor.fetchone()
                return cls._row_to_swarm(row) if row else None

    @classmethod
    async def get_by_session(cls, session_id: str) -> dict[str, Any] | None:
        """按主会话 session_id 获取最新的 Swarm 实例。"""
        async with db_manager.get_connection() as conn:
            async with conn.execute(
                """
                SELECT * FROM swarms
                WHERE session_id = ?
                ORDER BY CASE WHEN status = 'active' THEN 0 ELSE 1 END, created_at DESC, rowid DESC
                LIMIT 1
                """,
                (session_id,),
            ) as cursor:
                row = await cursor.fetchone()
                return cls._row_to_swarm(row) if row else None

    @classmethod
    async def update_swarm(
        cls,
        swarm_id: str,
        *,
        status: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any] | None:
        """更新 Swarm 状态或标题。"""
        updates: list[str] = ["updated_at = CURRENT_TIMESTAMP"]
        params: list[Any] = []
        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if title is not None:
            updates.append("title = ?")
            params.append(title)
        params.append(swarm_id)

        async with db_manager.get_connection() as conn:
            await conn.execute(
                f"UPDATE swarms SET {', '.join(updates)} WHERE id = ?",
                tuple(params),
            )
            await conn.commit()
        return await cls.get_swarm(swarm_id)

    @classmethod
    async def upsert_member(
        cls,
        *,
        swarm_id: str,
        name: str,
        role: str = "worker",
        agent_id: str | None = None,
        conv_id: str | None = None,
        model: str | None = None,
        state: str = "waiting",
        allowed_tools: list[str] | None = None,
        allowed_write_dirs: list[str] | None = None,
        skill_ids: list[str] | None = None,
        system_prompt: str = "",
        brief: str = "",
    ) -> dict[str, Any]:
        """创建或更新 Swarm 成员配置与状态。"""
        member_id = f"mem_{uuid.uuid4().hex[:12]}"
        tools_json = json.dumps(allowed_tools, ensure_ascii=False) if allowed_tools is not None else None
        dirs_json = json.dumps(allowed_write_dirs, ensure_ascii=False) if allowed_write_dirs is not None else None
        skills_json = json.dumps(skill_ids or [], ensure_ascii=False)

        async with db_manager.get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO swarm_members (
                    id, swarm_id, name, role, agent_id, conv_id, model, state,
                    allowed_tools_json, allowed_write_dirs_json, skill_ids_json,
                    system_prompt, last_seq, brief
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                ON CONFLICT(swarm_id, name) DO UPDATE SET
                    role = excluded.role,
                    agent_id = COALESCE(excluded.agent_id, swarm_members.agent_id),
                    conv_id = COALESCE(excluded.conv_id, swarm_members.conv_id),
                    model = COALESCE(excluded.model, swarm_members.model),
                    state = excluded.state,
                    allowed_tools_json = excluded.allowed_tools_json,
                    allowed_write_dirs_json = excluded.allowed_write_dirs_json,
                    skill_ids_json = excluded.skill_ids_json,
                    system_prompt = excluded.system_prompt,
                    brief = excluded.brief,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    member_id, swarm_id, name, role, agent_id, conv_id, model, state,
                    tools_json, dirs_json, skills_json, system_prompt, brief,
                ),
            )
            await conn.commit()
            async with conn.execute(
                "SELECT * FROM swarm_members WHERE swarm_id = ? AND name = ?",
                (swarm_id, name),
            ) as cursor:
                row = await cursor.fetchone()
                return cls._row_to_member(row)

    @classmethod
    async def get_member(cls, swarm_id: str, name: str) -> dict[str, Any] | None:
        """获取指定 Swarm 成员。"""
        async with db_manager.get_connection() as conn:
            async with conn.execute(
                "SELECT * FROM swarm_members WHERE swarm_id = ? AND name = ?",
                (swarm_id, name),
            ) as cursor:
                row = await cursor.fetchone()
                return cls._row_to_member(row) if row else None

    @classmethod
    async def list_members(cls, swarm_id: str) -> list[dict[str, Any]]:
        """列出 Swarm 下全部成员（按创建时间升序）。"""
        async with db_manager.get_connection() as conn:
            async with conn.execute(
                "SELECT * FROM swarm_members WHERE swarm_id = ? ORDER BY created_at ASC, rowid ASC",
                (swarm_id,),
            ) as cursor:
                rows = await cursor.fetchall()
                return [cls._row_to_member(r) for r in rows]

    @classmethod
    async def update_member_state(
        cls,
        swarm_id: str,
        name: str,
        state: str,
        *,
        last_seq: int | None = None,
        model: str | None = None,
    ) -> dict[str, Any] | None:
        """更新成员状态机状态与消费水位。"""
        updates = ["state = ?", "updated_at = CURRENT_TIMESTAMP"]
        params: list[Any] = [state]
        if last_seq is not None:
            updates.append("last_seq = ?")
            params.append(int(last_seq))
        if model is not None:
            updates.append("model = ?")
            params.append(model)
        params.extend([swarm_id, name])

        async with db_manager.get_connection() as conn:
            await conn.execute(
                f"UPDATE swarm_members SET {', '.join(updates)} WHERE swarm_id = ? AND name = ?",
                tuple(params),
            )
            await conn.commit()
        return await cls.get_member(swarm_id, name)

    @classmethod
    async def post_message(
        cls,
        swarm_id: str,
        sender: str,
        text: str,
        *,
        kind: str = "message",
        mentions: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        原子递增 seq 并向消息板写入新消息。
        使用 BEGIN IMMEDIATE 事务锁保证并发发帖时 seq 严格单调递增不冲突。
        """
        mentions_json = json.dumps(mentions or [], ensure_ascii=False)
        async with db_manager.get_connection() as conn:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = await conn.execute(
                    "UPDATE swarms SET head_seq = head_seq + 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (swarm_id,),
                )
                if cursor.rowcount == 0:
                    raise ValueError(f"Swarm 不存在: {swarm_id}")
                async with conn.execute("SELECT head_seq FROM swarms WHERE id = ?", (swarm_id,)) as seq_cur:
                    seq_row = await seq_cur.fetchone()
                    next_seq = int(seq_row["head_seq"])

                insert_cur = await conn.execute(
                    """
                    INSERT INTO swarm_messages (swarm_id, seq, sender, text, kind, mentions_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (swarm_id, next_seq, sender, text, kind, mentions_json),
                )
                msg_id = insert_cur.lastrowid
                await conn.commit()

                async with conn.execute("SELECT * FROM swarm_messages WHERE id = ?", (msg_id,)) as msg_cur:
                    row = await msg_cur.fetchone()
                    return cls._row_to_message(row)
            except Exception:
                await conn.rollback()
                raise

    @classmethod
    async def read_messages(
        cls,
        swarm_id: str,
        *,
        since: int = 0,
        limit: int = 50,
        seqs: list[int] | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """
        读取消息板内容。
        - 指定 seqs 时按精确序列号集合返回；
        - 否则返回 seq > since 的最早 limit 条消息（升序），同时返回当前最新 head_seq。
        """
        safe_limit = max(1, min(int(limit or 50), 200))
        async with db_manager.get_connection() as conn:
            async with conn.execute("SELECT head_seq FROM swarms WHERE id = ?", (swarm_id,)) as h_cur:
                h_row = await h_cur.fetchone()
                head_seq = int(h_row["head_seq"]) if h_row else 0

            if seqs:
                clean_seqs = [int(s) for s in seqs]
                placeholders = ",".join("?" for _ in clean_seqs)
                async with conn.execute(
                    f"""
                    SELECT * FROM swarm_messages
                    WHERE swarm_id = ? AND seq IN ({placeholders})
                    ORDER BY seq ASC
                    """,
                    (swarm_id, *clean_seqs),
                ) as cur:
                    rows = await cur.fetchall()
            else:
                async with conn.execute(
                    """
                    SELECT * FROM swarm_messages
                    WHERE swarm_id = ? AND seq > ?
                    ORDER BY seq ASC
                    LIMIT ?
                    """,
                    (swarm_id, int(since or 0), safe_limit),
                ) as cur:
                    rows = await cur.fetchall()

            return [cls._row_to_message(r) for r in rows], head_seq
