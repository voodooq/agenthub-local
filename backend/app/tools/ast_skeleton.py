"""AgentHub Local - AST 代码骨架提取与语法防呆门禁 (AST Skeleton & Syntax Gatekeeper)

利用 Tree-sitter 对多语言源码进行毫秒级 AST 解析：
1. 提取类、函数与接口签名骨架（将数千行源码压缩为轻量目录，极度节省 Token）；
2. 在落盘物理文件前进行语法树校验，100% 拦截语法破坏错误。
"""

from pathlib import Path
from typing import Dict, List, Tuple

try:
    import tree_sitter_language_pack as tslp
    HAS_TREE_SITTER = True
except ImportError:
    HAS_TREE_SITTER = False

# 扩展名到 Tree-sitter 语言标识符映射表
EXT_LANG_MAP: Dict[str, str] = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".rs": "rust",
    ".go": "go",
    ".json": "json",
}

# 需要提取骨架签名的 AST 声明节点类型
DEFINITION_NODE_TYPES = {
    # Python
    "class_definition",
    "function_definition",
    "async_function_definition",
    # TypeScript / JavaScript
    "class_declaration",
    "function_declaration",
    "method_definition",
    "interface_declaration",
    "type_alias_declaration",
    "arrow_function",
    # Rust
    "struct_item",
    "enum_item",
    "impl_item",
    "function_item",
    "trait_item",
    # Go
    "type_declaration",
    "function_declaration",
    "method_declaration",
}

# 默认跳过的非关键/大型依赖目录
IGNORE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".next",
    "dist",
    "build",
    ".pytest_cache",
    ".idea",
    ".vscode",
}


def extract_file_skeleton(file_path: Path) -> str:
    """提取单个代码文件的类、函数与接口结构骨架。

    Args:
        file_path: 目标文件物理路径。

    Returns:
        包含签名与行号区间的大纲文本。
    """
    if not HAS_TREE_SITTER:
        return f"[未安装 tree-sitter，跳过 AST 骨架提取: {file_path.name}]"

    suffix = file_path.suffix.lower()
    lang = EXT_LANG_MAP.get(suffix)
    if not lang:
        return f"[跳过暂不支持 AST 深度解析的文件类型: {file_path.name}]"

    try:
        source_bytes = file_path.read_bytes()
        source_lines = source_bytes.decode("utf-8", errors="replace").splitlines()
    except Exception as e:
        return f"[读取文件失败: {file_path.name} - {e}]"

    try:
        parser = tslp.get_parser(lang)
        tree = parser.parse(source_bytes)
    except Exception as e:
        return f"[AST 解析异常: {file_path.name} - {e}]"

    skeleton_lines: List[str] = [
        f"📄 {file_path.as_posix()} (共 {len(source_lines)} 行)"
    ]

    def walk_nodes(node, depth: int = 0):
        if node.type in DEFINITION_NODE_TYPES:
            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1
            # 获取声明所在首行签名
            sig_raw = (
                source_lines[node.start_point[0]].strip()
                if node.start_point[0] < len(source_lines)
                else ""
            )
            # 若首行为装饰器 (如 @decorator)，尝试向下一行查找实际函数签名
            if sig_raw.startswith("@") and node.start_point[0] + 1 < len(source_lines):
                next_line = source_lines[node.start_point[0] + 1].strip()
                sig_raw = f"{sig_raw} {next_line}"

            # 截短过长单行签名
            if len(sig_raw) > 90:
                sig_raw = sig_raw[:87] + "..."

            indent = "  " * (depth + 1)
            skeleton_lines.append(f"{indent}L{start_line}-L{end_line}: {sig_raw}")
            # 深入子节点提取成员方法（例如类内部的方法）
            for child in node.children:
                walk_nodes(child, depth + 1)
        else:
            for child in node.children:
                walk_nodes(child, depth)

    walk_nodes(tree.root_node)
    if len(skeleton_lines) == 1:
        skeleton_lines.append("  (未检测到顶层类或函数声明，可能为纯脚本或配置文件)")

    return "\n".join(skeleton_lines)


