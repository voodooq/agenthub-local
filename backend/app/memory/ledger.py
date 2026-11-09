"""
追加式原始事件账本服务 (EventLedgerService)
核心特性：
1. Append-Only 黑匣子事件流水，只追加写入、严禁原地覆盖或单条篡改
2. 所有载荷在落盘前统一经过 MemoryPolicyGuard 隐私脱敏
3. 支持按会话/工作区/Loop 分页审计追溯，以及为 ExperienceDistiller 提取有序纠偏与自愈轨迹切片
"""
from datetime import datetime, timezone
import json
from typing import Any
import uuid

from ..core.logging_config import logger
from ..repository.db import db_manager
from .policy_guard import MemoryPolicyGuard


class EventLedgerService:
    """追加式事件账本服务"""

    VALID_EVENT_TYPES: tuple[str, ...] = (
        "user_input",
        "inquiry_confirmed",
        "tool_executed",
        "tool_error_recovered",
        "user_intervention",
        "artifact_produced",
    )
    VALID_ACTORS: tuple[str, ...] = ("user", "agent", "system")

    @classmethod
    def _row_to_dict(cls, row: Any) -> dict[str, Any]:
        """将数据库 Row 转换并反序列化 payload_json"""
        d = dict(row)
        raw_payload = d.pop("payload_json", "{}")
        try:
            parsed = json.loads(raw_payload) if raw_payload else {}
        except (TypeError, ValueError):
            parsed = {"raw": raw_payload}
        redacted_types = parsed.get("_redacted_types", []) if isinstance(parsed, dict) else []
        d["payload"] = parsed
        d["redactions"] = redacted_types
        d["is_redacted"] = bool(redacted_types)
        return d

    @classmethod
    async def append_event(
        cls,
        *,
        workspace_id: str,
        conversation_id: str,
        turn_id: int,
        event_type: str,
        payload: dict[str, Any] | list[Any] | str,
        actor: str = "user",
        loop_id: str | None = None,
        skill_id: str | None = None,
        incognito: bool = False,
        event_id: str | None = None,
    ) -> dict[str, Any] | None:
        """
        向 event_ledger 追加写入一条原始交互事件（仅追加，不改写历史）。

        Args:
            workspace_id: 所属工作区 ID。
            conversation_id: 所属会话 ID。
            turn_id: 对话轮次编号。
            event_type: 事件类型（见 VALID_EVENT_TYPES）。
            payload: 结构化载荷或文本。
            actor: 事件发起方 ('user' | 'agent' | 'system')。
            loop_id: 关联的 TaskLoop ID（可选）。
            skill_id: 关联激活的 Skill ID（可选）。
            incognito: 是否处于无痕模式；为 True 时直接跳过落盘并返回 None。
            event_id: 可选外部指定主键 ID。

        Returns:
            dict[str, Any] | None: 落盘后的事件对象；无痕模式下返回 None。
        """
        if incognito:
            return None

        clean_type = (event_type or "").strip()
        if clean_type not in cls.VALID_EVENT_TYPES:
            raise ValueError(f"不支持的账本事件类型: {event_type}")

        clean_actor = (actor or "user").strip().lower()
        if clean_actor not in cls.VALID_ACTORS:
            clean_actor = "system"

        clean_ws = (workspace_id or "default").strip() or "default"
        clean_conv = (conversation_id or "").strip()
        if not clean_conv:
            raise ValueError("写入 event_ledger 必须提供 conversation_id")

        if isinstance(payload, str):
            payload_obj: dict[str, Any] = {"text": payload}
        elif isinstance(payload, dict):
            payload_obj = dict(payload)
        else:
            payload_obj = {"items": payload}

        # 写盘前统一经过隐私网关脱敏
        sanitized_payload, redactions = MemoryPolicyGuard.sanitize_payload(payload_obj)
        if redactions and isinstance(sanitized_payload, dict):
            sanitized_payload["_redacted_types"] = redactions

        serialized_payload = json.dumps(sanitized_payload, ensure_ascii=False)
        record_id = event_id or f"evt_{uuid.uuid4().hex[:14]}"
        now_ts = datetime.now(timezone.utc).isoformat()

        async with db_manager.get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO event_ledger (
                    id, workspace_id, conversation_id, turn_id, loop_id,
                    event_type, actor, skill_id, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    clean_ws,
                    clean_conv,
                    int(turn_id),
                    loop_id,
                    clean_type,
                    clean_actor,
                    skill_id,
                    serialized_payload,
                    now_ts,
                ),
            )
            await conn.commit()

        logger.debug(f"[Ledger] 已追加事件 {record_id} ({clean_type}) @ conv={clean_conv} turn={turn_id}")
        return {
            "id": record_id,
            "workspace_id": clean_ws,
            "conversation_id": clean_conv,
            "turn_id": int(turn_id),
            "loop_id": loop_id,
            "event_type": clean_type,
            "actor": clean_actor,
            "skill_id": skill_id,
            "payload": sanitized_payload,
            "redactions": redactions,
            "is_redacted": bool(redactions),
            "created_at": now_ts,
        }

    @classmethod
    async def list_events(
        cls,
        *,
        workspace_id: str | None = None,
        conversation_id: str | None = None,
        loop_id: str | None = None,
        event_type: str | None = None,
        ascending: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """
        分页查询追加事件账本（供审计视图与回溯使用）。

        Args:
            workspace_id: 可选工作区过滤。
            conversation_id: 可选会话过滤。
            loop_id: 可选任务循环 ID 过滤。
            event_type: 可选事件类型过滤。
            ascending: 是否按时间升序排列（默认 False 即最新在前）。
            limit: 每页条数上限。
            offset: 偏移量。

        Returns:
            dict[str, Any]: 包含 items 与 total 的分页响应字典。
        """
        conditions: list[str] = []
        params: list[Any] = []

        if workspace_id:
            conditions.append("workspace_id = ?")
            params.append(workspace_id.strip())
        if conversation_id:
            conditions.append("conversation_id = ?")
            params.append(conversation_id.strip())
        if loop_id:
            conditions.append("loop_id = ?")
            params.append(loop_id.strip())
        if event_type:
            conditions.append("event_type = ?")
            params.append(event_type.strip())

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        order_dir = "ASC" if ascending else "DESC"
        safe_limit = max(1, min(int(limit), 500))
        safe_offset = max(0, int(offset))

        async with db_manager.get_connection() as conn:
            async with conn.execute(
                f"SELECT COUNT(*) AS cnt FROM event_ledger {where_clause}",
                tuple(params),
            ) as cur_cnt:
                cnt_row = await cur_cnt.fetchone()
                total = int(cnt_row["cnt"]) if cnt_row else 0

            async with conn.execute(
                f"""
                SELECT id, workspace_id, conversation_id, turn_id, loop_id,
                       event_type, actor, skill_id, payload_json, created_at
                FROM event_ledger
                {where_clause}
                ORDER BY created_at {order_dir}, rowid {order_dir}
                LIMIT ? OFFSET ?
                """,
                (*params, safe_limit, safe_offset),
            ) as cur:
                rows = await cur.fetchall()

        return {
            "items": [cls._row_to_dict(r) for r in rows],
            "total": total,
        }

    @classmethod
    async def get_trace_for_distillation(
        cls,
        conversation_id: str,
        *,
        loop_id: str | None = None,
        turn_id: int | None = None,
        limit: int = 120,
    ) -> list[dict[str, Any]]:
        """
        按时间正序提取用于 Post-Task 经验提炼的事件轨迹切片。
        优先聚焦某次 loop_id 或某轮 turn_id，提取报错自愈、人工纠偏、工具执行与产物信息。
        """
        conditions = ["conversation_id = ?"]
        params: list[Any] = [conversation_id.strip()]

        if loop_id:
            conditions.append("loop_id = ?")
            params.append(loop_id.strip())
        if turn_id is not None:
            conditions.append("turn_id = ?")
            params.append(int(turn_id))

        where_clause = f"WHERE {' AND '.join(conditions)}"
        safe_limit = max(1, min(int(limit), 500))

        async with db_manager.get_connection() as conn:
            async with conn.execute(
                f"""
                SELECT id, workspace_id, conversation_id, turn_id, loop_id,
                       event_type, actor, skill_id, payload_json, created_at
                FROM event_ledger
                {where_clause}
                ORDER BY created_at ASC, rowid ASC
                LIMIT ?
                """,
                (*params, safe_limit),
            ) as cur:
                rows = await cur.fetchall()

        return [cls._row_to_dict(r) for r in rows]


event_ledger = EventLedgerService()

