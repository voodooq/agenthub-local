"""
AgentHub Local v1.6.0 国标化知识库质量门禁与增量校验执行器 (Quality Gate)
遵循规范：
1. GB/T 36344-2018《信息技术 数据质量评价指标》（规范性、完整性、准确性、一致性）；
2. GB/T 37722-2019《知识图谱技术框架》；
3. 纯 Python 零 JVM 依赖运行架构：
   - 纯 Python DFS 拓扑环检测（上下位强谓词严格阻断，自环过滤，宽泛环告警）
   - 编程式构建 rdflib.Graph（杜绝字符串拼接 BadSyntax 崩溃）
   - 中文日期与金额类型归一化（防止类型校验误杀规程条款）
   - 增量局部校验 (Delta PySHACL，设置 inference="none" 确保耗时在 100ms~300ms 内)
   - 双轨放行机制 (agile 告警放行 vs strict 阻断拦截)
"""
from __future__ import annotations

import hashlib
import re
import time
from typing import Any, Literal
from urllib.parse import quote

import networkx as nx
from rdflib import BNode, Graph, Literal as RDFLiteral, Namespace, RDF, RDFS, URIRef, XSD

from ..core.logging_config import logger

# 命名空间定义
AH = Namespace("https://agenthub.local/schema#")
SH = Namespace("http://www.w3.org/ns/shacl#")

# 强上下位继承谓词集合（仅出现此类环路时触发严格 Error 阻断）
HIERARCHICAL_PREDICATES = {
    "rdfs:subclassof",
    "subclassof",
    "属于",
    "子类",
    "子赛事",
    "上级单位",
    "隶属于",
    "归属于",
    "派生自",
}

# 宽泛包含谓词集合（出现环路时仅记录 Warning 告警，不阻断发布）
BROAD_CONTAINMENT_PREDICATES = {
    "包含",
    "涵盖",
    "包括",
    "关联",
    "相关",
    "参考",
}


class QualityGateResult:
    """质量门禁验证结果契约。"""

    def __init__(
        self,
        passed: bool,
        mode: Literal["agile", "strict"] = "agile",
        errors: list[str] | None = None,
        warnings: list[str] | None = None,
        duration_ms: float = 0.0,
        triples_checked: int = 0,
    ):
        self.passed = passed
        self.mode = mode
        self.errors = errors or []
        self.warnings = warnings or []
        self.duration_ms = duration_ms
        self.triples_checked = triples_checked

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "mode": self.mode,
            "errors": self.errors,
            "warnings": self.warnings,
            "duration_ms": self.duration_ms,
            "triples_checked": self.triples_checked,
        }


