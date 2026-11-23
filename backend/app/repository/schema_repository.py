"""
通用数据结构图谱（Schema Profile / Entity / Field / Relation）与数字专家绑定仓储
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from ..sql.auto_ontology_client import mask_api_key
from .db import db_manager

VALID_CATEGORIES = {"CORE_MAIN", "DETAIL", "CONFIG", "LOG"}
VALID_RELATION_TYPES = {"FK", "LOGICAL_JOIN", "LEFT_JOIN", "JSON_EXTRACT", "DO_NOT_JOIN"}
VALID_CARDINALITIES = {"1:1", "1:N", "N:1", "N:M"}


def _slugify_id(raw: str, prefix: str = "profile") -> str:
    cleaned = "".join(ch.lower() if (ch.isalnum() or ch in "_-") else "_" for ch in (raw or "").strip())
    cleaned = cleaned.strip("_-")
    if cleaned:
        return cleaned[:60]
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


class SchemaProfileRepository:
    """数据结构图谱与专家绑定异步仓储。"""

    @staticmethod
    def _serialize_profile(row: Any, masked: bool = True) -> dict[str, Any]:
        raw_key = str(row["ontology_api_key"] or "")
        return {
            "id": str(row["id"]),
            "name": str(row["name"]),
            "description": str(row["description"] or ""),
            "domain_prefix": str(row["domain_prefix"] or ""),
            "ontology_base_url": str(row["ontology_base_url"] or ""),
            "ontology_api_key": mask_api_key(raw_key) if masked else raw_key,
            "has_ontology_api_key": bool(raw_key.strip()),
            "is_active": bool(row["is_active"]),
            "entity_count": int(row["entity_count"]) if "entity_count" in row.keys() else 0,
            "relation_count": int(row["relation_count"]) if "relation_count" in row.keys() else 0,
            "created_at": str(row["created_at"] or ""),
            "updated_at": str(row["updated_at"] or ""),
        }

    @classmethod
    async def list_profiles(cls, masked: bool = True) -> list[dict[str, Any]]:
        """获取所有 Schema Profile 空间及实体/关系统计。"""
        async with db_manager.get_connection() as conn:
            cur = await conn.execute(
                """
                SELECT p.*,
                       (SELECT COUNT(*) FROM schema_entities e WHERE e.profile_id = p.id) AS entity_count,
                       (SELECT COUNT(*) FROM schema_relations r WHERE r.profile_id = p.id) AS relation_count
                FROM schema_profiles p
                ORDER BY p.created_at DESC, p.name ASC
                """
            )
            rows = await cur.fetchall()
            return [cls._serialize_profile(r, masked=masked) for r in rows]

    @classmethod
    async def get_profile(cls, profile_id: str, masked: bool = True) -> dict[str, Any] | None:
        """获取单个 Schema Profile 元信息。"""
        async with db_manager.get_connection() as conn:
            cur = await conn.execute(
                """
                SELECT p.*,
                       (SELECT COUNT(*) FROM schema_entities e WHERE e.profile_id = p.id) AS entity_count,
                       (SELECT COUNT(*) FROM schema_relations r WHERE r.profile_id = p.id) AS relation_count
                FROM schema_profiles p
                WHERE p.id = ?
                """,
                (profile_id,),
            )
            row = await cur.fetchone()
            return cls._serialize_profile(row, masked=masked) if row else None

    @classmethod
    async def create_profile(
        cls,
        *,
        name: str,
        profile_id: str | None = None,
        description: str = "",
        domain_prefix: str = "",
        ontology_base_url: str = "",
        ontology_api_key: str = "",
        is_active: bool = True,
    ) -> dict[str, Any]:
        """创建新的 Schema Profile。"""
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("数据结构空间名称不能为空")

        pid = _slugify_id(profile_id or clean_name, prefix="prof")
        async with db_manager.get_connection() as conn:
            cur = await conn.execute("SELECT id FROM schema_profiles WHERE id = ?", (pid,))
            if await cur.fetchone():
                pid = f"{pid}_{uuid.uuid4().hex[:4]}"

            await conn.execute(
                """
                INSERT INTO schema_profiles (
                    id, name, description, domain_prefix, ontology_base_url, ontology_api_key, is_active
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pid,
                    clean_name,
                    description.strip(),
                    domain_prefix.strip(),
                    ontology_base_url.strip().rstrip("/"),
                    ontology_api_key.strip(),
                    1 if is_active else 0,
                ),
            )
            await conn.commit()
        return await cls.get_profile(pid, masked=True) or {}

    @classmethod
    async def update_profile(
        cls,
        profile_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        domain_prefix: str | None = None,
        ontology_base_url: str | None = None,
        ontology_api_key: str | None = None,
        is_active: bool | None = None,
    ) -> dict[str, Any] | None:
        """更新 Schema Profile；ontology_api_key 传掩码串或 None 时保留原密钥。"""
        async with db_manager.get_connection() as conn:
            cur = await conn.execute("SELECT * FROM schema_profiles WHERE id = ?", (profile_id,))
            existing = await cur.fetchone()
            if not existing:
                return None

            next_name = name.strip() if name is not None and name.strip() else str(existing["name"])
            next_desc = description.strip() if description is not None else str(existing["description"] or "")
            next_prefix = domain_prefix.strip() if domain_prefix is not None else str(existing["domain_prefix"] or "")
            next_url = (
                ontology_base_url.strip().rstrip("/")
                if ontology_base_url is not None
                else str(existing["ontology_base_url"] or "")
            )

            next_key = str(existing["ontology_api_key"] or "")
            if ontology_api_key is not None:
                incoming = ontology_api_key.strip()
                # 仅当输入非空且不是掩码串时才覆盖原密钥
                if incoming and "..." not in incoming and not incoming.startswith("*"):
                    next_key = incoming
                elif incoming == "":
                    next_key = ""

            next_active = (1 if is_active else 0) if is_active is not None else int(existing["is_active"] or 1)

            await conn.execute(
                """
                UPDATE schema_profiles
                SET name = ?, description = ?, domain_prefix = ?, ontology_base_url = ?,
                    ontology_api_key = ?, is_active = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (next_name, next_desc, next_prefix, next_url, next_key, next_active, profile_id),
            )
            await conn.commit()
        return await cls.get_profile(profile_id, masked=True)

    @classmethod
    async def delete_profile(cls, profile_id: str) -> bool:
        """删除 Schema Profile（级联清理实体、字段、关系与绑定）。"""
        async with db_manager.get_connection() as conn:
            await conn.execute("PRAGMA foreign_keys = ON;")
            cur = await conn.execute("DELETE FROM schema_profiles WHERE id = ?", (profile_id,))
            await conn.commit()
            return (cur.rowcount or 0) > 0

    @classmethod
    async def get_profile_graph(
        cls,
        profile_id: str,
        entity_filter: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """获取指定 Profile 的完整图谱数据（Entities + Fields + Relations）。"""
        profile = await cls.get_profile(profile_id, masked=True)
        if not profile:
            return None

        async with db_manager.get_connection() as conn:
            cur = await conn.execute(
                """
                SELECT * FROM schema_entities
                WHERE profile_id = ?
                ORDER BY category ASC, table_name ASC
                """,
                (profile_id,),
            )
            entity_rows = await cur.fetchall()

            allow_set = {t.strip() for t in entity_filter if t.strip()} if entity_filter else None
            entities: list[dict[str, Any]] = []
            entity_id_set: set[str] = set()

            for e_row in entity_rows:
                t_name = str(e_row["table_name"])
                if allow_set is not None and t_name not in allow_set:
                    continue
                e_id = str(e_row["id"])
                entity_id_set.add(e_id)

                f_cur = await conn.execute(
                    """
                    SELECT * FROM schema_fields
                    WHERE entity_id = ?
                    ORDER BY sort_order ASC, is_primary_key DESC, field_name ASC
                    """,
                    (e_id,),
                )
                f_rows = await f_cur.fetchall()
                fields = [
                    {
                        "id": str(f["id"]),
                        "entity_id": str(f["entity_id"]),
                        "field_name": str(f["field_name"]),
                        "field_type": str(f["field_type"]),
                        "display_name": str(f["display_name"] or ""),
                        "description": str(f["description"] or ""),
                        "is_nullable": bool(f["is_nullable"]),
                        "is_primary_key": bool(f["is_primary_key"]),
                        "enum_values": str(f["enum_values"] or ""),
                        "sort_order": int(f["sort_order"] or 0),
                    }
                    for f in f_rows
                ]
                entities.append(
                    {
                        "id": e_id,
                        "profile_id": str(e_row["profile_id"]),
                        "table_name": t_name,
                        "display_name": str(e_row["display_name"]),
                        "category": str(e_row["category"] or "CORE_MAIN"),
                        "description": str(e_row["description"] or ""),
                        "primary_key": str(e_row["primary_key"] or "id"),
                        "pos_x": float(e_row["pos_x"] or 0),
                        "pos_y": float(e_row["pos_y"] or 0),
                        "fields": fields,
                    }
                )

            r_cur = await conn.execute(
                """
                SELECT * FROM schema_relations
                WHERE profile_id = ?
                ORDER BY is_warning DESC, created_at ASC
                """,
                (profile_id,),
            )
            r_rows = await r_cur.fetchall()
            relations: list[dict[str, Any]] = []
            for r in r_rows:
                src_id = str(r["source_entity_id"])
                tgt_id = str(r["target_entity_id"])
                if allow_set is not None and (src_id not in entity_id_set or tgt_id not in entity_id_set):
                    continue
                relations.append(
                    {
                        "id": str(r["id"]),
                        "profile_id": str(r["profile_id"]),
                        "source_entity_id": src_id,
                        "source_field": str(r["source_field"]),
                        "target_entity_id": tgt_id,
                        "target_field": str(r["target_field"]),
                        "relation_type": str(r["relation_type"]),
                        "cardinality": str(r["cardinality"]),
                        "join_condition": str(r["join_condition"] or ""),
                        "filter_rule": str(r["filter_rule"] or ""),
                        "is_warning": bool(r["is_warning"]),
                        "warning_message": str(r["warning_message"] or ""),
                    }
                )

            return {
                "profile": profile,
                "entities": entities,
                "relations": relations,
            }

    @classmethod
    async def upsert_entity(
        cls,
        *,
        profile_id: str,
        table_name: str,
        display_name: str = "",
        category: str = "CORE_MAIN",
        description: str = "",
        primary_key: str = "id",
        pos_x: float | None = None,
        pos_y: float | None = None,
        fields: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """创建或更新实体节点及字段列表。"""
        clean_table = table_name.strip()
        if not clean_table:
            raise ValueError("表名不能为空")

        clean_cat = category.strip().upper()
        if clean_cat not in VALID_CATEGORIES:
            clean_cat = "CORE_MAIN"

        entity_id = f"{profile_id}.{clean_table}"

        async with db_manager.get_connection() as conn:
            await conn.execute("PRAGMA foreign_keys = ON;")
            cur = await conn.execute("SELECT pos_x, pos_y FROM schema_entities WHERE id = ?", (entity_id,))
            existing = await cur.fetchone()

            next_x = float(pos_x) if pos_x is not None else (float(existing["pos_x"]) if existing else 0.0)
            next_y = float(pos_y) if pos_y is not None else (float(existing["pos_y"]) if existing else 0.0)

            await conn.execute(
                """
                INSERT INTO schema_entities (
                    id, profile_id, table_name, display_name, category, description, primary_key, pos_x, pos_y
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    display_name = excluded.display_name,
                    category = excluded.category,
                    description = excluded.description,
                    primary_key = excluded.primary_key,
                    pos_x = excluded.pos_x,
                    pos_y = excluded.pos_y,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    entity_id,
                    profile_id,
                    clean_table,
                    (display_name or clean_table).strip(),
                    clean_cat,
                    description.strip(),
                    (primary_key or "id").strip(),
                    next_x,
                    next_y,
                ),
            )

            if fields is not None:
                await conn.execute("DELETE FROM schema_fields WHERE entity_id = ?", (entity_id,))
                for idx, f in enumerate(fields):
                    f_name = str(f.get("field_name") or "").strip()
                    if not f_name:
                        continue
                    f_id = f"{entity_id}.{f_name}"
                    await conn.execute(
                        """
                        INSERT INTO schema_fields (
                            id, entity_id, field_name, field_type, display_name, description,
                            is_nullable, is_primary_key, enum_values, sort_order
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            f_id,
                            entity_id,
                            f_name,
                            str(f.get("field_type") or "VARCHAR").strip(),
                            str(f.get("display_name") or "").strip(),
                            str(f.get("description") or "").strip(),
                            1 if f.get("is_nullable", True) else 0,
                            1 if f.get("is_primary_key", f_name == primary_key) else 0,
                            str(f.get("enum_values") or "").strip(),
                            int(f.get("sort_order", idx)),
                        ),
                    )
            await conn.commit()

        graph = await cls.get_profile_graph(profile_id, entity_filter=[clean_table])
        return graph["entities"][0] if graph and graph["entities"] else {}

    @classmethod
    async def update_entity_positions(
        cls,
        profile_id: str,
        positions: list[dict[str, Any]],
    ) -> int:
        """批量保存画布节点拖拽坐标。"""
        updated = 0
        async with db_manager.get_connection() as conn:
            for item in positions:
                e_id = str(item.get("id") or "").strip()
                if not e_id:
                    continue
                cur = await conn.execute(
                    """
                    UPDATE schema_entities
                    SET pos_x = ?, pos_y = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND profile_id = ?
                    """,
                    (float(item.get("pos_x", 0)), float(item.get("pos_y", 0)), e_id, profile_id),
                )
                updated += cur.rowcount or 0
            await conn.commit()
        return updated

    @classmethod
    async def delete_entity(cls, entity_id: str) -> bool:
        """删除实体及其字段和关联边。"""
        async with db_manager.get_connection() as conn:
            await conn.execute("PRAGMA foreign_keys = ON;")
            await conn.execute(
                "DELETE FROM schema_relations WHERE source_entity_id = ? OR target_entity_id = ?",
                (entity_id, entity_id),
            )
            cur = await conn.execute("DELETE FROM schema_entities WHERE id = ?", (entity_id,))
            await conn.commit()
            return (cur.rowcount or 0) > 0

    @classmethod
    async def upsert_relation(
        cls,
        *,
        relation_id: str | None = None,
        profile_id: str,
        source_entity_id: str,
        source_field: str,
        target_entity_id: str,
        target_field: str,
        relation_type: str = "LOGICAL_JOIN",
        cardinality: str = "N:1",
        join_condition: str = "",
        filter_rule: str = "",
        is_warning: bool = False,
        warning_message: str = "",
    ) -> dict[str, Any]:
        """创建或更新实体关系边（含红线规则）。"""
        r_type = relation_type.strip().upper()
        if r_type not in VALID_RELATION_TYPES:
            raise ValueError(f"不支持的关系类型: {relation_type}")

        card = cardinality.strip()
        if card not in VALID_CARDINALITIES:
            card = "N:1"

        rid = (relation_id or "").strip() or f"rel_{uuid.uuid4().hex[:10]}"
        warning_flag = 1 if (is_warning or r_type == "DO_NOT_JOIN") else 0

        async with db_manager.get_connection() as conn:
            await conn.execute("PRAGMA foreign_keys = ON;")
            await conn.execute(
                """
                INSERT INTO schema_relations (
                    id, profile_id, source_entity_id, source_field, target_entity_id, target_field,
                    relation_type, cardinality, join_condition, filter_rule, is_warning, warning_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    source_entity_id = excluded.source_entity_id,
                    source_field = excluded.source_field,
                    target_entity_id = excluded.target_entity_id,
                    target_field = excluded.target_field,
                    relation_type = excluded.relation_type,
                    cardinality = excluded.cardinality,
                    join_condition = excluded.join_condition,
                    filter_rule = excluded.filter_rule,
                    is_warning = excluded.is_warning,
                    warning_message = excluded.warning_message,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    rid,
                    profile_id,
                    source_entity_id.strip(),
                    source_field.strip(),
                    target_entity_id.strip(),
                    target_field.strip(),
                    r_type,
                    card,
                    join_condition.strip(),
                    filter_rule.strip(),
                    warning_flag,
                    warning_message.strip(),
                ),
            )
            await conn.commit()

            cur = await conn.execute("SELECT * FROM schema_relations WHERE id = ?", (rid,))
            row = await cur.fetchone()
            if not row:
                return {}
            return {
                "id": str(row["id"]),
                "profile_id": str(row["profile_id"]),
                "source_entity_id": str(row["source_entity_id"]),
                "source_field": str(row["source_field"]),
                "target_entity_id": str(row["target_entity_id"]),
                "target_field": str(row["target_field"]),
                "relation_type": str(row["relation_type"]),
                "cardinality": str(row["cardinality"]),
                "join_condition": str(row["join_condition"] or ""),
                "filter_rule": str(row["filter_rule"] or ""),
                "is_warning": bool(row["is_warning"]),
                "warning_message": str(row["warning_message"] or ""),
            }

    @classmethod
    async def delete_relation(cls, relation_id: str) -> bool:
        """删除实体关系边。"""
        async with db_manager.get_connection() as conn:
            cur = await conn.execute("DELETE FROM schema_relations WHERE id = ?", (relation_id,))
            await conn.commit()
            return (cur.rowcount or 0) > 0

    @classmethod
    async def batch_import_parsed(
        cls,
        *,
        profile_id: str,
        entities: list[dict[str, Any]],
        relations: list[dict[str, Any]],
        business_rules_markdown: str = "",
        replace_existing: bool = False,
    ) -> dict[str, Any]:
        """将解析并确认后的实体、关系及业务口径章节批量导入指定 Profile。"""
        profile = await cls.get_profile(profile_id, masked=True)
        if not profile:
            raise ValueError(f"数据结构空间不存在: {profile_id}")

        rules_clean = (business_rules_markdown or "").strip()
        if rules_clean:
            prev_desc = str(profile.get("description") or "").strip()
            if rules_clean not in prev_desc:
                merged_desc = f"{prev_desc}\n\n{rules_clean}".strip() if prev_desc else rules_clean
                await cls.update_profile(profile_id, description=merged_desc)

        if replace_existing:
            async with db_manager.get_connection() as conn:
                await conn.execute("PRAGMA foreign_keys = ON;")
                await conn.execute("DELETE FROM schema_relations WHERE profile_id = ?", (profile_id,))
                await conn.execute("DELETE FROM schema_entities WHERE profile_id = ?", (profile_id,))
                await conn.commit()

        imported_entities = 0
        for idx, ent in enumerate(entities):
            t_name = str(ent.get("table_name") or "").strip()
            if not t_name:
                continue
            pos_x = ent.get("pos_x")
            pos_y = ent.get("pos_y")
            if pos_x is None or pos_y is None:
                pos_x = float((idx % 3) * 380 + 40)
                pos_y = float((idx // 3) * 320 + 40)
            await cls.upsert_entity(
                profile_id=profile_id,
                table_name=t_name,
                display_name=str(ent.get("display_name") or t_name),
                category=str(ent.get("category") or "CORE_MAIN"),
                description=str(ent.get("description") or ""),
                primary_key=str(ent.get("primary_key") or "id"),
                pos_x=float(pos_x),
                pos_y=float(pos_y),
                fields=ent.get("fields") or [],
            )
            imported_entities += 1

        imported_relations = 0
        for rel in relations:
            src_tbl = str(rel.get("source_table") or "").strip()
            tgt_tbl = str(rel.get("target_table") or "").strip()
            src_eid = str(rel.get("source_entity_id") or (f"{profile_id}.{src_tbl}" if src_tbl else "")).strip()
            tgt_eid = str(rel.get("target_entity_id") or (f"{profile_id}.{tgt_tbl}" if tgt_tbl else "")).strip()
            if not src_eid or not tgt_eid:
                continue
            await cls.upsert_relation(
                profile_id=profile_id,
                source_entity_id=src_eid,
                source_field=str(rel.get("source_field") or "id"),
                target_entity_id=tgt_eid,
                target_field=str(rel.get("target_field") or "id"),
                relation_type=str(rel.get("relation_type") or "LOGICAL_JOIN"),
                cardinality=str(rel.get("cardinality") or "N:1"),
                join_condition=str(rel.get("join_condition") or ""),
                filter_rule=str(rel.get("filter_rule") or ""),
                is_warning=bool(rel.get("is_warning", False)),
                warning_message=str(rel.get("warning_message") or ""),
            )
            imported_relations += 1

        return {
            "profile_id": profile_id,
            "imported_entities": imported_entities,
            "imported_relations": imported_relations,
        }

    @classmethod
    async def get_agent_bindings(cls, agent_id: str) -> list[dict[str, Any]]:
        """获取数字专家绑定的数据结构空间列表。"""
        async with db_manager.get_connection() as conn:
            cur = await conn.execute(
                """
                SELECT b.*, p.name AS profile_name, p.description AS profile_description
                FROM agent_schema_bindings b
                JOIN schema_profiles p ON b.profile_id = p.id
                WHERE b.agent_id = ?
                ORDER BY b.is_primary DESC, b.created_at ASC
                """,
                (agent_id,),
            )
            rows = await cur.fetchall()
            result: list[dict[str, Any]] = []
            for r in rows:
                raw_mounted = str(r["mounted_entities"] or "*").strip()
                if raw_mounted == "*":
                    mounted: str | list[str] = "*"
                else:
                    try:
                        parsed = json.loads(raw_mounted)
                        mounted = parsed if isinstance(parsed, list) else "*"
                    except Exception:
                        mounted = "*"
                result.append(
                    {
                        "id": str(r["id"]),
                        "agent_id": str(r["agent_id"]),
                        "profile_id": str(r["profile_id"]),
                        "profile_name": str(r["profile_name"]),
                        "profile_description": str(r["profile_description"] or ""),
                        "is_primary": bool(r["is_primary"]),
                        "mounted_entities": mounted,
                    }
                )
            return result

    @classmethod
    async def save_agent_bindings(
        cls,
        agent_id: str,
        bindings: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """覆盖保存数字专家的 Schema Profile 绑定配置。"""
        async with db_manager.get_connection() as conn:
            await conn.execute("PRAGMA foreign_keys = ON;")
            await conn.execute("DELETE FROM agent_schema_bindings WHERE agent_id = ?", (agent_id,))
            has_primary = any(bool(b.get("is_primary")) for b in bindings)
            for idx, b in enumerate(bindings):
                pid = str(b.get("profile_id") or "").strip()
                if not pid:
                    continue
                mounted = b.get("mounted_entities", "*")
                if isinstance(mounted, list):
                    mounted_str = json.dumps([str(x).strip() for x in mounted if str(x).strip()], ensure_ascii=False)
                else:
                    mounted_str = "*"
                is_pri = 1 if (b.get("is_primary") or (not has_primary and idx == 0)) else 0
                await conn.execute(
                    """
                    INSERT INTO agent_schema_bindings (id, agent_id, profile_id, is_primary, mounted_entities)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (f"bind_{uuid.uuid4().hex[:10]}", agent_id, pid, is_pri, mounted_str),
                )
            await conn.commit()
        return await cls.get_agent_bindings(agent_id)

    @classmethod
    async def export_profile_bundle(cls, profile_id: str) -> dict[str, Any] | None:
        """导出 Profile 完整结构包（不含敏感密钥）用于模板复用。"""
        graph = await cls.get_profile_graph(profile_id)
        if not graph:
            return None
        prof = dict(graph["profile"])
        prof.pop("ontology_api_key", None)
        return {
            "version": "1.8.0",
            "profile": prof,
            "entities": graph["entities"],
            "relations": graph["relations"],
        }

    @classmethod
    async def clone_profile(
        cls,
        source_profile_id: str,
        *,
        new_profile_name: str,
        new_profile_id: str | None = None,
        domain_prefix: str = "",
    ) -> dict[str, Any]:
        """深度克隆指定 Schema Profile 的实体、字段与关系边至新空间。"""
        bundle = await cls.export_profile_bundle(source_profile_id)
        if not bundle:
            raise ValueError(f"源数据结构空间不存在: {source_profile_id}")

        src_prof = bundle["profile"]
        created = await cls.create_profile(
            name=new_profile_name,
            profile_id=new_profile_id,
            description=str(src_prof.get("description") or ""),
            domain_prefix=domain_prefix or str(src_prof.get("domain_prefix") or ""),
        )
        new_pid = created["id"]

        old_to_new_table = {e["id"]: e["table_name"] for e in bundle["entities"]}
        mapped_relations: list[dict[str, Any]] = []
        for r in bundle["relations"]:
            src_tbl = old_to_new_table.get(r["source_entity_id"], r["source_entity_id"].split(".")[-1])
            tgt_tbl = old_to_new_table.get(r["target_entity_id"], r["target_entity_id"].split(".")[-1])
            mapped_relations.append(
                {
                    **r,
                    "source_entity_id": f"{new_pid}.{src_tbl}",
                    "target_entity_id": f"{new_pid}.{tgt_tbl}",
                }
            )

        imp = await cls.batch_import_parsed(
            profile_id=new_pid,
            entities=bundle["entities"],
            relations=mapped_relations,
        )
        return {
            "profile": await cls.get_profile(new_pid, masked=True),
            "copied_entities_count": imp["imported_entities"],
            "copied_relations_count": imp["imported_relations"],
        }

