"""
AgentHub Local v1.5.9 动态时间感知与三级时效重排器 (Temporal Reranker)
核心职责：
1. TemporalMetadataExtractor: 从文件名与正文中提取生效日期 (YYYY-MM-DD)、时间戳及公文等级；
2. TemporalReranker: 锚定系统动态当前时间 (datetime.now(), 如 2026-11-18)，
   对召回切片进行连续时间衰减计算与同族最新版本 (is_latest_in_cluster) 霸权加权；
3. 支持显式历史回溯 (如“2025年规程”) 与跨版本对比模式智能旁路。
"""
from dataclasses import dataclass
from datetime import datetime
import re
import time
from typing import Any

from ..core.logging_config import logger


@dataclass
class TemporalMeta:
    """文档时效与层级元数据。"""
    doc_year: int
    effective_date: str          # 'YYYY-MM-DD'
    effective_ts: int            # 秒级 Unix 时间戳
    doc_priority_type: str       # 'supplement' | 'main' | 'normal'
    topic_cluster_id: str        # 归一化同族指纹


class TemporalMetadataExtractor:
    """公文时效与层级元数据提取器。"""

    # 正文落款或发布日期正则
    BODY_DATE_RE = re.compile(
        r"(?:生效日期|发布日期|印发日期|落款日期|日期)[\s:：]*"
        r"(20[2-3]\d)[年\-/.]\s*([01]?\d)[月\-/.]\s*([0-3]?\d)?[日]?",
        re.I,
    )
    # 通用正文年月日正则
    DATE_GENERAL_RE = re.compile(
        r"(20[2-3]\d)[年\-/.]\s*([01]?\d)[月\-/.]\s*([0-3]?\d)[日]?"
    )

    # 文件名年月解析正则
    FILENAME_YEAR_MONTH_RE = re.compile(
        r"(20[2-3]\d)[年\-_.]\s*([01]?\d)月?"
    )
    FILENAME_YEAR_RE = re.compile(r"(20[2-3]\d)")

    @classmethod
    def extract_metadata(
        cls, filename: str, content_prefix: str = "", mtime: float = 0.0
    ) -> TemporalMeta:
        """从文件名与正文前缀提取完整时效与层级元数据。"""
        doc_year = 0
        effective_date = ""
        effective_ts = 0

        # 1. 尝试从正文前缀识别明确落款/生效日期
        if content_prefix:
            m_body = cls.BODY_DATE_RE.search(content_prefix)
            if not m_body:
                # 寻找最后的落款日期
                dates = cls.DATE_GENERAL_RE.findall(content_prefix)
                if dates:
                    y, m, d = dates[-1]
                    d_str = f"{int(d):02d}" if d else "01"
                    doc_year = int(y)
                    effective_date = f"{int(y):04d}-{int(m):02d}-{d_str}"
            else:
                y, m, d = m_body.group(1), m_body.group(2), m_body.group(3)
                d_str = f"{int(d):02d}" if d else "01"
                doc_year = int(y)
                effective_date = f"{int(y):04d}-{int(m):02d}-{d_str}"

        # 2. 正文未识别到，从文件名解析年月或年份
        if not effective_date:
            m_ym = cls.FILENAME_YEAR_MONTH_RE.search(filename)
            if m_ym:
                y, m = m_ym.group(1), m_ym.group(2)
                doc_year = int(y)
                effective_date = f"{int(y):04d}-{int(m):02d}-01"
            else:
                m_y = cls.FILENAME_YEAR_RE.search(filename)
                if m_y:
                    doc_year = int(m_y.group(1))
                    effective_date = f"{doc_year:04d}-01-01"

        # 3. 转换为 Unix 时间戳
        if effective_date:
            try:
                dt = datetime.strptime(effective_date, "%Y-%m-%d")
                effective_ts = int(dt.timestamp())
            except Exception:
                effective_ts = int(mtime) if mtime > 0 else int(time.time())
        else:
            if mtime > 0:
                effective_ts = int(mtime)
                dt = datetime.fromtimestamp(mtime)
                doc_year = dt.year
                effective_date = dt.strftime("%Y-%m-%d")
            else:
                now_dt = datetime.now()
                doc_year = now_dt.year
                effective_date = now_dt.strftime("%Y-%m-%d")
                effective_ts = int(now_dt.timestamp())

        # 4. 判定规程层级类型 (doc_priority_type)
        fn_lower = filename.lower()
        if any(k in fn_lower for k in ["补充通知", "补充规定", "修订版", "勘误", "核验细则"]):
            doc_priority_type = "supplement"
        elif any(k in fn_lower for k in ["总规程", "主规程", "管理办法", "章程", "竞赛办法"]):
            doc_priority_type = "main"
        else:
            doc_priority_type = "normal"

        # 5. 归一化同主题族指纹
        topic_cluster_id = cls.extract_topic_cluster_id(filename)

        return TemporalMeta(
            doc_year=doc_year,
            effective_date=effective_date,
            effective_ts=effective_ts,
            doc_priority_type=doc_priority_type,
            topic_cluster_id=topic_cluster_id,
        )

    @staticmethod
    def extract_topic_cluster_id(filename: str) -> str:
        """剥离年份、月份、版本、补充通知修饰词，生成主题族归一化指纹。"""
        name = re.sub(r"\.[^.]+$", "", filename)
        name = re.sub(r"20[2-3]\d年?", "", name)
        name = re.sub(r"[01]?\d月", "", name)
        name = re.sub(r"[\(（]\s*\d+\s*[\)）]|v\d+(\.\d+)*", "", name, flags=re.I)
        name = re.sub(r"补充通知|实施细则|修订版|正式版|定稿|草稿|试行|通知|办法", "", name)
        return re.sub(r"[\s_\-]+", "", name).strip()


