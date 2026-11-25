"""
数字专家（Agent）数据结构上下文动态注入、多库路由与 SQL 深度静态审计引擎 (R001 - R006)
"""
from __future__ import annotations

import re
from typing import Any

from ..repository.db import db_manager
from ..repository.schema_repository import SchemaProfileRepository
from ..sql.auto_ontology_client import (
    AutoOntologyClient,
    get_auto_ontology_config,
    infer_target_db,
    normalize_target_db,
)


async def _get_agent_name(agent_id: str | None) -> str:
    """从 agent_profiles 查询数字专家名称，用于辅助识别所属业务库。"""
    if not agent_id:
        return ""
    try:
        async with db_manager.get_connection() as conn:
            cur = await conn.execute("SELECT name FROM agent_profiles WHERE id = ?", (agent_id,))
            row = await cur.fetchone()
            return str(row["name"] or "") if row else ""
    except Exception:
        return ""


def filter_entities_for_target_db(
    entities: list[dict[str, Any]],
    target_db: str | None,
) -> list[dict[str, Any]]:
    """按目标数据库隔离过滤实体列表，防止跨库表结构混入上下文。"""
    norm_db = normalize_target_db(target_db)
    if not norm_db:
        return entities
    if norm_db == "equestrianinfo":
        return [e for e in entities if not str(e.get("table_name") or "").lower().startswith("fe_")]
    if norm_db == "fencinginfo":
        return [
            e
            for e in entities
            if not str(e.get("table_name") or "").lower().startswith(("eq_", "equestrian_"))
        ]
    return entities


def is_profile_compatible_with_target_db(
    profile: dict[str, Any],
    entities: list[dict[str, Any]] | None,
    target_db: str | None,
) -> bool:
    """判断 Schema Profile 是否与目标业务库兼容，防止击剑与马术约束串库。"""
    norm_db = normalize_target_db(target_db)
    if not norm_db:
        return True

    prof_db = normalize_target_db(profile.get("target_db")) or infer_target_db(
        f"{profile.get('id') or ''} {profile.get('name') or ''} {profile.get('domain_prefix') or ''}"
    )
    if prof_db and prof_db != norm_db:
        return False

    if entities:
        tbls = [str(e.get("table_name") or "").lower() for e in entities if e.get("table_name")]
        if tbls:
            if norm_db == "equestrianinfo" and all(t.startswith("fe_") for t in tbls):
                return False
            if norm_db == "fencinginfo" and all(t.startswith(("eq_", "equestrian_")) for t in tbls):
                return False
    return True


async def resolve_agent_target_db(
    agent_id: str | None,
    explicit_target_db: str | None = None,
    profile: dict[str, Any] | None = None,
    bindings: list[dict[str, Any]] | None = None,
) -> str | None:
    """解析数字专家对应的目标数据库（fencinginfo / equestrianinfo）。"""
    # 1. 调用方显式指定时优先采用（含合法性校验，非法值直接抛 ValueError）
    explicit = normalize_target_db(explicit_target_db)
    if explicit:
        return explicit

    # 2. 若已传入特定 Profile，检查其显式 target_db 或名称推断
    if profile:
        prof_db = normalize_target_db(profile.get("target_db")) or infer_target_db(
            f"{profile.get('id') or ''} {profile.get('name') or ''} {profile.get('domain_prefix') or ''}"
        )
        if prof_db:
            return prof_db

    # 3. 根据专家 ID 与专家名称推断（击剑赛事专家 -> fencinginfo，马术报名专家 -> equestrianinfo）
    if agent_id:
        agent_name = await _get_agent_name(agent_id)
        by_agent = infer_target_db(f"{agent_id} {agent_name}")
        if by_agent:
            return by_agent

        # 4. 检查专家绑定的 Schema Profile
        agent_bindings = bindings if bindings is not None else await SchemaProfileRepository.get_agent_bindings(agent_id)
        for b in agent_bindings:
            prof = await SchemaProfileRepository.get_profile(b["profile_id"], masked=True)
            if not prof:
                continue
            prof_db = normalize_target_db(prof.get("target_db")) or infer_target_db(
                f"{prof.get('id') or ''} {prof.get('name') or ''} {prof.get('domain_prefix') or ''}"
            )
            if prof_db:
                return prof_db

    # 5. 回退全局持久化配置中的 target_db
    try:
        cfg = await get_auto_ontology_config(masked=True)
        return normalize_target_db(cfg.get("target_db"))
    except Exception:
        return None


