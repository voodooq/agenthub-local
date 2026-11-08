# -*- coding: utf-8 -*-
"""
计划任务意图与自然语言时间解析器 (ScheduleIntentParser)
功能：
1. 深度分析用户口语化自然语言需求（例如：“工作日每天早上9点半检查未读邮件并生成报告”）
2. 精确拆解「时间 (When)」与「动作 (What)」
3. 自动生成标准 Cron 表达式与人性化中文周期解读
4. 提炼任务名称并扩充具身执行 Prompt 模板
5. 纯本地离线规则 + 可选大模型混合双引擎
"""
import re
from typing import Any
from ..core.logging_config import logger


class ScheduleIntentParser:
    """计划任务自然语言语义与时间分析器"""

    @classmethod
    def parse(cls, text: str) -> dict[str, Any]:
        """
        核心解析入口：输入任意自然语言，输出结构化计划任务配置
        """
        raw = text.strip()
        if not raw:
            return {
                "name": "新自动化计划任务",
                "cron_expr": "0 9 * * *",
                "human_schedule": "每天 09:00",
                "prompt_template": "请执行预定工作区巡检与任务。",
                "explanation": "未检测到具体时间要求，默认设为每天 09:00。",
            }

        # 1. 解析时间周期 (Cron 与 人性化解读)
        cron_expr, human_schedule, time_matched_spans = cls._extract_schedule(raw)

        # 2. 剥离时间词，提取核心动作 (What)
        action_text = raw
        # 将已匹配的时间词替换为空格
        for span in sorted(time_matched_spans, key=lambda s: s[0], reverse=True):
            action_text = action_text[:span[0]] + " " + action_text[span[1]:]

        # 进一步清理残留的常见时间虚词（如“每天”、“每日”、“每早”、“准时”、“定时”等）
        action_text = re.sub(
            r"\b(每天|每日|天天|每逢|工作日|周末|周[一二三四五六天日1-7]|星期[一二三四五六天日1-7]|早晨|早上|上午|中午|下午|晚上|傍晚|凌晨|定时|准时|帮我|请帮我|让AI|让智能体|系统)\b",
            " ",
            action_text,
        )
        
        # 清理多余动词前缀
        cleaned_action = re.sub(
            r"^(去|执行|完成|运行|做一次|进行|处理)\s*",
            "",
            action_text.strip(),
            flags=re.IGNORECASE,
        )
        cleaned_action = re.sub(r"\s+", " ", cleaned_action).strip("，, 。.；; 、 ")

        if not cleaned_action or len(cleaned_action) < 2:
            cleaned_action = raw

        # 3. 提炼精炼标题 (Name)
        task_name = cls._generate_task_name(cleaned_action, human_schedule)

        # 4. 扩充结构化具身指令 (Prompt Template)
        prompt_template = cls._generate_prompt_template(cleaned_action, task_name)

        explanation = f"已分析您的意图：将在「{human_schedule}」自动调度执行「{task_name}」。"

        return {
            "name": task_name,
            "cron_expr": cron_expr,
            "human_schedule": human_schedule,
            "prompt_template": prompt_template,
            "explanation": explanation,
        }

    @classmethod
    def _extract_schedule(cls, text: str) -> tuple[str, str, list[tuple[int, int]]]:
        """
        从文本中提取时间信息，返回 (cron, human_readable, matched_spans)
        """
        matched_spans: list[tuple[int, int]] = []

        # 检查是否是每隔 N 小时 / N 分钟
        m_interval_hour = re.search(r"每(?:隔)?\s*(\d+)\s*(?:个)?小时", text)
        if m_interval_hour:
            h = int(m_interval_hour.group(1))
            matched_spans.append(m_interval_hour.span())
            return f"0 */{h} * * *", f"每隔 {h} 小时", matched_spans

        m_interval_min = re.search(r"每(?:隔)?\s*(\d+)\s*分钟", text)
        if m_interval_min:
            m = int(m_interval_min.group(1))
            matched_spans.append(m_interval_min.span())
            return f"*/{m} * * * *", f"每隔 {m} 分钟", matched_spans

        # 提取星期/工作日/周末
        days_cron = "*"
        day_human = "每天"
        
        m_workday = re.search(r"(工作日|周一到周五|星期一到星期五|周一至周五)", text)
        m_weekend = re.search(r"(周末|周六周日|礼拜天)", text)
        m_weekday = re.search(r"(每周|每个星期|星期|周|礼拜)\s*([一二三四五六天日1-7])", text)

        if m_workday:
            days_cron = "1-5"
            day_human = "工作日 (周一至周五)"
            matched_spans.append(m_workday.span())
        elif m_weekend:
            days_cron = "6,0"
            day_human = "周末"
            matched_spans.append(m_weekend.span())
        elif m_weekday:
            wd_char = m_weekday.group(2)
            wd_map = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "天": 0, "日": 0, "7": 0}
            wd_num = wd_map.get(wd_char, int(wd_char) if wd_char.isdigit() else 1)
            days_cron = str(wd_num)
            day_human = f"每周{wd_char}"
            matched_spans.append(m_weekday.span())
        else:
            m_daily = re.search(r"(每天|每日|天天|每逢每天)", text)
            if m_daily:
                days_cron = "*"
                day_human = "每天"
                matched_spans.append(m_daily.span())

        # 提取时分
        hour = 9
        minute = 0

        # 优先匹配 "9:30" 或 "09:30" 或 "18:00"
        m_clock = re.search(r"(\d{1,2})[:：](\d{2})", text)
        # 匹配 "下午3点" / "晚上8点半" / "上午9点" / "9点45分"
        m_chinese_time = re.search(
            r"(早上|早晨|上午|中午|下午|晚上|傍晚|夜里|凌晨)?\s*(\d{1,2}|半)?\s*点\s*(半|\d{1,2}(?:分)?)?",
            text,
        )

        if m_clock:
            hour = int(m_clock.group(1))
            minute = int(m_clock.group(2))
            matched_spans.append(m_clock.span())
            # 如果前面有“下午/晚上”，且小于 12 点，转为 24 小时制
            prefix = text[:m_clock.start()]
            if re.search(r"(下午|晚上|傍晚)", prefix[-6:]):
                if hour < 12:
                    hour += 12
        elif m_chinese_time and (m_chinese_time.group(1) or m_chinese_time.group(2)):
            period = m_chinese_time.group(1) or ""
            h_str = m_chinese_time.group(2) or "9"
            m_str = m_chinese_time.group(3) or "0"

            h_val = int(h_str) if h_str.isdigit() else 9
            if period in ("下午", "晚上", "傍晚") and h_val < 12:
                h_val += 12
            elif period == "中午" and h_val < 11:
                h_val += 12

            if m_str == "半":
                m_val = 30
            elif m_str.replace("分", "").isdigit():
                m_val = int(m_str.replace("分", ""))
            else:
                m_val = 0

            hour = h_val
            minute = m_val
            matched_spans.append(m_chinese_time.span())
        else:
            hour = 9
            minute = 0

        cron_expr = f"{minute} {hour} * * {days_cron}"
        time_str = f"{hour:02d}:{minute:02d}"
        human_readable = f"{day_human} {time_str}"

        return cron_expr, human_readable, matched_spans

    @classmethod
    def _generate_task_name(cls, action_text: str, human_schedule: str) -> str:
        """
        根据动作与时间提炼精简的任务标题
        """
        core = action_text.strip().split("，")[0].split("。")[0]
        core = re.sub(r"^(帮我|把|将|对)\s*", "", core)
        
        prefix = ""
        if "工作日" in human_schedule:
            prefix = "工作日"
        elif "每周" in human_schedule:
            prefix = human_schedule.split()[0]
        elif "每隔" in human_schedule:
            prefix = "定时"
        else:
            prefix = "每日"

        if len(core) > 18:
            core = core[:18] + "..."

        if core:
            return f"{prefix}：{core}"
        return f"{prefix}自动任务"

    @classmethod
    def _generate_prompt_template(cls, action_text: str, task_name: str) -> str:
        """
        结构化扩充具身执行提示词模板
        """
        prompt = (
            f"【自动计划任务 - {task_name}】\n\n"
            f"🎯 任务目标：\n"
            f"{action_text}\n\n"
            f"📋 执行与验收要求：\n"
            f"1. 针对当前工作区上下文执行上述操作，如需调用相关脚本、技能或工具命令，自动受控执行；\n"
            f"2. 若产生输出文件、总结报告或对账结果，请将其格式化为 Markdown 写入工作区资产库目录并自动登记；\n"
            f"3. 遇到异常时自动记录排查日志，并在完成后通过系统通知告知执行结论。"
        )
        return prompt
