"""文本质量与乱码熔断器 (TextQualityGate)。

核心功能：
1. 监控文档解析后的原始文本流质量，防止缺失 CMap 的 PDF、GBK 误读与二进制乱码入库；
2. 切片级细粒度质量过滤，过滤空切片、残损切片与无效乱码片段；
3. 输出结构化质量评估报告 (valid_ratio, garbage_ratio, char_count)。
"""

from __future__ import annotations

import re
from typing import Any


class TextQualityGate:
    """文档级与切片级文本质量门控：彻底拦截乱码、损坏编码与无效碎片。"""

    # 常用汉字区 (CJK Unified Ideographs 基本区)
    _HAN_RE = re.compile(r"[\u4e00-\u9fa5]")
    # 常用 ASCII 字母数字
    _ALNUM_RE = re.compile(r"[a-zA-Z0-9]")
    # 常用中英文标点符号与空白
    _PUNCT_AND_SPACE_RE = re.compile(
        r"[\s，。！？；：“”‘’（）《》【】、—…·～\.,!?;:'\"()\[\]{}\-_/\\+=*&%$#@`^|<>℃㎡①②③④⑤⑥⑦⑧⑨⑩一二三四五六七八九十]"
    )

    # 乱码替换符、Unicode 私有使用区 (PUA) 与非法控制字符
    _GARBAGE_RE = re.compile(r"[\ufffd\ue000-\uf8ff\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

    # 现代汉语高频通用基础汉字表 (跨行业无偏置，用于拦截二进制流误解码产生的生僻字乱码)
    _COMMON_CHINESE_CHARS = frozenset(
        "的一是在不了有和人这中大为上个国我以要他时来用们生到作地于出就分对成会可主发年动同工也能下过子说产种面而方后多定行学法所民得经十三之进着等部度家电力里如水化高自二理起小物现实加量都两体制机当使点从业本去把性好应开它合还因由其些然前外天政四日那社义事平形相全表间样想向道命关白正气位元款项单据数额总计附注条第章程费标准核管制度"
    )

    # 剥离元数据头正则 (例如: 【文档来源】xxx | 【生效年份】xxx \n)
    _METADATA_HEADER_RE = re.compile(r"^【[^】]+】[^\n]*\n?", re.MULTILINE)

    @classmethod
    def is_gibberish_chinese(cls, text: str) -> bool:
        """判定中文文本是否为二进制流强行解码产生的全生僻字符乱码。

        说明：仅当汉字数量 >= 20 时执行检测，避免误伤以英文缩写或数字为主的财务报表/外文表格。
        """
        body = cls._METADATA_HEADER_RE.sub("", text or "").strip()
        han_chars = cls._HAN_RE.findall(body)
        if len(han_chars) >= 20:
            common_cnt = sum(1 for c in han_chars if c in cls._COMMON_CHINESE_CHARS)
            if (common_cnt / len(han_chars)) < 0.12:
                return True
        return False

    @classmethod
    def evaluate(cls, text: str) -> dict[str, Any]:
        """评估给定文本的文本质量指标。

        Args:
            text: 待评估文本字符串。

        Returns:
            dict[str, Any]: 包含 valid_ratio, garbage_ratio, char_count, han_count 等。
        """
        if not text:
            return {
                "char_count": 0,
                "valid_count": 0,
                "garbage_count": 0,
                "valid_ratio": 0.0,
                "garbage_ratio": 1.0,
                "han_count": 0,
                "is_empty": True,
            }

        total_chars = len(text)
        han_count = len(cls._HAN_RE.findall(text))
        alnum_count = len(cls._ALNUM_RE.findall(text))
        punct_space_count = len(cls._PUNCT_AND_SPACE_RE.findall(text))
        valid_count = han_count + alnum_count + punct_space_count

        garbage_count = len(cls._GARBAGE_RE.findall(text))

        valid_ratio = valid_count / total_chars if total_chars > 0 else 0.0
        garbage_ratio = garbage_count / total_chars if total_chars > 0 else 0.0

        return {
            "char_count": total_chars,
            "valid_count": valid_count,
            "garbage_count": garbage_count,
            "valid_ratio": round(valid_ratio, 4),
            "garbage_ratio": round(garbage_ratio, 4),
            "han_count": han_count,
            "is_empty": False,
        }

    @classmethod
    def is_document_acceptable(cls, text: str) -> tuple[bool, str]:
        """文档级熔断判定：评估整篇文档解析后内容是否具备业务可读性。

        判定规则：
        1. 剥离空白后总字数至少 >= 30 字；
        2. valid_ratio >= 0.65（有效字符占比不低于 65%）；
        3. garbage_ratio <= 0.15（乱码及控制字符占比不超过 15%）。

        Returns:
            tuple[bool, str]: (是否达标, 拦截原因描述)
        """
        cleaned = (text or "").strip()
        if len(cleaned) < 30:
            return False, f"文档内容过少（有效字数 {len(cleaned)} < 30 字），疑似空文档或未识别扫描件"

        metrics = cls.evaluate(cleaned)
        valid_ratio = metrics["valid_ratio"]
        garbage_ratio = metrics["garbage_ratio"]

        if garbage_ratio > 0.15:
            return False, f"文档乱码率过高 (乱码占比 {garbage_ratio:.1%} > 15.0%)，疑似缺失字体 CMap 或二进制解码错误"

        if valid_ratio < 0.65:
            return False, f"文档有效字符率不足 (有效比 {valid_ratio:.1%} < 65.0%)，存在大量未知扩展符号或受损编码"

        if cls.is_gibberish_chinese(cleaned):
            return False, "文档常用汉字密度过低，疑似旧版二进制文件误解码生成的生僻字乱码"

        return True, ""

    @classmethod
    def is_chunk_acceptable(cls, chunk_text: str, *, min_body_chars: int = 20) -> tuple[bool, str]:
        """切片级熔断判定：评估单个切片是否应被保留或丢弃。

        判定规则：
        1. 剥离元数据头后的正文字数 >= min_body_chars（默认 20 字）；
        2. valid_ratio >= 0.60；
        3. garbage_ratio <= 0.15。

        Returns:
            tuple[bool, str]: (是否达标, 丢弃原因描述)
        """
        if not chunk_text:
            return False, "切片内容为空"

        # 剥离元数据头，审查真实正文内容
        body = cls._METADATA_HEADER_RE.sub("", chunk_text).strip()
        if len(body) < min_body_chars:
            return False, f"切片有效正文过短 ({len(body)} < {min_body_chars} 字符)"

        metrics = cls.evaluate(body)
        valid_ratio = metrics["valid_ratio"]
        garbage_ratio = metrics["garbage_ratio"]

        if garbage_ratio > 0.15:
            return False, f"切片包含过多乱码字符 (乱码比 {garbage_ratio:.1%} > 15%)"

        if valid_ratio < 0.60:
            return False, f"切片有效字符比例偏低 (有效比 {valid_ratio:.1%} < 60%)"

        if cls.is_gibberish_chinese(body):
            return False, "切片常用汉字密度过低，疑似二进制解码残余乱码"

        return True, ""