async def resolve_agent_ontology_client(
    agent_id: str | None,
    target_db: str | None = None,
) -> AutoOntologyClient | None:
    """优先使用专家兼容 Schema Profile 的独立网关配置，未配置则回退全局默认，并绑定 target_db。"""
    resolved_db = await resolve_agent_target_db(agent_id, explicit_target_db=target_db)
    if agent_id:
        bindings = await SchemaProfileRepository.get_agent_bindings(agent_id)
        for binding in bindings:
            primary_id = binding["profile_id"]
            prof = await SchemaProfileRepository.get_profile(primary_id, masked=False)
            if not prof or not prof.get("ontology_base_url") or not prof.get("ontology_api_key"):
                continue
            if not is_profile_compatible_with_target_db(prof, None, resolved_db):
                continue
            profile_db = resolved_db or normalize_target_db(prof.get("target_db")) or infer_target_db(
                f"{prof.get('id') or ''} {prof.get('name') or ''} {prof.get('domain_prefix') or ''}"
            )
            return AutoOntologyClient(
                base_url=str(prof["ontology_base_url"]),
                api_key=str(prof["ontology_api_key"]),
                timeout_seconds=120,
                target_db=profile_db,
            )
    return await AutoOntologyClient.from_saved_config(target_db=resolved_db)


