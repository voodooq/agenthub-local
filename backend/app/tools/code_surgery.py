"""AgentHub Local - 精准代码手术刀 (Code Surgery Tools)

提供按行号切片读取、局部 SEARCH/REPLACE 补丁算子、三级容错匹配及 Unified Diff 生成。
避免模型大文件全量重写，大幅削减 Token 消耗与语法破坏风险。
"""

import difflib
import re
from pathlib import Path
from typing import List, Tuple

# 标准 SEARCH/REPLACE 块正则匹配器（兼容 Windows CRLF 与 Linux LF）
BLOCK_PATTERN = re.compile(
    r"<<<<<<< SEARCH\r?\n(.*?)\r?\n=======\r?\n(.*?)\r?\n>>>>>>> REPLACE",
    re.DOTALL,
)


def read_local_file_sliced(
    workspace_root: Path,
    file_path: str,
    start_line: int = 1,
    end_line: int = 250,
) -> dict:
    """按行号区间安全切片读取本地文件内容并附带行号。

    Args:
        workspace_root: 工作区物理根目录。
        file_path: 文件相对或绝对路径。
        start_line: 起始行号（从 1 开始）。
        end_line: 结束行号。

    Returns:
        包含读取状态、总行数、切片区间与带行号内容的字典。
    """
    try:
        target = (workspace_root / file_path).resolve()
    except Exception as e:
        return {"status": "error", "error": f"InvalidPathError: 路径格式不合法: {e}"}

    # 路径越界沙箱安全检查
    try:
        resolved_root = workspace_root.resolve()
        if not str(target).startswith(str(resolved_root)):
            return {
                "status": "error",
                "error": "SecurityError: 路径越界访问被安全网关拒绝。",
            }
    except Exception:
        return {"status": "error", "error": "SecurityError: 无法校验路径安全性。"}

    if not target.exists() or not target.is_file():
        return {"status": "error", "error": f"FileNotFound: 目标文件不存在: {file_path}"}

    try:
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as e:
        return {"status": "error", "error": f"ReadError: 读取文件失败: {e}"}

    total_lines = len(lines)
    # 行号区间边界保护：单次最大跨度 500 行
    s_line = max(1, start_line)
    e_line = min(total_lines, max(s_line, min(end_line, s_line + 500)))
    start_idx = s_line - 1
    end_idx = e_line

    numbered_lines = [
        f"{idx:4d} | {line}"
        for idx, line in enumerate(lines[start_idx:end_idx], start=start_idx + 1)
    ]
    header = f"[File: {file_path} | Lines {s_line}-{e_line} of {total_lines}]\n"
    content = header + "\n".join(numbered_lines)

    return {
        "status": "success",
        "file_path": file_path,
        "total_lines": total_lines,
        "shown_range": [s_line, e_line],
        "content": content,
    }


def apply_search_replace_blocks(
    original_text: str, diff_blocks_str: str
) -> Tuple[bool, str, str]:
    """对原始代码文本应用一个或多个 SEARCH/REPLACE 局部补丁块。

    Args:
        original_text: 原始文本内容。
        diff_blocks_str: 包含一个或多个 SEARCH/REPLACE 块的文本。

    Returns:
        元组 (是否成功, 修改后完整文本或错误提示, unified_diff 字符串)。
    """
    blocks = BLOCK_PATTERN.findall(diff_blocks_str)
    if not blocks:
        return (
            False,
            "格式错误：未检测到标准的 <<<<<<< SEARCH ... ======= ... >>>>>>> REPLACE 结构块。\n"
            "请严格使用如下格式：\n"
            "<<<<<<< SEARCH\n"
            "原代码若干行\n"
            "=======\n"
            "新代码若干行\n"
            ">>>>>>> REPLACE",
            "",
        )

    current_text = original_text
    for idx, (search_block, replace_block) in enumerate(blocks, 1):
        # Level 1: 精确匹配
        if search_block in current_text:
            match_count = current_text.count(search_block)
            if match_count > 1:
                return (
                    False,
                    f"Block #{idx} 匹配到 {match_count} 处相同代码片段，目标不唯一！\n"
                    "请扩大 SEARCH 块的上下文字文行（多包含几行不可变的前后代码），以确保其在文件中的唯一性。",
                    "",
                )
            current_text = current_text.replace(search_block, replace_block, 1)
            continue

        # Level 2: 换行符统一与缩进/行尾空白容错匹配
        matched, updated = _indent_tolerant_replace(
            current_text, search_block, replace_block
        )
        if matched:
            current_text = updated
            continue

        # Level 3: 相似度滑窗诊断与精准纠偏提示
        hint = _find_closest_match_hint(current_text, search_block)
        return (
            False,
            f"Block #{idx} 在目标文件中无法唯一定位。\n{hint}\n"
            "提示：请先调用 read_local_file 查看最新真实行号与代码，再提交精准补丁。",
            "",
        )

    # 生成标准 Unified Diff 用于前端红绿高亮与审计
    diff_lines = list(
        difflib.unified_diff(
            original_text.splitlines(keepends=True),
            current_text.splitlines(keepends=True),
            fromfile="a/original",
            tofile="b/modified",
            n=3,
        )
    )
    unified_diff = "".join(diff_lines)
    return True, current_text, unified_diff


