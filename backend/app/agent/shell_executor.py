"""
受控本地终端执行器 (LocalShellExecutor)
具备四大工程防线：
1. CWD 路径持久化：跨命令保持工作目录上下文（自动捕获 cd / Set-Location 变更）
2. 防交互卡死：强制 stdin=DEVNULL + 超时保护 + 引导非交互参数
3. 进程树强杀：Windows 下调用 taskkill /F /T /PID，Linux 下调用 killpg，杜绝残留僵尸进程
4. 编码容错与防绕过安全校验：UTF-8/GBK 双模解码 + 连接符(&&, |, ;, >)强制升级 CONFIRM 审批
"""
import asyncio
import base64
import os
import re
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..core.logging_config import logger
from ..core.runtime_paths import get_silent_subprocess_kwargs
from ..service.audit_service import audit_service


class LocalShellExecutor:
    """受控本地 Shell / PowerShell 执行器"""

    # 1. 黑名单高危破坏性指令正则（直接 DENY 拦截）
    _DENY_PATTERNS = [
        re.compile(r"\brm\s+(-[rRfF]+\s+)+(/|\*|~|c:\\|d:\\)", re.I),
        re.compile(r"\b(format|mkfs|fdisk|diskpart)\b", re.I),
        re.compile(r"\bdd\s+if=", re.I),
        re.compile(r"\b(del|erase|rd|rmdir)\s+(/[sSqQfF]+\s+)*[a-zA-Z]:\\?$", re.I),
        re.compile(r"\bRemove-Item\b.*-Recurse.*([a-zA-Z]:\\?$|/\s*$)", re.I),
        re.compile(r"\breg\s+(delete|add)\s+HKLM", re.I),
        re.compile(r"\b(shutdown|reboot|init\s+0|init\s+6|Restart-Computer|Stop-Computer)\b", re.I),
        re.compile(r":\(\)\{\s*:\|:&\s*\};:", re.I),
    ]

    # 2. 连接符、管道符、重定向符与子Shell符号（凡包含则禁止走 SAFE 直通，强制升级 CONFIRM）
    _COMPOUND_OPERATORS = re.compile(r"(&&|\|\||;|\||>|>>|<|\$\(|`)")

    # 3. 白名单纯只读前缀（仅在不含任何连接符/重定向符时判定为 SAFE）
    _SAFE_PREFIXES = (
        "git status",
        "git diff",
        "git log",
        "git branch",
        "git remote",
        "git show",
        "git rev-parse",
        "ls",
        "dir",
        "pwd",
        "get-location",
        "get-childitem",
        "cat ",
        "type ",
        "get-content ",
        "head ",
        "tail ",
        "whoami",
        "hostname",
        "ver",
        "uname",
        "get-process",
        "tasklist",
        "ps ",
        "netstat",
        "python --version",
        "python -V",
        "node -v",
        "node --version",
        "npm -v",
        "npm --version",
        "pip list",
        "pip show ",
        "where ",
        "which ",
        "findstr ",
        "grep ",
    )

    _CWD_MARKER = "__GH_CWD_MARKER__="
    # 识别需要可见桌面窗体的启动指令（未显式要求隐藏/无头时放行 GUI 窗口显示，避免继承 SW_HIDE）
    _GUI_LAUNCH_RE = re.compile(
        r"(\bStart-Process\b|\bcmd\s+/c\s+start\b|&\s*['\"]?[^'\"]*\b(msedge|chrome|explorer|notepad|code)\.exe\b)",
        re.I,
    )
    _HIDDEN_OR_HEADLESS_RE = re.compile(r"(-WindowStyle\s+Hidden\b|--headless\b)", re.I)

    @classmethod
    def _is_gui_launch_command(cls, command: str) -> bool:
        """判断命令是否旨在拉起可见桌面 GUI 应用，防止子窗体继承 SW_HIDE"""
        if not command or cls._HIDDEN_OR_HEADLESS_RE.search(command):
            return False
        return bool(cls._GUI_LAUNCH_RE.search(command))

    def __init__(self) -> None:
        # 挂起等待用户审批的事件字典: call_id -> asyncio.Event
        self._pending_approvals: dict[str, asyncio.Event] = {}
        # 审批结果: call_id -> bool (True=允许, False=拒绝)
        self._approval_results: dict[str, bool] = {}
        # 正在运行的子进程 PID 映射: session_id -> list[int]
        self._running_pids: dict[str, set[int]] = {}

    def check_security(self, command: str) -> str:
        """
        对本地命令进行三级安全判定。

        Args:
            command: 待执行的命令字符串

        Returns:
            'SAFE' (白名单直通) | 'CONFIRM' (灰名单需用户审批) | 'DENY' (黑名单高危拦截)
        """
        cmd_clean = (command or "").strip()
        if not cmd_clean:
            return "SAFE"

        # 1. 优先检查黑名单高危模式
        for pattern in self._DENY_PATTERNS:
            if pattern.search(cmd_clean):
                return "DENY"

        # 2. 检查是否包含管道符、连接符、重定向符或命令替换（防绕过：一律升级为 CONFIRM）
        if self._COMPOUND_OPERATORS.search(cmd_clean):
            return "CONFIRM"

        # 3. 检查纯只读白名单前缀
        cmd_lower = cmd_clean.lower()
        # 单独的 cd / Set-Location 允许直通以方便切换目录
        if cmd_lower == "cd" or cmd_lower.startswith("cd ") or cmd_lower.startswith("set-location "):
            return "SAFE"

        for prefix in self._SAFE_PREFIXES:
            if cmd_lower == prefix.strip() or cmd_lower.startswith(prefix):
                return "SAFE"

        # 4. 其余所有命令默认归入灰名单需确认
        return "CONFIRM"

    async def wait_for_user_approval(self, call_id: str, timeout: float = 120.0) -> bool:
        """
        挂起当前协程，等待前端用户点击「允许」或「拒绝」。

        Args:
            call_id: 工具调用唯一 ID
            timeout: 等待审批超时秒数

        Returns:
            bool: True 表示用户批准执行，False 表示拒绝或超时
        """
        event = asyncio.Event()
        self._pending_approvals[call_id] = event
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            return self._approval_results.get(call_id, False)
        except asyncio.TimeoutError:
            logger.warning(f"命令审批等待超时 (call_id={call_id})")
            return False
        finally:
            self._pending_approvals.pop(call_id, None)
            self._approval_results.pop(call_id, None)

    def resolve_approval(self, call_id: str, approved: bool) -> bool:
        """
        接收前端回传的审批决定并唤醒挂起的协程。
        """
        if call_id in self._pending_approvals:
            self._approval_results[call_id] = approved
            self._pending_approvals[call_id].set()
            return True
        return False

    @staticmethod
    def kill_process_tree(pid: int) -> None:
        """
        强制递归杀灭指定 PID 的整个进程树（防止子进程残留成为僵尸进程）。
        """
        if pid <= 0:
            return
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                    **get_silent_subprocess_kwargs(),
                )
            else:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
        except Exception as e:
            logger.debug(f"清理进程树 PID={pid} 结束: {e}")

    def cancel_session_processes(self, session_id: str) -> int:
        """
        强制中断某会话正在运行的所有本地命令子进程。
        """
        pids = list(self._running_pids.get(session_id, set()))
        for pid in pids:
            self.kill_process_tree(pid)
        self._running_pids.pop(session_id, None)
        return len(pids)

    @staticmethod
    def decode_output(raw_bytes: bytes) -> str:
        """
        Windows / Linux 容错解码器：优先 UTF-8，遇到中文 GBK (CP936) 字节流自动无缝回退。
        """
        if not raw_bytes:
            return ""
        try:
            return raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            try:
                return raw_bytes.decode("gbk")
            except UnicodeDecodeError:
                return raw_bytes.decode("utf-8", errors="replace")

    async def run(
        self,
        command: str,
        cwd: str | None = None,
        timeout: float = 60.0,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """
        在受控子进程中执行命令，自动维护 CWD 变更并执行编码容错与防卡死保护。

        Args:
            command: 待执行的命令
            cwd: 当前会话工作目录
            timeout: 最大执行超时时间（秒）
            session_id: 所属会话 ID（用于支持随时中断）

        Returns:
            dict: {
                "output": 组合后的标准输出与错误文本,
                "exit_code": 退出码,
                "new_cwd": 执行完毕后的最新工作目录路径,
                "timed_out": 是否超时
            }
        """
        if cwd and os.path.isdir(cwd):
            effective_cwd = cwd
        else:
            raw_cwd = os.getcwd()
            # NOTE: macOS 从 Finder 双击 .app 启动时 cwd 常为只读根目录 /，回退至可写应用根
            if sys.platform != "win32" and (raw_cwd == "/" or not os.access(raw_cwd, os.W_OK)):
                from ..core.runtime_paths import get_writable_app_root
                effective_cwd = str(get_writable_app_root())
            else:
                effective_cwd = raw_cwd

        # 构造带 UTF-8 编码声明与尾部 CWD 探测标记的包装命令
        if sys.platform == "win32":
            # 智能剥离模型自发嵌套的 powershell 外层包装，避免外层 Shell 将内部 $_ 变量吞掉
            inner_cmd = command.strip()
            ps_unwrap_pattern = re.compile(
                r"^(?:powershell(?:\.exe)?|pwsh(?:\.exe)?)\s+(?:-(?:NoProfile|NonInteractive|ExecutionPolicy\s+\w+|Command|c)\s+)*(?:\"([\s\S]*)\"|'([\s\S]*)'|([\s\S]+))$",
                re.IGNORECASE,
            )
            m_unwrap = ps_unwrap_pattern.match(inner_cmd)
            if m_unwrap:
                inner_cmd = m_unwrap.group(1) or m_unwrap.group(2) or m_unwrap.group(3) or inner_cmd

            full_ps_script = (
                f"$ProgressPreference = 'SilentlyContinue'; "
                f"$OutputEncoding = [System.Text.Encoding]::UTF8; "
                f"[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
                f"$global:LASTEXITCODE = $null; "
                f"{inner_cmd} ; "
                f"$__cmd_ok = $?; "
                f"$__real_exit = if ($LASTEXITCODE -ne $null -and $LASTEXITCODE -ne 0) {{ $LASTEXITCODE }} elseif (-not $__cmd_ok) {{ 1 }} else {{ 0 }}; "
                f"Write-Output \"`n{self._CWD_MARKER}$((Get-Location).Path)\"; "
                f"exit $__real_exit"
            )
            # 使用 UTF-16LE Base64 编码通过 -EncodedCommand 直通执行，彻底免疫 Shell 引号转义与 $_ 变量丢失
            b64_payload = base64.b64encode(full_ps_script.encode("utf-16le")).decode("ascii")
            exec_args = [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-EncodedCommand",
                b64_payload,
            ]
        else:
            wrapped_cmd = f"{command}; __real_exit=$?; echo \"\n{self._CWD_MARKER}$(pwd)\"; exit $__real_exit"
            exec_args = ["/bin/bash", "-c", wrapped_cmd]

        # 非冻结研发模式下将当前虚拟环境 Scripts/bin 优先注入子进程 PATH，杜绝 WindowsApps 假 Python 劫持
        child_env = os.environ.copy()
        if not getattr(sys, "frozen", False):
            venv_scripts = os.path.dirname(sys.executable)
            child_env["PATH"] = venv_scripts + os.pathsep + child_env.get("PATH", "")
        child_env["PYTHONIOENCODING"] = "utf-8"
        child_env["PYTHONUNBUFFERED"] = "1"

        allow_gui_window = self._is_gui_launch_command(command)

        def _sync_exec() -> tuple[bytes, bytes, int, bool]:
            proc = subprocess.Popen(
                exec_args,
                cwd=effective_cwd,
                stdin=subprocess.DEVNULL,  # 强制关闭标准输入，防交互式挂起
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=child_env,
                start_new_session=(sys.platform != "win32"),
                **get_silent_subprocess_kwargs(allow_gui=allow_gui_window),
            )
            if session_id:
                self._running_pids.setdefault(session_id, set()).add(proc.pid)

            try:
                out_bytes, err_bytes = proc.communicate(timeout=timeout)
                return out_bytes, err_bytes, proc.returncode or 0, False
            except subprocess.TimeoutExpired:
                self.kill_process_tree(proc.pid)
                try:
                    out_bytes, err_bytes = proc.communicate(timeout=2)
                except Exception:
                    out_bytes, err_bytes = b"", b""
                return out_bytes, err_bytes, -1, True
            finally:
                if session_id and session_id in self._running_pids:
                    self._running_pids[session_id].discard(proc.pid)

        out_bytes, err_bytes, exit_code, timed_out = await asyncio.to_thread(_sync_exec)

        stdout_str = self.decode_output(out_bytes)
        stderr_str = self.decode_output(err_bytes)
        # 过滤 PowerShell 默认输出的内部 CLIXML 进度伪日志
        if stderr_str:
            stderr_str = re.sub(r"#<\s*CLIXML[\s\S]*?</Objs>", "", stderr_str, flags=re.IGNORECASE).strip()

        # 解析并剥离尾部的 CWD 标记
        new_cwd = effective_cwd
        if self._CWD_MARKER in stdout_str:
            parts = stdout_str.rsplit(self._CWD_MARKER, 1)
            stdout_str = parts[0].rstrip()
            candidate_cwd = parts[1].strip().splitlines()[0].strip() if parts[1].strip() else ""
            if candidate_cwd and os.path.isdir(candidate_cwd):
                new_cwd = candidate_cwd

        combined = stdout_str
        if stderr_str.strip():
            combined = f"{combined}\n[STDERR]\n{stderr_str.strip()}".strip()

        if timed_out:
            combined += (
                f"\n[系统超时中断]: 命令执行超过 {int(timeout)} 秒已被进程树强杀。"
                f"若该命令需要交互确认，请改用非交互参数（如 -y / --yes / --non-interactive）。"
            )
        elif exit_code != 0 and not combined.strip():
            combined = f"命令执行结束，退出码: {exit_code}（若命令需交互输入，请添加 -y 等非交互标志）"

        # 智能诊断日志嗅探：若输出中包含指向外部 debug 日志文件的路径，自动抓取其底层关键报错
        combined = self.sniff_and_extract_log(combined)

        return {
            "output": combined.strip() or "(执行成功，无控制台输出)",
            "exit_code": exit_code,
            "new_cwd": new_cwd,
            "timed_out": timed_out,
        }

    @staticmethod
    def sniff_and_extract_log(raw_output: str) -> str:
        """
        自动嗅探命令输出中引用的外部排错日志文件（如 npm debug log, pip log, build log 等），
        若存在且为本地真实文件，自动抓取日志末尾关键报错堆栈并追加至输出中，供模型第一性原理自动自愈纠偏。
        """
        if not raw_output:
            return raw_output

        # 优先匹配形如: A complete log of this run can be found in: C:\...\debug-0.log
        # 以及 See log at /path/to/log 或直接出现的 .log 绝对路径
        patterns = [
            r"(?:can be found in|See log at|Log file(?:\s*is)?|debug log at|written to|log file:\s*)\s*[:：]?\s*([a-zA-Z]:\\[^\r\n]+\.log|/[^\r\n]+\.log)",
            r"([a-zA-Z]:\\[^\s\r\n]+\.log)",
            r"(/[\w./-]+\.log)",
        ]

        found_path = None
        for pat in patterns:
            m = re.search(pat, raw_output, re.IGNORECASE)
            if m:
                cand = m.group(1).strip()
                if os.path.isfile(cand):
                    found_path = cand
                    break

        if not found_path:
            return raw_output

        try:
            with open(found_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()

            tail_count = 35
            tail_lines = lines[-tail_count:] if len(lines) > tail_count else lines
            tail_text = "".join(tail_lines).strip()
            if tail_text:
                logger.info(f"[ShellExecutor] 自动成功嗅探并挂载底层关联诊断日志: {found_path} (共 {len(tail_lines)} 行)")
                return (
                    f"{raw_output}\n\n"
                    f"【📋 诊断日志自动定位与回填 ({found_path})】:\n"
                    f"```log\n{tail_text}\n```"
                )
        except Exception as e:
            logger.debug(f"读取嗅探到的日志文件 {found_path} 异常: {e}")

        return raw_output

    @staticmethod
    def compress_output(command: str, raw_output: str, exit_code: int) -> str:
        """
        Observation Masking 核心微缩函数：
        将冗长的终端输出压缩为微缩快照（Head 3 行 + Tail 3 行 + 退出码），
        既实现 90% 以上的 Token 降噪，又确保长程多步任务在汇总阶段不丢失关键文件或实体名称。
        """
        lines = [line.strip() for line in (raw_output or "").splitlines() if line.strip()]
        line_count = len(lines)
        short_cmd = command.strip().replace("\n", " ")[:60]

        if line_count == 0:
            return f"[已折叠命令 `{short_cmd}` 输出: (无输出), 退出码 {exit_code}]"

        if line_count <= 6:
            joined = " | ".join(lines)
            return f"[已折叠命令 `{short_cmd}` 输出: 共 {line_count} 行, 退出码 {exit_code} | 详情: {joined[:180]}]"

        head_parts = [l[:50] for l in lines[:3]]
        tail_parts = [l[:50] for l in lines[-3:]]
        preview_text = " / ".join(head_parts) + " ... " + " / ".join(tail_parts)
        return (
            f"[已折叠命令 `{short_cmd}` 输出: 共 {line_count} 行, 退出码 {exit_code} | "
            f"首尾预览: {preview_text[:200]}]"
        )


# 全局单例
shell_executor = LocalShellExecutor()