async def build_agent_schema_context(
    agent_id: str,
    max_chars: int = 5500,
    user_query: str = "",
) -> str:
    """根据专家绑定的 Schema Profile 与当前提问，精准展开相关实体字段与口径上下文。"""
    if not agent_id:
        return ""
    bindings = await SchemaProfileRepository.get_agent_bindings(agent_id)
    if not bindings:
        return ""

    agent_target_db = await resolve_agent_target_db(agent_id, bindings=bindings)

    q_low = (user_query or "").lower()
    sections: list[str] = [
        "========================= [数据结构图谱上下文 System Context] =========================",
        "在向 Auto Ontology 发起提问前，必须严格遵守以下绑定表范围、字段枚举口径与红线：",
    ]
    if agent_target_db:
        sections.append(f"- 当前专家专属目标数据库 (target_db): `{agent_target_db}`")

    has_compatible_profile = False
    for binding in bindings:
        pid = binding["profile_id"]
        mounted = binding.get("mounted_entities", "*")
        entity_filter = mounted if isinstance(mounted, list) else None
        graph = await SchemaProfileRepository.get_profile_graph(pid, entity_filter=entity_filter)
        if not graph:
            continue

        prof = graph["profile"]
        raw_entities = graph["entities"]
        if not is_profile_compatible_with_target_db(prof, raw_entities, agent_target_db):
            continue

        entities = filter_entities_for_target_db(raw_entities, agent_target_db)
        if not entities and raw_entities:
            continue

        has_compatible_profile = True
        allowed_entity_ids = {e["id"] for e in entities}
        relations = [
            r
            for r in graph["relations"]
            if r.get("source_entity_id") in allowed_entity_ids
            and r.get("target_entity_id") in allowed_entity_ids
        ]
        entity_map = {e["id"]: e["table_name"] for e in entities}

        # 表数较多时，优先展开与当前提问命中及 1-Hop 关联的实体字段详情，其余表仅列极简目录
        focus_tables: set[str] | None = None
        if q_low and len(entities) > 10:
            hit = {
                e["table_name"]
                for e in entities
                if e["table_name"].lower() in q_low or (e.get("display_name") and e["display_name"] in user_query)
            }
            if hit:
                focus_tables = set(hit)
                for r in relations:
                    src_t = entity_map.get(r["source_entity_id"], r["source_entity_id"].split(".")[-1])
                    tgt_t = entity_map.get(r["target_entity_id"], r["target_entity_id"].split(".")[-1])
                    if src_t in hit or tgt_t in hit:
                        focus_tables.add(src_t)
                        focus_tables.add(tgt_t)

        role_tag = "主数据结构" if binding.get("is_primary") else "辅助数据结构"
        sections.append(f"\n### {role_tag}: {prof['name']} ({pid})")
        if prof.get("description"):
            sections.append(f"说明: {prof['description']}")

        sections.append("\n【可访问实体清单】:")
        other_tables: list[str] = []
        for idx, ent in enumerate(entities, start=1):
            if focus_tables is not None and ent["table_name"] not in focus_tables:
                other_tables.append(f"`{ent['table_name']}`({ent['display_name']})")
                continue
            sections.append(f"{idx}. `{ent['table_name']}` ({ent['display_name']}) - PK: `{ent['primary_key']}`")
            field_summaries: list[str] = []
            for f in ent.get("fields", [])[:16]:
                enum_part = f" [枚举: {f['enum_values']}]" if f.get("enum_values") else ""
                desc_part = f" ({f['description'][:36]})" if f.get("description") else ""
                field_summaries.append(f"`{f['field_name']}`:{f['field_type']}{enum_part}{desc_part}")
            if field_summaries:
                sections.append("   - 字段: " + "; ".join(field_summaries))
        if other_tables:
            sections.append(f"- 其余可用表索引: {', '.join(other_tables)}")

        normal_rels = [r for r in relations if not r.get("is_warning") and r.get("relation_type") != "DO_NOT_JOIN"]
        if normal_rels:
            sections.append("\n【数据关系图谱与关联口径】:")
            for r in normal_rels:
                src_t = entity_map.get(r["source_entity_id"], r["source_entity_id"].split(".")[-1])
                tgt_t = entity_map.get(r["target_entity_id"], r["target_entity_id"].split(".")[-1])
                cond = f" | 条件: `{r['join_condition']}`" if r.get("join_condition") else ""
                sections.append(
                    f"- `{src_t}.{r['source_field']}` -> `{tgt_t}.{r['target_field']}` "
                    f"({r['cardinality']}, {r['relation_type']}){cond}"
                )

        warn_rels = [r for r in relations if r.get("is_warning") or r.get("relation_type") == "DO_NOT_JOIN"]
        if warn_rels:
            sections.append("\n【⚠️ 高危红线与避坑规则 (DO NOT VIOLATE)】:")
            for idx, r in enumerate(warn_rels, start=1):
                src_t = entity_map.get(r["source_entity_id"], r["source_entity_id"].split(".")[-1])
                tgt_t = entity_map.get(r["target_entity_id"], r["target_entity_id"].split(".")[-1])
                msg = r.get("warning_message") or r.get("filter_rule") or "高危关联，请谨慎校验！"
                sections.append(f"- [红线 {idx}] `{src_t}` <-> `{tgt_t}` ({r['relation_type']}): {msg}")

    if not has_compatible_profile:
        return ""

    sections.append("=====================================================================================")
    joined = "\n".join(sections)
    if len(joined) > max_chars:
        return joined[:max_chars] + "\n...(数据结构上下文已按预算截断)"
    return joined


