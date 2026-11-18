"""
多源知识冲突与时序权威仲裁器 (Conflict Resolver)
核心功能：
1. 入库四维打标：权威定级 (Tier 1~3)、生效时间正则提取 (effective_time)、
   归一化主题簇指纹 (topic_cluster_id) 与同主题新旧版本替代链标记 (is_superseded)；
2. 基于 SHA-256 content_hash 的跨源索引完全一致去重；
3. 检索阶段融合权威权重 W_authority 与时序新鲜度因子 W_freshness；
4. 切片“时空身份证”标准头部包装器与冲突显性披露提示词注入。
"""
from datetime import datetime
from pathlib import Path
import re
import sqlite3
import time
from typing import Any

from ..core.logging_config import logger


class ConflictResolver:
    """知识冲突与时序权威仲裁器。"""

    # 权威系数
    WEIGHT_AUTHORITY = {
        1: 1.25,  # 正式发布库 / 定稿区
        2: 1.00,  # 工作区活跃态
        3: 0.60,  # 草稿 / 归档旧版
    }

    # 时序新鲜度因子
    WEIGHT_FRESHNESS = {
        0: 1.20,  # is_superseded = 0 (最新版本)
        1: 0.45,  # is_superseded = 1 (已被新版本替代)
    }

    # 提取生效日期的正则表达式
    DATE_PATTERNS = [
        re.compile(r"(\d{4})[年\-_/](\d{1,2})[月\-_/](\d{1,2})(?:日)?"),
        re.compile(r"(\d{4})\.(\d{1,2})\.(\d{1,2})"),
    ]

    # 草稿与历史旧版关键词
    DRAFT_ARCHIVE_KEYWORDS = [
        "draft", "temp", "backup", "archive", "old", "bak", "copy",
        "草稿", "初稿", "暂存", "旧版", "归档", "废弃", "备份", "历史",
    ]

    @classmethod
    def resolve_authority_tier(cls, remote_uri: str, explicit_tier: int | None = None) -> int:
        """根据路径特征或显式指定判定权威等级 Tier 1~3。"""
        if explicit_tier in (1, 2, 3):
            return explicit_tier

        uri_lower = remote_uri.lower()

        # 1. 明确草稿/归档
        if any(kw in uri_lower for kw in cls.DRAFT_ARCHIVE_KEYWORDS):
            return 3

        # 2. 正式发布库/知识库
        if any(kw in uri_lower for kw in ["release", "official", "publish", "规程", "正式", "发布", "定稿", "wecom://"]):
            return 1

        # 3. 默认为工作区当前态
        return 2

    @classmethod
    def extract_effective_time(cls, text: str, filename: str, fallback_mtime: float = 0.0) -> float:
        """从文档前 500 字符及文件名中提取生效时间戳。"""
        sample = (filename + "\n" + text[:500]).strip()

        for pat in cls.DATE_PATTERNS:
            match = pat.search(sample)
            if match:
                try:
                    year = int(match.group(1))
                    month = int(match.group(2))
                    day = int(match.group(3))
                    if 2000 <= year <= 2099 and 1 <= month <= 12 and 1 <= day <= 31:
                        dt = datetime(year, month, day)
                        return dt.timestamp()
                except Exception:
                    continue

        return fallback_mtime or time.time()

    @classmethod
    def mark_superseded_in_db(
        cls, db_conn: sqlite3.Connection, pod_id: str, topic_cluster_id: str
    ) -> None:
        """扫描同主题所有记录，将生效时间最新的设为 is_superseded = 0，其余标记为 1。"""
        # 查询该主题下最大的 effective_time
        row = db_conn.execute("""
            SELECT MAX(effective_time) as max_eff
            FROM hybrid_chunks
            WHERE pod_id = ? AND topic_cluster_id = ?
        """, (pod_id, topic_cluster_id)).fetchone()

        if row and row["max_eff"] is not None:
            max_eff = row["max_eff"]
            # 最新的设为 0
            db_conn.execute("""
                UPDATE hybrid_chunks
                SET is_superseded = CASE WHEN effective_time >= ? THEN 0 ELSE 1 END
                WHERE pod_id = ? AND topic_cluster_id = ?
            """, (max_eff - 1.0, pod_id, topic_cluster_id))

    @classmethod
    def compute_arbitrated_score(cls, chunk: dict[str, Any]) -> float:
        """计算叠加权威权重与时效因子的最终得分。"""
        base_rrf = float(chunk.get("rrf_score", 0.0))
        tier = int(chunk.get("authority_tier", 2))
        is_sup = int(chunk.get("is_superseded", 0))

        w_auth = cls.WEIGHT_AUTHORITY.get(tier, 1.00)
        w_fresh = cls.WEIGHT_FRESHNESS.get(is_sup, 1.20)
        return base_rrf * w_auth * w_fresh

    @classmethod
    def format_spatial_temporal_id(cls, chunk: dict[str, Any], idx: int) -> str:
        """生成“时空身份证”头部包裹的切片文本。"""
        tier = int(chunk.get("authority_tier", 2))
        tier_label = {1: "正式发布(Tier 1)", 2: "工作区当前(Tier 2)", 3: "草稿/归档(Tier 3)"}.get(tier, "未知")

        eff_time = float(chunk.get("effective_time", 0.0))
        if eff_time > 0:
            eff_str = datetime.fromtimestamp(eff_time).strftime("%Y-%m-%d")
        else:
            eff_str = "未标注"

        is_sup = int(chunk.get("is_superseded", 0))
        status_label = "⚠️ 已被新版本替代(旧版)" if is_sup == 1 else "最新版本"

        uri = chunk.get("remote_uri", "")
        sec = chunk.get("section_path", "")
        loc = f"{uri} §{sec}" if sec else uri
        content = chunk.get("content_text", "").strip()

        header = f"[证据切片 #{idx} | 权威度: {tier_label} | 生效时间: {eff_str} | 版本状态: {status_label} | 来源: {loc}]"
        return f"{header}\n{content}"

    @classmethod
    def detect_conflicts_and_generate_disclosure(
        cls, chunks: list[dict[str, Any]]
    ) -> tuple[bool, str]:
        """检测召回切片中是否存在同主题多版本或新旧冲突，生成显性披露提示词。

        Returns:
            tuple[bool, str]: (has_conflict, disclosure_instruction)
        """
        topic_versions: dict[str, list[dict[str, Any]]] = {}
        for c in chunks:
            t = c.get("topic_cluster_id", "")
            if t and t != "general_topic":
                topic_versions.setdefault(t, []).append(c)

        conflict_topics: list[str] = []
        for t, items in topic_versions.items():
            statuses = {it.get("is_superseded", 0) for it in items}
            tiers = {it.get("authority_tier", 2) for it in items}
            # 如果同时包含最新版和被替代旧版，或者同时包含 Tier 1 和 Tier 3
            if len(statuses) > 1 or (1 in tiers and 3 in tiers):
                conflict_topics.append(t)

        if not conflict_topics:
            return False, ""

        instruction = (
            "\n> ⚠️ **【多源知识版本冲突审计提示】**\n"
            f"> 检测到当前命中证据中涉及主题【{', '.join(conflict_topics)}】的新旧版本或草稿共存。\n"
            "> **仲裁原则**：必须优先采信【正式发布(Tier 1)】与【最新版本】的条款与数值；\n"
            "> **披露义务**：在最终回复中，必须向用户明确指出历史旧版或草稿中的冲突条款/差异数值，提醒用户版本差异。"
        )
        return True, instruction
