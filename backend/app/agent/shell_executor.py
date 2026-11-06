"""
受控本地终端执行器 (LocalShellExecutor)
具备四大工程防线：
1. CWD 路径持久化：跨命令保持工作目录上下文（自动捕获 cd / Set-Location 变更）
2. 防交互卡死：强制 stdin=DEVNULL + 超时保护 + 引导非交互参数
3. 进程树强杀：Windows 下调用 taskkill /F /T /PID，Linux 下调用 killpg，杜绝残留僵尸进程
4. 编码容错与防绕过安全校验：UTF-8/GBK 双模解码 + 连接符(&&, |, ;, >)强制升级 CONFIRM 审批
"""
import asyncio
import os
import re
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..core.logging_config import logger
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
        effective_cwd = cwd if (cwd and os.path.isdir(cwd)) else os.getcwd()

        # 构造带 UTF-8 编码声明与尾部 CWD 探测标记的包装命令
        if sys.platform == "win32":
            wrapped_cmd = (
                f"[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
                f"{command} ; "
                f"Write-Output \"`n{self._CWD_MARKER}$((Get-Location).Path)\""
            )
            exec_args = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", wrapped_cmd]
        else:
            wrapped_cmd = f"{command} ; echo \"\n{self._CWD_MARKER}$(pwd)\""
            exec_args = ["/bin/bash", "-c", wrapped_cmd]

        # 强制将当前 Python 虚拟环境的 Scripts 目录与 bin 目录优先注入子进程 PATH，杜绝 WindowsApps 假 Python 劫持
        child_env = os.environ.copy()
        venv_scripts = os.path.dirname(sys.executable)
        child_env["PATH"] = venv_scripts + os.pathsep + child_env.get("PATH", "")
        child_env["PYTHONIOENCODING"] = "utf-8"
        child_env["PYTHONUNBUFFERED"] = "1"

        def _sync_exec() -> tuple[bytes, bytes, int, bool]:
            proc = subprocess.Popen(
                exec_args,
                cwd=effective_cwd,
                stdin=subprocess.DEVNULL,  # 强制关闭标准输入，防交互式挂起
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=child_env,
                start_new_session=(sys.platform != "win32"),
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

        return {
            "output": combined.strip() or "(执行成功，无控制台输出)",
            "exit_code": exit_code,
            "new_cwd": new_cwd,
            "timed_out": timed_out,
        }

    @staticmethod
    def compress_output(command: str, raw_output: str, exit_code: int) -> str:
        """
        Observation Masking 核心函数：
        将可能长达数千行的终端输出浓缩为单行摘要，供后续历史轮次折叠降噪使用。
        """
        lines = [line.strip() for line in (raw_output or "").splitlines() if line.strip()]
        line_count = len(lines)
        short_cmd = command.strip().replace("\n", " ")[:60]

        if line_count == 0:
            return f"[已折叠历史命令 `{short_cmd}` 输出: 无内容, 退出码 {exit_code}]"

        first_line = lines[0][:80]
        last_line = lines[-1][:80] if line_count > 1 else ""
        preview = f"{first_line} ... {last_line}" if last_line else first_line
        return f"[已折叠历史命令 `{short_cmd}` 输出: 共 {line_count} 行, 退出码 {exit_code}, 摘要: {preview}]"


# 全局单例
shell_executor = LocalShellExecutor()

