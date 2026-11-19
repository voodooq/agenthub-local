"""
AgentHub Local v1.6.0 通用业务规则引擎 (Universal Domain-Agnostic Rule Engine)
规范参考: SPEC-1.6.0-UNIVERSAL-RULE-ENGINE
核心功能:
1. 彻底解耦硬编码，基于 RuleDomainProfile 模板适配击剑、马术、橄榄球及企业通用规章;
2. Markdown 表格首列跨行合并单元格自动向下继承（专治马术多级别/多高度跨行表）;
3. 复合条件命中重合度加权排序 (match_score 实体 2 分 + 触发词 1 分) + 最新生效日期双重仲裁;
4. 100% 向后兼容 StructuredRuleExtractor 别名与历史数据库。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from ..core.logging_config import logger
from .rule_profiles import BUILTIN_RULE_PROFILES, RuleProfileManager


class UniversalRuleExtractor:
    """领域无关的多维条件结构化业务规则卡片抽取与检索引擎。"""

    @classmethod
    def resolve_profile(cls, domain_type: str = "general", rule_profile_json: str = "") -> dict[str, Any]:
        """解析知识库对应的领域规则模板。"""
        if rule_profile_json and rule_profile_json.strip():
            try:
                parsed = json.loads(rule_profile_json)
                if isinstance(parsed, dict) and "trigger_keywords" in parsed:
                    return parsed
            except Exception:
                pass
        return BUILTIN_RULE_PROFILES.get(domain_type, BUILTIN_RULE_PROFILES["general"])

    @classmethod
    def should_trigger_rule_search(cls, query: str, profile: dict[str, Any] | None = None) -> bool:
        """检查用户提问是否命中业务规则特征词。若未指定 profile，则检查所有领域特征。"""
        lower_q = query.lower()
        if profile:
            keywords = profile.get("trigger_keywords", [])
            return any(kw.lower() in lower_q for kw in keywords)

        # 兜底：聚合所有内置领域触发词
        for p in BUILTIN_RULE_PROFILES.values():
            if any(kw.lower() in lower_q for kw in p.get("trigger_keywords", [])):
                return True
        return False

    @classmethod
    def get_fallback_merged_profile(cls) -> dict[str, Any]:
        """合并所有内置领域的触发词、锚点、实体与正则，用于未显式指定领域时的向后兼容兜底。"""
        all_keywords: list[str] = []
        all_anchors: list[str] = []
        all_entities: list[str] = []
        all_patterns: list[dict[str, Any]] = []

        for p in BUILTIN_RULE_PROFILES.values():
            for kw in p.get("trigger_keywords", []):
                if kw not in all_keywords:
                    all_keywords.append(kw)
            for anc in p.get("table_header_anchors", []):
                if anc not in all_anchors:
                    all_anchors.append(anc)
            for ent in p.get("condition_tag_entities", []):
                if ent not in all_entities:
                    all_entities.append(ent)
            for pat in p.get("regex_patterns", []):
                all_patterns.append(pat)

        return {
            "domain_id": "merged_fallback",
            "domain_name": "全域融合规则模板",
            "trigger_keywords": all_keywords,
            "table_header_anchors": all_anchors,
            "condition_tag_entities": all_entities,
            "regex_patterns": all_patterns,
        }

    @classmethod
    def extract_rules_from_parent_chunk(
        cls,
        chunk: dict[str, Any],
        kb_id: str,
        profile: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """基于领域模板从父切片文本中提取结构化规则卡片。"""
        content = chunk.get("content", "")
        if not content:
            return []

        if not profile:
            lower_kbid = kb_id.lower()
            if "fencing" in lower_kbid:
                profile = BUILTIN_RULE_PROFILES["fencing"]
            elif "equestrian" in lower_kbid or "horse" in lower_kbid:
                profile = BUILTIN_RULE_PROFILES["equestrian"]
            elif "rugby" in lower_kbid:
                profile = BUILTIN_RULE_PROFILES["rugby"]
            else:
                profile = cls.get_fallback_merged_profile()

        doc_year = chunk.get("doc_year", 0)
        effective_date = chunk.get("effective_date", "")
        source_chunk_id = chunk.get("id", "")
        doc_id = chunk.get("remote_uri", "") or chunk.get("section_path", "")

        extracted: list[dict[str, Any]] = []
        extracted.extend(
            cls._extract_from_markdown_tables(
                content=content,
                kb_id=kb_id,
                doc_id=doc_id,
                doc_year=doc_year,
                effective_date=effective_date,
                source_chunk_id=source_chunk_id,
                profile=profile,
            )
        )
        extracted.extend(
            cls._extract_from_text_patterns(
                content=content,
                kb_id=kb_id,
                doc_id=doc_id,
                doc_year=doc_year,
                effective_date=effective_date,
                source_chunk_id=source_chunk_id,
                profile=profile,
            )
        )
        return extracted

    @classmethod
    def _extract_from_markdown_tables(
        cls,
        content: str,
        kb_id: str,
        doc_id: str,
        doc_year: int,
        effective_date: str,
        source_chunk_id: str,
        profile: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """解析 Markdown 表格规则，支持首列跨行合并单元格自动向下继承。"""
        rules: list[dict[str, Any]] = []
        lines = [ln.strip() for ln in content.splitlines() if ln.strip()]

        header_anchors = profile.get("table_header_anchors", [])
        tag_entities = profile.get("condition_tag_entities", [])

        in_table = False
        headers: list[str] = []
        last_non_empty_first_col = ""

        for line in lines:
            if line.startswith("|") and line.endswith("|"):
                cells = [c.strip() for c in line.split("|")[1:-1]]
                if not cells:
                    continue

                if not in_table:
                    # 识别表头行
                    if any(k in "".join(cells) for k in header_anchors):
                        headers = cells
                        in_table = True
                        last_non_empty_first_col = ""
                    continue

                # 跳过表头分隔线 (|:---|:---|)
                if all(set(c).issubset({"-", ":", " "}) for c in cells):
                    continue

                # 处理跨行合并单元格：首列为空时继承上一行首列（如马术“场地障碍”跨多行不同高度）
                if cells[0]:
                    last_non_empty_first_col = cells[0]
                elif last_non_empty_first_col:
                    cells[0] = last_non_empty_first_col

                row_dict = dict(zip(headers, cells))
                row_text = " | ".join(cells)

                # 提取行内匹配的条件标签
                tags: list[str] = []
                for val in cells:
                    for kw in tag_entities:
                        if kw.lower() in val.lower() and kw not in tags:
                            tags.append(kw)

                category = "fee_policy" if (any("费" in c for c in headers) or any("元" in c for c in cells)) else "domain_standard"

                if tags or category == "fee_policy":
                    rule_id = hashlib.md5(f"{kb_id}_{doc_id}_{row_text}_{source_chunk_id}".encode()).hexdigest()[:16]
                    rules.append({
                        "id": f"rule_tbl_{rule_id}",
                        "kb_id": kb_id,
                        "doc_id": doc_id,
                        "doc_year": doc_year,
                        "effective_date": effective_date,
                        "rule_category": category,
                        "condition_tags": json.dumps(tags, ensure_ascii=False),
                        "rule_summary": f"表格规程标准: {row_text}",
                        "structured_payload": json.dumps(row_dict, ensure_ascii=False),
                        "source_chunk_id": source_chunk_id,
                    })
            else:
                in_table = False
                last_non_empty_first_col = ""

        return rules

    @classmethod
    def _extract_from_text_patterns(
        cls,
        content: str,
        kb_id: str,
        doc_id: str,
        doc_year: int,
        effective_date: str,
        source_chunk_id: str,
        profile: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """按领域模板正则抽取正文刚性规则，并自动关联段落内出现的条件实体标签。"""
        rules: list[dict[str, Any]] = []
        regex_patterns = profile.get("regex_patterns", [])
        tag_entities = profile.get("condition_tag_entities", [])

        # 切分候选分句（以句号、分号、换行分句）
        sentences = [s.strip() for s in re.split(r"[。\n；;]+", content) if len(s.strip()) >= 8]

        for sentence in sentences:
            for spec in regex_patterns:
                pattern_str = spec.get("pattern", "")
                category = spec.get("category", "general_rule")
                base_tags = list(spec.get("tags", []))
                if not pattern_str:
                    continue

                if re.search(pattern_str, sentence):
                    # 叠加句内命中的条件实体标签（如“盛装舞步”、“中二级”、“U12”、“7人制”）
                    tags = list(base_tags)
                    for kw in tag_entities:
                        if kw.lower() in sentence.lower() and kw not in tags:
                            tags.append(kw)

                    rid = hashlib.md5(f"{category}_{kb_id}_{sentence[:40]}".encode()).hexdigest()[:16]
                    rules.append({
                        "id": f"rule_{category}_{rid}",
                        "kb_id": kb_id,
                        "doc_id": doc_id,
                        "doc_year": doc_year,
                        "effective_date": effective_date,
                        "rule_category": category,
                        "condition_tags": json.dumps(tags, ensure_ascii=False),
                        "rule_summary": sentence,
                        "structured_payload": json.dumps({"raw_rule": sentence}, ensure_ascii=False),
                        "source_chunk_id": source_chunk_id,
                    })
                    break  # 该分句已抽入规则卡，避免同句多重规则重复

        return rules

    @classmethod
    def batch_save_rules(cls, db_path: Path, rules: list[dict[str, Any]]) -> int:
        """将提取的规则卡片持久化到指定知识库的 kb_structured_rules 表。"""
        if not rules or not db_path.exists():
            return 0

        def _insert():
            conn = sqlite3.connect(str(db_path))
            try:
                with conn:
                    for r in rules:
                        conn.execute(
                            """
                            INSERT OR REPLACE INTO kb_structured_rules (
                                id, kb_id, doc_id, doc_year, effective_date, rule_category,
                                condition_tags, rule_summary, structured_payload, source_chunk_id
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                r["id"],
                                r["kb_id"],
                                r["doc_id"],
                                r.get("doc_year", 0),
                                r.get("effective_date", ""),
                                r["rule_category"],
                                r.get("condition_tags", "[]"),
                                r["rule_summary"],
                                r.get("structured_payload", "{}"),
                                r.get("source_chunk_id", ""),
                            ),
                        )
                return len(rules)
            finally:
                conn.close()

        try:
            return _insert()
        except Exception as e:
            logger.warning(f"[UniversalRuleExtractor] 写入规则卡片失败: {e}")
            return 0

    @classmethod
    def query_matching_rules(
        cls,
        db_path: Path,
        kb_id: str,
        query: str,
        profile: dict[str, Any] | None = None,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        """按【多条件标签命中重合度 match_score 降序 + 最新生效日期 effective_date 降序】检索规则。"""
        if not db_path.exists():
            return []

        if not profile:
            lower_kbid = kb_id.lower()
            if "fencing" in lower_kbid:
                profile = BUILTIN_RULE_PROFILES["fencing"]
            elif "equestrian" in lower_kbid or "horse" in lower_kbid:
                profile = BUILTIN_RULE_PROFILES["equestrian"]
            elif "rugby" in lower_kbid:
                profile = BUILTIN_RULE_PROFILES["rugby"]
            else:
                profile = cls.get_fallback_merged_profile()

        clean_q = query.lower()
        tag_entities = profile.get("condition_tag_entities", [])
        trigger_kws = profile.get("trigger_keywords", [])

        # 1. 提取查询中的条件实体标签（高权重：2分）与业务触发词（基础权重：1分）
        scored_terms: list[tuple[str, int]] = []
        for ent in tag_entities:
            if ent.lower() in clean_q:
                scored_terms.append((ent, 2))
        for kw in trigger_kws:
            if kw.lower() in clean_q and not any(kw.lower() == t[0].lower() for t in scored_terms):
                scored_terms.append((kw, 1))

        def _do_query() -> list[dict[str, Any]]:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            try:
                if not scored_terms:
                    sql = """
                    SELECT id, kb_id, doc_id, doc_year, effective_date, rule_category,
                           condition_tags, rule_summary, structured_payload, source_chunk_id, created_at,
                           1 AS match_score
                    FROM kb_structured_rules
                    WHERE kb_id = ? AND rule_summary LIKE ?
                    ORDER BY effective_date DESC, doc_year DESC, created_at DESC
                    LIMIT ?
                    """
                    rows = conn.execute(sql, [kb_id, f"%{query[:12]}%", limit]).fetchall()
                    return [dict(r) for r in rows]

                score_exprs: list[str] = []
                score_params: list[Any] = []
                for term, weight in scored_terms:
                    score_exprs.append(
                        f"(CASE WHEN condition_tags LIKE ? OR rule_summary LIKE ? THEN {weight} ELSE 0 END)"
                    )
                    score_params.extend([f"%{term}%", f"%{term}%"])

                score_sql = " + ".join(score_exprs)
                # 使用子查询避免重复绑定参数，并严格按 match_score 降序与 effective_date 降序复合排序
                sql = f"""
                SELECT * FROM (
                    SELECT id, kb_id, doc_id, doc_year, effective_date, rule_category,
                           condition_tags, rule_summary, structured_payload, source_chunk_id, created_at,
                           ({score_sql}) AS match_score
                    FROM kb_structured_rules
                    WHERE kb_id = ?
                ) sub
                WHERE sub.match_score > 0
                ORDER BY sub.match_score DESC, sub.effective_date DESC, sub.doc_year DESC, sub.created_at DESC
                LIMIT ?
                """
                all_params = [*score_params, kb_id, limit]
                rows = conn.execute(sql, all_params).fetchall()
                return [dict(r) for r in rows]
            except Exception as e:
                logger.debug(f"[UniversalRuleExtractor] 检索规则失败: {e}")
                return []
            finally:
                conn.close()

        return _do_query()

    @classmethod
    def format_rules_for_prompt(cls, rules: list[dict[str, Any]]) -> str:
        """将命中的结构化规则格式化为大模型置顶优先遵循的系统卡片。"""
        if not rules:
            return ""

        lines = [
            "\n=======================================================",
            "【权威结构化规则卡】（系统最新生效判定，优先级最高，不可违背）：",
        ]
        for idx, r in enumerate(rules, start=1):
            eff = r.get("effective_date") or f"{r.get('doc_year', '')}年"
            category = r.get("rule_category", "rule")
            summary = r.get("rule_summary", "")
            score_badge = f" [匹配度:{r.get('match_score')}]" if "match_score" in r else ""
            lines.append(f"• [规则卡 {idx}] [{category}]{score_badge} (生效版本/日期: {eff}): {summary}")

        lines.append("=======================================================\n")
        return "\n".join(lines)


# 向后兼容别名：保留既有引用不报错
StructuredRuleExtractor = UniversalRuleExtractor