def _indent_tolerant_replace(
    full_text: str, search_str: str, replace_str: str
) -> Tuple[bool, str]:
    """忽略行尾空白与相对基准缩进的容错匹配与替换。"""
    full_lines = full_text.splitlines()
    search_lines = [line.rstrip() for line in search_str.splitlines()]
    n = len(search_lines)
    if n == 0:
        return False, full_text

    def strip_base_indent(lines: List[str]) -> Tuple[List[str], int]:
        non_empty = [len(l) - len(l.lstrip()) for l in lines if l.strip()]
        base = min(non_empty) if non_empty else 0
        return [
            l[base:] if len(l) >= base else l.lstrip() for l in lines
        ], base

    norm_search, search_base = strip_base_indent(search_lines)

    for i in range(len(full_lines) - n + 1):
        window = [l.rstrip() for l in full_lines[i : i + n]]
        norm_window, window_base = strip_base_indent(window)
        if norm_window == norm_search:
            # 命中！根据源文件实际缩进差值重新对齐 replace_str
            indent_delta = window_base - search_base
            rep_lines = replace_str.splitlines()
            adjusted_rep = []
            for rl in rep_lines:
                if not rl.strip():
                    adjusted_rep.append("")
                elif indent_delta >= 0:
                    adjusted_rep.append((" " * indent_delta) + rl)
                else:
                    remove_n = min(abs(indent_delta), len(rl) - len(rl.lstrip()))
                    adjusted_rep.append(rl[remove_n:])

            new_lines = full_lines[:i] + adjusted_rep + full_lines[i + n :]
            ends_with_newline = full_text.endswith("\n") or full_text.endswith("\r\n")
            result = "\n".join(new_lines) + ("\n" if ends_with_newline else "")
            return True, result

    return False, full_text


def _find_closest_match_hint(full_text: str, search_str: str) -> str:
    """使用 SequenceMatcher 滑窗定位源文件中最接近的代码行区间并生成提示。"""
    full_lines = full_text.splitlines()
    search_lines = search_str.splitlines()
    n = len(search_lines)
    if n == 0 or len(full_lines) == 0:
        return "目标文件为空或 SEARCH 块为空。"

    best_ratio, best_start = 0.0, 0
    for i in range(max(1, len(full_lines) - n + 1)):
        chunk = "\n".join(full_lines[i : i + n])
        ratio = difflib.SequenceMatcher(None, search_str, chunk).ratio()
        if ratio > best_ratio:
            best_ratio, best_start = ratio, i

    if best_ratio > 0.5:
        match_len = min(n, len(full_lines) - best_start)
        snippet = "\n".join(
            f"{best_start + 1 + j:4d} | {full_lines[best_start + j]}"
            for j in range(match_len)
        )
        return (
            f"🔍 系统在第 {best_start + 1} 行附近发现了最相似的代码（相似度 {best_ratio:.0%}）：\n"
            f"{snippet}"
        )
    return "未能定位到与 SEARCH 块相似的代码段。"

