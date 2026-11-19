"""
流式轻量文档解析器与 LRU 精读缓存 (Document Extractor & LRU Cache)
核心功能：
1. 内存流解析（PDF, DOCX, XLSX, Markdown, TXT），用完即焚，零本地文件持久化；
2. 纯标准库 zipfile + XML 解析 DOCX（零额外重型依赖）；
3. pypdf 流式抽取 PDF 页面与目录树；
4. openpyxl 只读模式抽取 Excel 工作表与 Markdown 表格；
5. 24 小时 TTL 本地 LRU 章节缓存 (~/.agenthub/cache/cloud_vfs/)。
"""
import io
import json
from pathlib import Path
import re
import struct
import time
from typing import Any
import xml.etree.ElementTree as ET
import zipfile
from html.parser import HTMLParser

from ..core.logging_config import logger

try:
    import pypdf
    HAS_PYPDF = True
except ImportError:
    HAS_PYPDF = False

try:
    import pymupdf as fitz  # PyMuPDF 现代标准包名
    HAS_FITZ = True
except ImportError:
    try:
        import fitz  # 传统回退
        HAS_FITZ = True
    except ImportError:
        HAS_FITZ = False

try:
    import openpyxl
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

_ocr_engine_instance = None


def get_ocr_engine():
    """获取 RapidOCR 单例实例（纯 ONNX 运行，零显存消耗，CPU 毫秒级识别）。"""
    global _ocr_engine_instance
    if _ocr_engine_instance is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
            _ocr_engine_instance = RapidOCR()
            logger.info("RapidOCR 离线 OCR 引擎初始化成功")
        except Exception as e:
            logger.debug(f"RapidOCR 初始化跳过: {e}")
            _ocr_engine_instance = False
    return _ocr_engine_instance if _ocr_engine_instance is not False else None