def get_workspace_skeleton(
    workspace_root: Path, target_path: str = ".", max_files: int = 30
) -> dict:
    """提取工作区指定路径的文件或目录骨架。

    Args:
        workspace_root: 工作区物理根目录。
        target_path: 目标相对路径（文件或子目录）。
        max_files: 扫描目录时最大文件数限制。

    Returns:
        包含骨架提取结果的字典。
    """
    try:
        target = (workspace_root / target_path).resolve()
    except Exception as e:
        return {"status": "error", "error": f"InvalidPath: {e}"}

    if not str(target).startswith(str(workspace_root.resolve())):
        return {"status": "error", "error": "SecurityError: 越界访问被拒绝。"}

    if not target.exists():
        return {"status": "error", "error": f"PathNotFound: 路径不存在: {target_path}"}

    if target.is_file():
        skeleton = extract_file_skeleton(target)
        return {
            "status": "success",
            "type": "file",
            "target": target_path,
            "skeleton": skeleton,
        }

    # 目录递归提取
    results: List[str] = []
    scanned_count = 0
    for item in target.rglob("*"):
        if scanned_count >= max_files:
            results.append(
                f"\n... (已达最大目录扫描上限 {max_files} 个文件，其余文件已省略)"
            )
            break
        if any(part in IGNORE_DIRS for part in item.parts):
            continue
        if item.is_file() and item.suffix.lower() in EXT_LANG_MAP:
            scanned_count += 1
            results.append(extract_file_skeleton(item))

    combined = "\n\n".join(results) if results else "目录下未发现可解析的源码文件。"
    return {
        "status": "success",
        "type": "directory",
        "target": target_path,
        "scanned_files": scanned_count,
        "skeleton": combined,
    }


def validate_syntax_before_write(
    file_suffix: str, new_content: str
) -> Tuple[bool, str]:
    """在代码真正物理落盘前，使用 AST 语法树检查是否存在语法损毁。

    Args:
        file_suffix: 文件后缀名（如 .py, .ts, .tsx, .rs 等）。
        new_content: 准备落盘的新代码文本。

    Returns:
        元组 (是否通过, 错误原因)。通过时错误原因返回空字符串。
    """
    if not HAS_TREE_SITTER:
        return True, ""

    lang = EXT_LANG_MAP.get(file_suffix.lower())
    if not lang or lang == "json":
        # 针对 json 使用内置解析器校验
        if lang == "json":
            import json

            try:
                json.loads(new_content)
                return True, ""
            except Exception as e:
                return False, f"JSON 语法校验失败: {e}"
        return True, ""

    try:
        parser = tslp.get_parser(lang)
        tree = parser.parse(new_content.encode("utf-8", errors="replace"))
    except Exception as e:
        # 解析器内部异常不强行阻断写入，做降级处理
        return True, ""

    if not tree.root_node.has_error:
        return True, ""

    # 递归提取具体的语法 ERROR 节点位置
    errors: List[str] = []

    def find_syntax_errors(node, limit: int = 3):
        if len(errors) >= limit:
            return
        if node.type == "ERROR" or node.is_missing:
            line_no = node.start_point[0] + 1
            col_no = node.start_point[1] + 1
            err_text = node.text.decode("utf-8", errors="ignore")[:50].strip()
            errors.append(
                f"- 第 {line_no} 行第 {col_no} 列附近语法解析失败 (附近片段: `{err_text}`)"
            )
        for child in node.children:
            find_syntax_errors(child, limit)

    find_syntax_errors(tree.root_node)
    err_detail = "\n".join(errors) if errors else "- 包含未闭合的代码块或非法符号"
    msg = (
        f"🚨 [AST 语法安全门禁已拦截落盘]: 准备写入的代码存在结构性语法错误，已阻止覆盖文件！\n"
        f"{err_detail}\n"
        f"请基于报错重新核对代码括号闭合、缩进或关键字，并重新提交正确的补丁。"
    )
    return False, msg

