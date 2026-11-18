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
import time
from typing import Any
import xml.etree.ElementTree as ET
import zipfile

from ..core.logging_config import logger

try:
    import pypdf
    HAS_PYPDF = True
except ImportError:
    HAS_PYPDF = False

try:
    import openpyxl
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False


class DocumentStreamExtractor:
    """流式文档轻量解析器。"""

    MAX_CHUNK_CHARS = 1000

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

        if suffix == ".pdf" and HAS_PYPDF:
            return cls._extract_pdf(raw_bytes, filename, remote_uri, authority_tier)
        elif suffix in (".docx", ".doc"):
            return cls._extract_docx(raw_bytes, filename, remote_uri, authority_tier)
        elif suffix in (".xlsx", ".xls") and HAS_OPENPYXL:
            return cls._extract_xlsx(raw_bytes, filename, remote_uri, authority_tier)
        else:
            return cls._extract_plain_or_markdown(raw_bytes, filename, remote_uri, authority_tier)

    @classmethod
    def _extract_pdf(
        cls, raw_bytes: bytes, filename: str, remote_uri: str, authority_tier: int
    ) -> list[dict[str, Any]]:
        """流式解析 PDF 页面。"""
        chunks: list[dict[str, Any]] = []
        try:
            reader = pypdf.PdfReader(io.BytesIO(raw_bytes))
            for page_idx, page in enumerate(reader.pages, 1):
                text = page.extract_text() or ""
                text = text.strip()
                if not text:
                    continue
                header = f"# Doc: {filename} | Page {page_idx}\n"
                chunks.append({
                    "remote_uri": remote_uri,
                    "title": filename,
                    "section_path": f"Page_{page_idx}",
                    "start_line": page_idx,
                    "end_line": page_idx,
                    "content_text": header + text,
                    "authority_tier": authority_tier,
                    "source_type": remote_uri.split("://")[0] if "://" in remote_uri else "cloud",
                })
        except Exception as e:
            logger.warning(f"流式解析 PDF 失败 ({filename}): {e}")
        return chunks

    @classmethod
    def _extract_docx(
        cls, raw_bytes: bytes, filename: str, remote_uri: str, authority_tier: int
    ) -> list[dict[str, Any]]:
        """通过标准库 zipfile 解析 DOCX word/document.xml。"""
        chunks: list[dict[str, Any]] = []
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
                    # 每 5 个段落或 800 字符聚合为一个切片
                    cur_group: list[str] = []
                    cur_len = 0
                    part_idx = 1
                    for p in paragraphs:
                        cur_group.append(p)
                        cur_len += len(p)
                        if cur_len >= cls.MAX_CHUNK_CHARS or len(cur_group) >= 5:
                            body = "\n\n".join(cur_group)
                            chunks.append({
                                "remote_uri": remote_uri,
                                "title": filename,
                                "section_path": f"Part_{part_idx}",
                                "start_line": part_idx,
                                "end_line": part_idx + len(cur_group),
                                "content_text": f"# Doc: {filename} (Part {part_idx})\n" + body,
                                "authority_tier": authority_tier,
                                "source_type": remote_uri.split("://")[0] if "://" in remote_uri else "cloud",
                            })
                            part_idx += 1
                            cur_group = []
                            cur_len = 0

                    if cur_group:
                        body = "\n\n".join(cur_group)
                        chunks.append({
                            "remote_uri": remote_uri,
                            "title": filename,
                            "section_path": f"Part_{part_idx}",
                            "start_line": part_idx,
                            "end_line": part_idx + len(cur_group),
                            "content_text": f"# Doc: {filename} (Part {part_idx})\n" + body,
                            "authority_tier": authority_tier,
                            "source_type": remote_uri.split("://")[0] if "://" in remote_uri else "cloud",
                        })
        except Exception as e:
            logger.warning(f"流式解析 DOCX 失败 ({filename}): {e}")
        return chunks

    @classmethod
    def _extract_xlsx(
        cls, raw_bytes: bytes, filename: str, remote_uri: str, authority_tier: int
    ) -> list[dict[str, Any]]:
        """通过 openpyxl 只读流式解析 Excel 工作表并转为 Markdown 表格。"""
        chunks: list[dict[str, Any]] = []
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

                for r in rows[1:50]:  # 截取前 50 行
                    r_str = [str(cell) if cell is not None else "" for cell in r]
                    if any(r_str):
                        table_lines.append("| " + " | ".join(r_str) + " |")

                table_text = "\n".join(table_lines)
                chunks.append({
                    "remote_uri": remote_uri,
                    "title": filename,
                    "section_path": sheetname,
                    "start_line": 1,
                    "end_line": len(rows),
                    "content_text": f"# Sheet: {filename} | {sheetname}\n" + table_text,
                    "authority_tier": authority_tier,
                    "source_type": remote_uri.split("://")[0] if "://" in remote_uri else "cloud",
                })
        except Exception as e:
            logger.warning(f"流式解析 Excel 失败 ({filename}): {e}")
        return chunks

    @classmethod
    def _extract_plain_or_markdown(
        cls, raw_bytes: bytes, filename: str, remote_uri: str, authority_tier: int
    ) -> list[dict[str, Any]]:
        """纯文本或 Markdown 切分。"""
        content = raw_bytes.decode("utf-8", errors="replace")
        chunks: list[dict[str, Any]] = []
        lines = content.splitlines(keepends=True)
        # 滑动窗口切分
        start = 0
        text_len = len(content)
        part_idx = 1
        while start < text_len:
            end = min(start + cls.MAX_CHUNK_CHARS, text_len)
            seg = content[start:end].strip()
            if seg:
                chunks.append({
                    "remote_uri": remote_uri,
                    "title": filename,
                    "section_path": f"Part_{part_idx}",
                    "start_line": 1,
                    "end_line": len(lines),
                    "content_text": f"# Doc: {filename} (Part {part_idx})\n" + seg,
                    "authority_tier": authority_tier,
                    "source_type": remote_uri.split("://")[0] if "://" in remote_uri else "cloud",
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