class DocumentStreamExtractor:
    """流式文档轻量解析器。"""

    MAX_CHUNK_CHARS = 1000

    @staticmethod
    def extract_doc_year(file_name: str, head_text: str = "") -> int:
        """从文件名或首页落款提取生效年份（2020~2039）。"""
        from ..search.temporal_reranker import TemporalMetadataExtractor
        tmeta = TemporalMetadataExtractor.extract_metadata(file_name, head_text)
        return tmeta.doc_year

    @staticmethod
    def extract_topic_cluster_id(file_name: str) -> str:
        """剥离年份、副本序号、版本标识与扩展名，生成同主题族归一化 ID。"""
        from ..search.temporal_reranker import TemporalMetadataExtractor
        return TemporalMetadataExtractor.extract_topic_cluster_id(file_name)

    @staticmethod
    def detect_authority_tier(file_name: str, fallback_tier: int = 2) -> int:
        """判定文档权威等级：1=正式规程/办法，2=通知细则，3=草稿/历史归档。"""
        if any(k in file_name for k in ["草稿", "draft", "旧版", "废弃", "归档", "backup"]):
            return 3
        if any(k in file_name for k in ["规程", "规则", "管理办法", "实施细则", "标准"]):
            return 1
        return fallback_tier

    @classmethod
    def _needs_ocr_fallback(cls, raw_text: str, page: Any = None) -> bool:
        """页面提取质量探针：字符过少或乱码占比过高时触发 OCR。"""
        cleaned = re.sub(r"\s+", "", raw_text)
        if len(cleaned) < 20:
            return True
        valid_chars = re.findall(r"[\u4e00-\u9fa5a-zA-Z0-9]", cleaned)
        if len(cleaned) > 0 and (len(valid_chars) / len(cleaned)) < 0.45:
            return True
        return False

    @classmethod
    def extract_chunks(
        cls, raw_bytes: bytes, filename: str, remote_uri: str, authority_tier: int = 2
    ) -> list[dict[str, Any]]:
        """从二进制字节流中提取章节树切片。

        Args:
            raw_bytes: 原始文件字节流。
            filename: 文件名。
            remote_uri: 溯源远程 URI。
            authority_tier: 权威等级。

        Returns:
            list[dict[str, Any]]: 标准切片字典列表。
        """
        suffix = Path(filename).suffix.lower()

        # 智能魔数与内容嗅探（应对微盘中无后缀的表格或文档）
        if not suffix or suffix not in (".pdf", ".docx", ".doc", ".xlsx", ".xls", ".md", ".txt", ".json", ".csv"):
            if raw_bytes.startswith(b"%PDF-") and HAS_PYPDF:
                suffix = ".pdf"
            elif raw_bytes.startswith(b"PK\x03\x04"):
                try:
                    with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
                        names = zf.namelist()
                        if any(n.startswith("xl/") for n in names):
                            suffix = ".xlsx"
                        elif any(n.startswith("word/") for n in names):
                            suffix = ".docx"
                except Exception:
                    pass

        if suffix == ".pdf" and (HAS_FITZ or HAS_PYPDF):
            return cls._extract_pdf(raw_bytes, filename, remote_uri, authority_tier)
        elif suffix == ".docx":
            if raw_bytes.startswith(b"PK\x03\x04"):
                return cls._extract_docx(raw_bytes, filename, remote_uri, authority_tier)
            return cls._extract_doc(raw_bytes, filename, remote_uri, authority_tier)
        elif suffix == ".doc":
            # 区分真实 DOCX 换皮与二进制 OLE2 / RTF / HTML .doc
            if raw_bytes.startswith(b"PK\x03\x04"):
                return cls._extract_docx(raw_bytes, filename, remote_uri, authority_tier)
            return cls._extract_doc(raw_bytes, filename, remote_uri, authority_tier)
        elif suffix in (".xlsx", ".xls") and HAS_OPENPYXL:
            return cls._extract_xlsx(raw_bytes, filename, remote_uri, authority_tier)
        else:
            return cls._extract_plain_or_markdown(raw_bytes, filename, remote_uri, authority_tier)

    @classmethod
    def _extract_pdf(
        cls, raw_bytes: bytes, filename: str, remote_uri: str, authority_tier: int
    ) -> list[dict[str, Any]]:
        """四级递进式 PDF 鲁棒解析器：原生表格解析 -> 文本层 -> 乱码/稀疏检测 -> RapidOCR 离线兜底。"""
        chunks: list[dict[str, Any]] = []
        from ..search.temporal_reranker import TemporalMetadataExtractor
        tmeta = TemporalMetadataExtractor.extract_metadata(filename)
        doc_year = tmeta.doc_year
        topic_cluster_id = tmeta.topic_cluster_id
        effective_date = tmeta.effective_date
        effective_ts = tmeta.effective_ts
        doc_priority_type = tmeta.doc_priority_type
        effective_tier = cls.detect_authority_tier(filename, authority_tier)

        # 1. 优先使用 PyMuPDF (fitz)
        if HAS_FITZ:
            try:
                doc = fitz.open(stream=raw_bytes, filetype="pdf")
                ocr_used_pages = 0
                for page_idx, page in enumerate(doc, 1):
                    # 1.1 结构化表格抽取为 Markdown
                    md_tables: list[str] = []
                    try:
                        tables = page.find_tables()
                        for tab in (tables or []):
                            md_str = tab.to_markdown()
                            if md_str and md_str.strip():
                                md_tables.append(md_str.strip())
                    except Exception as tab_err:
                        logger.debug(f"[{filename}] P{page_idx} 表格抽取跳过: {tab_err}")

                    # 1.2 页面常规文本层
                    raw_text_val = page.get_text("text")
                    raw_text: str = str(raw_text_val) if raw_text_val is not None else ""

                    # 1.3 质量门控探针：若乱码或文本过少，触发离线 RapidOCR 兜底
                    if cls._needs_ocr_fallback(raw_text, page):
                        ocr_eng = get_ocr_engine()
                        if ocr_eng is not None:
                            try:
                                pix = page.get_pixmap(dpi=150, colorspace=fitz.csGRAY)
                                ocr_res, _ = ocr_eng(pix.tobytes("png"))
                                if ocr_res:
                                    ocr_text = "\n".join([item[1] for item in ocr_res if item[1]])
                                    if len(ocr_text) > len(raw_text):
                                        raw_text = ocr_text
                                        ocr_used_pages += 1
                            except Exception as ocr_err:
                                logger.debug(f"[{filename}] P{page_idx} OCR 兜底异常: {ocr_err}")

                    merged_content = raw_text.strip()
                    if md_tables:
                        merged_content = f"{merged_content}\n\n" + "\n\n".join(md_tables) if merged_content else "\n\n".join(md_tables)

                    if not merged_content:
                        continue

                    if doc_year == 0 and page_idx == 1:
                        doc_year = cls.extract_doc_year(filename, merged_content)

                    # 注入时空上下文强元数据头
                    header = (
                        f"【文档来源】{filename} | "
                        f"【生效年份】{doc_year if doc_year > 0 else '通用'} | "
                        f"【主题族】{topic_cluster_id or filename} | "
                        f"【页码】第 {page_idx} 页\n"
                    )

                    chunks.append({
                        "remote_uri": remote_uri,
                        "title": filename,
                        "section_path": f"Page_{page_idx}",
                        "start_line": page_idx,
                        "end_line": page_idx,
                        "content_text": header + merged_content,
                        "authority_tier": effective_tier,
                        "source_type": remote_uri.split("://")[0] if "://" in remote_uri else "cloud",
                        "doc_year": doc_year,
                        "effective_date": effective_date,
                        "effective_ts": effective_ts,
                        "doc_priority_type": doc_priority_type,
                        "topic_cluster_id": topic_cluster_id,
                        "is_latest_in_cluster": 1,
                    })

                if chunks:
                    logger.info(
                        f"PyMuPDF 成功解析 PDF [{filename}]: 总页数={len(doc)}, 生成切片={len(chunks)}, OCR兜底={ocr_used_pages}页"
                    )
                    return chunks
            except Exception as fitz_err:
                logger.warning(f"PyMuPDF 解析 [{filename}] 失败，尝试回退至 pypdf: {fitz_err}")

        # 2. 回退机制：若无 fitz 或 fitz 解析报错，使用 pypdf
        if HAS_PYPDF:
            try:
                reader = pypdf.PdfReader(io.BytesIO(raw_bytes))
                for page_idx, page in enumerate(reader.pages, 1):
                    text = page.extract_text() or ""
                    text = text.strip()
                    if not text:
                        continue
                    if doc_year == 0 and page_idx == 1:
                        doc_year = cls.extract_doc_year(filename, text)

                    header = (
                        f"【文档来源】{filename} | "
                        f"【生效年份】{doc_year if doc_year > 0 else '通用'} | "
                        f"【主题族】{topic_cluster_id or filename} | "
                        f"【页码】第 {page_idx} 页\n"
                    )
                    chunks.append({
                        "remote_uri": remote_uri,
                        "title": filename,
                        "section_path": f"Page_{page_idx}",
                        "start_line": page_idx,
                        "end_line": page_idx,
                        "content_text": header + text,
                        "authority_tier": effective_tier,
                        "source_type": remote_uri.split("://")[0] if "://" in remote_uri else "cloud",
                        "doc_year": doc_year,
                        "effective_date": effective_date,
                        "effective_ts": effective_ts,
                        "doc_priority_type": doc_priority_type,
                        "topic_cluster_id": topic_cluster_id,
                        "is_latest_in_cluster": 1,
                    })
            except Exception as e:
                logger.warning(f"流式解析 PDF 失败 ({filename}): {e}")

        # 补丁一：PDF 提取后切片质量门控，过滤无有效字或乱码切片
        from ..search.text_quality_gate import TextQualityGate
        valid_chunks: list[dict[str, Any]] = []
        for c in chunks:
            c_text = c.get("content_text", "")
            is_ok, _ = TextQualityGate.is_chunk_acceptable(c_text, min_body_chars=10)
            if is_ok:
                valid_chunks.append(c)
            else:
                logger.debug(f"[{filename}] PDF 页面质量未达标，丢弃切片: {c.get('section_path')}")

        return valid_chunks

    @classmethod
    def _extract_docx(
        cls, raw_bytes: bytes, filename: str, remote_uri: str, authority_tier: int
    ) -> list[dict[str, Any]]:
        """通过标准库 zipfile 解析 DOCX word/document.xml。"""
        chunks: list[dict[str, Any]] = []
        from ..search.temporal_reranker import TemporalMetadataExtractor
        tmeta = TemporalMetadataExtractor.extract_metadata(filename)
        doc_year = tmeta.doc_year
        topic_cluster_id = tmeta.topic_cluster_id
        effective_date = tmeta.effective_date
        effective_ts = tmeta.effective_ts
        doc_priority_type = tmeta.doc_priority_type
        effective_tier = cls.detect_authority_tier(filename, authority_tier)

        try:
            with zipfile.ZipFile(io.BytesIO(raw_bytes)) as z:
                if "word/document.xml" not in z.namelist():
                    return []
                xml_content = z.read("word/document.xml")
                root = ET.fromstring(xml_content)

                # 命名空间 w: http://schemas.openxmlformats.org/wordprocessingml/2006/main
                ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
                paragraphs: list[str] = []

                for p in root.findall(".//w:p", ns):
                    texts = [t.text for t in p.findall(".//w:t", ns) if t.text]
                    p_text = "".join(texts).strip()
                    if p_text:
                        paragraphs.append(p_text)

                if paragraphs:
                    if doc_year == 0:
                        doc_year = cls.extract_doc_year(filename, "\n".join(paragraphs[:5]))

                    # 每 5 个段落或 800 字符聚合为一个切片
                    cur_group: list[str] = []
                    cur_len = 0
                    part_idx = 1
                    for p in paragraphs:
                        cur_group.append(p)
                        cur_len += len(p)
                        if cur_len >= cls.MAX_CHUNK_CHARS or len(cur_group) >= 5:
                            body = "\n\n".join(cur_group)
                            header = (
                                f"【文档来源】{filename} | "
                                f"【生效年份】{doc_year if doc_year > 0 else '通用'} | "
                                f"【主题族】{topic_cluster_id or filename} | "
                                f"【分段】第 {part_idx} 节\n"
                            )
                            chunks.append({
                                "remote_uri": remote_uri,
                                "title": filename,
                                "section_path": f"Part_{part_idx}",
                                "start_line": part_idx,
                                "end_line": part_idx + len(cur_group),
                                "content_text": header + body,
                                "authority_tier": effective_tier,
                                "source_type": remote_uri.split("://")[0] if "://" in remote_uri else "cloud",
                                "doc_year": doc_year,
                                "topic_cluster_id": topic_cluster_id,
                                "is_latest_in_cluster": 1,
                            })
                            part_idx += 1
                            cur_group = []
                            cur_len = 0

                    if cur_group:
                        body = "\n\n".join(cur_group)
                        header = (
                            f"【文档来源】{filename} | "
                            f"【生效年份】{doc_year if doc_year > 0 else '通用'} | "
                            f"【主题族】{topic_cluster_id or filename} | "
                            f"【分段】第 {part_idx} 节\n"
                        )
                        chunks.append({
                            "remote_uri": remote_uri,
                            "title": filename,
                            "section_path": f"Part_{part_idx}",
                            "start_line": part_idx,
                            "end_line": part_idx + len(cur_group),
                            "content_text": header + body,
                            "authority_tier": effective_tier,
                            "source_type": remote_uri.split("://")[0] if "://" in remote_uri else "cloud",
                            "doc_year": doc_year,
                            "topic_cluster_id": topic_cluster_id,
                            "is_latest_in_cluster": 1,
                        })
        except Exception as e:
            logger.warning(f"流式解析 DOCX 异常 ({filename})，尝试兼容解析二进制 DOC: {e}")
            return cls._extract_doc(raw_bytes, filename, remote_uri, authority_tier)
        return chunks

    @classmethod
    def _extract_doc(
        cls, raw_bytes: bytes, filename: str, remote_uri: str, authority_tier: int
    ) -> list[dict[str, Any]]:
        """流式解析旧版 Word 97-2003 (.doc)、RTF 或 HTML 导出的二进制复合文档。"""
        chunks: list[dict[str, Any]] = []
        from ..search.temporal_reranker import TemporalMetadataExtractor
        tmeta = TemporalMetadataExtractor.extract_metadata(filename)
        doc_year = tmeta.doc_year
        topic_cluster_id = tmeta.topic_cluster_id
        effective_date = tmeta.effective_date
        effective_ts = tmeta.effective_ts
        doc_priority_type = tmeta.doc_priority_type
        effective_tier = cls.detect_authority_tier(filename, authority_tier)

        try:
            paragraphs: list[str] = []

            # 1. 检查是否为 RTF 格式
            if raw_bytes.lstrip().startswith(b"{\\rtf"):
                paragraphs = cls._extract_rtf_paragraphs(raw_bytes)
            # 2. 检查是否为 HTML / XML 伪装的 .doc
            elif raw_bytes.lstrip()[:100].lower().startswith((b"<html", b"<!doctype", b"<?xml")):
                paragraphs = cls._extract_html_paragraphs(raw_bytes)
            # 3. 检查是否为标准 OLE2 CFBF 复合格式 (Word 97-2003)
            elif raw_bytes.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
                word_stream = cls._extract_ole2_word_stream(raw_bytes)
                if word_stream:
                    paragraphs = cls._extract_text_from_binary_stream(word_stream)
                if not paragraphs:
                    paragraphs = cls._extract_text_from_binary_stream(raw_bytes)
            # 4. 其他未知二进制 .doc，直接尝试多编码字符抽取
            else:
                paragraphs = cls._extract_text_from_binary_stream(raw_bytes)

            if not paragraphs:
                return []

            full_extracted_text = "\n\n".join(paragraphs).strip()
            # 补丁一：二进制 .doc 文本质量熔断门控审查，严禁乱码入库
            from ..search.text_quality_gate import TextQualityGate
            is_ok, reason = TextQualityGate.is_document_acceptable(full_extracted_text)
            if not is_ok:
                logger.warning(
                    f"[{filename}] 二进制 DOC 文本质量门控拦截 ({reason})，中止切片入库。"
                    "建议使用 Word/WPS 或 LibreOffice 将该文件转存为 .docx 或 .pdf 后重新上传。"
                )
                return []

            if doc_year == 0:
                doc_year = cls.extract_doc_year(filename, "\n".join(paragraphs[:5]))

            cur_group: list[str] = []
            cur_len = 0
            part_idx = 1
            for p in paragraphs:
                cur_group.append(p)
                cur_len += len(p)
                if cur_len >= cls.MAX_CHUNK_CHARS or len(cur_group) >= 5:
                    body = "\n\n".join(cur_group)
                    header = (
                        f"【文档来源】{filename} | "
                        f"【生效年份】{doc_year if doc_year > 0 else '通用'} | "
                        f"【主题族】{topic_cluster_id or filename} | "
                        f"【分段】第 {part_idx} 节\n"
                    )
                    chunks.append({
                        "remote_uri": remote_uri,
                        "title": filename,
                        "section_path": f"Part_{part_idx}",
                        "start_line": part_idx,
                        "end_line": part_idx + len(cur_group),
                        "content_text": header + body,
                        "authority_tier": effective_tier,
                        "source_type": remote_uri.split("://")[0] if "://" in remote_uri else "cloud",
                        "doc_year": doc_year,
                        "effective_date": effective_date,
                        "effective_ts": effective_ts,
                        "doc_priority_type": doc_priority_type,
                        "topic_cluster_id": topic_cluster_id,
                        "is_latest_in_cluster": 1,
                    })
                    part_idx += 1
                    cur_group = []
                    cur_len = 0

            if cur_group:
                body = "\n\n".join(cur_group)
                header = (
                    f"【文档来源】{filename} | "
                    f"【生效年份】{doc_year if doc_year > 0 else '通用'} | "
                    f"【主题族】{topic_cluster_id or filename} | "
                    f"【分段】第 {part_idx} 节\n"
                )
                chunks.append({
                    "remote_uri": remote_uri,
                    "title": filename,
                    "section_path": f"Part_{part_idx}",
                    "start_line": part_idx,
                    "end_line": part_idx + len(cur_group),
                    "content_text": header + body,
                    "authority_tier": effective_tier,
                    "source_type": remote_uri.split("://")[0] if "://" in remote_uri else "cloud",
                    "doc_year": doc_year,
                    "topic_cluster_id": topic_cluster_id,
                    "is_latest_in_cluster": 1,
                })
        except Exception as e:
            logger.warning(f"流式解析 DOC 失败 ({filename}): {e}")
        return chunks

    @classmethod
    def _extract_ole2_word_stream(cls, data: bytes) -> bytes | None:
        """从 OLE2 复合二进制文件中提取 WordDocument 流。"""
        try:
            if not data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1") or len(data) < 512:
                return None
            sector_size = 1 << struct.unpack_from("<H", data, 30)[0]
            if sector_size not in (512, 4096):
                sector_size = 512

            num_fat_sectors = struct.unpack_from("<I", data, 44)[0]
            fat_start_sectors: list[int] = []
            for i in range(min(num_fat_sectors, 109)):
                sec = struct.unpack_from("<I", data, 76 + i * 4)[0]
                if sec < 0xFFFFFFFA:
                    fat_start_sectors.append(sec)

            fat: list[int] = []
            for fat_sec in fat_start_sectors:
                offset = (fat_sec + 1) * sector_size
                sec_bytes = data[offset : offset + sector_size]
                entries = struct.unpack(f"<{len(sec_bytes) // 4}I", sec_bytes)
                fat.extend(entries)

            dir_start_sec = struct.unpack_from("<I", data, 48)[0]
            dir_sectors: list[int] = []
            curr = dir_start_sec
            visited = set()
            while curr < 0xFFFFFFFA and curr < len(fat) and curr not in visited:
                visited.add(curr)
                dir_sectors.append(curr)
                curr = fat[curr]

            dir_bytes = b"".join(
                data[(s + 1) * sector_size : (s + 2) * sector_size] for s in dir_sectors
            )

            for i in range(0, len(dir_bytes), 128):
                entry = dir_bytes[i : i + 128]
                if len(entry) < 128:
                    break
                name_len = struct.unpack_from("<H", entry, 64)[0]
                if name_len <= 2:
                    continue
                try:
                    name = entry[: name_len - 2].decode("utf-16le", errors="ignore")
                except Exception:
                    continue

                elem_type = entry[66]  # 2 = stream
                if elem_type == 2 and name == "WordDocument":
                    start_sec = struct.unpack_from("<I", entry, 116)[0]
                    size = struct.unpack_from("<I", entry, 120)[0]
                    chain: list[int] = []
                    curr = start_sec
                    stream_visited = set()
                    while curr < 0xFFFFFFFA and curr < len(fat) and curr not in stream_visited:
                        stream_visited.add(curr)
                        chain.append(curr)
                        curr = fat[curr]
                    return b"".join(
                        data[(s + 1) * sector_size : (s + 2) * sector_size] for s in chain
                    )[:size]
        except Exception as e:
            logger.debug(f"OLE2 WordDocument 解析回退: {e}")
        return None

    @classmethod
    def _extract_text_from_binary_stream(cls, raw_bytes: bytes) -> list[str]:
        """从二进制字节流中智能抽取 UTF-16LE / GBK 连续文本段落。"""
        # 1. 尝试匹配 UTF-16LE 文本 (Word 97-2003 最核心的正文存储格式)
        utf16_pattern = re.compile(
            b"(?:[\x20-\x7e\r\n\t]\x00|[\x00-\xff][\x4e-\x9f]|[\x00-\xff][\x30-\x33]|[\x00-\xff][\xff]){3,}"
        )
        utf16_blocks: list[str] = []
        for match in utf16_pattern.finditer(raw_bytes):
            chunk = match.group(0)
            try:
                text = chunk.decode("utf-16le", errors="ignore").strip()
                if len(text) >= 2:
                    utf16_blocks.append(text)
            except Exception:
                pass

        # 2. 尝试匹配 GBK / CP936 文本
        gbk_pattern = re.compile(b"(?:[\x81-\xfe][\x40-\xfe]|[\x20-\x7e\r\n\t]){4,}")
        gbk_blocks: list[str] = []
        for match in gbk_pattern.finditer(raw_bytes):
            chunk = match.group(0)
            try:
                text = chunk.decode("gbk", errors="ignore").strip()
                if len(text) >= 2:
                    gbk_blocks.append(text)
            except Exception:
                pass

        # 统计中文字符数进行优选
        utf16_chinese_count = sum(1 for b in utf16_blocks for c in b if "\u4e00" <= c <= "\u9fff")
        gbk_chinese_count = sum(1 for b in gbk_blocks for c in b if "\u4e00" <= c <= "\u9fff")

        if utf16_chinese_count >= gbk_chinese_count and utf16_blocks:
            selected_blocks = utf16_blocks
        elif gbk_blocks:
            selected_blocks = gbk_blocks
        else:
            selected_blocks = utf16_blocks

        # 常见 Word 样式与字体元数据噪音过滤表
        noise_names = {
            "Normal", "Heading", "Default Paragraph Font", "Table Grid",
            "Times New Roman", "SimSun", "Songti", "Calibri", "Arial",
            "Microsoft YaHei", "SimHei", "FangSong", "KaiTi", "Body Text",
        }

        clean_paragraphs: list[str] = []
        for block in selected_blocks:
            # 去除低级控制符
            block = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x07]", "", block).strip()
            if not block or block in noise_names:
                continue
            for line in block.splitlines():
                line = line.strip()
                if line and line not in noise_names and len(line) > 1:
                    clean_paragraphs.append(line)

        return clean_paragraphs

    @classmethod
    def _extract_rtf_paragraphs(cls, raw_bytes: bytes) -> list[str]:
        """从 RTF 格式字节流中解析纯文本段落。"""
        try:
            content = ""
            for enc in ("utf-8", "gb18030", "gbk", "cp1252", "latin-1"):
                try:
                    content = raw_bytes.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            if not content:
                content = raw_bytes.decode("utf-8", errors="replace")

            # 移除字体表、颜色表、样式表等头信息
            content = re.sub(r"\{\\fonttbl[^}]*\}", "", content)
            content = re.sub(r"\{\\colortbl[^}]*\}", "", content)
            content = re.sub(r"\{\\stylesheet[^}]*\}", "", content)

            # 替换十六进制字节 \'xx
            def hex_repl(match):
                hex_str = match.group(1)
                try:
                    return bytes.fromhex(hex_str).decode("gbk", errors="ignore")
                except Exception:
                    return ""

            content = re.sub(r"\\\'([0-9a-fA-F]{2})", hex_repl, content)

            # 替换 Unicode 转义 \u12345?
            def unicode_repl(match):
                code = int(match.group(1))
                if code < 0:
                    code += 65536
                try:
                    return chr(code)
                except Exception:
                    return ""

            content = re.sub(r"\\u(-?[0-9]+)\??", unicode_repl, content)

            # 换行与分段命令映射
            content = re.sub(r"\\(par|line)\b", "\n", content)

            # 移除 RTF 命令控制字与大括号
            content = re.sub(r"\\[a-zA-Z]+-?[0-9]*\s?", " ", content)
            content = re.sub(r"[{}\\]", "", content)

            lines = [line.strip() for line in content.splitlines() if line.strip()]
            return lines
        except Exception as e:
            logger.debug(f"RTF 解析异常: {e}")
            return []

    @classmethod
    def _extract_html_paragraphs(cls, raw_bytes: bytes) -> list[str]:
        """从 HTML 导出的伪 doc 中解析段落。"""
        class _TextExtractHTMLParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.text_parts: list[str] = []
                self.ignore = False

            def handle_starttag(self, tag, attrs):
                if tag.lower() in ("script", "style"):
                    self.ignore = True

            def handle_endtag(self, tag):
                if tag.lower() in ("script", "style"):
                    self.ignore = False

            def handle_data(self, data):
                if not self.ignore:
                    txt = data.strip()
                    if txt:
                        self.text_parts.append(txt)

        try:
            html_str = raw_bytes.decode("utf-8", errors="replace")
            parser = _TextExtractHTMLParser()
            parser.feed(html_str)
            return parser.text_parts
        except Exception as e:
            logger.debug(f"HTML doc 解析异常: {e}")
            return []

    @classmethod
    def _extract_xlsx(
        cls, raw_bytes: bytes, filename: str, remote_uri: str, authority_tier: int
    ) -> list[dict[str, Any]]:
        """通过 openpyxl 只读流式解析 Excel 工作表并转为 Markdown 表格。"""
        chunks: list[dict[str, Any]] = []
        from ..search.temporal_reranker import TemporalMetadataExtractor
        tmeta = TemporalMetadataExtractor.extract_metadata(filename)
        doc_year = tmeta.doc_year
        topic_cluster_id = tmeta.topic_cluster_id
        effective_date = tmeta.effective_date
        effective_ts = tmeta.effective_ts
        doc_priority_type = tmeta.doc_priority_type
        effective_tier = cls.detect_authority_tier(filename, authority_tier)

        try:
            wb = openpyxl.load_workbook(io.BytesIO(raw_bytes), read_only=True, data_only=True)
            for sheetname in wb.sheetnames:
                ws = wb[sheetname]
                rows = list(ws.iter_rows(values_only=True))
                if not rows:
                    continue

                # 转换为 Markdown 表格
                table_lines = [f"### 工作表: {sheetname}"]
                header_row = [str(cell) if cell is not None else "" for cell in rows[0]]
                if any(header_row):
                    table_lines.append("| " + " | ".join(header_row) + " |")
                    table_lines.append("| " + " | ".join(["---"] * len(header_row)) + " |")

                for r in rows[1:60]:  # 截取前 60 行
                    r_str = [str(cell) if cell is not None else "" for cell in r]
                    if any(r_str):
                        table_lines.append("| " + " | ".join(r_str) + " |")

                table_text = "\n".join(table_lines)
                header = (
                    f"【文档来源】{filename} | "
                    f"【生效年份】{doc_year if doc_year > 0 else '通用'} | "
                    f"【主题族】{topic_cluster_id or filename} | "
                    f"【工作表】{sheetname}\n"
                )
                chunks.append({
                    "remote_uri": remote_uri,
                    "title": filename,
                    "section_path": sheetname,
                    "start_line": 1,
                    "end_line": len(rows),
                    "content_text": header + table_text,
                    "authority_tier": effective_tier,
                    "source_type": remote_uri.split("://")[0] if "://" in remote_uri else "cloud",
                    "doc_year": doc_year,
                    "topic_cluster_id": topic_cluster_id,
                    "is_latest_in_cluster": 1,
                })
        except Exception as e:
            logger.warning(f"流式解析 Excel 失败 ({filename}): {e}")
        return chunks

    @classmethod
    def _extract_plain_or_markdown(
        cls, raw_bytes: bytes, filename: str, remote_uri: str, authority_tier: int
    ) -> list[dict[str, Any]]:
        """纯文本或 Markdown 切分，采用 ParentChildASTChunker 实施父子分层与表格原子保护。"""
        # 1. 采用 charset-normalizer 自动探测编码并解码，防止 GBK / GB18030 / UTF-16 强行转 UTF-8 产生大量替换符乱码
        content = ""
        try:
            import charset_normalizer
            best_match = charset_normalizer.from_bytes(raw_bytes).best()
            if best_match and best_match.encoding:
                content = str(best_match)
        except Exception as e:
            logger.debug(f"charset_normalizer 探测异常: {e}")

        if not content:
            for enc in ("utf-8", "gb18030", "gbk", "utf-16", "big5"):
                try:
                    content = raw_bytes.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
        if not content:
            content = raw_bytes.decode("utf-8", errors="replace")

        # 2. TextQualityGate 文档级质量熔断审查
        from ..search.text_quality_gate import TextQualityGate
        is_ok, reason = TextQualityGate.is_document_acceptable(content)
        if not is_ok:
            logger.warning(f"[{filename}] 文档质量熔断未通过: {reason}，拦截入库")
            return []

        from ..search.temporal_reranker import TemporalMetadataExtractor
        tmeta = TemporalMetadataExtractor.extract_metadata(filename, content[:600])
        effective_tier = cls.detect_authority_tier(filename, authority_tier)

        try:
            from ..search.ast_chunker import ParentChildASTChunker
            h_chunks = ParentChildASTChunker.chunk_document(
                text=content,
                filename=filename,
                remote_uri=remote_uri,
                authority_tier=effective_tier,
                doc_year=tmeta.doc_year,
                effective_date=tmeta.effective_date,
                effective_ts=tmeta.effective_ts,
                doc_priority_type=tmeta.doc_priority_type,
                topic_cluster_id=tmeta.topic_cluster_id,
                is_latest_in_cluster=1,
            )
            if h_chunks:
                return [
                    {
                        "id": hc.id,
                        "chunk_role": hc.chunk_role,
                        "parent_chunk_id": hc.parent_chunk_id,
                        "remote_uri": hc.remote_uri,
                        "title": hc.title,
                        "section_path": hc.section_path,
                        "start_line": 1,
                        "end_line": 1,
                        "content_text": hc.content_text,
                        "authority_tier": hc.authority_tier,
                        "source_type": remote_uri.split("://")[0] if "://" in remote_uri else "cloud",
                        "doc_year": hc.doc_year,
                        "effective_date": hc.effective_date,
                        "effective_ts": hc.effective_ts,
                        "doc_priority_type": hc.doc_priority_type,
                        "topic_cluster_id": hc.topic_cluster_id,
                        "is_latest_in_cluster": hc.is_latest_in_cluster,
                        "skip_hype": hc.skip_hype,
                    }
                    for hc in h_chunks
                ]
        except Exception as e:
            logger.warning(f"ParentChildASTChunker 切分异常，回退至常规切分: {e}")

        # 回退常规切分
        chunks: list[dict[str, Any]] = []
        doc_year = tmeta.doc_year
        topic_cluster_id = tmeta.topic_cluster_id
        effective_date = tmeta.effective_date
        effective_ts = tmeta.effective_ts
        doc_priority_type = tmeta.doc_priority_type
        lines = content.splitlines(keepends=True)
        start = 0
        text_len = len(content)
        part_idx = 1
        while start < text_len:
            end = min(start + cls.MAX_CHUNK_CHARS, text_len)
            seg = content[start:end].strip()
            if seg:
                header = (
                    f"【文档来源】{filename} | "
                    f"【生效年份】{doc_year if doc_year > 0 else '通用'} | "
                    f"【主题族】{topic_cluster_id or filename} | "
                    f"【分段】第 {part_idx} 节\n"
                )
                chunks.append({
                    "remote_uri": remote_uri,
                    "title": filename,
                    "section_path": f"Part_{part_idx}",
                    "start_line": 1,
                    "end_line": len(lines),
                    "content_text": header + seg,
                    "authority_tier": effective_tier,
                    "source_type": remote_uri.split("://")[0] if "://" in remote_uri else "cloud",
                    "doc_year": doc_year,
                    "effective_date": effective_date,
                    "effective_ts": effective_ts,
                    "doc_priority_type": doc_priority_type,
                    "topic_cluster_id": topic_cluster_id,
                    "is_latest_in_cluster": 1,
                })
                part_idx += 1
            if end >= text_len:
                break
            start += cls.MAX_CHUNK_CHARS - 120
        return chunks


