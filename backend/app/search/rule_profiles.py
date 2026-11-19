"""
AgentHub Local v1.6.0 领域规则模板库与冷启动生成器 (Domain Rule Profiles)
规范参考: SPEC-1.6.0-UNIVERSAL-RULE-ENGINE
核心功能:
1. 提供 fencing(击剑), equestrian(马术), rugby(橄榄球), general(通用规章) 四大内置预设模板;
2. 支持未知行业/自定义领域首次上传前 3 个 parent 切片时，由后台轻量大模型冷启动生成 JSON 规则模板;
3. 纯内存与配置驱动，零额外大模型推理负担。
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from ..core.logging_config import logger


BUILTIN_RULE_PROFILES: dict[str, dict[str, Any]] = {
    "fencing": {
        "domain_id": "fencing",
        "domain_name": "击剑赛事与规程",
        "trigger_keywords": [
            "u10", "u12", "u14", "u16", "u17", "u20", "公开组", "老将组", "出生", "年龄",
            "重剑", "花剑", "佩剑", "儿童剑", "成人剑", "0号", "5号", "剑条", "护具", "350n", "800n", "900n",
            "报名费", "参赛费", "退费", "退款", "申诉", "仲裁", "检录"
        ],
        "table_header_anchors": ["组别", "年龄", "出生", "项目", "剑种", "器材", "护具", "标准", "费用"],
        "condition_tag_entities": [
            "U10", "U12", "U14", "U16", "U17", "U20", "公开组", "老将组",
            "重剑", "花剑", "佩剑", "个人赛", "团体赛"
        ],
        "regex_patterns": [
            {
                "category": "fee_policy",
                "tags": ["费用政策", "报名退费"],
                "pattern": r"(?:报名费|参赛费|服务费|退费|退款)[^\n。；]*?(?:\d+\s*元|\d+\s*%)?"
            },
            {
                "category": "appeal_matrix",
                "tags": ["仲裁申诉", "违规处罚"],
                "pattern": r"(?:申诉|仲裁|异议)[^\n。；]*?(?:\d+\s*分钟|\d+\s*元|书面)?"
            }
        ]
    },
    "equestrian": {
        "domain_id": "equestrian",
        "domain_name": "马术赛事与通级规则",
        "trigger_keywords": [
            "场地障碍", "盛装舞步", "三项赛", "耐力赛", "绕桶",
            "高度", "0.60m", "0.90m", "1.10m", "1.20m", "1.35m", "1.45m",
            "通级", "初三级", "初二级", "初一级", "中三级", "中二级", "中一级",
            "马匹护照", "芯片", "疫苗", "流感", "验马", "马厩费", "草料费",
            "罚分", "拒跳", "落马", "超时", "淘汰", "报名费", "退费", "申诉"
        ],
        "table_header_anchors": ["级别", "高度", "科目", "年龄", "马龄", "费用", "奖金", "罚分", "标准"],
        "condition_tag_entities": [
            "场地障碍", "盛装舞步", "三项赛", "耐力赛",
            "初三级", "初二级", "初一级", "中三级", "中二级", "中一级",
            "0.60M", "0.90M", "1.10M", "1.20M", "1.35M", "1.45M",
            "U12", "U14", "U16", "U18", "青年组", "公开组"
        ],
        "regex_patterns": [
            {
                "category": "horse_health_quarantine",
                "tags": ["马匹检疫", "护照疫苗"],
                "pattern": r"(?:马匹护照|芯片|疫苗|流感|检疫|验马)"
            },
            {
                "category": "fee_policy",
                "tags": ["马厩费", "报名退费"],
                "pattern": r"(?:报名费|马厩费|草料费|垫料费|退费|退款)[^\n。；]*?(?:\d+\s*元|\d+\s*%)?"
            },
            {
                "category": "penalty_elimination",
                "tags": ["罚分淘汰", "仲裁申诉"],
                "pattern": r"(?:拒跳|落马|超时|路线错误|罚\s*\d+\s*分|淘汰|申诉)"
            }
        ]
    },
    "rugby": {
        "domain_id": "rugby",
        "domain_name": "橄榄球赛事与竞赛规程",
        "trigger_keywords": [
            "7人制", "15人制", "触式橄榄球", "腰旗", "达阵", "附加射门", "罚踢",
            "黄牌", "红牌", "受罚席", "停赛", "替补", "报名费", "申诉", "护齿"
        ],
        "table_header_anchors": ["组别", "赛制", "时间", "人数", "费用", "处罚", "标准"],
        "condition_tag_entities": ["7人制", "15人制", "触式橄榄球", "男子组", "女子组", "U12", "U14", "U16", "U18", "成年组"],
        "regex_patterns": [
            {
                "category": "discipline_penalty",
                "tags": ["红黄牌", "停赛处罚"],
                "pattern": r"(?:红牌|黄牌|停赛|受罚席|取消资格)"
            },
            {
                "category": "fee_policy",
                "tags": ["费用政策", "报名退费"],
                "pattern": r"(?:报名费|参赛费|保证金|退费|退款)[^\n。；]*?(?:\d+\s*元|\d+\s*%)?"
            }
        ]
    },
    "general": {
        "domain_id": "general",
        "domain_name": "通用规章与财务制度",
        "trigger_keywords": ["标准", "费用", "报销", "补贴", "上限", "期限", "审批", "违约", "申诉", "退款"],
        "table_header_anchors": ["级别", "职级", "城市", "标准", "上限", "费用", "比例", "期限"],
        "condition_tag_entities": ["一线城市", "二线城市", "高级", "中级", "初级", "全额", "按比例"],
        "regex_patterns": [
            {
                "category": "fee_policy",
                "tags": ["费用标准", "退款报销"],
                "pattern": r"(?:费用|报销|补贴|退款|违约金|上限)[^\n。；]*?(?:\d+\s*元|\d+\s*%)?"
            }
        ]
    }
}


class RuleProfileManager:
    """领域规则模板调度中心。"""

    @classmethod
    def get_builtin_profile(cls, domain_type: str = "general") -> dict[str, Any]:
        """获取内置领域规则模板。"""
        return BUILTIN_RULE_PROFILES.get(domain_type, BUILTIN_RULE_PROFILES["general"])

    @classmethod
    async def generate_custom_profile_from_llm(
        cls,
        sample_texts: list[str],
        domain_name: str = "自定义行业",
    ) -> dict[str, Any]:
        """通过轻量大模型冷启动生成未知行业领域规则模板 JSON。"""
        combined_text = "\n\n---\n\n".join(sample_texts[:3])[:3000]
        prompt = f"""你是一个规程文档分析专家。请分析以下规程/制度文档样本，提取用于抽取“多维刚性规则卡片”的配置信息。

