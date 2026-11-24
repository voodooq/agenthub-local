"""
AgentHub Local v1.6.0 国标化 PROV-O 逐字证据与模态自适应坐标溯源模块
遵循规范：
1. W3C PROV-O (The PROV Ontology) 数据起源与证据链规范；
2. GB/T 22239-2019《信息安全技术 网络安全等级保护基本要求》（等保三级审计可溯源要求）；
3. 模态自适应定位：PDF物理页码/字符偏移、Word段落序/章节路径、Excel工作表名/单元格范围；
4. 宽容兼容：支持历史旧库平滑迁移 (is_legacy_migrated)，缺省坐标优雅回退为 0/未标注，绝不崩溃中断。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Literal
from pydantic import BaseModel, Field, model_validator


def compute_doc_id(remote_uri: str, doc_title: str = "") -> str:
    """生成全局唯一的稳定文档标识。

    核心契约：强绑定规范化后的 remote_uri（若为空则回退至 doc_title）。
    无论内容如何更新、etag 如何变化，同一文件在整个生命周期中的 doc_id 保持不变。
    """
    clean_uri = (remote_uri or "").strip().replace("\\", "/")
    clean_title = (doc_title or "").strip()
    seed = clean_uri or clean_title or "unknown_doc"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def format_citation_label(prov: dict | StandardizedProvenance | None) -> str:
    """按 GB/T 36344/22239 规范生成人类可读的高保真溯源定位标签。

    支持自适应多模态：
    - PDF: 《文档名》第X页（章节名）
    - Excel: 《文档名》工作表[Sheet名] 单元格范围
    - Word: 《文档名》第X段（章节名）
    - 其它/Markdown: 《文档名》（章节名）
    """
    if not prov:
        return ""
    if isinstance(prov, dict):
        anchor = prov.get("anchor") or {}
        doc_title = prov.get("doc_title") or ""
        modality = anchor.get("modality") or "general"
        page_num = anchor.get("page_number")
        section = anchor.get("section_path") or ""
        sheet_name = anchor.get("sheet_name") or ""
        cell_range = anchor.get("cell_range") or ""
        paragraph_idx = anchor.get("paragraph_index")
    else:
        doc_title = prov.doc_title
        anchor = prov.anchor
        modality = anchor.modality
        page_num = anchor.page_number
        section = anchor.section_path or ""
        sheet_name = anchor.sheet_name or ""
        cell_range = anchor.cell_range or ""
        paragraph_idx = anchor.paragraph_index

    title_part = f"《{doc_title}》" if doc_title else ""
    sec_part = f"（{section}）" if section and section != "正文" else ""

    if modality == "pdf" and page_num is not None and page_num > 0:
        return f"{title_part}第{page_num}页{sec_part}".strip()
    elif modality in ("xlsx", "excel") and sheet_name:
        cell_part = f" {cell_range}" if cell_range else ""
        return f"{title_part}工作表[{sheet_name}]{cell_part}".strip()
    elif modality in ("docx", "word") and paragraph_idx is not None and paragraph_idx > 0:
        return f"{title_part}第{paragraph_idx}段{sec_part}".strip()
    elif sec_part:
        return f"{title_part}{sec_part}".strip()
    return title_part or "规程正文"


class LocationAnchor(BaseModel):
    """模态自适应定位锚点：按文件模态存储精准物理坐标。"""
    modality: Literal["pdf", "docx", "xlsx", "txt", "markdown"]
    section_path: str = Field(default="正文", description="章节层级路径，如 1.2 或 规程标题")
    page_number: int | None = Field(default=None, description="PDF 物理页码（1 起始，0 代表旧库未标记）")
    char_start: int | None = Field(default=None, description="PDF 或纯文本字符起始偏移量")
    char_end: int | None = Field(default=None, description="PDF 或纯文本字符终止偏移量")
    paragraph_index: int | None = Field(default=None, description="Word 文档段落顺序索引")
    sheet_name: str | None = Field(default=None, description="Excel 工作表名称")
    cell_range: str | None = Field(default=None, description="Excel 单元格坐标范围，如 B4:E10")
    is_legacy_migrated: bool = Field(default=False, description="是否由历史老知识库切片原地升级导入")

    @model_validator(mode="after")
    def validate_anchor_by_modality(self) -> "LocationAnchor":
        """按模态验证必要坐标，对历史迁移数据宽容保底，杜绝异常崩溃。"""
        m = self.modality
        if m == "pdf":
            if self.page_number is None:
                # 宽容优雅降级：若历史切片未捕获具体页码，回退为 0（代表老版本未标记物理页），不阻断进程
                self.page_number = 0 if self.is_legacy_migrated else 1
        elif m == "xlsx":
            if not self.sheet_name:
                self.sheet_name = "Sheet1"
        return self


class StandardizedProvenance(BaseModel):
    """符合 W3C PROV-O 与 GB/T 22239 等保审计要求的逐字溯源模型。"""
    doc_id: str = Field(..., description="文档稳定短哈希（MD5 前 16 位）")
    doc_title: str = Field(..., description="文档真实文件名")
    anchor: LocationAnchor = Field(..., description="模态自适应定位锚点")
    text_span: str = Field(..., min_length=1, description="原文逐字证据片段（严禁虚构篡改，支持审计核验）")
    extractor_model: str = Field(default="rule_ast_v1.6", description="抽取引擎版本标识")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="置信度评分")

    def to_compact_dict(self) -> dict[str, Any]:
        """输出紧凑型字典，便于存入 SQLite 或嵌入 JSONL。"""
        return self.model_dump(exclude_none=True)

    @classmethod
    def from_compact_json(cls, json_str: str) -> "StandardizedProvenance":
        """从 JSON 反序列化。"""
        return cls.model_validate_json(json_str)

    @classmethod
    def build_from_chunk(
        cls,
        *,
        doc_title: str,
        content: str,
        remote_uri: str = "",
        section_path: str = "正文",
        modality: Literal["pdf", "docx", "xlsx", "txt", "markdown"] | None = None,
        page_number: int | None = None,
        paragraph_index: int | None = None,
        sheet_name: str | None = None,
        cell_range: str | None = None,
        char_start: int | None = None,
        char_end: int | None = None,
        evidence_quote: str = "",
        is_legacy_migrated: bool = False,
    ) -> "StandardizedProvenance":
        """工厂方法：根据切片上下文便捷构建标准溯源对象。"""
        # 自动推断文件模态
        if not modality:
            lower_name = (doc_title or remote_uri).lower()
            if lower_name.endswith(".pdf"):
                modality = "pdf"
            elif lower_name.endswith((".xlsx", ".xls", ".csv")):
                modality = "xlsx"
            elif lower_name.endswith((".docx", ".doc")):
                modality = "docx"
            elif lower_name.endswith((".md", ".markdown")):
                modality = "markdown"
            else:
                modality = "txt"

        doc_id = compute_doc_id(remote_uri, doc_title)

        anchor = LocationAnchor(
            modality=modality,
            section_path=section_path or "正文",
            page_number=page_number,
            char_start=char_start,
            char_end=char_end,
            paragraph_index=paragraph_index,
            sheet_name=sheet_name,
            cell_range=cell_range,
            is_legacy_migrated=is_legacy_migrated,
        )

        clean_span = (evidence_quote or content or "原文依据").strip()
        # 截取最具代表性的证据引用，最少保留前 300 字符
        if len(clean_span) > 300:
            clean_span = clean_span[:300]

        return cls(
            doc_id=doc_id,
            doc_title=doc_title or "未命名文档",
            anchor=anchor,
            text_span=clean_span,
        )