class CloudWikiLRUCache:
    """按需精读章节的 24 小时 TTL 本地 LRU 缓存。"""

    CACHE_DIR = Path.home() / ".agenthub" / "cache" / "cloud_vfs"
    TTL_SECONDS = 86400  # 24 小时

    @classmethod
    def get_section_cache(cls, remote_uri: str, section_path: str) -> str | None:
        """读取未过期的章节缓存。"""
        cls.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        import hashlib
        cache_key = hashlib.md5(f"{remote_uri}:{section_path}".encode("utf-8")).hexdigest()
        cache_file = cls.CACHE_DIR / f"{cache_key}.json"

        if not cache_file.exists():
            return None

        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            if time.time() - data.get("cached_at", 0) < cls.TTL_SECONDS:
                return data.get("content")
        except Exception:
            pass
        return None

    @classmethod
    def set_section_cache(cls, remote_uri: str, section_path: str, content: str) -> None:
        """写入章节缓存。"""
        cls.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        import hashlib
        cache_key = hashlib.md5(f"{remote_uri}:{section_path}".encode("utf-8")).hexdigest()
        cache_file = cls.CACHE_DIR / f"{cache_key}.json"
        try:
            data = {
                "remote_uri": remote_uri,
                "section_path": section_path,
                "content": content,
                "cached_at": time.time(),
            }
            cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.warning(f"写入 CloudWiki 缓存失败: {e}")
