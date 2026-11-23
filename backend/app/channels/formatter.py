"""
IM 出站富文本保真格式化器：
1. 剥离内部控制注释（如 <!-- FOLLOWUP_SUGGESTIONS: [...] -->）；
2. 将 Markdown 宽表格按「表头: 值」逐行降级为微信/IM 易读的紧凑列表，完整保留数字、单位与核心结论；
3. 按段落/行边界安全分片，默认留出充足余量适配微信/企微单条消息长度限制。
"""
from __future__ import annotations

import re

from ..schema.intent_router import extract_followup_suggestions

_SEP_ROW_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")
_TOOL_CALL_RE = re.compile(r"<tool_call\b[^>]*>[\s\S]*?(?:</tool_call>|$)", re.IGNORECASE)
_THINK_TAG_RE = re.compile(r"<think\b[^>]*>[\s\S]*?(?:</think>|$)", re.IGNORECASE)
_FUNC_CALL_RE = re.compile(r"<function_call\b[^>]*>[\s\S]*?(?:</function_call>|$)", re.IGNORECASE)
_CODE_FENCE_RE = re.compile(r"```[\s\S]*?```", re.MULTILINE)

# IM 端后台信息脱敏规则：严禁泄露文件后缀、doc_id、tool_ref、切片号与中间检索旁白
_BOOK_TITLE_EXT_RE = re.compile(
    r"《([^》]+?)\.(?:pdf|docx?|xlsx?|pptx?|csv|txt|md|json|html?|rtf)》",
    re.IGNORECASE,
)
_INLINE_DOC_EXT_RE = re.compile(
    r"(?<![A-Za-z0-9_:/\\])([\u4e00-\u9fffA-Za-z0-9_\-]{2,})\.(?:pdf|docx?|xlsx?|pptx?|csv|txt|md)\b",
    re.IGNORECASE,
)
_PAREN_INTERNAL_ID_RE = re.compile(
    r"[（(]\s*(?:(?:doc_id|kb_id|chunk_id|slice_id)\s*[:：=]?\s*[A-Za-z0-9_\-]+|tool_ref_[A-Za-z0-9_]+)\s*[）)]",
    re.IGNORECASE,
)
_BARE_INTERNAL_ID_RE = re.compile(
    r"(?:\b(?:doc_id|kb_id|chunk_id|slice_id)\s*[:：=]\s*[A-Za-z0-9_\-]+|\btool_ref_[A-Za-z0-9_]+\b)",
    re.IGNORECASE,
)
_SLICE_REF_RE = re.compile(
    r"(?:刚才)?第\s*\d+\s*(?:[-~至到]\s*\d+\s*)?个?切片(?:的|中)?",
)
_PROCESS_NARRATION_RE = re.compile(
    r"^\s*(?:我将|我先|正在|接下来我将|下面我将|让我先?)"
    r"(?:为您|帮您|先)?(?:读取|查询|检索|查找|查看|查阅|翻阅|调取|获取|搜索)"
    r"[^\n]{0,140}(?:切片|doc_id|tool_ref|原文|知识库|文档|规程|详情|内容|通知)[^\n]{0,40}[。！!]?\s*$",
    re.IGNORECASE,
)