文档样本：
{combined_text}

请严格按以下 JSON 结构输出，不要包含任何多余文字或 Markdown 说明：
{{
  "domain_id": "custom",
  "domain_name": "{domain_name}",
  "trigger_keywords": ["至少10个该行业规程常见触发词，如特定项目名、费用词、限制词"],
  "table_header_anchors": ["规程表格中常见的表头关键词，如组别、级别、费用、标准"],
  "condition_tag_entities": ["常见的分类实体或分组标签，如组别名称、级别名、项目名"],
  "regex_patterns": [
    {{
      "category": "fee_policy",
      "tags": ["费用政策"],
      "pattern": "([^\n。；]*?(?:费用|报名费|保证金)[^\n。；]*?(?:\\d+元|\\d+%)?[^\n。；]*?)(?:[。；\n]|$)"
    }}
  ]
}}
"""
        try:
            from ..service.engine_pool import engine_pool

            raw_output = ""
            async for evt in engine_pool.execute_stream_with_failover(
                conversation_id="custom_profile_gen",
                user_prompt=prompt,
                model_name="",
                thinking_level="low",
                system_override="你是一个只输出严格合法 JSON 的助手。",
                temperature=0.2,
                pre_built_history=[],
                task_role="light",
            ):
                if evt.get("type") == "chunk":
                    raw_output += evt.get("content", "")
                elif evt.get("type") == "done":
                    raw_output = evt.get("full_content", raw_output)
                    break

            # 提取 JSON 块
            match = re.search(r"\{.*\}", raw_output, re.DOTALL)
            if match:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, dict) and "trigger_keywords" in parsed:
                    return parsed
        except Exception as e:
            logger.warning(f"[RuleProfiles] 大模型生成自定义领域模板跳过，回退至通用制度模板: {e}")

        # 兜底回退通用制度模板
        fallback = dict(BUILTIN_RULE_PROFILES["general"])
        fallback["domain_id"] = "custom"
        fallback["domain_name"] = domain_name
        return fallback