def audit_sql_rules_for_graph(
    *,
    sql_code: str,
    graph: dict[str, Any],
    mounted_entities: str | list[str] = "*",
    question_hint: str = "",
) -> dict[str, Any]:
    """O3 深度 SQL 静态审计核心引擎（R001 - R006），支持结构化返回。"""
    sql = (sql_code or "").strip()
    if not sql:
        return {"audit_passed": True, "warnings": []}

    sql_low = re.sub(r"\s+", " ", sql.lower())
    referenced_tables = set(
        re.findall(r"\b(?:from|join)\s+(?:[`\"\[]?\w+[`\"\]]?\.)?[`\"\[]?([a-zA-Z0-9_]+)[`\"\]]?", sql_low)
    )

    entities = graph.get("entities") or []
    relations = graph.get("relations") or []
    profile_desc = str((graph.get("profile") or {}).get("description") or "")
    profile_desc_low = profile_desc.lower()
    entity_map = {e["id"]: str(e["table_name"]).lower() for e in entities}
    entity_by_table = {str(e["table_name"]).lower(): e for e in entities}
    warnings: list[dict[str, Any]] = []

    # 子集表越权检查
    if isinstance(mounted_entities, list) and mounted_entities:
        allowed_tables = {t.strip().lower() for t in mounted_entities if t.strip()}
        profile_tables = set(entity_map.values())
        unauthorized = (referenced_tables & profile_tables) - allowed_tables
        if unauthorized:
            warnings.append(
                {
                    "rule_code": "R000",
                    "severity": "HARD_BLOCK",
                    "message": f"SQL 访问了未挂载给当前专家的子集外数据表: {', '.join(sorted(unauthorized))}",
                }
            )

    # R001 (DO_NOT_JOIN) & R002 (INNER JOIN 误用)
    for rel in relations:
        is_danger = bool(rel.get("is_warning")) or rel.get("relation_type") == "DO_NOT_JOIN"
        if not is_danger:
            continue
        src_t = entity_map.get(rel["source_entity_id"], rel["source_entity_id"].split(".")[-1].lower())
        tgt_t = entity_map.get(rel["target_entity_id"], rel["target_entity_id"].split(".")[-1].lower())
        if src_t not in referenced_tables or tgt_t not in referenced_tables:
            continue

        warn_msg = rel.get("warning_message") or rel.get("filter_rule") or "触犯结构红线"
        if rel.get("relation_type") == "DO_NOT_JOIN":
            warnings.append(
                {
                    "rule_code": "R001",
                    "severity": "HARD_BLOCK",
                    "source_table": src_t,
                    "target_table": tgt_t,
                    "message": f"禁止直接关联禁连表 `{src_t}` 与 `{tgt_t}`：{warn_msg}",
                }
            )
        elif rel.get("relation_type") == "LEFT_JOIN" or "left join" in warn_msg.lower() or "inner join" in warn_msg.lower():
            has_left_join_tgt = bool(
                re.search(
                    rf"\bleft\s+(?:outer\s+)?join\s+(?:[`\"\[]?\w+[`\"\]]?\.)?[`\"\[]?{re.escape(tgt_t)}[`\"\]]?\b",
                    sql_low,
                )
            )
            has_inner_join_tgt = bool(
                re.search(
                    rf"\b(?:inner\s+)?join\s+(?:[`\"\[]?\w+[`\"\]]?\.)?[`\"\[]?{re.escape(tgt_t)}[`\"\]]?\b",
                    sql_low,
                )
            )
            if has_inner_join_tgt and not has_left_join_tgt:
                warnings.append(
                    {
                        "rule_code": "R002",
                        "severity": "WARNING",
                        "source_table": src_t,
                        "target_table": tgt_t,
                        "message": f"`{src_t}` 关联 `{tgt_t}` 时使用了非 LEFT JOIN：{warn_msg}",
                    }
                )
            elif has_left_join_tgt and " where " in sql_low:
                where_part = sql_low.split(" where ", 1)[1]
                # 提取 LEFT JOIN 目标表的别名或表名，检测是否在 WHERE 中加了非 IS NULL 条件导致 LEFT JOIN 退化
                alias_m = re.search(
                    rf"\bleft\s+(?:outer\s+)?join\s+(?:[`\"\[]?\w+[`\"\]]?\.)?[`\"\[]?{re.escape(tgt_t)}[`\"\]]?(?:\s+(?:as\s+)?([a-z0-9_]+))?",
                    sql_low,
                )
                tgt_ref = alias_m.group(1) if (alias_m and alias_m.group(1) and alias_m.group(1) not in ("on", "where")) else tgt_t
                if re.search(rf"\b{re.escape(tgt_ref)}\.[a-z0-9_]+\s*(?:=|!=|<>|in\s*\()", where_part):
                    warnings.append(
                        {
                            "rule_code": "R002",
                            "severity": "WARNING",
                            "source_table": src_t,
                            "target_table": tgt_t,
                            "message": f"检测到在 `WHERE` 子句中对 LEFT JOIN 右表 `{tgt_t}` 施加了过滤条件，这会导致 LEFT JOIN 退化为 INNER JOIN 并过滤掉 -100 等个人/无归属记录！请移入 `ON` 条件或使用 `CASE WHEN`。",
                        }
                    )

    # R003 金额换算遗漏（完全由字段描述含“分”或 Wiki 口径声明动态识别，不硬编码字段名）
    cent_fields: set[str] = set()
    for ent in entities:
        for f in ent.get("fields") or []:
            fname = str(f.get("field_name") or "").lower()
            desc = str(f.get("description") or "")
            if "分" in desc and any(k in fname for k in ("fee", "amount", "price", "money", "fen")):
                cent_fields.add(fname)
    for line in profile_desc.splitlines():
        if "分" in line and ("100" in line or "元" in line):
            for tok in re.findall(r"`?(?:[a-z0-9_]+\.)?([a-z0-9_]+)`?", line.lower()):
                if any(k in tok for k in ("fee", "amount", "price", "money", "fen")):
                    cent_fields.add(tok)
    for cf in cent_fields:
        if re.search(rf"\bsum\s*\([^)]*\b{re.escape(cf)}\b[^)]*\)", sql_low) and "/ 100" not in sql_low and "/100" not in sql_low:
            warnings.append(
                {
                    "rule_code": "R003",
                    "severity": "WARNING",
                    "message": f"检测到金额字段 `{cf}` 聚合未做 `/ 100.0`（分转元）换算，请注意单位是否为分。",
                }
            )
            break

    # R004 父子订单/必加过滤门禁遗漏（由表字段元数据或 Wiki 口径动态识别 `pid = 0` 要求）
    for tbl_name in referenced_tables:
        ent = entity_by_table.get(tbl_name)
        if not ent:
            continue
        has_pid_rule_in_fields = any(
            str(f.get("field_name") or "").lower() == "pid"
            and ("0" in str(f.get("description") or "") or "父" in str(f.get("description") or ""))
            for f in (ent.get("fields") or [])
        )
        has_pid_rule_in_wiki = any(
            tbl_name in line.lower() and "pid" in line.lower() and "= 0" in line.lower()
            for line in profile_desc.splitlines()
        )
        if (has_pid_rule_in_fields or has_pid_rule_in_wiki) and ("sum(" in sql_low or any(cf in sql_low for cf in cent_fields)):
            if not re.search(r"\bpid\s*=\s*0\b", sql_low):
                warnings.append(
                    {
                        "rule_code": "R004",
                        "severity": "HARD_BLOCK",
                        "message": f"统计 `{tbl_name}` 金额/订单时未过滤 `pid = 0`（仅统计父订单），可能导致金额重复计算！",
                    }
                )

    # R005 时间条件缺失（动态识别实体时间列 + 常见时间函数）
    q_hint = (question_hint or "").strip()
    if q_hint and re.search(r"(20\d{2}|今年|去年|本月|上月|近\d+天)", q_hint):
        time_cols: set[str] = {"create_time", "update_time", "audit_time", "pay_time", "reg_time"}
        for ent in entities:
            for f in ent.get("fields") or []:
                ft = str(f.get("field_type") or "").upper()
                fn = str(f.get("field_name") or "").lower()
                if any(t in ft for t in ("DATE", "TIME")) or fn.endswith(("_time", "_date", "_at")):
                    time_cols.add(fn)
        time_pat = "|".join(re.escape(c) for c in sorted(time_cols))
        if not re.search(rf"\b(?:{time_pat})\b|\byear\s*\(|\bdate_format\s*\(", sql_low):
            warnings.append(
                {
                    "rule_code": "R005",
                    "severity": "WARNING",
                    "message": "提问包含明确时间范围，但 SQL 中未检测到时间筛选字段，当前可能为全量累计结果。",
                }
            )

    # R006 跨表状态字段错用（基于当前 Profile 各表真实字段清单动态判定，零硬编码）
    referenced_known = [t for t in referenced_tables if t in entity_by_table]
    if len(referenced_known) == 1:
        only_tbl = referenced_known[0]
        only_ent = entity_by_table[only_tbl]
        only_fields = {str(f.get("field_name") or "").lower() for f in (only_ent.get("fields") or []) if f.get("field_name")}
        if only_fields:
            other_status_fields: set[str] = set()
            for other_tbl, other_ent in entity_by_table.items():
                if other_tbl == only_tbl:
                    continue
                for f in other_ent.get("fields") or []:
                    fn = str(f.get("field_name") or "").lower()
                    if fn and fn not in only_fields and ("status" in fn or "state" in fn):
                        other_status_fields.add(fn)
            for wrong_col in sorted(other_status_fields):
                if re.search(rf"\b{re.escape(wrong_col)}\b", sql_low):
                    warnings.append(
                        {
                            "rule_code": "R006",
                            "severity": "HARD_BLOCK",
                            "message": f"疑似将其他表的状态字段 `{wrong_col}` 错用于 `{only_tbl}`（`{only_tbl}` 表中无此字段）。",
                        }
                    )

    # R007 年度时间字段错用（完全由 Wiki 口径中的“严禁用/禁用 <字段>”与表名动态解析）
    for line in profile_desc.splitlines():
        line_low = line.lower()
        if not any(k in line for k in ("严禁用", "禁用", "禁止使用", "不要用")):
            continue
        for tbl_name in referenced_tables:
            if tbl_name not in line_low:
                continue
            bad_cols = re.findall(r"(?:严禁用|禁用|禁止使用|不要用)\s*`?(?:year\s*\(\s*)?([a-z0-9_]+)(?:\s*\))?`?", line_low)
            for bad_col in bad_cols:
                if bad_col and re.search(rf"\byear\s*\(\s*(?:[a-z0-9_]+\.)?{re.escape(bad_col)}\s*\)", sql_low):
                    warnings.append(
                        {
                            "rule_code": "R007",
                            "severity": "WARNING",
                            "message": f"检测到 `{tbl_name}` 使用了 Wiki 禁用的年度时间字段 `YEAR({bad_col})`：{line.strip()}",
                        }
                    )

    has_hard_block = any(w["severity"] == "HARD_BLOCK" for w in warnings)
    return {
        "audit_passed": not has_hard_block and len(warnings) == 0,
        "has_hard_block": has_hard_block,
        "warnings": warnings,
    }


