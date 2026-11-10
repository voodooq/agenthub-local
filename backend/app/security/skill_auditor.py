"""AgentHub Local - 供应链与技能导入安全审计网关 (Skill Auditor)

在第三方 Skill 安装、导入或热更新前，执行 AST 静态语法树深度扫描与 Prompt 注入特征检测。
彻底防范恶意代码删盘、窃取环境变量凭据与模型越狱行为。
"""

import ast
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List


@dataclass
class AuditIssue:
    severity: str  # "CRITICAL" | "WARNING"
    file_name: str
    line_no: int
    code_snippet: str
    reason: str


# 高危破坏性与进程越权函数黑名单
CRITICAL_CALLS = {
    "os.remove",
    "os.unlink",
    "os.rmdir",
    "os.removedirs",
    "os.system",
    "os.popen",
    "shutil.rmtree",
    "shutil.move",
    "Path.unlink",
    "Path.rmdir",
    "subprocess.Popen",
    "subprocess.run",
    "subprocess.call",
    "subprocess.check_output",
    "eval",
    "exec",
    "compile",
    "__import__",
    "ctypes.windll",
    "ctypes.cdll",
}

# 敏感网络/外部通信模块
WARNING_MODULES = {
    "socket",
    "requests",
    "urllib",
    "httpx",
    "aiohttp",
    "ftplib",
    "smtplib",
    "paramiko",
}

# Prompt 注入与越狱诱导模式特征库
PROMPT_INJECTION_PATTERNS = [
    (r"(?i)ignore\s+(all\s+)?previous\s+instructions", "疑似包含覆盖系统指令的越狱诱导话术"),
    (
        r"(?i)(rm\s+-rf|del\s+/[fqs]|format\s+[a-z]:|remove-item\s+.*-recurse)",
        "包含高危删盘或清空目录 Shell 指令",
    ),
    (r"(?i)base64\s+(-d|--decode)", "包含 Base64 混淆解码执行指令"),
    (r"(?i)bypass\s+security\s+gatekeeper", "疑似包含越过安全网关的指令"),
]


class SkillASTVisitor(ast.NodeVisitor):
    """Python AST 静态访问器，用于定位潜在高危函数调用。"""

    def __init__(self, filename: str, source_lines: List[str]):
        self.filename = filename
        self.source_lines = source_lines
        self.issues: List[AuditIssue] = []
        self.aliases: Dict[str, str] = {}

    def _get_line(self, lineno: int) -> str:
        if 0 < lineno <= len(self.source_lines):
            return self.source_lines[lineno - 1].strip()
        return ""

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            real_name = alias.name.split(".")[0]
            if real_name in WARNING_MODULES:
                self.issues.append(
                    AuditIssue(
                        "WARNING",
                        self.filename,
                        node.lineno,
                        self._get_line(node.lineno),
                        f"引入网络外部通信模块: `{real_name}`",
                    )
                )
            if real_name == "ctypes":
                self.issues.append(
                    AuditIssue(
                        "CRITICAL",
                        self.filename,
                        node.lineno,
                        self._get_line(node.lineno),
                        "引入底层 C 接口模块 ctypes (可能导致沙箱击穿)",
                    )
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        mod_name = node.module or ""
        if mod_name in WARNING_MODULES:
            self.issues.append(
                AuditIssue(
                    "WARNING",
                    self.filename,
                    node.lineno,
                    self._get_line(node.lineno),
                    f"从网络模块 `{mod_name}` 引入对象",
                )
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        func_name = self._resolve_func_name(node.func)
        if func_name in CRITICAL_CALLS or func_name.endswith(
            (".unlink", ".rmdir", ".rmtree")
        ):
            self.issues.append(
                AuditIssue(
                    "CRITICAL",
                    self.filename,
                    node.lineno,
                    self._get_line(node.lineno),
                    f"检测到高危系统破坏/越权执行调用: `{func_name}()`",
                )
            )
        self.generic_visit(node)

    def _resolve_func_name(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return self.aliases.get(node.id, node.id)
        elif isinstance(node, ast.Attribute):
            prefix = self._resolve_func_name(node.value)
            return f"{prefix}.{node.attr}" if prefix else node.attr
        return ""


def audit_skill_package(skill_dir: Path) -> dict:
    """对整个 Skill 包目录执行全方位静态代码与提示词审计。

    Args:
        skill_dir: 技能目录根路径。

    Returns:
        包含审计结果、风险评级和问题列表的字典。
    """
    issues: List[AuditIssue] = []

    # 1. 扫描 SKILL.md 提示词与脚本说明
    skill_md = skill_dir / "SKILL.md"
    if skill_md.exists():
        try:
            content = skill_md.read_text(encoding="utf-8", errors="replace")
            for idx, line in enumerate(content.splitlines(), 1):
                for pat, reason in PROMPT_INJECTION_PATTERNS:
                    if re.search(pat, line):
                        issues.append(
                            AuditIssue(
                                "CRITICAL",
                                "SKILL.md",
                                idx,
                                line.strip()[:80],
                                reason,
                            )
                        )
        except Exception as e:
            issues.append(
                AuditIssue("WARNING", "SKILL.md", 0, "", f"读取 SKILL.md 失败: {e}")
            )

    # 2. 遍历扫描所有 Python 脚本文件
    for py_file in skill_dir.rglob("*.py"):
        rel_name = py_file.relative_to(skill_dir).as_posix()
        try:
            source = py_file.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source, filename=rel_name)
            visitor = SkillASTVisitor(rel_name, source.splitlines())
            visitor.visit(tree)
            issues.extend(visitor.issues)
        except SyntaxError as e:
            issues.append(
                AuditIssue(
                    "CRITICAL",
                    rel_name,
                    e.lineno or 0,
                    "",
                    f"Python 语法错误无法解析: {e.msg}",
                )
            )
        except Exception as e:
            issues.append(
                AuditIssue("WARNING", rel_name, 0, "", f"读取或解析异常: {e}")
            )

    has_critical = any(i.severity == "CRITICAL" for i in issues)
    risk_level = "CRITICAL" if has_critical else ("WARNING" if issues else "SAFE")

    return {
        "passed": not has_critical,
        "risk_level": risk_level,
        "issues": [asdict(i) for i in issues],
        "summary": (
            "✅ 审计通过，未检测到破坏性行为"
            if not has_critical
            else f"🚨 拦截高危风险: 发现 {sum(1 for i in issues if i.severity == 'CRITICAL')} 项严重违规"
        ),
    }

