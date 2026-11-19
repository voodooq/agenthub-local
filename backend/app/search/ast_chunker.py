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


@dataclass
class HierarchicalChunk:
    """父子分层切片模型 (v1.5.9+)"""
    id: str
    chunk_role: str  # 'parent' | 'child' | 'hype_question'
    parent_chunk_id: str | None
    section_path: str
    content_text: str
    title: str
    remote_uri: str
    authority_tier: int = 2
    doc_year: int = 0
    effective_date: str = ""
    effective_ts: int = 0
    doc_priority_type: str = "normal"
    topic_cluster_id: str = ""
    is_latest_in_cluster: int = 1
    mtime: float = 0.0

    @property
    def content(self) -> str:
        return self.content_text

    @content.setter
    def content(self, val: str) -> None:
        self.content_text = val


class ParentChildASTChunker:
    """父子分层结构化切分器 (ParentChildASTChunker)。
    
    1. 父切片 (Parent Chunk): 目标 1000~1500 字，按公文标题层级或 Markdown 章节聚合；
       对连续 '|' 开头的 Markdown 表格执行原子保护，整表不跨切片截断；
    2. 子切片 (Child Chunk): 目标 200~280 字 (40 字重叠)，注入来源与章节元数据前缀，
       绑定所属 parent_chunk_id，用于向量化与全文召回；
    """

    PARENT_MAX_CHARS = 1400
    CHILD_MAX_CHARS = 260
    CHILD_OVERLAP_CHARS = 40

    # 中文公文与 Markdown 标题正则匹配器
    SECTION_HEADER_RE = re.compile(
        r"^(?:"
        r"#{1,4}\s+.+|"  # Markdown 标题 # ## ### ####
        r"[第][一二三四五六七八九十百]+[条章节部篇卷]\s*.+|"  # 第一条 / 第一章
        r"[一二三四五六七八九十]+[、.]\s*.+|"  # 一、 竞赛办法
        r"[(（][一二三四五六七八九十]+[)）]\s*.+|"  # (一) 器材装备
        r"\d{1,2}[、.]\s*.+"  # 1. 2.
        r")$",
        re.MULTILINE,
    )

    @classmethod
    def chunk_document(
        cls,
        text: str,
        filename: str = "",
        remote_uri: str = "",
        authority_tier: int = 2,
        doc_year: int = 0,
        effective_date: str = "",
        effective_ts: int = 0,
        doc_priority_type: str = "normal",
        topic_cluster_id: str = "",
        is_latest_in_cluster: int = 1,
        mtime: float = 0.0,
        title: str = "",
    ) -> list[HierarchicalChunk]:
        """将规程或文档切分为父子两级切片。"""
        if not filename and title:
            filename = title
        if not text.strip():
            return []

        import hashlib

        # 1. 提取并保护 Markdown 表格块 (将连续 table 行整块保护)
        cleaned_text = cls._preserve_markdown_tables(text)

        # 2. 按标题和段落分段聚合父切片
        parent_blocks = cls._aggregate_parent_blocks(cleaned_text, filename)

        all_chunks: list[HierarchicalChunk] = []

        for p_idx, (sec_path, p_content) in enumerate(parent_blocks, 1):
            p_content_hash = hashlib.md5(p_content.encode("utf-8")).hexdigest()
            parent_id = hashlib.md5(f"parent:{remote_uri}:{sec_path}:{p_content_hash}".encode("utf-8")).hexdigest()

            # 写入父切片
            parent_chunk = HierarchicalChunk(
                id=parent_id,
                chunk_role="parent",
                parent_chunk_id=None,
                section_path=sec_path,
                content_text=p_content,
                title=filename,
                remote_uri=remote_uri,
                authority_tier=authority_tier,
                doc_year=doc_year,
                effective_date=effective_date,
                effective_ts=effective_ts,
                doc_priority_type=doc_priority_type,
                topic_cluster_id=topic_cluster_id,
                is_latest_in_cluster=is_latest_in_cluster,
                mtime=mtime,
            )
            all_chunks.append(parent_chunk)

            # 3. 在父切片内部切割子切片 (Child Chunks)
            child_texts = cls._split_into_children(p_content, cls.CHILD_MAX_CHARS, cls.CHILD_OVERLAP_CHARS)
            for c_idx, c_raw in enumerate(child_texts, 1):
                # 拼接时空上下文强元数据前缀
                date_str = effective_date or (str(doc_year) if doc_year else "通用")
                header_prefix = f"【来源】{filename} (生效日期: {date_str}) | 【章节】{sec_path}\n"
                child_content = header_prefix + c_raw

                c_hash = hashlib.md5(child_content.encode("utf-8")).hexdigest()
                child_id = hashlib.md5(f"child:{parent_id}:{c_idx}:{c_hash}".encode("utf-8")).hexdigest()

                child_chunk = HierarchicalChunk(
                    id=child_id,
                    chunk_role="child",
                    parent_chunk_id=parent_id,
                    section_path=f"{sec_path}#c{c_idx}",
                    content_text=child_content,
                    title=filename,
                    remote_uri=remote_uri,
                    authority_tier=authority_tier,
                    doc_year=doc_year,
                    effective_date=effective_date,
                    effective_ts=effective_ts,
                    doc_priority_type=doc_priority_type,
                    topic_cluster_id=topic_cluster_id,
                    is_latest_in_cluster=is_latest_in_cluster,
                    mtime=mtime,
                )
                all_chunks.append(child_chunk)

        return all_chunks

    @classmethod
    def _preserve_markdown_tables(cls, text: str) -> str:
        """保护 Markdown 表格：确保整表行之间无空行打断，便于作为原子块识别。"""
        lines = text.splitlines()
        result_lines: list[str] = []
        in_table = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("|") and stripped.endswith("|"):
                in_table = True
                result_lines.append(line)
            else:
                if in_table and not stripped:
                    # 表格内空行，跳过以防断表
                    continue
                in_table = False
                result_lines.append(line)
        return "\n".join(result_lines)

    @classmethod
    def _aggregate_parent_blocks(cls, text: str, filename: str) -> list[tuple[str, str]]:
        """按标题与段落边界聚合 1000~1500 字符的父切片。"""
        lines = text.splitlines()
        sections: list[tuple[str, list[str]]] = []
        current_title = "正文"
        current_lines: list[str] = []

        for line in lines:
            stripped = line.strip()
            if cls.SECTION_HEADER_RE.match(stripped):
                if current_lines:
                    sections.append((current_title, current_lines))
                    current_lines = []
                current_title = stripped.lstrip("#").strip()
            current_lines.append(line)

        if current_lines:
            sections.append((current_title, current_lines))

        # 将过长的单个小节再按段落聚合成 PARENT_MAX_CHARS 大小的块，整表不切断
        parents: list[tuple[str, str]] = []
        for sec_title, sec_lines in sections:
            accum_lines: list[str] = []
            accum_len = 0
            sub_idx = 1
            for line in sec_lines:
                line_len = len(line) + 1
                # 如果当前是一张 Markdown 表格的行，优先保持完整
                if accum_len + line_len > cls.PARENT_MAX_CHARS and accum_lines and not (line.strip().startswith("|") and line.strip().endswith("|")):
                    p_text = "\n".join(accum_lines).strip()
                    if p_text:
                        s_name = f"{sec_title} (分段{sub_idx})" if sub_idx > 1 else sec_title
                        parents.append((s_name, p_text))
                        sub_idx += 1
                    accum_lines = []
                    accum_len = 0
                accum_lines.append(line)
                accum_len += line_len

            if accum_lines:
                p_text = "\n".join(accum_lines).strip()
                if p_text:
                    s_name = f"{sec_title} (分段{sub_idx})" if sub_idx > 1 else sec_title
                    parents.append((s_name, p_text))

        return parents if parents else [("正文", text)]

    @classmethod
    def _split_into_children(cls, text: str, max_chars: int, overlap: int) -> list[str]:
        """将父切片文本滑动切分为子切片。"""
        if len(text) <= max_chars:
            return [text.strip()]

        # 优先按段落切分
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        chunks: list[str] = []
        cur_chunk: list[str] = []
        cur_len = 0

        for p in paragraphs:
            p_len = len(p)
            if cur_len + p_len > max_chars and cur_chunk:
                joined = "\n\n".join(cur_chunk).strip()
                if joined:
                    chunks.append(joined)
                cur_chunk = []
                cur_len = 0
            cur_chunk.append(p)
            cur_len += p_len + 2

        if cur_chunk:
            joined = "\n\n".join(cur_chunk).strip()
            if joined:
                chunks.append(joined)

        # 若段落聚合后仍有超过 max_chars 的（如大表格），按字符滑动窗口兜底
        final_children: list[str] = []
        for c in chunks:
            if len(c) > max_chars:
                start = 0
                while start < len(c):
                    end = min(start + max_chars, len(c))
                    sub = c[start:end].strip()
                    if sub:
                        final_children.append(sub)
                    if end >= len(c):
                        break
                    start += max_chars - overlap
            else:
                final_children.append(c)

        return final_children if final_children else [text]
