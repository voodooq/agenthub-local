"""
动态置信度多维评分器与人机协同门控 (Confidence Evaluator & HITL Gate)
核心功能：
1. 综合四维指标评分（0.00 ~ 1.00）：检索契合度、权威一致性、Self-Critique 结论与工具执行完整性；
2. 提炼结构化归因标签 (reasons)；
3. 当 strict_mode 且 score < 0.60 时，判定触发 low_confidence_confirmation 拦截挂起。
"""
from dataclasses import dataclass, field
from typing import Any

from .self_critique import CritiqueResult


@dataclass
class ConfidenceReport:
    score: float
    tier_level: str  # "high" | "medium" | "low"
    reasons: list[str] = field(default_factory=list)
    requires_confirmation: bool = False
    evidence_sources: list[str] = field(default_factory=list)


class ConfidenceEvaluator:
    """动态置信度评估器。"""

    @classmethod
    def evaluate(
        cls,
        retrieved_chunks: list[dict[str, Any]],
        critique_res: CritiqueResult,
        tools_executed: list[dict[str, Any]],
        strict_mode: bool = False,
        has_version_conflict: bool = False,
    ) -> ConfidenceReport:
        """多维动态置信度计算。

        Args:
            retrieved_chunks: 检索返回的切片集合。
            critique_res: 自审核验结果。
            tools_executed: 本轮执行的工具调用记录。
            strict_mode: 是否处于严谨工作模式。
            has_version_conflict: 是否检测到版本冲突。

        Returns:
            ConfidenceReport: 完整置信度报告。
        """
        reasons: list[str] = []
        sources: list[str] = []

        # 1. 检索契合度得分 (0.0 ~ 0.35)
        if retrieved_chunks:
            score_retrieval = 0.30
            top_rrf = float(retrieved_chunks[0].get("rrf_score", 0.0))
            if top_rrf > 0.015:
                score_retrieval = 0.35
            reasons.append(f"检索命中 {len(retrieved_chunks)} 处高相关知识切片")
            for c in retrieved_chunks[:4]:
                uri = c.get("remote_uri", "")
                if uri and uri not in sources:
                    sources.append(uri)
        else:
            score_retrieval = 0.10
            reasons.append("未检索到直接依据，基于通用推理")

        # 2. 权威版本一致性得分 (0.0 ~ 0.25)
        if retrieved_chunks:
            score_authority = 0.20
            tiers = [c.get("authority_tier", 2) for c in retrieved_chunks]
            if 1 in tiers:
                score_authority = 0.25
                reasons.append("已采信正式发布库(Tier 1)权威依据")
            elif all(t == 3 for t in tiers):
                score_authority = 0.10
                reasons.append("⚠️ 依据主要来源于草稿/历史归档(Tier 3)")
        else:
            score_authority = 0.0

        if has_version_conflict:
            score_authority -= 0.10
            reasons.append("⚠️ 涉及新旧规程条款冲突")

        score_authority = max(0.0, score_authority)

        # 3. Self-Critique 自审得分 (0.0 ~ 0.25)
        score_critique = max(0.0, 0.25 - critique_res.score_deduction)
        if critique_res.passed:
            reasons.append("输出前事实自审 100% 通过")
        else:
            for iss in critique_res.issues[:2]:
                reasons.append(f"自审疑点: {iss}")

        # 4. 工具执行完整性 (0.0 ~ 0.15)
        score_tools = 0.15
        for t in tools_executed:
            if t.get("status") in ("failed", "error"):
                score_tools -= 0.08
                reasons.append(f"工具 {t.get('name')} 执行异常")
        score_tools = max(0.0, score_tools)

        # 综合求和并四舍五入
        total_score = round(score_retrieval + score_authority + score_critique + score_tools, 2)
        total_score = max(0.0, min(1.0, total_score))

        if total_score >= 0.85:
            tier_level = "high"
        elif total_score >= 0.60:
            tier_level = "medium"
        else:
            tier_level = "low"

        # 严谨模式下置信度 < 0.60 触发人工介入
        requires_confirm = strict_mode and (total_score < 0.60)

        return ConfidenceReport(
            score=total_score,
            tier_level=tier_level,
            reasons=reasons,
            requires_confirmation=requires_confirm,
            evidence_sources=sources,
        )
