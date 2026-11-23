"""
双通道数据结构解析器：
1. SQL DDL 解析器（MySQL / PostgreSQL CREATE TABLE）
2. Markdown 结构化文档解析器（兼容表格与口径红线提取）
统一输出可供前端预览微调的 {source_format, entities, relations, warnings} 结构。
"""
from __future__ import annotations

import re
from typing import Any

# 匹配 CREATE TABLE [IF NOT EXISTS] [db.]table_name ( ... ) [table_options];
_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:[`\"\[]?[a-zA-Z0-9_]+[`\"\]]?\.)?[`\"\[]?(?P<table>[a-zA-Z0-9_]+)[`\"\]]?\s*\(",
    re.IGNORECASE,
)
_TABLE_COMMENT_RE = re.compile(r"COMMENT\s*=?\s*['\"]([^'\"]+)['\"]", re.IGNORECASE)
_INLINE_COMMENT_RE = re.compile(r"\bCOMMENT\s+['\"]([^'\"]+)['\"]", re.IGNORECASE)
_PK_CONSTRAINT_RE = re.compile(
    r"^(?:CONSTRAINT\s+[`\"\[]?\w+[`\"\]]?\s+)?PRIMARY\s+KEY\s*\(([^)]+)\)",
    re.IGNORECASE,
)
_FK_CONSTRAINT_RE = re.compile(
    r"^(?:CONSTRAINT\s+[`\"\[]?\w+[`\"\]]?\s+)?FOREIGN\s+KEY\s*\(([^)]+)\)\s+REFERENCES\s+(?:[`\"\[]?[a-zA-Z0-9_]+[`\"\]]?\.)?[`\"\[]?(?P<ref_table>[a-zA-Z0-9_]+)[`\"\]]?\s*\(([^)]+)\)",
    re.IGNORECASE,
)
_ENUM_HINT_RE = re.compile(r"(?:^|[,，;；\s(（])(-?\d+\s*[:：=]\s*[^,，;；)）]+)")
_MD_HEADING_RE = re.compile(r"^#{2,3}\s+(.+?)\s*$", re.MULTILINE)
_MD_TABLE_CODE_RE = re.compile(r"`([a-zA-Z][a-zA-Z0-9_]{2,})`")
_MD_PAREN_NAME_RE = re.compile(r"^`?(?P<table>[a-zA-Z][a-zA-Z0-9_]+)`?\s*[（(](?P<display>[^)）]+)[)）]")


def _split_top_level_commas(body: str) -> list[str]:
    """按顶层逗号切分建表语句内部定义，保护 DECIMAL(10,2) 与 COMMENT 中的逗号。"""
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    in_quote: str | None = None
    escaped = False

    for ch in body:
        if escaped:
            buf.append(ch)
            escaped = False
            continue
        if ch == "\\":
            buf.append(ch)
            escaped = True
            continue
        if in_quote:
            buf.append(ch)
            if ch == in_quote:
                in_quote = None
            continue
        if ch in ("'", '"', "`"):
            in_quote = ch
            buf.append(ch)
            continue
        if ch == "(":
            depth += 1
            buf.append(ch)
            continue
        if ch == ")":
            depth = max(0, depth - 1)
            buf.append(ch)
            continue
        if ch == "," and depth == 0:
            item = "".join(buf).strip()
            if item:
                parts.append(item)
            buf = []
            continue
        buf.append(ch)

    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def _extract_balanced_parens(text: str, open_idx: int) -> tuple[str, int]:
    """从左括号索引提取配对括号内容及结束位置。"""
    depth = 0
    in_quote: str | None = None
    escaped = False
    for idx in range(open_idx, len(text)):
        ch = text[idx]
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if in_quote:
            if ch == in_quote:
                in_quote = None
            continue
        if ch in ("'", '"', "`"):
            in_quote = ch
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[open_idx + 1 : idx], idx
    return text[open_idx + 1 :], len(text) - 1


def _extract_enum_hint(desc: str) -> str:
    """从注释中抽取状态码枚举提示（如 2=审核通过, -100=散客）。"""
    matches = _ENUM_HINT_RE.findall(desc or "")
    if not matches:
        return ""
    return "; ".join(m.strip() for m in matches[:12])


