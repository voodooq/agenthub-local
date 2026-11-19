"""
AgentHub Local v1.5.9 多轮上下文短问句重写器与主动追问澄清判定器
核心特性:
1. 继承上下文核心实体与年份，消除短口语代词指代歧义（如“怎么报名”、“多少钱” -> 补全“2025年全国击剑俱乐部联赛 报名办法”）；
2. 宽泛宏观提问检测（ClarificationDetector）：检测极度宽泛问法并生成引导建议；
3. 跨实体歧义检测：当存在同名赛事或多年份规程时，输出 suggested_followups 供前端渲染追问胶囊。
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from ..core.logging_config import logger


@dataclass
class ClarificationResult:
    needs_clarification: bool
    clarification_type: str = "none"  # "broad_overview" | "ambiguity" | "none"
    lead_hint: str = ""
    suggested_followups: list[str] = field(default_factory=list)


class MultiTurnQueryRewriter:
    """多轮对话短问句与槽位继承重写器。"""

    PRONOUNS_OR_SHORT = (
        "这个", "那个", "它", "它们", "还有吗", "怎么", "如何", "多少", "几号",
        "在哪", "谁", "费用", "时间", "要求", "规定", "有啥", "怎么报名", "怎么算",
    )

    ENTITY_EXTRACT_PATTERNS = [
        re.compile(r"(\d{4}年?[^\s，,。？?]+(?:联赛|锦标赛|公开赛|杯赛|大奖赛|规程|方案|办法|通知))"),
        re.compile(r"([^\s，,。？?]+(?:联赛|锦标赛|总决赛|发票|账单|规程|方案))"),
    ]

    @classmethod
    def rewrite_query(
        cls,
        current_query: str,
        conversation_history: list[dict[str, Any]] | None = None,
    ) -> str:
        """根据前置多轮对话上下文，补全短口语或代词提问中的实体槽位。

        Args:
            current_query: 当前用户最新输入。
            conversation_history: 历史对话列表（最近若干轮，含 role 和 content）。

        Returns:
            str: 重写扩充后的检索关键词。
        """
        raw_q = current_query.strip()
        if not raw_q:
            return ""

        # 如果已经是长句子且自包含实体（>20字），通常无需强制继承
        if len(raw_q) > 25 and not any(p in raw_q for p in ("这个", "那个", "它们", "上述")):
            return raw_q

        if not conversation_history:
            return raw_q

        # 逆序向前检索最近 3 轮的用户或助手消息，提取最核心的业务实体
        recent_entity: str = ""
        for msg in reversed(conversation_history[-6:]):
            content = str(msg.get("content") or "")
            for pattern in cls.ENTITY_EXTRACT_PATTERNS:
                m = pattern.search(content)
                if m:
                    candidate = m.group(1).strip()
                    if len(candidate) >= 4 and candidate not in raw_q:
                        recent_entity = candidate
                        break
            if recent_entity:
                break

        if not recent_entity:
            return raw_q

        # 若当前输入属于极短问句或含有明显代词，则将实体前置拼接
        is_short_or_implicit = len(raw_q) <= 12 or any(p in raw_q for p in cls.PRONOUNS_OR_SHORT)
        if is_short_or_implicit:
            rewritten = f"{recent_entity} {raw_q}"
            logger.info(f"[QueryRewriter] 多轮短问句槽位补全: '{raw_q}' -> '{rewritten}'")
            return rewritten

        return raw_q


class ClarificationDetector:
    """宽泛提问识别与主动追问引导器。"""

    # 极宽泛、宏观概述提问模式
    BROAD_OVERVIEW_PATTERNS = [
        re.compile(r"^(?:介绍一下|讲讲|说说|有什么规定|怎么比|总则是什么|规程介绍|概述|全部内容)(?:.*?)$"),
        re.compile(r"^(?:[^\s，,。？?]+(?:规程|办法|文件|总则))(?:介绍|概述|总结|大纲|框架)?$"),
    ]

    @classmethod
    def evaluate_clarification(
        cls,
        query: str,
        retrieved_chunks: list[dict[str, Any]] | None = None,
    ) -> ClarificationResult:
        """评估提问是否过于宽泛或存在跨赛事/跨年份歧义。

        Args:
            query: 用户提问。
            retrieved_chunks: 检索命中的切片列表。

        Returns:
            ClarificationResult: 澄清判断结论与快捷追问按钮列表。
        """
        q = query.strip()
        # 1. 宽泛宏观提问检测
        is_broad = False
        for p in cls.BROAD_OVERVIEW_PATTERNS:
            if p.match(q):
                is_broad = True
                break

        if is_broad:
            suggested = [
                "比赛设项与年龄分组规定",
                "报名办法与资格审查要求",
                "奖励办法与积分排名计算",
            ]
            return ClarificationResult(
                needs_clarification=True,
                clarification_type="broad_overview",
                lead_hint="该规程篇幅较长、涵盖板块较多，您可以点击下方快捷方向深入了解：",
                suggested_followups=suggested,
            )

        # 2. 跨实体/多年份歧义检测：若召回的切片中出现了 2 个以上不同年份或不同赛事标题
        if retrieved_chunks and len(retrieved_chunks) >= 2:
            years = set()
            titles = set()
            for c in retrieved_chunks:
                y = c.get("doc_year")
                if y and int(y) > 0:
                    years.add(str(y))
                t = str(c.get("title") or "")
                if t and len(t) <= 30:
                    titles.add(t)

            if len(years) >= 2:
                sorted_years = sorted(list(years), reverse=True)
                followups = [f"核对 {yr} 年规程条款" for yr in sorted_years[:3]]
                return ClarificationResult(
                    needs_clarification=True,
                    clarification_type="ambiguity",
                    lead_hint=f"检索发现当前涉及 {', '.join(sorted_years)} 多个历史年度规程，建议按年度细化：",
                    suggested_followups=followups,
                )

        return ClarificationResult(needs_clarification=False)

