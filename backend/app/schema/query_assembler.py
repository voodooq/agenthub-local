"""
O1: 结构化查数指令拼装引擎 (QueryInstructionAssembler)
面向任意业务系统（不限于特定行业），结合 Schema Profile 动态元数据、
1-Hop 关联路径与 Markdown 业务口径章节，精准提炼高信噪比查询约束。
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from ..sql.auto_ontology_client import infer_target_db, normalize_target_db


class AssembledQueryRequest(BaseModel):
    profile_id: str
    original_question: str
    target_tables: list[str] = Field(default_factory=list)
    time_filter_hint: str | None = None
    status_filter_hint: str | None = None
    group_by_fields: list[str] = Field(default_factory=list)
    deduplication_hint: str | None = None
    output_columns_contract: list[str] = Field(default_factory=list)
    boundary_rules: list[str] = Field(default_factory=list)


_YEAR_RE = re.compile(r"(20\d{2})\s*年?")
_CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```")
_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]{2,}|[\u4e00-\u9fff]{2,}")
_PROHIBITION_RE = re.compile(r"(严禁|禁止|切勿|不得|不可|不能|不要|DO\s+NOT|FORBIDDEN)", re.IGNORECASE)

# 通用跨行业数据库分析意图词簇
_GENERIC_INTENT_CLUSTERS: tuple[tuple[str, ...], ...] = (
    ("年", "年度", "月", "季度", "时间", "日期", "期限", "生效", "创建", "更新", "202", "time", "date", "year"),
    ("金额", "费用", "价格", "营收", "实收", "补贴", "预算", "元", "分", "订单", "支付", "缴费", "退款", "sum", "amount", "fee", "price"),
    ("省", "市", "区", "县", "街道", "地区", "区域", "归属", "个人", "团体", "单位", "散客", "left join", "region", "area"),
    ("人数", "数量", "人次", "条数", "去重", "统计", "汇总", "平均", "占比", "count", "distinct", "group by"),
    ("审核", "状态", "有效", "通过", "生效", "作废", "删除", "测试", "status", "audit", "approve", "deleted"),
)


def _extract_bigrams(text: str) -> set[str]:
    """提取中英文词元与中文双字切分（无需外部分词库即可实现跨业务通用匹配）。"""
    grams: set[str] = set()
    for tok in _TOKEN_RE.findall((text or "").lower()):
        grams.add(tok)
        if "\u4e00" <= tok[0] <= "\u9fff" and len(tok) > 2:
            for i in range(len(tok) - 1):
                grams.add(tok[i : i + 2])
    return grams


def extract_relevant_business_rules(
    question: str,
    expanded_tables: set[str],
    rules_markdown: str,
    *,
    all_known_tables: set[str] | None = None,
    explicit_table_hit: bool = True,
    domain_terms: set[str] | None = None,
    max_items: int = 6,
    max_chars: int = 800,
) -> list[str]:
    """跨业务通用业务口径提炼器：结合小节语义继承、动态实体词根与双字切分精准筛选条目。"""
    raw_md = (rules_markdown or "").strip()
    if not raw_md:
        return []

    cleaned_md = _CODE_BLOCK_RE.sub("", raw_md)
    candidates: list[tuple[str, str]] = []  # (section_heading, item_text)
    current_heading = ""

    for line in cleaned_md.splitlines():
        s = line.strip()
        if not s or s.startswith((">", "---")):
            continue
        if s.startswith("#"):
            current_heading = s.lstrip("#").strip()
            continue
        item = re.sub(r"^(?:[-*]|\d+\.)\s+", "", s).strip()
        if len(item) >= 8:
            candidates.append((current_heading, item))

    if not candidates:
        return []

    q_low = (question or "").lower()
    q_grams = _extract_bigrams(question)
    active_clusters = [c for c in _GENERIC_INTENT_CLUSTERS if any(kw in q_low for kw in c)]

    table_tokens = {t.lower() for t in expanded_tables if t}
    unrelated_tables = (
        {t.lower() for t in (all_known_tables or set()) if t} - table_tokens
        if explicit_table_hit
        else set()
    )
    extra_terms = {t.lower() for t in (domain_terms or set()) if t and len(t) >= 2}

    prohibition_scored: list[tuple[int, int, str]] = []
    regular_scored: list[tuple[int, int, str]] = []
    for idx, (heading, item) in enumerate(candidates):
        item_low = item.lower()
        search_scope = f"{heading} {item}".lower()

        # 若问题明确命中了某主表，而该规则只提到了其他无关物理表，则直接跳过
        mentions_target = any(tbl in search_scope for tbl in table_tokens)
        mentions_unrelated = any(
            re.search(rf"\b{re.escape(ut)}\b", item_low) for ut in unrelated_tables
        )
        if mentions_unrelated and not mentions_target:
            continue

        mentions_any_known = any(
            re.search(rf"\b{re.escape(kt.lower())}\b", item_low) for kt in (all_known_tables or set())
        )
        is_prohibition = bool(_PROHIBITION_RE.search(item))

        score = 0

        # 1. 表名精确命中（最核心信号）
        for tbl in table_tokens:
            if tbl in search_scope:
                score += 12 if tbl in q_low else 7

        # 2. 动态实体中文名 / 字段名 / 业务词根命中（跨行业自适应）
        for term in extra_terms:
            if term in search_scope:
                score += 4

        # 3. 问题中英文词元与中文双字重合度（包容各种行业术语）
        item_grams = _extract_bigrams(search_scope)
        overlap = len(q_grams & item_grams)
        if overlap > 0:
            score += min(overlap * 3, 12)

        # 4. 通用分析意图词簇共振
        for cluster in active_clusters:
            if any(kw in search_scope for kw in cluster):
                score += 4

        # 5. 全局无表名红线保底（如“严禁使用 SELECT *”“全局通用排除测试账号”）
        if not mentions_any_known and any(k in item for k in ("全局", "通用", "所有表", "统一")):
            score += 3

        # 通道分离：凡命中明令禁止词且（属于当前命中表 OR 为全局无表名禁令），强制纳入禁止通道
        if is_prohibition and (mentions_target or not mentions_any_known):
            prohibition_priority = (20 if mentions_target else 5) + score
            prohibition_scored.append((prohibition_priority, -idx, item))
        elif score > 0:
            regular_scored.append((score, -idx, item))

    prohibition_scored.sort(reverse=True)
    regular_scored.sort(reverse=True)

    picked: list[str] = []
    seen_compact: set[str] = set()
    used_chars = 0

    def _append_item(raw_item: str, limit_chars: int) -> bool:
        nonlocal used_chars
        compact = re.sub(r"\s+", " ", raw_item).strip()
        if len(compact) > 240:
            compact = compact[:237] + "..."
        if compact in seen_compact:
            return False
        if used_chars + len(compact) > limit_chars and picked:
            return False
        seen_compact.add(compact)
        picked.append(compact)
        used_chars += len(compact)
        return True

    # 先强制保底纳入最多 4 条明令禁止内容，防止被正相匹配挤掉
    for _, _, item in prohibition_scored[:4]:
        _append_item(item, max_chars)

    # 再按相关度补充正相业务口径
    for _, _, item in regular_scored:
        if len(picked) >= max_items:
            break
        _append_item(item, max_chars)

    return picked


class QueryInstructionAssembler:
    """结合 Schema Profile 元数据、1-Hop 关联路径与通用业务口径，精准拼装查询指令。"""

    @classmethod
    def assemble_instruction(
        cls,
        question: str,
        profile_context: dict[str, Any],
        selected_tables: list[str] | None = None,
        target_db: str | None = None,
    ) -> dict[str, Any]:
        """根据问题、命中表与 1-Hop 图谱关系生成结构化指令文本与边界规则。"""
        q = (question or "").strip()
        profile_meta: dict[str, Any] = profile_context.get("profile") or {}
        effective_db = (
            normalize_target_db(target_db)
            or normalize_target_db(profile_meta.get("target_db"))
            or infer_target_db(
                f"{profile_meta.get('id') or ''} {profile_meta.get('name') or ''} {profile_meta.get('domain_prefix') or ''}"
            )
        )
        raw_entities: list[dict[str, Any]] = profile_context.get("entities") or []
        if effective_db == "equestrianinfo":
            entities = [e for e in raw_entities if not str(e.get("table_name") or "").lower().startswith("fe_")]
        elif effective_db == "fencinginfo":
            entities = [
                e
                for e in raw_entities
                if not str(e.get("table_name") or "").lower().startswith(("eq_", "equestrian_"))
            ]
        else:
            entities = raw_entities
        allowed_entity_ids = {str(e.get("id") or "") for e in entities}
        relations: list[dict[str, Any]] = [
            r
            for r in (profile_context.get("relations") or [])
            if (not allowed_entity_ids)
            or (
                str(r.get("source_entity_id") or "") in allowed_entity_ids
                and str(r.get("target_entity_id") or "") in allowed_entity_ids
            )
        ]

        allow_set = {t.strip() for t in selected_tables if t.strip()} if selected_tables else None
        candidate_entities = [
            e for e in entities if allow_set is None or e.get("table_name") in allow_set
        ]

        # 1. 识别命中的主表（支持表名、中文业务名及去“表/字典”后缀匹配）
        matched_entities: list[dict[str, Any]] = []
        q_low = q.lower()
        for ent in candidate_entities:
            t_name = str(ent.get("table_name") or "")
            d_name = str(ent.get("display_name") or "").strip()
            d_core = re.sub(r"(?:基础|主|明细|信息|统计|配置|字典)?表$|字典$", "", d_name).strip()
            if (
                (t_name and t_name.lower() in q_low)
                or (d_name and len(d_name) >= 2 and d_name in q)
                or (d_core and len(d_core) >= 2 and d_core in q)
            ):
                matched_entities.append(ent)
        explicit_hit = len(matched_entities) > 0
        if not matched_entities:
            matched_entities = candidate_entities[:4]

        hit_tables = {str(e["table_name"]) for e in matched_entities if e.get("table_name")}
        entity_id_to_table = {str(e.get("id") or ""): str(e.get("table_name") or "") for e in entities}

        # 2. 沿 schema_relations 自动扩展 1-Hop 关联表与关联规则
        expanded_tables: set[str] = set(hit_tables)
        relevant_relations: list[dict[str, Any]] = []
        for rel in relations:
            src_t = str(
                rel.get("source_table")
                or entity_id_to_table.get(str(rel.get("source_entity_id") or ""), "")
                or str(rel.get("source_entity_id") or "").split(".")[-1]
            )
            tgt_t = str(
                rel.get("target_table")
                or entity_id_to_table.get(str(rel.get("target_entity_id") or ""), "")
                or str(rel.get("target_entity_id") or "").split(".")[-1]
            )
            if not src_t or not tgt_t:
                continue
            if src_t in hit_tables or tgt_t in hit_tables:
                expanded_tables.add(src_t)
                expanded_tables.add(tgt_t)
                relevant_relations.append({**rel, "_src_t": src_t, "_tgt_t": tgt_t})

        target_tables = sorted(expanded_tables)
        year_m = _YEAR_RE.search(q)
        time_hint = f"严格限定 {year_m.group(1)} 年时间范围" if year_m else None

        status_hints: list[str] = []
        domain_terms: set[str] = set()
        for ent in candidate_entities:
            if ent.get("table_name") not in expanded_tables:
                continue
            if ent.get("display_name"):
                domain_terms.add(str(ent["display_name"]))
            for f in ent.get("fields") or []:
                if f.get("field_name"):
                    domain_terms.add(str(f["field_name"]))
                enum_val = str(f.get("enum_values") or "").strip()
                if enum_val:
                    status_hints.append(f"{ent['table_name']}.{f['field_name']}: {enum_val}")

        boundary_rules: list[str] = []
        join_lines: list[str] = []
        for rel in relevant_relations:
            r_type = str(rel.get("relation_type") or "LEFT_JOIN")
            cond = str(rel.get("join_condition") or "").strip()
            warn = str(rel.get("warning_message") or rel.get("filter_rule") or "").strip()
            line = f"  * `{rel['_src_t']}` -> `{rel['_tgt_t']}` USING {r_type}"
            if cond:
                line += f" ON {cond}"
            if warn:
                line += f" (CRITICAL: {warn})"
            join_lines.append(line)
            if (rel.get("is_warning") or r_type == "DO_NOT_JOIN") and warn:
                boundary_rules.append(warn)

        dedup_hint = (
            "按主实体主键去重统计 (COUNT(DISTINCT id))"
            if any(k in q for k in ("人数", "数量", "多少", "统计"))
            else None
        )

        parts: list[str] = [q, "[Schema Constraints]"]
        if effective_db:
            parts.append(f"- 目标数据库: {effective_db}")
        if explicit_hit and target_tables:
            parts.append(f"- 表范围: {', '.join(target_tables)}")
        if join_lines:
            parts.append("- 1-Hop 关联:")
            parts.extend(join_lines[:15])
        if time_hint:
            parts.append(f"- 时间: {time_hint}")
        if status_hints:
            parts.append(f"- 枚举: {'; '.join(status_hints[:6])}")
        if dedup_hint:
            parts.append(f"- 去重: {dedup_hint}")
        parts.append("- 仅返回聚合指标/关键列，禁止 SELECT *")
        if boundary_rules:
            parts.append(f"- 红线: {'；'.join(boundary_rules[:4])}")

        all_known = {str(e.get("table_name") or "") for e in entities if e.get("table_name")}
        matched_rules = extract_relevant_business_rules(
            question=q,
            expanded_tables=expanded_tables,
            rules_markdown=str(profile_meta.get("description") or ""),
            all_known_tables=all_known,
            explicit_table_hit=explicit_hit,
            domain_terms=domain_terms,
        )
        if matched_rules:
            parts.append("- 命中业务口径:")
            for r_line in matched_rules:
                parts.append(f"  * {r_line}")

        return {
            "assembled_prompt": "\n".join(parts),
            "target_tables": target_tables,
            "time_filter_hint": time_hint,
            "status_filter_hint": "; ".join(status_hints[:6]) if status_hints else None,
            "deduplication_hint": dedup_hint,
            "boundary_rules": boundary_rules,
            "matched_rules": matched_rules,
        }