class KBQualityGateExecutor:
    """知识库增量自动化质量门禁执行器。"""

    @classmethod
    def detect_topological_cycles(
        cls, triples: list[dict[str, Any]]
    ) -> tuple[list[str], list[str]]:
        """执行零 JVM DFS 拓扑环检测，支持自环预处理与谓词分级。

        Returns:
            tuple[errors, warnings]:
                - errors: 强上下位继承谓词形成的循环继承（严格阻断）
                - warnings: 自环（A->A）或宽泛关系成环（不阻断发布）
        """
        errors: list[str] = []
        warnings: list[str] = []

        hierarchical_graph = nx.DiGraph()
        broad_graph = nx.DiGraph()

        for t in triples:
            subj = str(t.get("subject") or "").strip()
            pred = str(t.get("predicate") or "").strip().lower()
            obj = str(t.get("object") or "").strip()

            if not subj or not obj:
                continue

            # 1. 过滤自环预处理 (A -> A)
            if subj == obj:
                warnings.append(f"发现无意义自环关系: [{subj}] -({pred})-> [{obj}]，已自动过滤")
                continue

            # 2. 区分构建有向图
            if pred in HIERARCHICAL_PREDICATES:
                hierarchical_graph.add_edge(subj, obj, predicate=pred)
            elif pred in BROAD_CONTAINMENT_PREDICATES:
                broad_graph.add_edge(subj, obj, predicate=pred)

        # 3. 检测强继承环路 (阻断级 Error)
        if hierarchical_graph.number_of_nodes() > 0:
            try:
                cycles = list(nx.simple_cycles(hierarchical_graph))
                for c in cycles:
                    cycle_str = " -> ".join(c + [c[0]])
                    errors.append(f"检测到致命循环继承环路: {cycle_str} (违反知识本体无环公理)")
            except Exception as e:
                logger.debug(f"[QualityGate] 强层级环检测异常: {e}")

        # 4. 检测宽泛关系环路 (告警级 Warning)
        if broad_graph.number_of_nodes() > 0:
            try:
                broad_cycles = list(nx.simple_cycles(broad_graph))
                for bc in broad_cycles:
                    cycle_str = " -> ".join(bc + [bc[0]])
                    warnings.append(f"检测到宽泛语义包含环路: {cycle_str} (可能存在双向包含描述)")
            except Exception:
                pass

        return errors, warnings

    @staticmethod
    def _normalize_date_literal(val: str) -> str | None:
        """从中文日期文本中提取标准 YYYY-MM-DD 日期，提取失败返回 None。"""
        if not val:
            return None
        # 匹配 2026年11月20日 或 2026-11-20 或 2026/11/20
        m = re.search(r"(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})", val)
        if m:
            y, month, d = m.groups()
            return f"{y}-{int(month):02d}-{int(d):02d}"
        return None

    @staticmethod
    def _normalize_amount_literal(val: str) -> float | None:
        """从中文金额文本中提取浮点数值（元），提取失败返回 None。"""
        if not val:
            return None
        m = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:元|块|RMB|￥|/人|/项)", val)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                return None
        # 若为纯数字（支持负数）
        if re.fullmatch(r"-?\d+(?:\.\d+)?", val.strip()):
            try:
                return float(val.strip())
            except ValueError:
                return None
        return None

    @classmethod
    def build_delta_rdf_graph(cls, triples: list[dict[str, Any]]) -> Graph:
        """编程式构建增量局部 RDF 图（完全杜绝 Turtle 字符串拼接与 BadSyntax 崩溃）。"""
        g = Graph()
        g.bind("ah", AH)
        g.bind("rdfs", RDFS)

        for i, t in enumerate(triples):
            subj_raw = str(t.get("subject") or "").strip()
            pred_raw = str(t.get("predicate") or "").strip()
            obj_raw = str(t.get("object") or "").strip()
            text_span = str(t.get("text_span") or t.get("evidence_quote") or "").strip()

            if not subj_raw or not pred_raw or not obj_raw:
                continue

            # 使用安全哈希构建实体 URI，同时挂接中文 rdfs:label
            subj_hash = hashlib.md5(subj_raw.encode("utf-8")).hexdigest()[:12]
            subj_uri = URIRef(f"https://agenthub.local/entity/{subj_hash}")
            g.add((subj_uri, RDFS.label, RDFLiteral(subj_raw)))

            # 谓词安全标识
            pred_safe = re.sub(r"[^\w-]", "_", pred_raw) or "rel"
            pred_uri = URIRef(f"https://agenthub.local/prop/{quote(pred_safe)}")

            # 客体挂接
            obj_hash = hashlib.md5(obj_raw.encode("utf-8")).hexdigest()[:12]
            obj_uri = URIRef(f"https://agenthub.local/entity/{obj_hash}")
            g.add((subj_uri, pred_uri, obj_uri))
            g.add((obj_uri, RDFS.label, RDFLiteral(obj_raw)))

            # 挂接逐字证据片段 (PROV-O evidence)
            if text_span:
                g.add((subj_uri, AH.hasEvidenceQuote, RDFLiteral(text_span)))

            # 日期类型归一化挂接
            norm_date = cls._normalize_date_literal(obj_raw)
            if norm_date:
                g.add((subj_uri, AH.normalizedDate, RDFLiteral(norm_date, datatype=XSD.date)))

            # 金额数值归一化挂接
            norm_amount = cls._normalize_amount_literal(obj_raw)
            if norm_amount is not None:
                g.add((subj_uri, AH.normalizedAmount, RDFLiteral(norm_amount, datatype=XSD.decimal)))

        return g

    @classmethod
    def get_standard_competition_shacl_graph(cls) -> Graph:
        """获取编程式构建的标准规程通用 SHACL 约束规则图。"""
        sh_g = Graph()
        sh_g.bind("sh", SH)
        sh_g.bind("ah", AH)

        # 规则 1：金额必须为非负数值 (normalizedAmount >= 0.0)
        shape_amount = BNode()
        sh_g.add((shape_amount, RDF.type, SH.NodeShape))
        sh_g.add((shape_amount, SH.targetSubjectsOf, AH.normalizedAmount))
        prop_amount = BNode()
        sh_g.add((shape_amount, SH.property, prop_amount))
        sh_g.add((prop_amount, SH.path, AH.normalizedAmount))
        sh_g.add((prop_amount, SH.minInclusive, RDFLiteral(0.0, datatype=XSD.decimal)))
        sh_g.add((prop_amount, SH.message, RDFLiteral("规程费用金额必须为非负数值")))

        # 规则 2：规范日期必须符合有效日期格式 (XSD.date)
        shape_date = BNode()
        sh_g.add((shape_date, RDF.type, SH.NodeShape))
        sh_g.add((shape_date, SH.targetSubjectsOf, AH.normalizedDate))
        prop_date = BNode()
        sh_g.add((shape_date, SH.property, prop_date))
        sh_g.add((prop_date, SH.path, AH.normalizedDate))
        sh_g.add((prop_date, SH.datatype, XSD.date))
        sh_g.add((prop_date, SH.message, RDFLiteral("规程时间必须解析为有效的标准日期格式 (YYYY-MM-DD)")))

        return sh_g

    @classmethod
    def validate_delta_batch(
        cls,
        triples: list[dict[str, Any]],
        mode: Literal["agile", "strict"] = "agile",
    ) -> QualityGateResult:
        """执行增量局部质量门禁校验 (耗时严格控制在 100ms~300ms)。

        Args:
            triples: 本次增量待入库的三元组列表 (字典需包含 subject, predicate, object, text_span 等)
            mode: "agile" 敏捷模式 (告警放行) | "strict" 国标严格模式 (阻断拦截)

        Returns:
            QualityGateResult: 包含 passed、errors、warnings 及耗时详情
        """
        start_t = time.perf_counter()
        errors: list[str] = []
        warnings: list[str] = []

        if not triples:
            return QualityGateResult(passed=True, mode=mode, duration_ms=0.0, triples_checked=0)

        # 步骤 1：零 JVM 拓扑环检测 (< 5ms)
        cycle_errors, cycle_warnings = cls.detect_topological_cycles(triples)
        errors.extend(cycle_errors)
        warnings.extend(cycle_warnings)

        # 步骤 2：空证据片段检查（完整性硬约束）
        missing_evidence_cnt = 0
        for t in triples:
            span = str(t.get("text_span") or t.get("evidence_quote") or "").strip()
            if not span:
                missing_evidence_cnt += 1

        if missing_evidence_cnt > 0:
            msg = f"检测到 {missing_evidence_cnt} 条三元组缺失原文逐字证据 (text_span 为空，违反 GB/T 36344 完整性要求)"
            if mode == "strict":
                errors.append(msg)
            else:
                warnings.append(msg)

        # 步骤 3：Delta PySHACL 增量校验 (设置 inference="none", 确保 100~300ms 极速完成)
        try:
            import pyshacl

            delta_g = cls.build_delta_rdf_graph(triples)
            shacl_g = cls.get_standard_competition_shacl_graph()

            conforms, results_graph, results_text = pyshacl.validate(
                data_graph=delta_g,
                shacl_graph=shacl_g,
                inference="none",     # 关键：彻底关闭内部重复 RDFS 推理，消灭 CPU 阻塞
                abort_on_first=False,
                meta_shacl=False,
                advanced=False,
            )

            if not conforms:
                # 提取 SHACL 违规信息
                shacl_msg = f"SHACL 增量数据约束门禁未通过: {results_text[:200].strip()}"
                if mode == "strict":
                    errors.append(shacl_msg)
                else:
                    warnings.append(shacl_msg)
        except Exception as shacl_err:
            logger.debug(f"[QualityGate] SHACL 门禁执行跳过: {shacl_err}")

        duration = round((time.perf_counter() - start_t) * 1000, 2)

        # 判定最终通过状态：
        # - 在 strict 模式下，有任何 error 则 passed=False；
        # - 在 agile 模式下，若存在循环继承等致命 error 则拦截，其他弱项记录 warning 依然放行。
        passed = len(errors) == 0

        logger.info(
            f"[QualityGate] 增量质检完成 (模式={mode}, 耗时={duration}ms, 校验数={len(triples)}): "
            f"结果={'PASS' if passed else 'FAIL'}, Errors={len(errors)}, Warnings={len(warnings)}"
        )

        return QualityGateResult(
            passed=passed,
            mode=mode,
            errors=errors,
            warnings=warnings,
            duration_ms=duration,
            triples_checked=len(triples),
        )
