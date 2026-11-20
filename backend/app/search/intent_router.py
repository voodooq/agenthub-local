"""
查询意图路由器 (Query Intent Router)
纯正则零延迟 (< 2ms) 前置在检索入口，实现三路精准分流：
1. cross_version_compare: 跨期/跨版本对比 -> 召回同族最新与次新两版 summary_l2 并关闭最新霸权
2. macro_process_summary: 全流程/宏观总结 -> 召回 summary_l1 (Top-4) + summary_l2 (Top-1) + Top-2 parent
3. detail_rule_lookup: 默认细节/刚性规则 -> 三路召回 + wiki_page 加权 + UniversalRuleExtractor 规则卡
"""
from __future__ import annotations

import re
from typing import Literal

QueryIntent = Literal[
    "cross_version_compare",
    "macro_process_summary",
    "detail_rule_lookup",
]

_COMPARE_KEYWORD_RE = re.compile(
    r"(变化|区别|对比|差异|相比|改了什么|改动|修订|新增|调整|不同)"
)
_VERSION_OR_YEAR_RE = re.compile(
    r"(20[1-3]\d|v\d+\.\d+|新版|旧版|去年|今年|前年|历年|上版|上一版|往年)"
)
_MACRO_SUMMARY_RE = re.compile(
    r"(全流程|梳理|总结|整体|有哪些规定|一页纸|概览|总览|全局|总体要求)"
)


class QueryIntentRouter:
    """轻量零延迟查询意图路由器。"""

    @classmethod
    def classify(cls, query: str) -> QueryIntent:
        """根据查询文本分类检索意图，耗时 < 2ms。"""
        q = (query or "").strip()
        if not q:
            return "detail_rule_lookup"

        if _COMPARE_KEYWORD_RE.search(q) and _VERSION_OR_YEAR_RE.search(q):
            return "cross_version_compare"

        if _MACRO_SUMMARY_RE.search(q):
            return "macro_process_summary"

        return "detail_rule_lookup"

