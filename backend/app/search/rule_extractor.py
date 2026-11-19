"""
AgentHub Local v1.5.9 结构化业务规则卡片抽取与在线置顶过滤引擎 (Structured Rule Extractor)
规范参考: SPEC-1.5.9-HARDENING-ADDENDUM
核心功能:
1. 从规程 parent 切片中抽取多维刚性规则（组别、剑种、器材规格、护具牛数、费用政策、申诉仲裁）；
2. 存入独立知识库 index.db 的 kb_structured_rules 表；
3. 支持导出打入 structured_rules.jsonl，导入还原入库；
4. 检索阶段按多维条件与最新 effective_date 置顶注入【权威结构化规则卡】。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
from typing import Any

from ..core.logging_config import logger


# 规则特征触发词
RULE_TRIGGER_KEYWORDS = {
    # 组别与年龄
    "u10", "u12", "u14", "u16", "u17", "u20", "公开组", "老将组", "出生", "年龄", "组别",
    # 剑种与器材
    "重剑", "花剑", "佩剑", "儿童剑", "成人剑", "0号", "5号", "剑条", "剑长", "护具", "防护服", "牛", "350n", "800n", "护胸",
    # 费用与政策
    "报名费", "参赛费", "退费", "退款", "服务费", "保证金",
    # 申诉与规则
    "申诉", "仲裁", "申诉费", "异议", "判罚", "检录",
}


class StructuredRuleExtractor:
    """多维条件结构化业务规则卡片抽取与检索器。"""

    @classmethod
    def should_trigger_rule_search(cls, query: str) -> bool:
        """检查用户提问是否命中多维刚性业务规则特征。"""
        lower_q = query.lower()
        return any(kw in lower_q for kw in RULE_TRIGGER_KEYWORDS)

    @classmethod
    def extract_rules_from_parent_chunk(
        cls,
        chunk: dict[str, Any],
        kb_id: str,
    ) -> list[dict[str, Any]]:
        """从父切片文本中提取结构化刚性规则卡片。"""
        content = chunk.get("content", "")
        if not content:
            return []

        doc_year = chunk.get("doc_year", 0)
        effective_date = chunk.get("effective_date", "")
        source_chunk_id = chunk.get("id", "")
        doc_id = chunk.get("remote_uri", "") or chunk.get("section_path", "")

        extracted_rules: list[dict[str, Any]] = []

        # 1. 尝试解析 Markdown 表格中的规则行
        table_rules = cls._extract_from_markdown_tables(
            content=content,
            kb_id=kb_id,
            doc_id=doc_id,
            doc_year=doc_year,
            effective_date=effective_date,
            source_chunk_id=source_chunk_id,
        )
        extracted_rules.extend(table_rules)

        # 2. 尝试从正文段落中通过启发式正则模式抽取
        text_rules = cls._extract_from_text_patterns(
            content=content,
            kb_id=kb_id,
            doc_id=doc_id,
            doc_year=doc_year,
            effective_date=effective_date,
            source_chunk_id=source_chunk_id,
        )
        extracted_rules.extend(text_rules)

        return extracted_rules

    @classmethod
    def _extract_from_markdown_tables(
        cls,
        content: str,
        kb_id: str,
        doc_id: str,
        doc_year: int,
        effective_date: str,
        source_chunk_id: str,
    ) -> list[dict[str, Any]]:
        """解析 Markdown 表格中包含组别/剑种/器材规格的结构化数据。"""
        rules = []
        lines = [ln.strip() for ln in content.splitlines() if ln.strip()]

        in_table = False
        headers: list[str] = []

        for line in lines:
            if line.startswith("|") and line.endswith("|"):
                cells = [c.strip() for c in line.split("|")[1:-1]]
                if not in_table:
                    # 表头行
                    if any(k in "".join(cells) for k in ["组别", "年龄", "出生", "项目", "剑种", "器材", "护具", "标准"]):
                        headers = cells
                        in_table = True
                        continue
                else:
                    # 分隔线
                    if all(set(c).issubset({"-", ":", " "}) for c in cells):
                        continue

                    # 表格内容行
                    row_dict = dict(zip(headers, cells))
                    row_text = " | ".join(cells)

                    # 判断行内特征
                    tags: list[str] = []
                    category = "equipment_standard"
                    for val in cells:
                        for kw in ["U10", "U12", "U14", "U16", "U17", "U20", "公开组", "老将组", "重剑", "花剑", "佩剑"]:
                            if kw.lower() in val.lower() and kw not in tags:
                                tags.append(kw)

                    if any("费" in c for c in headers) or any("元" in c for c in cells):
                        category = "fee_policy"

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
    ) -> list[dict[str, Any]]:
        """从正文句子中识别刚性费用、年龄对应、护具及申诉规则。"""
        rules = []

        # 费用模式匹配：例如 "报名费为每人每项 450 元"、"退费需在赛前...退还 80%"
        fee_matches = re.finditer(
            r"([^\n。；]*?(?:报名费|参赛费|服务费|退费|退款)[^\n。；]*?(?:\d+元|\d+%)?[^\n。；]*?)(?:[。；\n]|$)",
            content
        )
        for m in fee_matches:
            match_text = m.group(1).strip()
            if len(match_text) >= 8 and any(char.isdigit() for char in match_text):
                rid = hashlib.md5(f"fee_{kb_id}_{match_text[:30]}".encode()).hexdigest()[:16]
                rules.append({
                    "id": f"rule_fee_{rid}",
                    "kb_id": kb_id,
                    "doc_id": doc_id,
                    "doc_year": doc_year,
                    "effective_date": effective_date,
                    "rule_category": "fee_policy",
                    "condition_tags": json.dumps(["费用政策", "报名退费"], ensure_ascii=False),
                    "rule_summary": match_text,
                    "structured_payload": json.dumps({"raw_rule": match_text}, ensure_ascii=False),
                    "source_chunk_id": source_chunk_id,
                })

        # 申诉仲裁模式：例如 "需在比赛结束后15分钟内缴纳1000元申诉费"
        appeal_matches = re.finditer(
            r"([^\n。；]*?(?:申诉|仲裁|异议)[^\n。；]*?(?:\d+分钟|\d+元|书面)[^\n。；]*?)(?:[。；\n]|$)",
            content
        )
        for m in appeal_matches:
            match_text = m.group(1).strip()
            if len(match_text) >= 10:
                rid = hashlib.md5(f"appeal_{kb_id}_{match_text[:30]}".encode()).hexdigest()[:16]
                rules.append({
                    "id": f"rule_appeal_{rid}",
                    "kb_id": kb_id,
                    "doc_id": doc_id,
                    "doc_year": doc_year,
                    "effective_date": effective_date,
                    "rule_category": "appeal_matrix",
                    "condition_tags": json.dumps(["仲裁申诉", "违规处罚"], ensure_ascii=False),
                    "rule_summary": match_text,
                    "structured_payload": json.dumps({"raw_rule": match_text}, ensure_ascii=False),
                    "source_chunk_id": source_chunk_id,
                })

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
            logger.warning(f"[RuleExtractor] 写入规则卡片失败: {e}")
            return 0

    @classmethod
    def query_matching_rules(
        cls,
        db_path: Path,
        kb_id: str,
        query: str,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        """在知识库的 kb_structured_rules 中检索匹配当前提问条件的最新规则。"""
        if not db_path.exists():
            return []

        clean_q = query.lower()
        # 提取用户提问中的匹配标签
        matched_tags: list[str] = []
        for kw in ["u10", "u12", "u14", "u16", "u17", "u20", "公开组", "老将组", "重剑", "花剑", "佩剑", "退款", "退费", "申诉", "报名费"]:
            if kw in clean_q:
                matched_tags.append(kw.upper())

        def _do_query() -> list[dict[str, Any]]:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            try:
                # 优先按条件标签 LIKE 检索，其次模糊匹配 rule_summary，按 effective_date DESC 降序
                where_clauses = []
                params: list[Any] = [kb_id]

                for tag in matched_tags:
                    where_clauses.append("(condition_tags LIKE ? OR rule_summary LIKE ?)")
                    params.extend([f"%{tag}%", f"%{tag}%"])

                if not where_clauses:
                    # 若未捕获显式标签，按全词模糊匹配
                    where_clauses.append("rule_summary LIKE ?")
                    params.append(f"%{query[:12]}%")

                sql = f"""
                SELECT id, kb_id, doc_id, doc_year, effective_date, rule_category,
                       condition_tags, rule_summary, structured_payload, source_chunk_id
                FROM kb_structured_rules
                WHERE kb_id = ? AND ({" OR ".join(where_clauses)})
                ORDER BY effective_date DESC, doc_year DESC, created_at DESC
                LIMIT ?
                """
                params.append(limit)
                rows = conn.execute(sql, params).fetchall()
                return [dict(r) for r in rows]
            except Exception as e:
                logger.debug(f"[RuleExtractor] 查询结构化规则卡异常: {e}")
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
            lines.append(f"• [规则卡 {idx}] [{category}] (生效版本/日期: {eff}): {summary}")

        lines.append("=======================================================\n")
        return "\n".join(lines)
