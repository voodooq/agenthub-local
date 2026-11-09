"""
双时间轴结构化事实管理器 (FactManager - Bi-temporal Active Fact View)
核心特性：
1. 严格区分有效时间线 (valid_from / valid_to) 与记录时间 (recorded_at)，彻底消解新旧规则冲突
2. 数据库局部唯一索引 (uq_facts_active_key) + BEGIN IMMEDIATE 事务双保险防并发分裂
3. 工作区物理沙箱隔离：同名 fact_key 下工作区契约严格高优覆盖 GLOBAL 全局偏好
4. 入口强制 fact_key 标准化清洗与 MemoryPolicyGuard 准入/脱敏校验
"""
from datetime import datetime, timezone
import re
from typing import Any
import uuid

from ..core.logging_config import logger
from ..repository.db import db_manager
from .policy_guard import MemoryAdmissionError, MemoryPolicyGuard


class FactManager:
    """双时间轴精确事实管理器"""

    VALID_CATEGORIES: tuple[str, ...] = ("user_pref", "biz_rule", "env_config")

    _CATEGORY_ALIASES: dict[str, str] = {
        "user_pref": "user_pref",
        "preference": "user_pref",
        "pref": "user_pref",
        "biz_rule": "biz_rule",
        "rule": "biz_rule",
        "biz": "biz_rule",
        "env_config": "env_config",
        "env_info": "env_config",
        "env": "env_config",
    }

    @staticmethod
    def normalize_fact_key(fact_key: str) -> str:
        """
        标准化清洗 fact_key，防止大小写或空格/连字符差异造成同一概念分裂：
        例如 'Refund-Fee  Rate' -> 'refund_fee_rate'。
        """
        raw = (fact_key or "").strip().lower()
        raw = raw.replace(" ", "_").replace("-", "_")
        # 允许小写英文、数字、下划线与中文字符，其余分隔符归一为下划线
        cleaned = re.sub(r"[^a-z0-9_\u4e00-\u9fff]+", "_", raw)
        cleaned = re.sub(r"_+", "_", cleaned).strip("_")
        if not cleaned:
            raise ValueError("fact_key 不能为空或仅包含无效分隔符")
        return cleaned

    @staticmethod
    def normalize_workspace_id(workspace_id: str | None) -> str:
        """归一化工作区标识：空或任意大小写 global 均统一为 GLOBAL"""
        raw = (workspace_id or "").strip()
        if not raw or raw.upper() == "GLOBAL":
            return "GLOBAL"
        return raw

    @classmethod
    def normalize_category(cls, category: str | None) -> str:
        """归一化事实分类到标准枚举 (user_pref | biz_rule | env_config)"""
        raw = (category or "").strip().lower()
        return cls._CATEGORY_ALIASES.get(raw, "biz_rule")

    @staticmethod
    def _row_to_dict(row: Any) -> dict[str, Any]:
        d = dict(row)
        d["is_active"] = d.get("valid_to") is None
        d["scope"] = "global" if d.get("workspace_id") == "GLOBAL" else "workspace"
        return d

    @classmethod
    async def upsert_fact(
        cls,
        *,
        fact_key: str,
        fact_value: str,
        workspace_id: str | None = "GLOBAL",
        category: str = "biz_rule",
        source_type: str = "inquiry",
        source_event_id: str | None = None,
        confidence: float = 1.0,
        incognito: bool = False,
        scene_mode: str = "agent",
        verified: bool = True,
    ) -> dict[str, Any]:
        """
        双时间轴事实写入/冲突消解事务：
        - 若同 (workspace_id, fact_key) 无生效记录 -> 直接写入新激活行 (valid_to = NULL)
        - 若存在生效记录且值完全相同 -> 幂等返回 action='unchanged'
        - 若存在生效记录但值变更 -> BEGIN IMMEDIATE 事务内关闭旧行 valid_to，写入新激活行

        Raises:
            MemoryAdmissionError: 来源不符合准入策略或内容非法。
            ValueError: fact_key 清洗后为空。
        """
        norm_key = cls.normalize_fact_key(fact_key)
        norm_ws = cls.normalize_workspace_id(workspace_id)
        norm_cat = cls.normalize_category(category)

        decision = MemoryPolicyGuard.evaluate_write(
            source_type=source_type,
            content=fact_value,
            incognito=incognito,
            scene_mode=scene_mode,
            verified=verified,
        )
        if not decision.allowed:
            if incognito:
                return {
                    "status": "skipped_incognito",
                    "action": "skipped",
                    "fact": None,
                    "redactions": [],
                }
            raise MemoryAdmissionError(decision.reason)

        clean_value = decision.sanitized_text.strip()
        clean_source = (source_type or "inquiry").strip().lower()
        clean_conf = round(max(0.0, min(1.0, float(confidence))), 3)

        async with db_manager.get_connection() as conn:
            await conn.execute("BEGIN IMMEDIATE;")
            try:
                async with conn.execute(
                    """
                    SELECT * FROM memory_facts
                    WHERE workspace_id = ? AND fact_key = ? AND valid_to IS NULL
                    LIMIT 1
                    """,
                    (norm_ws, norm_key),
                ) as cur:
                    existing_row = await cur.fetchone()

                if existing_row is not None:
                    existing = dict(existing_row)
                    if existing["fact_value"] == clean_value and existing["category"] == norm_cat:
                        await conn.commit()
                        return {
                            "status": "ok",
                            "action": "unchanged",
                            "fact": cls._row_to_dict(existing),
                            "redactions": decision.redactions,
                        }

                    now_ts = datetime.now(timezone.utc).isoformat()
                    await conn.execute(
                        "UPDATE memory_facts SET valid_to = ? WHERE fact_id = ?",
                        (now_ts, existing["fact_id"]),
                    )
                    new_id = f"fact_{uuid.uuid4().hex[:12]}"
                    await conn.execute(
                        """
                        INSERT INTO memory_facts (
                            fact_id, workspace_id, category, fact_key, fact_value,
                            confidence, source_type, source_event_id, valid_from, valid_to, recorded_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
                        """,
                        (
                            new_id,
                            norm_ws,
                            norm_cat,
                            norm_key,
                            clean_value,
                            clean_conf,
                            clean_source,
                            source_event_id,
                            now_ts,
                            now_ts,
                        ),
                    )
                    await conn.commit()
                    new_fact = {
                        "fact_id": new_id,
                        "workspace_id": norm_ws,
                        "category": norm_cat,
                        "fact_key": norm_key,
                        "fact_value": clean_value,
                        "confidence": clean_conf,
                        "source_type": clean_source,
                        "source_event_id": source_event_id,
                        "valid_from": now_ts,
                        "valid_to": None,
                        "recorded_at": now_ts,
                        "is_active": True,
                        "scope": "global" if norm_ws == "GLOBAL" else "workspace",
                    }
                    logger.info(
                        f"[FactManager] 事实冲突演进 ({norm_ws}:{norm_key}): "
                        f"旧版本 {existing['fact_id']} 归档，激活新版本 {new_id}"
                    )
                    return {
                        "status": "ok",
                        "action": "updated",
                        "fact": new_fact,
                        "previous_fact_id": existing["fact_id"],
                        "redactions": decision.redactions,
                    }

                # 无当前生效记录，直接激活写入
                now_ts = datetime.now(timezone.utc).isoformat()
                new_id = f"fact_{uuid.uuid4().hex[:12]}"
                await conn.execute(
                    """
                    INSERT INTO memory_facts (
                        fact_id, workspace_id, category, fact_key, fact_value,
                        confidence, source_type, source_event_id, valid_from, valid_to, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
                    """,
                    (
                        new_id,
                        norm_ws,
                        norm_cat,
                        norm_key,
                        clean_value,
                        clean_conf,
                        clean_source,
                        source_event_id,
                        now_ts,
                        now_ts,
                    ),
                )
                await conn.commit()
                new_fact = {
                    "fact_id": new_id,
                    "workspace_id": norm_ws,
                    "category": norm_cat,
                    "fact_key": norm_key,
                    "fact_value": clean_value,
                    "confidence": clean_conf,
                    "source_type": clean_source,
                    "source_event_id": source_event_id,
                    "valid_from": now_ts,
                    "valid_to": None,
                    "recorded_at": now_ts,
                    "is_active": True,
                    "scope": "global" if norm_ws == "GLOBAL" else "workspace",
                }
                logger.info(f"[FactManager] 新增确定性事实 ({norm_ws}:{norm_key}) -> {new_id}")
                return {
                    "status": "ok",
                    "action": "created",
                    "fact": new_fact,
                    "redactions": decision.redactions,
                }
            except Exception:
                await conn.rollback()
                raise

    @classmethod
    async def get_active_facts(
        cls,
        workspace_id: str | None = None,
        *,
        include_global: bool = True,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        编译当前工作区的 Active Fact View（valid_to IS NULL）。
        合并规则：同名 fact_key 下，具体 workspace_id 契约严格覆盖 GLOBAL 全局偏好。
        输出顺序按 (category, fact_key) 稳定排序，利于系统提示词前缀缓存命中。
        """
        norm_ws = cls.normalize_workspace_id(workspace_id)
        conditions = ["valid_to IS NULL"]
        params: list[Any] = []

        if norm_ws == "GLOBAL" or not include_global:
            conditions.append("workspace_id = ?")
            params.append(norm_ws)
        else:
            conditions.append("workspace_id IN (?, 'GLOBAL')")
            params.append(norm_ws)

        if category:
            conditions.append("category = ?")
            params.append(cls.normalize_category(category))

        where_clause = " AND ".join(conditions)
        async with db_manager.get_connection() as conn:
            async with conn.execute(
                f"""
                SELECT * FROM memory_facts
                WHERE {where_clause}
                ORDER BY valid_from ASC, rowid ASC
                """,
                tuple(params),
            ) as cur:
                rows = [cls._row_to_dict(r) for r in await cur.fetchall()]

        # 先放入 GLOBAL 视图，再使用当前工作区条目执行严格覆盖
        merged: dict[str, dict[str, Any]] = {}
        for item in rows:
            if item["workspace_id"] == "GLOBAL":
                item["overridden_global"] = False
                merged[item["fact_key"]] = item

        for item in rows:
            if item["workspace_id"] != "GLOBAL":
                key = item["fact_key"]
                if key in merged:
                    item["overridden_global"] = True
                    item["global_fallback_value"] = merged[key]["fact_value"]
                else:
                    item["overridden_global"] = False
                merged[key] = item

        return sorted(merged.values(), key=lambda x: (x.get("category", ""), x.get("fact_key", "")))

    @classmethod
    async def list_facts(
        cls,
        workspace_id: str | None = None,
        *,
        include_history: bool = False,
        include_global: bool = True,
        category: str | None = None,
        fact_key: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        查询结构化事实列表或完整双时间轴演进历史。
        """
        if not include_history and not fact_key:
            return await cls.get_active_facts(
                workspace_id=workspace_id,
                include_global=include_global,
                category=category,
            )

        norm_ws = cls.normalize_workspace_id(workspace_id)
        conditions: list[str] = []
        params: list[Any] = []

        if not include_history:
            conditions.append("valid_to IS NULL")

        if norm_ws == "GLOBAL" or not include_global:
            conditions.append("workspace_id = ?")
            params.append(norm_ws)
        else:
            conditions.append("workspace_id IN (?, 'GLOBAL')")
            params.append(norm_ws)

        if category:
            conditions.append("category = ?")
            params.append(cls.normalize_category(category))

        if fact_key:
            conditions.append("fact_key = ?")
            params.append(cls.normalize_fact_key(fact_key))

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        async with db_manager.get_connection() as conn:
            async with conn.execute(
                f"""
                SELECT * FROM memory_facts
                {where_clause}
                ORDER BY fact_key ASC, valid_from DESC, rowid DESC
                """,
                tuple(params),
            ) as cur:
                rows = await cur.fetchall()

        return [cls._row_to_dict(r) for r in rows]

    @classmethod
    async def rollback_fact(
        cls,
        fact_id: str,
        *,
        source_event_id: str | None = None,
    ) -> dict[str, Any]:
        """
        将指定的历史版本事实重新激活为当前有效事实：
        关闭当前活跃行的 valid_to，并以 rollback 来源追加生成一条新的 valid_to = NULL 生效行，
        完整保留时间线审计演进链 (V1 -> V2 -> V1)。
        """
        clean_id = (fact_id or "").strip()
        if not clean_id:
            raise ValueError("fact_id 不能为空")

        async with db_manager.get_connection() as conn:
            await conn.execute("BEGIN IMMEDIATE;")
            try:
                async with conn.execute(
                    "SELECT * FROM memory_facts WHERE fact_id = ? LIMIT 1",
                    (clean_id,),
                ) as cur:
                    target_row = await cur.fetchone()

                if target_row is None:
                    await conn.rollback()
                    raise LookupError(f"未找到事实记录: {clean_id}")

                target = dict(target_row)
                if target["valid_to"] is None:
                    await conn.commit()
                    return {
                        "status": "ok",
                        "action": "already_active",
                        "fact": cls._row_to_dict(target),
                        "redactions": [],
                    }

                sanitized_val, redactions = MemoryPolicyGuard.sanitize_text(target["fact_value"])
                now_ts = datetime.now(timezone.utc).isoformat()

                # 查找并关闭当前同键活跃版本（若有）
                async with conn.execute(
                    """
                    SELECT fact_id FROM memory_facts
                    WHERE workspace_id = ? AND fact_key = ? AND valid_to IS NULL
                    LIMIT 1
                    """,
                    (target["workspace_id"], target["fact_key"]),
                ) as cur_active:
                    active_row = await cur_active.fetchone()

                previous_active_id = active_row["fact_id"] if active_row else None
                if previous_active_id:
                    await conn.execute(
                        "UPDATE memory_facts SET valid_to = ? WHERE fact_id = ?",
                        (now_ts, previous_active_id),
                    )

                new_id = f"fact_{uuid.uuid4().hex[:12]}"
                resolved_event_id = source_event_id or target.get("source_event_id")
                await conn.execute(
                    """
                    INSERT INTO memory_facts (
                        fact_id, workspace_id, category, fact_key, fact_value,
                        confidence, source_type, source_event_id, valid_from, valid_to, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'rollback', ?, ?, NULL, ?)
                    """,
                    (
                        new_id,
                        target["workspace_id"],
                        target["category"],
                        target["fact_key"],
                        sanitized_val,
                        target["confidence"],
                        resolved_event_id,
                        now_ts,
                        now_ts,
                    ),
                )
                await conn.commit()

                new_fact = {
                    "fact_id": new_id,
                    "workspace_id": target["workspace_id"],
                    "category": target["category"],
                    "fact_key": target["fact_key"],
                    "fact_value": sanitized_val,
                    "confidence": target["confidence"],
                    "source_type": "rollback",
                    "source_event_id": resolved_event_id,
                    "valid_from": now_ts,
                    "valid_to": None,
                    "recorded_at": now_ts,
                    "is_active": True,
                    "scope": "global" if target["workspace_id"] == "GLOBAL" else "workspace",
                }
                logger.info(
                    f"[FactManager] 已将事实 {target['workspace_id']}:{target['fact_key']} "
                    f"从 {previous_active_id} 回滚恢复至历史版本 {clean_id} (新激活 ID: {new_id})"
                )
                return {
                    "status": "ok",
                    "action": "rolled_back",
                    "fact": new_fact,
                    "restored_from_fact_id": clean_id,
                    "previous_active_fact_id": previous_active_id,
                    "redactions": redactions,
                }
            except Exception:
                await conn.rollback()
                raise

    @classmethod
    async def deactivate_fact(cls, fact_id: str) -> bool:
        """
        将一条当前生效中的事实软归档（设置 valid_to），使其退出 Active Fact View 并保留审计历史。
        """
        clean_id = (fact_id or "").strip()
        if not clean_id:
            return False
        now_ts = datetime.now(timezone.utc).isoformat()
        async with db_manager.get_connection() as conn:
            cur = await conn.execute(
                "UPDATE memory_facts SET valid_to = ? WHERE fact_id = ? AND valid_to IS NULL",
                (now_ts, clean_id),
            )
            await conn.commit()
            return (cur.rowcount or 0) > 0

    @classmethod
    async def search_history(
        cls,
        query: str,
        *,
        workspace_id: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        按关键词检索确定性事实（覆盖当前有效与历史已退位版本），供按需工具召回历史变更。
        """
        clean_query = (query or "").strip()
        if not clean_query:
            return []

        like_pattern = f"%{clean_query}%"
        conditions = ["(fact_key LIKE ? OR fact_value LIKE ?)"]
        params: list[Any] = [like_pattern, like_pattern]

        if workspace_id:
            norm_ws = cls.normalize_workspace_id(workspace_id)
            if norm_ws == "GLOBAL":
                conditions.append("workspace_id = 'GLOBAL'")
            else:
                conditions.append("workspace_id IN (?, 'GLOBAL')")
                params.append(norm_ws)

        safe_limit = max(1, min(int(limit), 50))
        where_clause = " AND ".join(conditions)

        async with db_manager.get_connection() as conn:
            async with conn.execute(
                f"""
                SELECT * FROM memory_facts
                WHERE {where_clause}
                ORDER BY (valid_to IS NULL) DESC, valid_from DESC, rowid DESC
                LIMIT ?
                """,
                (*params, safe_limit),
            ) as cur:
                rows = await cur.fetchall()

        return [cls._row_to_dict(r) for r in rows]


fact_manager = FactManager()