def _infer_category(table_name: str, description: str = "") -> str:
    low = f"{table_name} {description}".lower()
    if any(k in low for k in ("log", "audit", "history", "record", "日志")):
        return "LOG"
    if any(k in low for k in ("dict", "config", "setting", "type", "level", "配置", "字典")):
        return "CONFIG"
    if any(k in low for k in ("item", "detail", "line", "registration", "明细", "报名")):
        return "DETAIL"
    return "CORE_MAIN"


def parse_sql_ddl(ddl_text: str) -> dict[str, Any]:
    """解析 SQL CREATE TABLE DDL，提取实体、字段、主键、注释与外键关系。"""
    entities: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    warnings: list[str] = []

    pos = 0
    idx = 0
    while True:
        match = _CREATE_TABLE_RE.search(ddl_text, pos)
        if not match:
            break
        table_name = match.group("table").strip()
        open_paren = match.end() - 1
        body, close_paren = _extract_balanced_parens(ddl_text, open_paren)

        # 读取括号后到分号之前的表注释
        tail_end = ddl_text.find(";", close_paren)
        tail = ddl_text[close_paren + 1 : tail_end if tail_end != -1 else close_paren + 300]
        pos = tail_end + 1 if tail_end != -1 else close_paren + 1

        table_comment_m = _TABLE_COMMENT_RE.search(tail)
        table_desc = table_comment_m.group(1).strip() if table_comment_m else ""
        display_name = table_desc.split("（")[0].split("(")[0].strip() if table_desc else table_name

        fields: list[dict[str, Any]] = []
        pk_cols: set[str] = set()

        for line_raw in _split_top_level_commas(body):
            line = line_raw.strip()
            if not line:
                continue
            upper = line.upper()

            pk_m = _PK_CONSTRAINT_RE.match(line)
            if pk_m:
                for col in pk_m.group(1).split(","):
                    pk_cols.add(col.strip(" `\"[]"))
                continue

            fk_m = _FK_CONSTRAINT_RE.match(line)
            if fk_m:
                src_col = fk_m.group(1).split(",")[0].strip(" `\"[]")
                ref_table = fk_m.group("ref_table").strip()
                ref_col = fk_m.group(3).split(",")[0].strip(" `\"[]")
                relations.append(
                    {
                        "source_table": table_name,
                        "source_field": src_col,
                        "target_table": ref_table,
                        "target_field": ref_col,
                        "relation_type": "FK",
                        "cardinality": "N:1",
                        "join_condition": f"{table_name}.{src_col} = {ref_table}.{ref_col}",
                        "filter_rule": "",
                        "is_warning": False,
                        "warning_message": "",
                    }
                )
                continue

            # 跳过纯索引/约束声明
            if upper.startswith(("KEY ", "INDEX ", "UNIQUE ", "FULLTEXT ", "SPATIAL ", "CONSTRAINT ", "CHECK ")):
                continue

            col_m = re.match(r"^[`\"\[]?(?P<col>[a-zA-Z0-9_]+)[`\"\]]?\s+(?P<rest>.+)$", line, re.DOTALL)
            if not col_m:
                continue
            col_name = col_m.group("col").strip()
            rest = col_m.group("rest").strip()

            type_m = re.match(r"^([a-zA-Z0-9_]+(?:\s*\([^)]+\))?)", rest)
            col_type = type_m.group(1).upper() if type_m else "VARCHAR"

            comment_m = _INLINE_COMMENT_RE.search(rest)
            col_desc = comment_m.group(1).strip() if comment_m else ""
            is_pk = "PRIMARY KEY" in rest.upper()
            if is_pk:
                pk_cols.add(col_name)
            is_nullable = "NOT NULL" not in rest.upper() and not is_pk

            fields.append(
                {
                    "field_name": col_name,
                    "field_type": col_type,
                    "display_name": col_desc[:24] if col_desc else col_name,
                    "description": col_desc,
                    "is_nullable": is_nullable,
                    "is_primary_key": is_pk,
                    "enum_values": _extract_enum_hint(col_desc),
                    "sort_order": len(fields),
                }
            )

        # 回填表级 PRIMARY KEY 标记
        for f in fields:
            if f["field_name"] in pk_cols:
                f["is_primary_key"] = True
                f["is_nullable"] = False

        primary_key = next((f["field_name"] for f in fields if f["is_primary_key"]), "id")
        entities.append(
            {
                "table_name": table_name,
                "display_name": display_name or table_name,
                "category": _infer_category(table_name, table_desc),
                "description": table_desc,
                "primary_key": primary_key,
                "pos_x": float((idx % 3) * 380 + 40),
                "pos_y": float((idx // 3) * 320 + 40),
                "fields": fields,
            }
        )
        idx += 1

    if not entities:
        warnings.append("未检测到有效的 CREATE TABLE 语句，请检查 SQL DDL 格式。")

    return {
        "source_format": "sql",
        "entities": entities,
        "relations": relations,
        "warnings": warnings,
    }


def _parse_md_table(lines: list[str]) -> list[dict[str, Any]]:
    """从 Markdown 片段中解析字段表格（兼容中英文表头）。"""
    fields: list[dict[str, Any]] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("|") and i + 1 < len(lines) and "---" in lines[i + 1]:
            headers = [h.strip().lower() for h in line.strip("|").split("|")]
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip().strip("`") for c in lines[i].strip().strip("|").split("|")]
                i += 1
                if len(cells) < 2:
                    continue
                row_map = {headers[k]: cells[k] for k in range(min(len(headers), len(cells)))}
                f_name = (
                    row_map.get("field_name")
                    or row_map.get("字段名")
                    or row_map.get("字段")
                    or row_map.get("列名")
                    or cells[0]
                ).strip()
                if not f_name or not re.match(r"^[a-zA-Z0-9_]+$", f_name):
                    continue
                f_type = (
                    row_map.get("field_type")
                    or row_map.get("类型")
                    or row_map.get("数据类型")
                    or (cells[1] if len(cells) > 1 else "VARCHAR")
                ).strip()
                nullable_raw = (row_map.get("nullable") or row_map.get("可空") or "YES").upper()
                desc = (
                    row_map.get("description")
                    or row_map.get("说明")
                    or row_map.get("口径")
                    or row_map.get("业务口径")
                    or (cells[-1] if len(cells) >= 3 else "")
                ).strip()
                fields.append(
                    {
                        "field_name": f_name,
                        "field_type": f_type or "VARCHAR",
                        "display_name": desc[:24] if desc else f_name,
                        "description": desc,
                        "is_nullable": nullable_raw not in ("NO", "FALSE", "0", "否", "NOT NULL"),
                        "is_primary_key": f_name == "id" or "主键" in desc,
                        "enum_values": _extract_enum_hint(desc),
                        "sort_order": len(fields),
                    }
                )
            if fields:
                break
        i += 1
    return fields


_RELATION_LINE_RE = re.compile(
    r"^\s*[-*]\s+`?([a-zA-Z0-9_]+)(?:\.([a-zA-Z0-9_]+))?`?\s*(?:->|→|=>)\s*`?([a-zA-Z0-9_]+)(?:\.([a-zA-Z0-9_]+))?`?"
    r"(?:\s*\(\s*`?([A-Z_]+)`?\s*(?:,\s*`?is_warning\s*=\s*(true|false)`?)?\s*\))?"
    r"(.*)$",
    re.IGNORECASE,
)
_KNOWN_RELATION_TYPES = {"FK", "LOGICAL_JOIN", "LEFT_JOIN", "JSON_EXTRACT", "DO_NOT_JOIN"}
_RELATION_WARNING_KEYWORDS = (
    "严禁",
    "禁止",
    "翻倍",
    "膨胀",
    "串表",
    "串号",
    "-100",
    "必须加",
    "必须指定",
    "别名",
    "去重",
)


def _extract_on_condition(tail: str) -> str:
    """从关系行尾部提取 ON 条件，保留括号内的逗号/分号不被提前截断。"""
    match = re.search(r"ON\s*条件[：:\s]+", tail, re.IGNORECASE)
    if not match:
        return ""
    rest = tail[match.end() :].lstrip()
    if rest.startswith("`"):
        end_tick = rest.find("`", 1)
        if end_tick != -1:
            return rest[1:end_tick].strip()

    depth = 0
    chars: list[str] = []
    for ch in rest:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch in ("；", ";", "\n") and depth == 0:
            break
        chars.append(ch)
    return "".join(chars).strip().strip("`")


def parse_markdown_relations(
    markdown_text: str,
    known_tables: set[str] | None = None,
) -> list[dict[str, Any]]:
    """解析 Markdown 中的关系行，生成可直接写入 schema_relations 的关系边列表。"""
    relations: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()

    for raw_line in (markdown_text or "").splitlines():
        m = _RELATION_LINE_RE.match(raw_line)
        if not m:
            continue

        src_tbl, src_col, dst_tbl, dst_col, rel_type_raw, warn_flag, tail = m.groups()
        src_tbl = (src_tbl or "").strip()
        dst_tbl = (dst_tbl or "").strip()
        if not src_tbl or not dst_tbl:
            continue
        if known_tables and (src_tbl not in known_tables or dst_tbl not in known_tables):
            continue

        src_field = (src_col or "").strip() or "id"
        dst_field = (dst_col or "").strip() or "id"
        tail_str = tail or ""

        join_condition = _extract_on_condition(tail_str)
        if not join_condition and src_col and dst_col:
            join_condition = f"{src_tbl}.{src_col} = {dst_tbl}.{dst_col}"

        note_m = re.search(r"(?:说明与红线|说明|红线)[：:]\s*(.+)$", tail_str)
        if note_m:
            note = note_m.group(1).strip()
        else:
            note = re.sub(r"^[：:\s]+", "", tail_str).strip()

        rel_type = (rel_type_raw or "").strip().upper()
        if rel_type not in _KNOWN_RELATION_TYPES:
            upper_all = f"{join_condition} {tail_str}".upper()
            if "DO_NOT_JOIN" in upper_all or "严禁" in note or "禁止" in note:
                rel_type = "DO_NOT_JOIN"
            elif "JSON_EXTRACT" in upper_all:
                rel_type = "JSON_EXTRACT"
            elif "LEFT JOIN" in upper_all or "-100" in note:
                rel_type = "LEFT_JOIN"
            else:
                rel_type = "LOGICAL_JOIN"

        if warn_flag is not None:
            is_warning = warn_flag.strip().lower() == "true"
        else:
            is_warning = rel_type == "DO_NOT_JOIN" or any(kw in note for kw in _RELATION_WARNING_KEYWORDS)

        dedup_key = (src_tbl, src_field, dst_tbl, dst_field, join_condition)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        cardinality_m = re.search(r"\b(1:1|1:N|N:1|N:M)\b", note)
        cardinality = cardinality_m.group(1) if cardinality_m else "N:1"

        relations.append(
            {
                "source_table": src_tbl,
                "source_field": src_field,
                "target_table": dst_tbl,
                "target_field": dst_field,
                "relation_type": rel_type,
                "cardinality": cardinality,
                "join_condition": join_condition,
                "filter_rule": note if not is_warning else "",
                "is_warning": is_warning,
                "warning_message": note if is_warning else "",
                "description": note,
            }
        )

    return relations


def parse_business_rules_section(markdown_text: str) -> str:
    """提取 Markdown 文档中定义的核心业务口径、防坑红线与 CTE 规范章节。"""
    pattern = r"(^#{1,3}\s*(?:[一二三四五六七八九十0-9]+[、.]\s*)?(?:核心业务口径|业务统计口径|防坑红线|CTE\s*规范)[\s\S]*?)(?=\n#{1,2}\s+[^#]|\Z)"
    match = re.search(pattern, markdown_text or "", re.IGNORECASE | re.MULTILINE)
    if match:
        return match.group(1).strip()
    return ""


def parse_markdown_schema(md_text: str) -> dict[str, Any]:
    """解析 Markdown 数据结构文档，提取实体表、字段、关系边与红线提示。"""
    entities: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    warnings: list[str] = []

    matches = list(_MD_HEADING_RE.finditer(md_text))
    for idx, m in enumerate(matches):
        heading = m.group(1).strip()
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(md_text)
        section_body = md_text[start:end].strip()

        # 尝试从标题中识别表名：如 `fe_athlete (运动员基础表)`
        name_m = _MD_PAREN_NAME_RE.match(heading)
        if name_m:
            table_name = name_m.group("table").strip()
            display_name = name_m.group("display").strip()
        else:
            code_m = _MD_TABLE_CODE_RE.search(heading)
            if not code_m:
                continue
            table_name = code_m.group(1).strip()
            display_name = heading.replace(f"`{table_name}`", "").strip(" -：:") or table_name

        fields = _parse_md_table(section_body.splitlines())
        primary_key = next((f["field_name"] for f in fields if f.get("is_primary_key")), "id")

        if any(k in section_body for k in ("散客", "-100", "翻倍", "严禁", "DO_NOT_JOIN")):
            warnings.append(f"在 {table_name} 章节检测到避坑/红线关键词，请在图谱中确认或补充关联边规则。")

        entities.append(
            {
                "table_name": table_name,
                "display_name": display_name,
                "category": _infer_category(table_name, section_body[:120]),
                "description": section_body[:300],
                "primary_key": primary_key,
                "pos_x": float((len(entities) % 3) * 380 + 40),
                "pos_y": float((len(entities) // 3) * 320 + 40),
                "fields": fields,
            }
        )

    # 提取全文关系行，并与已有关系按五元组去重合并
    md_relations = parse_markdown_relations(md_text)
    seen_keys = {
        (
            r.get("source_table", ""),
            r.get("source_field", ""),
            r.get("target_table", ""),
            r.get("target_field", ""),
            r.get("join_condition", ""),
        )
        for r in relations
    }
    for rel in md_relations:
        key = (
            rel.get("source_table", ""),
            rel.get("source_field", ""),
            rel.get("target_table", ""),
            rel.get("target_field", ""),
            rel.get("join_condition", ""),
        )
        if key not in seen_keys:
            seen_keys.add(key)
            relations.append(rel)

    business_rules = parse_business_rules_section(md_text)

    if not entities and not relations and not business_rules:
        warnings.append("Markdown 中未匹配到 `table_name (中文名)` 格式的表定义或关系行，建议使用标准 SQL DDL 或补充表名标题。")

    return {
        "source_format": "markdown",
        "entities": entities,
        "relations": relations,
        "business_rules_markdown": business_rules,
        "warnings": warnings,
    }


def parse_schema_content(content: str, fmt: str = "auto") -> dict[str, Any]:
    """统一解析入口：自动识别或按指定格式解析 SQL DDL / Markdown。"""
    text = (content or "").strip()
    if not text:
        return {"source_format": fmt, "entities": [], "relations": [], "warnings": ["输入内容为空"]}

    chosen = fmt.strip().lower()
    if chosen == "auto":
        chosen = "sql" if _CREATE_TABLE_RE.search(text) else "markdown"

    if chosen == "sql":
        return parse_sql_ddl(text)
    return parse_markdown_schema(text)


def export_profile_to_markdown(bundle: dict[str, Any]) -> str:
    """将 Schema Profile 完整图谱（业务口径 + 实体表字段 + 关系与红线）导出为与导入器 100% 兼容的 .md 文档。"""
    prof = bundle.get("profile") or {}
    entities = bundle.get("entities") or []
    relations = bundle.get("relations") or []

    prof_name = str(prof.get("name") or prof.get("id") or "数据结构空间").strip()
    desc = str(prof.get("description") or "").strip()

    lines: list[str] = [f"# {prof_name} · 数据结构与业务口径文档", ""]

    # 1. 核心业务口径与防坑红线（确保标题能被 parse_business_rules_section 完整识别）
    lines.append("## 一、核心业务口径与防坑红线")
    if desc:
        # 若原描述中带有一级/二级同名标题，去掉重复外层标题行，避免截断
        cleaned_desc = re.sub(
            r"^\s*#{1,3}\s*(?:[一二三四五六七八九十0-9]+[、.]\s*)?(?:核心业务口径|业务统计口径|防坑红线)[^\n]*\n?",
            "",
            desc,
            flags=re.IGNORECASE,
        ).strip()
        # 将内部子标题转为加粗段落行，防止 parse_business_rules_section 提前截断或被 _MD_HEADING_RE 误识别为表名
        cleaned_desc = re.sub(r"^#{1,4}\s+(.+)$", r"**\1**", cleaned_desc, flags=re.MULTILINE)
        lines.append(cleaned_desc)
    else:
        lines.append("> 暂无全局业务统计口径说明。")
    lines.append("")

    # 建立 entity_id -> table_name 映射
    id_to_table: dict[str, str] = {}
    for ent in entities:
        eid = str(ent.get("id") or "")
        tname = str(ent.get("table_name") or "").strip()
        if eid and tname:
            id_to_table[eid] = tname

    # 2. 实体表与字段定义（格式：### `table_name` (中文显示名) + Markdown 字段表）
    lines.append("## 二、实体表与字段定义")
    lines.append("")
    for ent in entities:
        tname = str(ent.get("table_name") or "").strip()
        if not tname:
            continue
        dname = str(ent.get("display_name") or tname).strip()
        pk = str(ent.get("primary_key") or "id").strip()
        lines.append(f"### `{tname}` ({dname})")
        lines.append("")
        lines.append("| 字段名 | 类型 | 可空 | 说明 |")
        lines.append("| --- | --- | --- | --- |")
        fields = ent.get("fields") or []
        if not fields:
            lines.append(f"| `{pk}` | VARCHAR | NO | 主键 |")
        else:
            for f in fields:
                fname = str(f.get("field_name") or "").strip()
                if not fname:
                    continue
                ftype = str(f.get("field_type") or "VARCHAR").strip().replace("|", "/")
                is_null = "YES" if f.get("is_nullable", True) else "NO"
                fdesc = str(f.get("description") or f.get("display_name") or "").strip()
                enum_v = str(f.get("enum_values") or "").strip()
                if f.get("is_primary_key") or fname == pk:
                    if "主键" not in fdesc:
                        fdesc = f"主键；{fdesc}" if fdesc else "主键"
                if enum_v and enum_v not in fdesc:
                    fdesc = f"{fdesc}（枚举：{enum_v}）" if fdesc else f"枚举：{enum_v}"
                fdesc_clean = re.sub(r"\s+", " ", fdesc).replace("|", "｜")
                lines.append(f"| `{fname}` | {ftype} | {is_null} | {fdesc_clean} |")
        lines.append("")

    # 3. 表间关联关系与高危红线（严格对齐 _RELATION_LINE_RE 正则语法）
    lines.append("## 三、表间关联关系与高危红线")
    lines.append("")
    if not relations:
        lines.append("> 暂无表间关联关系。")
    else:
        for r in relations:
            raw_src = str(r.get("source_entity_id") or r.get("source_table") or "")
            raw_dst = str(r.get("target_entity_id") or r.get("target_table") or "")
            src_tbl = id_to_table.get(raw_src, raw_src.split(".")[-1]).strip()
            dst_tbl = id_to_table.get(raw_dst, raw_dst.split(".")[-1]).strip()
            if not src_tbl or not dst_tbl:
                continue
            src_fld = str(r.get("source_field") or "id").strip() or "id"
            dst_fld = str(r.get("target_field") or "id").strip() or "id"
            rel_type = str(r.get("relation_type") or "LOGICAL_JOIN").strip().upper()
            is_warn = "true" if r.get("is_warning") or rel_type == "DO_NOT_JOIN" else "false"
            join_cond = str(r.get("join_condition") or f"{src_tbl}.{src_fld} = {dst_tbl}.{dst_fld}").strip()
            card = str(r.get("cardinality") or "N:1").strip()
            note = str(r.get("warning_message") or r.get("filter_rule") or r.get("description") or "").strip()
            if card and card not in note:
                note = f"[{card}] {note}".strip()
            tail_parts = [f"ON 条件：`{join_cond}`"]
            if note:
                tail_parts.append(f"说明与红线：{note}")
            lines.append(
                f"- `{src_tbl}.{src_fld}` -> `{dst_tbl}.{dst_fld}` (`{rel_type}`, `is_warning={is_warn}`) {'；'.join(tail_parts)}"
            )
    lines.append("")
    return "\n".join(lines)