class TemporalReranker:
    """动态时间感知与三级时效重排器。"""

    # 检测用户提问是否明确指定了历史年份或跨期对比
    HISTORICAL_QUERY_RE = re.compile(
        r"(?:20[12]\d年?|去年|前年|历史|旧版|老版本|往期|对比|变化|差异|区别)",
        re.I,
    )

    @classmethod
    def is_historical_or_comparison_query(cls, query: str) -> bool:
        """判断查询是否包含历史版本回溯或跨期对比意图。"""
        return bool(cls.HISTORICAL_QUERY_RE.search(query))

    @classmethod
    def rerank(
        cls,
        candidates: list[dict[str, Any]],
        query: str = "",
        current_dt: datetime | None = None,
        now_dt: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """执行动态当前时间感知加权与同族最新版本仲裁。
        
        Args:
            candidates: 待重排的检索候选列表 (必须包含 score 或 rrf_score)。
            query: 用户原始查询问句。
            current_dt: 当前系统基准时间 (缺省为 datetime.now())。
            now_dt: 兼容别名。
            
        Returns:
            list[dict[str, Any]]: 经时效重排后的候选列表 (降序排列)。
        """
        if not candidates:
            return []

        now = current_dt or now_dt or datetime.now()
        now_ts = int(now.timestamp())
        is_historical = cls.is_historical_or_comparison_query(query)

        reranked = []
        for c in candidates:
            base_score = float(c.get("score") or c.get("rrf_score") or 0.0)
            eff_ts = int(c.get("effective_ts") or 0)
            is_latest = int(c.get("is_latest_in_cluster") if c.get("is_latest_in_cluster") is not None else 1)
            p_type = c.get("doc_priority_type") or "normal"

            if is_historical:
                # 历史回溯模式：保持语义相关度主导，不压制旧文档
                final_score = base_score
                freshness_factor = 1.0
                cluster_boost = 0.0
            else:
                # 隐式求新模式 (用户求最新权威规则)
                # 计算与当前时间的相差天数 (正数为历史过去，负数可能为未来拟生效)
                delta_days = (now_ts - eff_ts) / 86400.0 if eff_ts > 0 else 365.0

                if delta_days <= 30:
                    # 近 30 天新发
                    freshness_factor = 1.25
                elif delta_days <= 180:
                    # 半年内新发
                    freshness_factor = 1.10
                elif delta_days <= 330:
                    # 当年较早
                    freshness_factor = 0.95
                else:
                    # 跨年旧文档 (大于 330 天，强烈压制)
                    freshness_factor = 0.45

                # 同族最新版本与补充通知额外加权
                cluster_boost = 0.35 if (is_latest == 1 or p_type == "supplement") else 0.0
                final_score = base_score * freshness_factor + cluster_boost

            item = dict(c)
            item["base_score"] = base_score
            item["final_score"] = round(final_score, 4)
            item["freshness_factor"] = round(freshness_factor, 2)
            item["cluster_boost"] = round(cluster_boost, 2)
            reranked.append(item)

        # 按综合最终得分降序排序
        reranked.sort(key=lambda x: x["final_score"], reverse=True)
        return reranked

    @classmethod
    def rerank_chunks(
        cls,
        chunks: list[dict[str, Any]],
        current_query: str = "",
        now_dt: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """向后兼容别名方法。"""
        return cls.rerank(candidates=chunks, query=current_query, now_dt=now_dt)

