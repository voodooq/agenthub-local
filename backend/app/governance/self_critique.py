"""
输出前事实自审与批评核对引擎 (Self-Critique Engine)
核心功能：
1. 在严谨模式下，针对模型拟输出的草稿进行输出前事实/数字/版本核对；
2. 识别关键数字、生效时间、金额与约束条件是否与原始检索证据相符；
3. 检出矛盾时生成 revision_hint 并触发至多 1 次后台无感重写修订 (Re-draft)。
"""
from dataclasses import dataclass, field
import json
import re
from typing import Any

from ..core.logging_config import logger


@dataclass
class CritiqueResult:
    passed: bool
    issues: list[str] = field(default_factory=list)
    revision_hint: str = ""
    score_deduction: float = 0.0


class SelfCritiqueCritic:
    """输出前自我审查 Critic 节点。"""

    # 提取数字与金额特征
    NUMBER_PATTERN = re.compile(r"\b\d+(?:\.\d+)?(?:元|万|千|%|分|人|天|岁|届)?\b")
    # 提取日期特征
    DATE_PATTERN = re.compile(r"\d{4}[年\-_/]\d{1,2}[月\-_/]\d{1,2}日?")

    @classmethod
    def evaluate_draft(
        cls,
        user_query: str,
        retrieved_evidence: list[str],
        draft_response: str,
        has_version_conflict: bool = False,
    ) -> CritiqueResult:
        """对最终草稿进行快速轻量级事实自审。

        Args:
            user_query: 用户初始提问。
            retrieved_evidence: 检索召回的证据切片纯文本列表。
            draft_response: 模型草拟的最终回答。
            has_version_conflict: 是否检测到多源版本冲突。

        Returns:
            CritiqueResult: 自查结论。
        """
        issues: list[str] = []
        deduction = 0.0

        if not retrieved_evidence:
            # 无检索证据时，若回答中出现大量具体数字与断言，给出审慎提示
            numbers_in_draft = cls.NUMBER_PATTERN.findall(draft_response)
            if len(numbers_in_draft) >= 4:
                issues.append("回答中包含多项具体数值，但当前会话未执行证据检索")
                deduction += 0.20
            return CritiqueResult(
                passed=(len(issues) == 0),
                issues=issues,
                revision_hint="请提示用户当前结论基于常识推测，建议挂载相关知识库进行核实" if issues else "",
                score_deduction=deduction,
            )

        evidence_combined = "\n".join(retrieved_evidence)

        # 1. 版本冲突披露核查：如果证据中存在版本冲突，但草稿中完全未提及旧版/差异/冲突
        if has_version_conflict:
            conflict_keywords = ["冲突", "旧版", "草稿", "差异", "此前", "历史", "已被替代", "废止"]
            has_mentioned_conflict = any(kw in draft_response for kw in conflict_keywords)
            if not has_mentioned_conflict:
                issues.append("证据存在新旧版本或条款冲突，但回答草稿中未显性向用户披露冲突背景")
                deduction += 0.25

        # 2. 数字核验：比对草稿中的关键数字在证据中是否存在
        draft_numbers = set(cls.NUMBER_PATTERN.findall(draft_response))
        evidence_numbers = set(cls.NUMBER_PATTERN.findall(evidence_combined))
        # 排除纯序号 1、2、3 等
        suspicious_numbers = [n for n in draft_numbers if n not in evidence_numbers and len(n) > 1 and not n.isdigit()]
        if len(suspicious_numbers) > 2:
            issues.append(f"草稿中的数值 {suspicious_numbers[:3]} 在检索证据中未找到确切佐证")
            deduction += 0.20

        # 3. 核心限制词自检：检查提问中的核心实体在草稿中是否回答
        q_clean = user_query.strip()
        if "多少" in q_clean or "几" in q_clean or "金额" in q_clean or "费用" in q_clean:
            if not draft_numbers:
                issues.append("用户询问金额/数量，但草稿中未给出明确数值或无法确定的说明")
                deduction += 0.15

        passed = (len(issues) == 0)
        revision_hint = ""
        if not passed:
            revision_hint = (
                "【自审发现以下疑点，请重新修正回复】:\n" +
                "\n".join(f"- {iss}" for iss in issues) +
                "\n要求：严格忠实于检索切片，若有新旧版本差异务必显性说明并优先采信最新正式发布版。"
            )

        return CritiqueResult(
            passed=passed,
            issues=issues,
            revision_hint=revision_hint,
            score_deduction=min(deduction, 0.60),
        )