async def audit_sql_against_schema(
    agent_id: str,
    sql_code: str,
    question_hint: str = "",
    caller: Any | None = None,
) -> list[str]:
    """事后红线静态审计（R000-R007 口径红线 + P001-P003 权限硬拦截）：返回可读告警列表。"""
    sql = (sql_code or "").strip()
    if not agent_id or not sql:
        return []

    alerts: list[str] = []

    # 优先执行 P001-P003 权限硬审计
    if caller is not None:
        from .access_control import audit_sql_access_control

        for pv in audit_sql_access_control(sql, caller):
            alerts.append(f"[{pv['rule_code']} 权限越权拦截] {pv['message']}")

    bindings = await SchemaProfileRepository.get_agent_bindings(agent_id)
    if not bindings:
        return alerts
    agent_target_db = await resolve_agent_target_db(agent_id, bindings=bindings)
    for binding in bindings:
        pid = binding["profile_id"]
        mounted = binding.get("mounted_entities", "*")
        graph = await SchemaProfileRepository.get_profile_graph(pid)
        if not graph:
            continue
        if not is_profile_compatible_with_target_db(graph["profile"], graph["entities"], agent_target_db):
            continue
        result = audit_sql_rules_for_graph(
            sql_code=sql,
            graph=graph,
            mounted_entities=mounted,
            question_hint=question_hint,
        )
        for w in result["warnings"]:
            tag = "红线违规拦截" if w["severity"] == "HARD_BLOCK" else "口径风险提醒"
            alerts.append(f"[{w['rule_code']} {tag}] {w['message']}")

    return alerts
