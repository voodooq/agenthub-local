"""
AST 语法感知代码分块器与 Markdown 结构化切片器 (AST Chunker)
利用 Tree-sitter 实现代码按完整类与函数边界切分，注入上下文面包屑头，
避免腰斩函数，提升符号与自然语言语义对齐率；
兼容 Markdown 标题层级切分与文本滑动窗口。
"""
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, cast

from ..core.logging_config import logger

try:
    import tree_sitter_language_pack as tslp
    HAS_TREE_SITTER = True
except ImportError:
    HAS_TREE_SITTER = False

LANG_EXTENSIONS = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "javascript",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".cpp": "cpp",
    ".c": "c",
    ".h": "c",
    ".hpp": "cpp",
}


@dataclass
class CodeChunk:
    """分块切片结果模型。"""
    rel_path: str
    title: str
    section_path: str
    start_line: int
    end_line: int
    content_text: str
    is_code: bool = True


class AstChunker:
    """基于 Tree-sitter AST 与文档结构的切片引擎。"""

    MAX_CHUNK_CHARS = 1200
    OVERLAP_CHARS = 120

    @classmethod
    def chunk_file(cls, file_path: Path, rel_path: str) -> list[CodeChunk]:
        """将物理文件切分为结构化语义切片。

        Args:
            file_path: 目标文件物理路径。
            rel_path: 工作区相对路径或云端 URI。

        Returns:
            list[CodeChunk]: 切片集合。
        """
        try:
            raw_bytes = file_path.read_bytes()
            content = raw_bytes.decode("utf-8", errors="replace")
        except Exception as e:
            logger.warning(f"读取文件失败 ({file_path}): {e}")
            return []

        suffix = file_path.suffix.lower()
        title = file_path.name

        if suffix in LANG_EXTENSIONS and HAS_TREE_SITTER:
            lang = LANG_EXTENSIONS[suffix]
            chunks = cls._chunk_code_ast(raw_bytes, content, rel_path, title, lang)
            if chunks:
                return chunks

        if suffix in (".md", ".markdown"):
            return cls._chunk_markdown(content, rel_path, title)

        return cls._chunk_plain_text(content, rel_path, title)

    @classmethod
    def _chunk_code_ast(
        cls, raw_bytes: bytes, content: str, rel_path: str, title: str, lang: str
    ) -> list[CodeChunk]:
        """通过 Tree-sitter 切分类与函数。"""
        try:
            parser = tslp.get_parser(cast(Any, lang))
            tree = parser.parse(raw_bytes)
        except Exception as e:
            logger.warning(f"Tree-sitter 解析失败 ({rel_path}): {e}")
            return cls._chunk_plain_text(content, rel_path, title, is_code=True)

        lines = content.splitlines(keepends=True)
        chunks: list[CodeChunk] = []
        covered_lines = set()

        def get_node_text(node: Any) -> str:
            start_b = node.start_byte
            end_b = node.end_byte
            return raw_bytes[start_b:end_b].decode("utf-8", errors="replace")

        def get_identifier_name(node: Any) -> str:
            for child in node.children:
                if child.type in ("identifier", "name", "type_identifier", "property_identifier"):
                    return raw_bytes[child.start_byte : child.end_byte].decode("utf-8", errors="replace")
            return ""

        root = tree.root_node
        nodes_to_visit = list(root.children)

        while nodes_to_visit:
            node = nodes_to_visit.pop(0)
            ntype = node.type

            # 类定义
            if ntype in ("class_definition", "class_declaration", "struct_item", "impl_item"):
                class_name = get_identifier_name(node) or "AnonymousClass"
                start_l = node.start_point[0] + 1
                end_l = node.end_point[0] + 1
                class_text = get_node_text(node)

                # 若类长度在阈值内，直接作为单个类切片
                if len(class_text) <= cls.MAX_CHUNK_CHARS:
                    header = f"# File: {rel_path} | Class: {class_name} (L{start_l}-L{end_l})\n"
                    chunks.append(
                        CodeChunk(
                            rel_path=rel_path,
                            title=title,
                            section_path=class_name,
                            start_line=start_l,
                            end_line=end_l,
                            content_text=header + class_text,
                            is_code=True,
                        )
                    )
                    covered_lines.update(range(start_l, end_l + 1))
                else:
                    # 超长类拆解：遍历类内方法与成员
                    for child in node.children:
                        if child.type in (
                            "function_definition",
                            "method_definition",
                            "method_declaration",
                            "function_item",
                            "arrow_function",
                        ):
                            fn_name = get_identifier_name(child) or "method"
                            m_start = child.start_point[0] + 1
                            m_end = child.end_point[0] + 1
                            fn_text = get_node_text(child)
                            header = f"# File: {rel_path} | Class: {class_name} | Method: {fn_name} (L{m_start}-L{m_end})\n"
                            chunks.append(
                                CodeChunk(
                                    rel_path=rel_path,
                                    title=title,
                                    section_path=f"{class_name}.{fn_name}",
                                    start_line=m_start,
                                    end_line=m_end,
                                    content_text=header + fn_text,
                                    is_code=True,
                                )
                            )
                            covered_lines.update(range(m_start, m_end + 1))

            # 顶层函数定义
            elif ntype in (
                "function_definition",
                "async_function_definition",
                "function_declaration",
                "function_item",
            ):
                fn_name = get_identifier_name(node) or "anonymous_fn"
                start_l = node.start_point[0] + 1
                end_l = node.end_point[0] + 1
                fn_text = get_node_text(node)
                header = f"# File: {rel_path} | Function: {fn_name} (L{start_l}-L{end_l})\n"
                chunks.append(
                    CodeChunk(
                        rel_path=rel_path,
                        title=title,
                        section_path=fn_name,
                        start_line=start_l,
                        end_line=end_l,
                        content_text=header + fn_text,
                        is_code=True,
                    )
                )
                covered_lines.update(range(start_l, end_l + 1))

        # 补全未被类与函数覆盖的顶层代码段（如导包、常量、配置）
        all_lines = set(range(1, len(lines) + 1))
        uncovered = sorted(list(all_lines - covered_lines))
        if uncovered:
            # 连续行聚合并切分
            blocks: list[list[int]] = []
            cur_block: list[int] = []
            for l in uncovered:
                if not cur_block or l == cur_block[-1] + 1:
                    cur_block.append(l)
                else:
                    blocks.append(cur_block)
                    cur_block = [l]
            if cur_block:
                blocks.append(cur_block)

            for b in blocks:
                b_start = b[0]
                b_end = b[-1]
                block_lines = lines[b_start - 1 : b_end]
                block_text = "".join(block_lines).strip()
                if not block_text:
                    continue
                header = f"# File: {rel_path} | Module (L{b_start}-L{b_end})\n"
                chunks.append(
                    CodeChunk(
                        rel_path=rel_path,
                        title=title,
                        section_path="module_scope",
                        start_line=b_start,
                        end_line=b_end,
                        content_text=header + block_text,
                        is_code=True,
                    )
                )

        chunks.sort(key=lambda c: c.start_line)
        return chunks if chunks else cls._chunk_plain_text(content, rel_path, title, is_code=True)

    @classmethod
    def _chunk_markdown(cls, content: str, rel_path: str, title: str) -> list[CodeChunk]:
        """按 Markdown 标题层级 (# ~ ####) 切分。"""
        lines = content.splitlines(keepends=True)
        chunks: list[CodeChunk] = []

        header_regex = re.compile(r"^(#{1,6})\s+(.+)$")
        sections: list[dict[str, Any]] = []

        cur_title = "Introduction"
        cur_start = 1
        cur_lines: list[str] = []

        for idx, line in enumerate(lines):
            line_no = idx + 1
            match = header_regex.match(line.strip())
            if match:
                if cur_lines:
                    text = "".join(cur_lines).strip()
                    if text:
                        sections.append({
                            "section": cur_title,
                            "start": cur_start,
                            "end": line_no - 1,
                            "text": text,
                        })
                    cur_lines = []
                cur_title = match.group(2).strip()
                cur_start = line_no
            cur_lines.append(line)

        if cur_lines:
            text = "".join(cur_lines).strip()
            if text:
                sections.append({
                    "section": cur_title,
                    "start": cur_start,
                    "end": len(lines),
                    "text": text,
                })

        for s in sections:
            sec_text = s["text"]
            # 若切片超长，进一步滑动窗口切分
            if len(sec_text) > cls.MAX_CHUNK_CHARS:
                sub_chunks = cls._split_sliding_window(
                    sec_text, cls.MAX_CHUNK_CHARS, cls.OVERLAP_CHARS
                )
                for i, sub in enumerate(sub_chunks):
                    hdr = f"# Doc: {rel_path} | Section: {s['section']} (Part {i+1})\n"
                    chunks.append(
                        CodeChunk(
                            rel_path=rel_path,
                            title=title,
                            section_path=f"{s['section']}#p{i+1}",
                            start_line=s["start"],
                            end_line=s["end"],
                            content_text=hdr + sub,
                            is_code=False,
                        )
                    )
            else:
                hdr = f"# Doc: {rel_path} | Section: {s['section']}\n"
                chunks.append(
                    CodeChunk(
                        rel_path=rel_path,
                        title=title,
                        section_path=s["section"],
                        start_line=s["start"],
                        end_line=s["end"],
                        content_text=hdr + sec_text,
                        is_code=False,
                    )
                )

        return chunks if chunks else cls._chunk_plain_text(content, rel_path, title, is_code=False)

    @classmethod
    def _chunk_plain_text(
        cls, content: str, rel_path: str, title: str, is_code: bool = False
    ) -> list[CodeChunk]:
        """纯文本按行数与字符滑动窗口切分。"""
        if not content.strip():
            return []
        lines = content.splitlines(keepends=True)
        sub_texts = cls._split_sliding_window(content, cls.MAX_CHUNK_CHARS, cls.OVERLAP_CHARS)
        chunks: list[CodeChunk] = []
        for idx, text in enumerate(sub_texts):
            hdr = f"# File: {rel_path} (Part {idx + 1})\n"
            chunks.append(
                CodeChunk(
                    rel_path=rel_path,
                    title=title,
                    section_path=f"part_{idx + 1}",
                    start_line=1,
                    end_line=len(lines),
                    content_text=hdr + text,
                    is_code=is_code,
                )
            )
        return chunks

    @staticmethod
    def _split_sliding_window(text: str, max_chars: int, overlap: int) -> list[str]:
        """滑动窗口切分字符串。"""
        results: list[str] = []
        start = 0
        text_len = len(text)
        while start < text_len:
            end = min(start + max_chars, text_len)
            segment = text[start:end].strip()
            if segment:
                results.append(segment)
            if end >= text_len:
                break
            start += max_chars - overlap
        return results