def _sanitize_im_internal_leaks(text: str) -> str:
    """剥离 IM 用户不关心且易泄露后台细节的文件后缀、doc_id/tool_ref、切片号及检索过程旁白。"""
    # 1. 先按行剔除纯检索过程播报（如“我将读取《xxx.pdf》第 5 至 9 切片...”）
    kept_lines: list[str] = []
    for line in text.splitlines():
        if _PROCESS_NARRATION_RE.match(line.strip()):
            continue
        kept_lines.append(line)
    cleaned = "\n".join(kept_lines)

    # 2. 剥离括号内及裸露的 doc_id / tool_ref / 切片编号
    cleaned = _PAREN_INTERNAL_ID_RE.sub("", cleaned)
    cleaned = _BARE_INTERNAL_ID_RE.sub("", cleaned)
    cleaned = _SLICE_REF_RE.sub("", cleaned)

    # 3. 剥离书名号与正文中的文档文件后缀（如 《昆明D赛竞赛规程.pdf》 -> 《昆明D赛竞赛规程》）
    cleaned = _BOOK_TITLE_EXT_RE.sub(r"《\1》", cleaned)
    cleaned = _INLINE_DOC_EXT_RE.sub(r"\1", cleaned)

    # 4. 清理剥离后残留的多余空格与空括号
    cleaned = re.sub(r"[（(]\s*[）)]", "", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned


def _convert_markdown_tables_to_im_list(text: str) -> str:
    """将 Markdown 表格转换为紧凑易读的「列标题: 数值」条目，防止微信气泡错乱且不丢数字。"""
    lines = text.splitlines()
    out_lines: list[str] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        # 检测表头行 + 分隔行
        if (
            "|" in line
            and i + 1 < n
            and _SEP_ROW_RE.match(lines[i + 1])
        ):
            headers = [c.strip().strip("*") for c in line.strip().strip("|").split("|")]
            i += 2
            row_idx = 0
            while i < n and "|" in lines[i] and lines[i].strip():
                cells = [c.strip().strip("*") for c in lines[i].strip().strip("|").split("|")]
                row_idx += 1
                pairs: list[str] = []
                for col_i, cell in enumerate(cells):
                    if not cell:
                        continue
                    h_name = headers[col_i] if col_i < len(headers) else f"列{col_i + 1}"
                    if h_name in ("序号", "#", "No"):
                        continue
                    pairs.append(f"{h_name}: {cell}")
                if pairs:
                    out_lines.append(f"• {' | '.join(pairs)}")
                i += 1
            out_lines.append("")
            continue

        out_lines.append(line)
        i += 1

    return "\n".join(out_lines)


def format_for_im_channel(
    raw_markdown: str, *, max_chunk_chars: int = 1500
) -> tuple[str, list[str]]:
    """将专家 Markdown 回复格式化为 IM 友好纯文本并按行边界安全分片（彻底剥离 tool_call/think/代码块等源码噪音）。"""
    clean_md, _ = extract_followup_suggestions(raw_markdown or "")
    # 严防源码/工具协议字符泄露至 IM 客户端
    clean_md = _TOOL_CALL_RE.sub("", clean_md)
    clean_md = _THINK_TAG_RE.sub("", clean_md)
    clean_md = _FUNC_CALL_RE.sub("", clean_md)
    clean_md = _CODE_FENCE_RE.sub("", clean_md)
    # 转换宽表格为保真紧凑列表
    text = _convert_markdown_tables_to_im_list(clean_md)
    # 清理 Markdown 语法噪音（加粗星号、行内反引号、引用尖括号、分割线），让微信气泡呈现自然口语纯文本
    text = re.sub(r"^\s*[-*_]{3,}\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*>\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # 通用脱敏：剥离文件后缀、doc_id/tool_ref、切片号与纯检索过程旁白
    text = _sanitize_im_internal_leaks(text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    if not text:
        return "", []

    limit = max(300, max_chunk_chars)
    if len(text) <= limit:
        return text, [text]

    chunks: list[str] = []
    current_lines: list[str] = []
    current_len = 0

    for line in text.splitlines():
        line_len = len(line) + 1
        if current_lines and current_len + line_len > limit:
            chunks.append("\n".join(current_lines).strip())
            current_lines = [line]
            current_len = line_len
        elif line_len > limit:
            # 单行超长时按句号/分号安全切分，避免截断数字
            if current_lines:
                chunks.append("\n".join(current_lines).strip())
                current_lines = []
                current_len = 0
            start = 0
            while start < len(line):
                chunks.append(line[start : start + limit])
                start += limit
        else:
            current_lines.append(line)
            current_len += line_len

    if current_lines:
        chunks.append("\n".join(current_lines).strip())

    # 多分片时自动标注 (1/N) 方便微信端阅读顺序
    if len(chunks) > 1:
        total = len(chunks)
        chunks = [f"({idx + 1}/{total})\n{c}" for idx, c in enumerate(chunks)]

    return text, chunks

