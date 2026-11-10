"""
对话附件安全校验、魔数探测、内容摘要提取与路径锚点服务 (AttachmentService)

核心职责：
1. 前后端统一的白名单与大小限制门禁：仅放行常见图片、办公文档、纯文本/源码文件，拒收并说明原因。
2. 二进制混淆与伪装后缀防护：校验文件头魔数（PNG/JPG/GIF/WEBP/PDF/ZIP）与空字节二进制检测。
3. 零外部强制依赖的多格式内容提取：纯文本容错解码、.xlsx (openpyxl 优先 + zipfile/xml 兜底)、.docx/.pptx (标准库 zipfile/xml)。
4. 跨步/跨轮路径防失忆：通过标准化 <agenthub_attachments> 锚点持久化物理绝对路径，并在历史上下文回放时展开，根治 `./attachments FileNotFoundError`。
"""
import hashlib
import io
import json
import os
import re
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote
import xml.etree.ElementTree as ET

from ..core.config import settings
from ..core.logging_config import logger


class AttachmentValidationError(Exception):
    """附件上传校验异常（携带明确的 HTTP 状态码与面向用户的中文说明）"""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class AttachmentService:
    """对话附件校验与解析服务"""

    MAX_ATTACHMENTS_PER_MESSAGE = 5
    MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB
    MAX_PDF_BYTES = 10 * 1024 * 1024    # 10 MB
    MAX_OFFICE_BYTES = 5 * 1024 * 1024  # 5 MB
    MAX_TEXT_BYTES = 5 * 1024 * 1024    # 5 MB
    MAX_INLINE_CHARS = 16000

    ANCHOR_PATTERN = re.compile(
        r"\n*<agenthub_attachments>([\s\S]*?)</agenthub_attachments>\s*$",
        re.IGNORECASE,
    )

    IMAGE_EXTENSIONS: dict[str, str] = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }

    PDF_EXTENSIONS: dict[str, str] = {
        ".pdf": "application/pdf",
    }

    OFFICE_EXTENSIONS: dict[str, str] = {
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }

    TEXT_EXTENSIONS: dict[str, str] = {
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".markdown": "text/markdown",
        ".log": "text/plain",
        ".csv": "text/csv",
        ".tsv": "text/tab-separated-values",
        ".json": "application/json",
        ".jsonl": "application/x-ndjson",
        ".xml": "application/xml",
        ".yaml": "application/yaml",
        ".yml": "application/yaml",
        ".toml": "application/toml",
        ".ini": "text/plain",
        ".cfg": "text/plain",
        ".conf": "text/plain",
        ".svg": "image/svg+xml",
        ".py": "text/x-python",
        ".js": "text/javascript",
        ".jsx": "text/jsx",
        ".ts": "text/typescript",
        ".tsx": "text/tsx",
        ".mjs": "text/javascript",
        ".cjs": "text/javascript",
        ".html": "text/html",
        ".htm": "text/html",
        ".css": "text/css",
        ".scss": "text/x-scss",
        ".less": "text/x-less",
        ".sql": "application/sql",
        ".sh": "text/x-shellscript",
        ".bash": "text/x-shellscript",
        ".ps1": "text/plain",
        ".bat": "text/plain",
        ".cmd": "text/plain",
        ".java": "text/x-java-source",
        ".go": "text/x-go",
        ".rs": "text/rust",
        ".c": "text/x-c",
        ".cpp": "text/x-c++",
        ".h": "text/x-c",
        ".hpp": "text/x-c++",
        ".cs": "text/plain",
        ".php": "text/x-php",
        ".rb": "text/x-ruby",
        ".vue": "text/plain",
        ".svelte": "text/plain",
    }

    SPECIAL_TEXT_FILENAMES: set[str] = {"dockerfile", "makefile"}

    LEGACY_OFFICE_EXTENSIONS: set[str] = {".doc", ".xls", ".ppt"}
    ARCHIVE_EXTENSIONS: set[str] = {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".tgz"}
    EXECUTABLE_EXTENSIONS: set[str] = {".exe", ".msi", ".dll", ".so", ".dylib", ".apk", ".dmg", ".iso", ".app", ".com"}
    UNSUPPORTED_IMAGE_EXTENSIONS: set[str] = {".bmp", ".tiff", ".tif", ".ico", ".heic", ".heif", ".avif", ".raw", ".psd", ".ai", ".sketch", ".fig"}
    MEDIA_OR_BINARY_EXTENSIONS: set[str] = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".wmv", ".mp3", ".wav", ".flac", ".aac", ".ogg", ".db", ".sqlite", ".sqlite3", ".bin", ".dat", ".pyc"}

    @staticmethod
    def sanitize_filename(raw_name: str | None) -> str:
        """剥离目录路径与危险字符，生成安全的展示与落盘文件名"""
        text = (raw_name or "").replace("\\", "/").strip()
        base = text.split("/")[-1] if text else ""
        cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", base).strip(" .")
        return cleaned or f"attachment_{id(raw_name)}"

    @staticmethod
    def format_size_str(size_bytes: int) -> str:
        """格式化字节数为可读字符串"""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        kb = size_bytes / 1024
        if kb < 1024:
            return f"{kb:.1f} KB"
        return f"{kb / 1024:.2f} MB"

    @classmethod
    def classify_extension(cls, filename: str) -> tuple[str, str | None, str | None]:
        """
        识别文件扩展名与受支持分类。

        Returns:
            (ext, category, canonical_mime)
            其中 category 为 'image' | 'pdf' | 'office' | 'text' | None
        """
        safe_name = cls.sanitize_filename(filename)
        ext = Path(safe_name).suffix.lower()
        lower_name = safe_name.lower()

        if ext in cls.IMAGE_EXTENSIONS:
            return ext, "image", cls.IMAGE_EXTENSIONS[ext]
        if ext in cls.PDF_EXTENSIONS:
            return ext, "pdf", cls.PDF_EXTENSIONS[ext]
        if ext in cls.OFFICE_EXTENSIONS:
            return ext, "office", cls.OFFICE_EXTENSIONS[ext]
        if ext in cls.TEXT_EXTENSIONS:
            return ext, "text", cls.TEXT_EXTENSIONS[ext]
        if not ext and lower_name in cls.SPECIAL_TEXT_FILENAMES:
            return "", "text", "text/plain"
        return ext, None, None

    @classmethod
    def build_unsupported_reason(cls, filename: str, ext: str) -> str:
        """按文件类型生成清晰可读的不支持原因与替代建议"""
        ext_disp = ext or "无扩展名"
        if ext in cls.LEGACY_OFFICE_EXTENSIONS:
            return (
                f"文件「{filename}」为老式二进制办公格式 ({ext_disp})，暂不支持直接解析。"
                "请用 Office / WPS 另存为 .docx / .xlsx / .pptx 或导出为 .pdf 后再上传。"
            )
        if ext in cls.ARCHIVE_EXTENSIONS:
            return (
                f"文件「{filename}」为压缩归档包 ({ext_disp})，对话附件暂不支持直接解析。"
                "请解压后上传具体文件；如需导入 ChatGPT/Claude 备份，请前往左侧「数据导入导出」；或使用「挂载本地工作区」浏览整个目录。"
            )
        if ext in cls.EXECUTABLE_EXTENSIONS:
            return f"出于本地安全防护考虑，禁止上传可执行程序、动态库或安装包 ({ext_disp})。"
        if ext in cls.UNSUPPORTED_IMAGE_EXTENSIONS:
            return (
                f"图片「{filename}」({ext_disp}) 暂不受常见多模态视觉引擎支持。"
                "请先转换为常见位图格式（.png / .jpg / .webp / .gif）后再上传。"
            )
        if ext in cls.MEDIA_OR_BINARY_EXTENSIONS:
            return (
                f"文件「{filename}」({ext_disp}) 属于音视频或大型二进制/数据库文件，对话附件暂不支持直接读取。"
                "目前支持常见图片、PDF/Word/Excel/PPT 及纯文本/代码文件。"
            )
        return (
            f"不支持文件「{filename}」的格式 ({ext_disp})。"
            "当前支持：图片 (.png/.jpg/.jpeg/.webp/.gif)、文档 (.pdf/.docx/.xlsx/.pptx) 以及纯文本/代码文件 (.txt/.md/.csv/.json/.py/.ts 等)。"
        )

    @classmethod
    def get_max_bytes_for_category(cls, category: str) -> tuple[int, str]:
        """返回指定分类的字节上限及标签"""
        if category == "image":
            return cls.MAX_IMAGE_BYTES, "图片 (10 MB)"
        if category == "pdf":
            return cls.MAX_PDF_BYTES, "PDF 文档 (10 MB)"
        if category == "office":
            return cls.MAX_OFFICE_BYTES, "Office 文档 (5 MB)"
        return cls.MAX_TEXT_BYTES, "文本/代码文件 (5 MB)"

    @classmethod
    def _validate_magic_and_content(cls, safe_name: str, ext: str, category: str, raw_bytes: bytes) -> None:
        """校验文件头魔数或文本可读性，拦截改名伪装的非法文件"""
        if category == "image":
            if ext == ".png" and not raw_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
                raise AttachmentValidationError(400, f"文件「{safe_name}」不是有效的 PNG 图片数据（文件头校验失败）。")
            if ext in (".jpg", ".jpeg") and not raw_bytes.startswith(b"\xff\xd8\xff"):
                raise AttachmentValidationError(400, f"文件「{safe_name}」不是有效的 JPEG 图片数据（文件头校验失败）。")
            if ext == ".gif" and not (raw_bytes.startswith(b"GIF87a") or raw_bytes.startswith(b"GIF89a")):
                raise AttachmentValidationError(400, f"文件「{safe_name}」不是有效的 GIF 图片数据（文件头校验失败）。")
            if ext == ".webp" and not (len(raw_bytes) >= 12 and raw_bytes[:4] == b"RIFF" and raw_bytes[8:12] == b"WEBP"):
                raise AttachmentValidationError(400, f"文件「{safe_name}」不是有效的 WebP 图片数据（文件头校验失败）。")

        elif category == "pdf":
            if b"%PDF-" not in raw_bytes[:1024]:
                raise AttachmentValidationError(400, f"文件「{safe_name}」不是有效的 PDF 文档（缺少 %PDF 文件头）。")

        elif category == "office":
            if not (raw_bytes.startswith(b"PK\x03\x04") or raw_bytes.startswith(b"PK\x05\x06")):
                raise AttachmentValidationError(
                    400,
                    f"文件「{safe_name}」不是有效的现代 Office (OOXML) 文档，请确认未损坏或并非老式二进制重命名。",
                )
            try:
                with zipfile.ZipFile(io.BytesIO(raw_bytes), "r") as zf:
                    names = zf.namelist()
                    required_prefix = {".xlsx": "xl/", ".docx": "word/", ".pptx": "ppt/"}.get(ext, "")
                    if "[Content_Types].xml" not in names or (required_prefix and not any(n.startswith(required_prefix) for n in names)):
                        raise AttachmentValidationError(
                            400,
                            f"文件「{safe_name}」压缩结构不符合 {ext} 规范（疑似普通压缩包改名），已拒绝上传。",
                        )
            except AttachmentValidationError:
                raise
            except (zipfile.BadZipFile, OSError):
                raise AttachmentValidationError(
                    400,
                    f"文件「{safe_name}」压缩容器已损坏，无法读取该 {ext} 文档。",
                )

        elif category == "text":
            sample = raw_bytes[:8192]
            has_utf16_bom = sample.startswith(b"\xff\xfe") or sample.startswith(b"\xfe\xff")
            if not has_utf16_bom and b"\x00" in sample:
                raise AttachmentValidationError(
                    400,
                    f"文件「{safe_name}」包含二进制空字节，疑似伪装为文本的非纯文本文件，已拒绝上传。",
                )
            if not cls._can_decode_text(sample):
                raise AttachmentValidationError(
                    400,
                    f"文件「{safe_name}」无法识别为有效文本编码 (UTF-8 / UTF-16 / GBK)，暂不支持上传。",
                )

    @staticmethod
    def _can_decode_text(raw_bytes: bytes) -> bool:
        """检查字节切片是否可作为文本正常解码（容忍末尾截断的半个多字节字符）"""
        if not raw_bytes:
            return True
        if raw_bytes.startswith((b"\xff\xfe", b"\xfe\xff")):
            try:
                raw_bytes.decode("utf-16")
                return True
            except UnicodeDecodeError:
                return False
        for enc in ("utf-8", "gb18030"):
            try:
                raw_bytes.decode(enc)
                return True
            except UnicodeDecodeError as err:
                # 若仅是 8KB 样本末尾截断了多字节字符，仍视为有效文本
                if err.end >= len(raw_bytes) - 4 and err.start > 0:
                    return True
                continue
        return False

    @classmethod
    def decode_text_bytes(cls, raw_bytes: bytes) -> str:
        """容错解码纯文本字节流（支持 UTF-8 BOM / UTF-16 / UTF-8 / GB18030）"""
        if raw_bytes.startswith(b"\xef\xbb\xbf"):
            return raw_bytes.decode("utf-8-sig", errors="replace")
        if raw_bytes.startswith((b"\xff\xfe", b"\xfe\xff")):
            return raw_bytes.decode("utf-16", errors="replace")
        for enc in ("utf-8", "gb18030"):
            try:
                return raw_bytes.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw_bytes.decode("utf-8", errors="replace")

    @classmethod
    def validate_upload(
        cls,
        filename: str | None,
        raw_bytes: bytes,
        content_type: str | None = None,
    ) -> dict[str, Any]:
        """
        对上传附件执行完整门禁校验。

        Raises:
            AttachmentValidationError: 当格式不支持、大小超限、空文件或内容伪造时抛出。
        """
        safe_name = cls.sanitize_filename(filename)
        ext, category, canonical_mime = cls.classify_extension(safe_name)

        if not category or not canonical_mime:
            raise AttachmentValidationError(400, cls.build_unsupported_reason(safe_name, ext))

        size_bytes = len(raw_bytes)
        if size_bytes == 0:
            raise AttachmentValidationError(400, f"文件「{safe_name}」内容为空（0 字节），无法上传。")

        max_bytes, limit_label = cls.get_max_bytes_for_category(category)
        if size_bytes > max_bytes:
            actual_mb = size_bytes / (1024 * 1024)
            raise AttachmentValidationError(
                413,
                f"文件「{safe_name}」体积为 {actual_mb:.2f} MB，超出 {limit_label} 上限。",
            )

        cls._validate_magic_and_content(safe_name, ext, category, raw_bytes)

        effective_mime = canonical_mime
        if content_type and content_type.startswith("image/") and category == "image":
            effective_mime = canonical_mime

        return {
            "safe_name": safe_name,
            "ext": ext,
            "category": category,
            "mime_type": effective_mime,
            "size_bytes": size_bytes,
            "size_str": cls.format_size_str(size_bytes),
        }

    @classmethod
    def _truncate_text(cls, text: str, max_chars: int = MAX_INLINE_CHARS) -> tuple[str, bool]:
        """对超长预览文本做首尾保留截断，防止撑爆模型上下文窗口"""
        clean = (text or "").strip()
        if len(clean) <= max_chars:
            return clean, False
        head_len = int(max_chars * 0.75)
        tail_len = max_chars - head_len - 200
        truncated_text = (
            f"{clean[:head_len]}\n\n"
            f"... [内容过长，中间已省略 {len(clean) - head_len - tail_len} 字符；如需完整处理请在任务模式按物理绝对路径读取] ...\n\n"
            f"{clean[-tail_len:]}"
        )
        return truncated_text, True

    @classmethod
    def _extract_xlsx_preview(cls, file_path: Path) -> tuple[str, str]:
        """提取 .xlsx 工作表名、总行列维度与表格头部预览（openpyxl 优先，缺失时回退标准库 zipfile+xml）"""
        max_sheets = 6
        max_rows = 30
        max_cols = 20

        try:
            import openpyxl  # type: ignore

            wb = openpyxl.load_workbook(filename=str(file_path), read_only=True, data_only=True)
            try:
                sheet_names = list(wb.sheetnames)
                lines = [f"工作表列表 (共 {len(sheet_names)} 个): {', '.join(sheet_names)}"]
                has_truncated_rows = False
                for s_name in sheet_names[:max_sheets]:
                    ws = wb[s_name]
                    declared_rows = ws.max_row
                    declared_cols = ws.max_column
                    preview_lines: list[str] = []
                    row_count = 0
                    non_empty_cols = 0
                    for row in ws.iter_rows(max_col=max_cols, values_only=True):
                        row_count += 1
                        if len(row) > non_empty_cols:
                            non_empty_cols = len(row)
                        if row_count <= max_rows:
                            row_vals = ["" if v is None else str(v).replace("\n", " ") for v in row]
                            if any(cell != "" for cell in row_vals):
                                preview_lines.append(f"Row {row_count}: " + " | ".join(row_vals))
                        elif declared_rows and row_count >= max_rows:
                            # NOTE: 已有精确 dimension 元数据时无需遍历全表即可停止，避免大表重复扫描
                            break

                    total_rows = declared_rows or row_count
                    total_cols = declared_cols or non_empty_cols
                    lines.append(f"\n### Sheet: {s_name} (总规模: {total_rows} 行 × {total_cols} 列)")
                    lines.extend(preview_lines)
                    if total_rows > max_rows:
                        has_truncated_rows = True
                        lines.append(
                            f"... (该表共有 {total_rows} 行 × {total_cols} 列，上方仅预览前 {max_rows} 行；严禁仅凭预览推断全表内容) ..."
                        )
                if has_truncated_rows:
                    lines.append(
                        "\n⚠️ [全量处理硬约束]: 本工作簿包含超出预览窗口的大量真实行数据，禁止编造数据或只基于前 30 行下结论，请用 Python 脚本直接读取上述沙盒文件路径全量解析！"
                    )
                return "\n".join(lines), "openpyxl"
            finally:
                wb.close()
        except ImportError:
            logger.debug("[AttachmentService] 未检测到 openpyxl，使用标准库 zipfile+xml 提取 .xlsx 摘要")
        except Exception as ox_err:
            logger.debug(f"[AttachmentService] openpyxl 读取异常，尝试标准库回退 ({ox_err})")

        # 零依赖标准库兜底提取 .xlsx
        lines: list[str] = []
        with zipfile.ZipFile(file_path, "r") as zf:
            shared_strings: list[str] = []
            if "xl/sharedStrings.xml" in zf.namelist():
                ss_root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
                for si in ss_root.iter():
                    if si.tag.endswith("}si") or si.tag == "si":
                        txt_parts = [t.text or "" for t in si.iter() if (t.tag.endswith("}t") or t.tag == "t")]
                        shared_strings.append("".join(txt_parts))

            sheet_titles: list[str] = []
            if "xl/workbook.xml" in zf.namelist():
                wb_root = ET.fromstring(zf.read("xl/workbook.xml"))
                for el in wb_root.iter():
                    if el.tag.endswith("}sheet") or el.tag == "sheet":
                        name_attr = el.attrib.get("name")
                        if name_attr:
                            sheet_titles.append(name_attr)

            sheet_files = sorted(
                [n for n in zf.namelist() if re.match(r"^xl/worksheets/sheet\d+\.xml$", n)],
                key=lambda p: int(re.search(r"sheet(\d+)\.xml$", p).group(1)),  # type: ignore
            )
            if sheet_titles:
                lines.append(f"工作表列表 (共 {len(sheet_titles)} 个): {', '.join(sheet_titles)}")

            has_truncated_rows = False
            for idx, s_file in enumerate(sheet_files[:max_sheets]):
                title = sheet_titles[idx] if idx < len(sheet_titles) else s_file
                s_root = ET.fromstring(zf.read(s_file))
                row_elements = [
                    row_el
                    for row_el in s_root.iter()
                    if (row_el.tag.endswith("}row") or row_el.tag == "row")
                ]
                total_row_nodes = len(row_elements)
                lines.append(f"\n### Sheet: {title} (共 {total_row_nodes} 个数据行节点)")
                extracted_rows = 0
                for row_el in row_elements[:max_rows]:
                    cells: list[str] = []
                    for c_el in list(row_el)[:max_cols]:
                        c_type = c_el.attrib.get("t", "")
                        val_text = ""
                        if c_type == "s":
                            v_el = next((ch for ch in c_el if ch.tag.endswith("}v") or ch.tag == "v"), None)
                            if v_el is not None and v_el.text and v_el.text.isdigit():
                                ss_idx = int(v_el.text)
                                if 0 <= ss_idx < len(shared_strings):
                                    val_text = shared_strings[ss_idx]
                        elif c_type == "inlineStr":
                            val_text = "".join(t.text or "" for t in c_el.iter() if t.tag.endswith("}t") or t.tag == "t")
                        else:
                            v_el = next((ch for ch in c_el if ch.tag.endswith("}v") or ch.tag == "v"), None)
                            if v_el is not None and v_el.text:
                                val_text = v_el.text
                        cells.append(val_text.replace("\n", " "))
                    if any(cell != "" for cell in cells):
                        lines.append(f"Row {extracted_rows + 1}: " + " | ".join(cells))
                        extracted_rows += 1
                if total_row_nodes > max_rows:
                    has_truncated_rows = True
                    lines.append(
                        f"... (该表共 {total_row_nodes} 行，已达预览上限 {max_rows} 行，完整表格请使用脚本读取全量数据) ..."
                    )
            if has_truncated_rows:
                lines.append(
                    "\n⚠️ [全量处理硬约束]: 本工作簿存在未完全预览的数据行，严禁仅凭头部行猜测全表结论或造假数据。"
                )
        return "\n".join(lines) or "(空工作簿)", "stdlib_zipfile_xml"

    @classmethod
    def _extract_docx_preview(cls, file_path: Path) -> str:
        """零依赖通过标准库 zipfile+xml 提取 .docx 段落与表格文本"""
        blocks: list[str] = []
        with zipfile.ZipFile(file_path, "r") as zf:
            if "word/document.xml" not in zf.namelist():
                return "(未找到 word/document.xml 正文节点)"
            root = ET.fromstring(zf.read("word/document.xml"))
            body = next((el for el in root.iter() if el.tag.endswith("}body") or el.tag == "body"), root)
            for child in list(body):
                tag_name = child.tag.split("}")[-1]
                if tag_name == "p":
                    para_txt = "".join(t.text or "" for t in child.iter() if t.tag.split("}")[-1] == "t").strip()
                    if para_txt:
                        blocks.append(para_txt)
                elif tag_name == "tbl":
                    for tr in child.iter():
                        if tr.tag.split("}")[-1] != "tr":
                            continue
                        row_cells: list[str] = []
                        for tc in tr:
                            if tc.tag.split("}")[-1] == "tc":
                                cell_txt = " ".join(
                                    "".join(t.text or "" for t in p.iter() if t.tag.split("}")[-1] == "t").strip()
                                    for p in tc
                                    if p.tag.split("}")[-1] == "p"
                                ).strip()
                                row_cells.append(cell_txt)
                        if any(row_cells):
                            blocks.append(" | ".join(row_cells))
        return "\n".join(blocks) or "(Word 文档正文为空)"

    @classmethod
    def _extract_pptx_preview(cls, file_path: Path) -> str:
        """零依赖通过标准库 zipfile+xml 提取 .pptx 各页幻灯片正文文本"""
        slides_text: list[str] = []
        with zipfile.ZipFile(file_path, "r") as zf:
            slide_files = sorted(
                [n for n in zf.namelist() if re.match(r"^ppt/slides/slide\d+\.xml$", n)],
                key=lambda p: int(re.search(r"slide(\d+)\.xml$", p).group(1)),  # type: ignore
            )
            for idx, s_file in enumerate(slide_files[:40], 1):
                s_root = ET.fromstring(zf.read(s_file))
                texts: list[str] = []
                for p_el in s_root.iter():
                    if p_el.tag.split("}")[-1] == "p":
                        line = "".join(t.text or "" for t in p_el.iter() if t.tag.split("}")[-1] == "t").strip()
                        if line:
                            texts.append(line)
                if texts:
                    slides_text.append(f"### Slide {idx}\n" + "\n".join(texts))
            if len(slide_files) > 40:
                slides_text.append(f"... (演示文稿共 {len(slide_files)} 页，已预览前 40 页) ...")
        return "\n\n".join(slides_text) or "(PPTX 演示文稿无可见文本内容)"

    @classmethod
    def extract_attachment_context(cls, file_path: str | Path, mime_type: str | None = None, original_name: str | None = None) -> dict[str, Any]:
        """
        提取本地已存储附件的内容摘要，供模型在对话模式与任务模式直接读取理解。
        """
        path_obj = Path(file_path)
        name = original_name or path_obj.name
        ext, category, canonical_mime = cls.classify_extension(name)
        if not category and mime_type:
            if mime_type.startswith("image/"):
                category = "image"
            elif mime_type == "application/pdf":
                category = "pdf"
            elif mime_type.startswith("text/"):
                category = "text"

        result: dict[str, Any] = {
            "name": name,
            "file_path": str(path_obj.resolve()),
            "ext": ext,
            "category": category or "file",
            "mime_type": canonical_mime or mime_type or "application/octet-stream",
            "preview_text": "",
            "truncated": False,
            "extractor": "none",
        }

        if not path_obj.exists() or not path_obj.is_file():
            result["preview_text"] = f"[警告: 本地附件文件未找到 `{path_obj}`]"
            return result

        try:
            if category == "text":
                raw_bytes = path_obj.read_bytes()
                decoded = cls.decode_text_bytes(raw_bytes)
                preview, is_trunc = cls._truncate_text(decoded)
                result["preview_text"] = preview
                result["truncated"] = is_trunc
                result["extractor"] = "text_decode"
            elif category == "office":
                if ext == ".xlsx":
                    raw_preview, extractor_name = cls._extract_xlsx_preview(path_obj)
                    preview, is_trunc = cls._truncate_text(raw_preview)
                    result["preview_text"] = preview
                    result["truncated"] = is_trunc
                    result["extractor"] = extractor_name
                elif ext == ".docx":
                    raw_preview = cls._extract_docx_preview(path_obj)
                    preview, is_trunc = cls._truncate_text(raw_preview)
                    result["preview_text"] = preview
                    result["truncated"] = is_trunc
                    result["extractor"] = "stdlib_docx_xml"
                elif ext == ".pptx":
                    raw_preview = cls._extract_pptx_preview(path_obj)
                    preview, is_trunc = cls._truncate_text(raw_preview)
                    result["preview_text"] = preview
                    result["truncated"] = is_trunc
                    result["extractor"] = "stdlib_pptx_xml"
            elif category == "pdf":
                result["extractor"] = "pdf_inline_multimodal"
                result["preview_text"] = (
                    "该文件为 PDF 文档。支持多模态的 Gemini 模型通道将直接读取整份 PDF 页面与图文内容；"
                    "若处于纯文本模型通道或需精确批量抽数，请按上述真实磁盘绝对路径处理。"
                )
        except Exception as e:
            logger.warning(f"[AttachmentService] 提取附件 {name} 摘要失败: {e}")
            result["preview_text"] = f"[附件内容自动摘要跳过 ({e})；请在任务模式使用上述真实物理绝对路径直接读写文件]"

        return result

    @staticmethod
    def _clean_workspace_id(workspace_id: str | None) -> str:
        """规范化工作区 ID，防止非法路径段穿越"""
        cleaned = re.sub(r"[^a-zA-Z0-9_\-]", "_", (workspace_id or "default").strip())
        return cleaned or "default"

    @staticmethod
    def _resolve_legacy_uploads_dirs() -> list[Path]:
        """返回历史遗留的全局 uploads 目录候选列表（兼容 v1.1.6/v1.1.7 老记录）"""
        dirs = [Path(settings.db_path).parent / "uploads"]
        backend_legacy = Path(__file__).resolve().parent.parent.parent / "data" / "uploads"
        if backend_legacy not in dirs:
            dirs.append(backend_legacy)
        return dirs

    @classmethod
    async def resolve_workspace_attachments_dir(
        cls,
        workspace_id: str | None = None,
    ) -> tuple[Path, str, str, Path]:
        """
        解析指定工作区的沙盒附件存储目录。

        Returns:
            (att_dir, rel_dir, effective_workspace_id, workspace_root)
            - 虚拟沙盒工作区 (virtual): <ws_root>/attachments/
            - 物理挂载工作区 (physical): <ws_root>/.agenthub/attachments/（避免污染本地 Git 目录）
        """
        eff_ws_id = cls._clean_workspace_id(workspace_id)
        ws_data: dict[str, Any] | None = None
        try:
            from ..repository.workspace_repository import WorkspaceRepository

            ws_data = await WorkspaceRepository.get_by_id(eff_ws_id)
            if not ws_data and eff_ws_id != "default":
                ws_data = await WorkspaceRepository.get_by_id("default")
                if ws_data:
                    eff_ws_id = str(ws_data.get("id") or "default")
        except Exception as e:
            logger.debug(f"[AttachmentService] 查询工作区 {eff_ws_id} 跳过: {e}")

        if ws_data and ws_data.get("root_path"):
            ws_root = Path(ws_data["root_path"]).resolve()
            ws_type = str(ws_data.get("type") or "virtual").lower()
            eff_ws_id = cls._clean_workspace_id(str(ws_data.get("id") or eff_ws_id))
        else:
            ws_root = (Path(settings.db_path).parent / "workspaces" / eff_ws_id).resolve()
            ws_type = "virtual"

        ws_root.mkdir(parents=True, exist_ok=True)

        if ws_type == "physical":
            rel_dir = ".agenthub/attachments"
            att_dir = (ws_root / ".agenthub" / "attachments").resolve()
            # 顺手确保物理工作区 .agenthub/.gitignore 忽略 attachments/ 防止污染宿主 Git 状态
            try:
                agenthub_dir = ws_root / ".agenthub"
                agenthub_dir.mkdir(parents=True, exist_ok=True)
                gi = agenthub_dir / ".gitignore"
                if gi.exists():
                    existing_gi = gi.read_text(encoding="utf-8", errors="ignore")
                    if "attachments/" not in existing_gi:
                        gi.write_text(existing_gi.rstrip() + "\nattachments/\n", encoding="utf-8")
                else:
                    gi.write_text("index.db\nsnapshots/\nlogs/\nattachments/\n", encoding="utf-8")
            except Exception as gi_err:
                logger.debug(f"[AttachmentService] 更新 .agenthub/.gitignore 提示: {gi_err}")
        else:
            rel_dir = "attachments"
            att_dir = (ws_root / "attachments").resolve()

        att_dir.mkdir(parents=True, exist_ok=True)
        return att_dir, rel_dir, eff_ws_id, ws_root

    @classmethod
    async def resolve_workspace_attachment_file(
        cls,
        workspace_id: str | None,
        filename: str,
    ) -> Path | None:
        """
        在指定工作区沙盒附件目录内安全解析目标文件（含严格防路径穿越边界校验）。
        """
        raw_name = unquote((filename or "").strip())
        if not raw_name or "/" in raw_name or "\\" in raw_name or raw_name in (".", ".."):
            return None
        safe_name = Path(raw_name).name
        if safe_name != raw_name:
            return None

        att_dir, _, _, _ = await cls.resolve_workspace_attachments_dir(workspace_id)
        candidate = (att_dir / safe_name).resolve()
        try:
            candidate.relative_to(att_dir.resolve())
        except ValueError:
            return None
        if candidate.exists() and candidate.is_file():
            return candidate
        return None

    @classmethod
    async def save_bytes_to_workspace_attachments(
        cls,
        *,
        workspace_id: str | None,
        safe_name: str,
        raw_bytes: bytes,
        conversation_id: str | None = None,
        register_artifact: bool = True,
    ) -> dict[str, Any]:
        """
        将附件字节流真实落盘到目标工作区沙盒附件目录：
        - 保留干净易读原文件名；
        - 同名同内容 (SHA-256 一致) 直接幂等复用，不同内容自动顺延 `__2` 等序号后缀防覆盖；
        - 自动同步登记至工作区资产库 (source="user_uploaded")。
        """
        att_dir, rel_dir, eff_ws_id, ws_root = await cls.resolve_workspace_attachments_dir(workspace_id)
        clean_name = cls.sanitize_filename(safe_name)
        incoming_hash = hashlib.sha256(raw_bytes).hexdigest()

        stem = Path(clean_name).stem or "attachment"
        suffix = Path(clean_name).suffix

        target_path: Path | None = None
        for idx in range(1, 200):
            candidate_name = clean_name if idx == 1 else f"{stem}__{idx}{suffix}"
            candidate = (att_dir / candidate_name).resolve()
            try:
                candidate.relative_to(att_dir.resolve())
            except ValueError:
                continue
            if candidate.exists() and candidate.is_file():
                try:
                    existing_hash = hashlib.sha256(candidate.read_bytes()).hexdigest()
                    if existing_hash == incoming_hash:
                        target_path = candidate
                        break
                except OSError:
                    continue
            else:
                candidate.write_bytes(raw_bytes)
                target_path = candidate
                break

        if target_path is None:
            fallback_name = f"{stem}_{incoming_hash[:8]}{suffix}"
            target_path = (att_dir / fallback_name).resolve()
            target_path.write_bytes(raw_bytes)

        stored_name = target_path.name
        rel_path = f"./{rel_dir}/{stored_name}".replace("\\", "/")
        abs_path = str(target_path.resolve())
        url = f"/api/v1/chat/uploads/ws/{quote(eff_ws_id, safe='')}/{quote(stored_name, safe='')}"

        if register_artifact:
            try:
                from ..repository.artifact_repository import ArtifactRepository

                f_type = ArtifactRepository._detect_file_type(target_path)
                await ArtifactRepository.register_artifact(
                    workspace_id=eff_ws_id,
                    file_path=abs_path,
                    file_name=stored_name,
                    file_type=f_type,
                    file_size=len(raw_bytes),
                    conversation_id=conversation_id,
                    source="user_uploaded",
                    summary=f"用户上传附件 ({rel_path})",
                )
            except Exception as art_err:
                logger.warning(f"[AttachmentService] 自动登记用户上传附件至资产库异常: {art_err}")

        return {
            "workspace_id": eff_ws_id,
            "ws_root": str(ws_root),
            "stored_name": stored_name,
            "file_path": abs_path,
            "rel_path": rel_path,
            "url": url,
        }

    @classmethod
    def _locate_existing_source_file(cls, att_dict: dict[str, Any]) -> Path | None:
        """定位附件既有源文件（优先真实 file_path，其次兜底解析历史 uploads 静态 URL）"""
        raw_fp = str(att_dict.get("file_path") or "").strip()
        if raw_fp:
            p = Path(raw_fp)
            if p.exists() and p.is_file():
                return p.resolve()

        raw_url = str(att_dict.get("url") or "").strip()
        if raw_url.startswith("/api/v1/chat/uploads/") and "/ws/" not in raw_url:
            tail = unquote(raw_url.rsplit("/", 1)[-1])
            safe_tail = Path(tail).name
            if safe_tail and safe_tail == tail:
                for legacy_dir in cls._resolve_legacy_uploads_dirs():
                    cand = legacy_dir / safe_tail
                    if cand.exists() and cand.is_file():
                        return cand.resolve()
        return None

    @classmethod
    async def ensure_attachments_in_workspace(
        cls,
        workspace_id: str | None,
        attachments: list[Any] | None,
        conversation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        在进入推理或落库消息前，确保本轮附件 100% 物理落盘在当前目标工作区沙盒附件目录中：
        - 若文件尚未进入目标工作区沙盒（例如用户先上传再切换工作区，或迁移旧版 v1.1.6/v1.1.7 历史消息），
          自动将其拷贝入当前工作区沙盒（恢复干净原文件名）并同步更新 file_path / rel_path / url；
        - 若已处于当前工作区沙盒内，则规范化补全 rel_path 与资产库登记。
        """
        if not attachments:
            return []

        att_dir, rel_dir, eff_ws_id, _ = await cls.resolve_workspace_attachments_dir(workspace_id)
        att_dir_resolved = att_dir.resolve()
        normalized_list: list[dict[str, Any]] = []

        for att in attachments:
            d = dict(att) if isinstance(att, dict) else att.model_dump()
            src_path = cls._locate_existing_source_file(d)
            if src_path is None:
                normalized_list.append(d)
                continue

            already_in_target_sandbox = False
            try:
                src_path.resolve().relative_to(att_dir_resolved)
                already_in_target_sandbox = True
            except ValueError:
                already_in_target_sandbox = False

            if already_in_target_sandbox:
                stored_name = src_path.name
                rel_path = f"./{rel_dir}/{stored_name}".replace("\\", "/")
                d["name"] = d.get("name") or stored_name
                d["file_path"] = str(src_path.resolve())
                d["rel_path"] = rel_path
                d["url"] = f"/api/v1/chat/uploads/ws/{quote(eff_ws_id, safe='')}/{quote(stored_name, safe='')}"
                try:
                    from ..repository.artifact_repository import ArtifactRepository

                    f_type = ArtifactRepository._detect_file_type(src_path)
                    await ArtifactRepository.register_artifact(
                        workspace_id=eff_ws_id,
                        file_path=d["file_path"],
                        file_name=stored_name,
                        file_type=f_type,
                        file_size=src_path.stat().st_size,
                        conversation_id=conversation_id,
                        source="user_uploaded",
                        summary=f"用户上传附件 ({rel_path})",
                    )
                except Exception as art_err:
                    logger.debug(f"[AttachmentService] 补齐登记工作区附件跳过: {art_err}")
                normalized_list.append(d)
                continue

            # 跨工作区或历史全局目录来源 -> 复制入当前工作区沙盒附件目录并恢复干净文件名
            preferred_name = str(d.get("name") or "").strip() or src_path.name
            # 若原文件带 12 位 hex 前缀且 name 未清理，剔除前缀恢复干净文件名
            if preferred_name == src_path.name and re.match(r"^[0-9a-f]{12}_.+", preferred_name):
                preferred_name = preferred_name[13:]
            raw_bytes = src_path.read_bytes()
            saved = await cls.save_bytes_to_workspace_attachments(
                workspace_id=eff_ws_id,
                safe_name=preferred_name,
                raw_bytes=raw_bytes,
                conversation_id=conversation_id,
                register_artifact=True,
            )
            d["name"] = saved["stored_name"]
            d["file_path"] = saved["file_path"]
            d["rel_path"] = saved["rel_path"]
            d["url"] = saved["url"]
            if not d.get("size_str"):
                d["size_str"] = cls.format_size_str(len(raw_bytes))
            logger.info(
                f"[AttachmentService] 已自动将附件 `{preferred_name}` 迁入工作区 [{eff_ws_id}] 沙盒: {saved['file_path']}"
            )
            normalized_list.append(d)

        return normalized_list

    @staticmethod
    def is_path_in_attachments_dir(target_path: Path, workspace_root: Path | None = None) -> bool:
        """
        校验给定路径是否位于工作区只读输入附件目录 (`attachments/` 或 `.agenthub/attachments/`) 内。
        用于物理阻止 Agent 在用户输入区私自写入或伪造 Mock 测试文件。
        """
        resolved_target = target_path.resolve()
        if workspace_root is not None:
            resolved_root = workspace_root.resolve()
            try:
                rel = resolved_target.relative_to(resolved_root)
                parts_lower = [p.lower() for p in rel.parts]
                if parts_lower and parts_lower[0] == "attachments":
                    return True
                if len(parts_lower) >= 2 and parts_lower[0] == ".agenthub" and parts_lower[1] == "attachments":
                    return True
            except ValueError:
                pass

        # 兜底检查历史上或全局 uploads 目录
        norm_str = str(resolved_target).replace("\\", "/").lower()
        if "/data/uploads/" in norm_str:
            return True
        return False

    @classmethod
    def list_workspace_sandbox_attachments(
        cls,
        workspace_root: str | Path | None,
        limit: int = 15,
    ) -> list[dict[str, Any]]:
        """
        扫描指定工作区物理根目录下的真实沙盒附件清单 (`attachments/` 与 `.agenthub/attachments/`)，
        供 ContextBuilder 主动展示现有原料及 read_local_file 缺失回退提示使用。
        """
        if not workspace_root:
            return []
        root = Path(workspace_root).resolve()
        if not root.exists() or not root.is_dir():
            return []

        items: list[dict[str, Any]] = []
        candidates = [
            ("attachments", root / "attachments"),
            (".agenthub/attachments", root / ".agenthub" / "attachments"),
        ]
        for rel_prefix, folder in candidates:
            if not folder.exists() or not folder.is_dir():
                continue
            try:
                for entry in folder.iterdir():
                    if not entry.is_file() or entry.name.startswith("."):
                        continue
                    try:
                        st = entry.stat()
                        items.append({
                            "name": entry.name,
                            "rel_path": f"./{rel_prefix}/{entry.name}",
                            "file_path": str(entry.resolve()),
                            "size_bytes": st.st_size,
                            "size_str": cls.format_size_str(st.st_size),
                            "mtime": st.st_mtime,
                        })
                    except OSError:
                        continue
            except OSError:
                continue

        items.sort(key=lambda x: x["mtime"], reverse=True)
        return items[:max(1, limit)]

    @classmethod
    def append_attachments_anchor(cls, user_message: str, attachments: list[Any] | None) -> str:
        """
        将附件元数据以标准锚点块持久化到用户消息正文末尾，
        既能让前端渲染历史附件徽标与大图，又能防止多步/多轮推理丢失工作区相对路径与磁盘绝对路径。
        """
        base = (user_message or "").rstrip()
        if not attachments:
            return base

        meta_list: list[dict[str, Any]] = []
        for att in attachments:
            d = att if isinstance(att, dict) else att.model_dump()
            name = str(d.get("name") or "附件")
            _, inferred_cat, _ = cls.classify_extension(name)
            entry: dict[str, Any] = {
                "name": name,
                "file_path": d.get("file_path") or "",
                "url": d.get("url") or "",
                "mime_type": d.get("mime_type") or "application/octet-stream",
                "size_str": d.get("size_str") or "",
                "category": inferred_cat or ("image" if str(d.get("mime_type") or "").startswith("image/") else "file"),
            }
            if d.get("rel_path"):
                entry["rel_path"] = d.get("rel_path")
            meta_list.append(entry)

        anchor_json = json.dumps(meta_list, ensure_ascii=False)
        return f"{base}\n\n<agenthub_attachments>{anchor_json}</agenthub_attachments>".strip()

    @classmethod
    def render_attachments_for_llm_history(cls, content: str) -> str:
        """
        将历史用户消息中的 <agenthub_attachments> 锚点展开为 LLM 可读的工作区沙盒路径与绝对路径档案，
        并重申禁止伪造或用 Mock 文件替代真实附件。
        """
        raw = content or ""
        m = cls.ANCHOR_PATTERN.search(raw)
        if not m:
            return raw

        clean_body = cls.ANCHOR_PATTERN.sub("", raw).rstrip()
        try:
            att_list = json.loads(m.group(1))
            if not isinstance(att_list, list) or not att_list:
                return clean_body
        except Exception:
            return clean_body

        lines = [
            "【该轮次附带的当前工作区真实附件档案 - 严禁编造或生成 Mock 数据替代真实文件】:"
        ]
        for item in att_list:
            name = item.get("name", "附件")
            fpath = item.get("file_path", "")
            rel_path = item.get("rel_path", "")
            cat = item.get("category", "file")
            size_s = item.get("size_str", "")
            loc_parts: list[str] = []
            if rel_path:
                loc_parts.append(f"工作区相对路径: `{rel_path}`")
            if fpath:
                loc_parts.append(f"磁盘绝对路径: `{fpath}`")
            path_part = " | ".join(loc_parts) if loc_parts else "暂未持久化物理路径"
            lines.append(f"- `{name}` ({cat}, {size_s}) -> {path_part}")

        return f"{clean_body}\n\n" + "\n".join(lines) if clean_body else "\n".join(lines)

    @classmethod
    def strip_attachments_for_display(cls, content: str) -> str:
        """
        将消息末尾的 <agenthub_attachments> JSON 锚点折叠为紧凑可读标签（如 `[附件: report.xlsx, fig.png]`），
        供全文检索结果摘要与 L3 会话压缩摘要使用，避免暴露原始 HTML/JSON 标记。
        """
        raw = content or ""
        m = cls.ANCHOR_PATTERN.search(raw)
        if not m:
            return raw

        clean_body = cls.ANCHOR_PATTERN.sub("", raw).strip()
        names: list[str] = []
        try:
            att_list = json.loads(m.group(1))
            if isinstance(att_list, list):
                names = [str(item.get("name") or "").strip() for item in att_list if isinstance(item, dict) and item.get("name")]
        except Exception:
            names = []

        badge = f"[附件: {', '.join(names)}]" if names else ""
        if clean_body and badge:
            return f"{clean_body} {badge}"
        return clean_body or badge

    @classmethod
    def register_workspace_artifact(
        cls,
        workspace_root: Any = None,
        file_path: Any = None,
        conversation_id: str = "",
        source: str = "ai_generated",
        summary: str = "",
        workspace_id: str = "",
    ) -> Any:
        """
        向后兼容别名方法：将文件注册到当前工作区资产库。
        内部自动代理转发给 ArtifactRepository.register_artifact，兼容旧代码同步/异步调用。
        """
        try:
            import asyncio
            from ..repository.artifact_repository import ArtifactRepository
            p = Path(file_path) if file_path else None
            if not p or not p.exists():
                return None
            f_type = ArtifactRepository._detect_file_type(p)
            f_size = p.stat().st_size
            coro = ArtifactRepository.register_artifact(
                workspace_id=workspace_id or "default",
                file_path=str(p),
                file_name=p.name,
                file_type=f_type,
                file_size=f_size,
                conversation_id=conversation_id,
                source=source,
                summary=summary or f"AI 修改落盘: {p.name}",
            )
            try:
                loop = asyncio.get_running_loop()
                return loop.create_task(coro)
            except RuntimeError:
                return asyncio.run(coro)
        except Exception as e:
            logger.debug(f"[AttachmentService] register_workspace_artifact 代理登记跳过: {e}")
            return None

    @classmethod
    def build_rules_summary(cls) -> dict[str, Any]:
        """导出统一的前后端校验规则清单"""
        return {
            "max_files_per_message": cls.MAX_ATTACHMENTS_PER_MESSAGE,
            "limits_mb": {
                "image": cls.MAX_IMAGE_BYTES // (1024 * 1024),
                "pdf": cls.MAX_PDF_BYTES // (1024 * 1024),
                "office": cls.MAX_OFFICE_BYTES // (1024 * 1024),
                "text": cls.MAX_TEXT_BYTES // (1024 * 1024),
            },
            "allowed_extensions": {
                "image": sorted(cls.IMAGE_EXTENSIONS.keys()),
                "pdf": sorted(cls.PDF_EXTENSIONS.keys()),
                "office": sorted(cls.OFFICE_EXTENSIONS.keys()),
                "text": sorted(cls.TEXT_EXTENSIONS.keys()),
            },
        }


attachment_service = AttachmentService()
