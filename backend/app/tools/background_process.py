"""
后台长驻进程托管池 (BackgroundProcessManager)
为 AI Agent 提供“异步拉起 -> 探活等待 -> 环形日志排障 -> 生命周期自动回收”的完整闭环，
彻底解决 Web 开发服务器 (npm run dev / vite / uvicorn) 与后台守护脚本阻塞执行流及僵尸进程残留问题。
"""
import asyncio
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from ..core.logging_config import logger

# 单工作区最大并发托管进程数（防资源耗尽）
MAX_PROCESSES_PER_WORKSPACE: int = 5
# 单进程日志环形缓冲区最大保留行数（防 OOM）
MAX_LOG_BUFFER_LINES: int = 1000
# 单进程独立错误行环形缓冲区最大保留行数
MAX_ERROR_RING_LINES: int = 200

# 正则表达式：用于清洗 ANSI 转义序列
_ANSI_ESCAPE_RE = re.compile(
    r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])"
    r"|(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]"
    r"|\x1b\]\d+;[^\x07\x1b]*?(?:\x07|\x1b\\)"
    r"|\x1b[@-Z\\-_]"
)

# 端口嗅探正则：严格限定回环绑定特征，避免把远端代理或版本号误判为端口
_PORT_SNIFF_RE = re.compile(
    r"(?:https?://)?(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::\]):(\d{2,5})\b",
    re.IGNORECASE,
)
_FALLBACK_PORT_RE = re.compile(
    r"\b(?:on port|listening on port|listening at port|port)\s+[:=]?\s*(\d{2,5})\b",
    re.IGNORECASE,
)

# 错误行特征关键词
_ERROR_LINE_KEYWORDS = (
    "traceback (most recent call last)",
    "error:",
    "exception:",
    "fatal:",
    "failed:",
    "eaddrinuse",
    "syntaxerror",
    "importerror",
    "modulenotfounderror",
    "cannothandle",
    "panic:",
)


@dataclass
class ManagedProcess:
    """受管后台进程实体模型"""
    proc_id: str
    workspace_id: str
    conversation_id: str
    command: str
    cwd: str
    pid: int
    status: Literal["starting", "running", "exited", "killed", "crashed"] = "starting"
    exit_code: int | None = None
    started_at: float = field(default_factory=time.time)
    log_buffer: deque[str] = field(default_factory=lambda: deque(maxlen=MAX_LOG_BUFFER_LINES))
    error_ring: deque[str] = field(default_factory=lambda: deque(maxlen=MAX_ERROR_RING_LINES))
    port_hints: list[int] = field(default_factory=list)
    process_handle: subprocess.Popen | None = None

    def add_log_line(self, line: str) -> None:
        """追加清洗后的单行日志并实时提取端口与高优错误"""
        if not line:
            return
        self.log_buffer.append(line)

        # 1. 错误行捕获
        line_lower = line.lower()
        if any(kw in line_lower for kw in _ERROR_LINE_KEYWORDS):
            self.error_ring.append(line)

        # 2. 端口嗅探（保序去重，严格回环特征）
        found_ports: list[int] = []
        for match in _PORT_SNIFF_RE.finditer(line):
            try:
                p = int(match.group(1))
                if 80 <= p <= 65535 and p not in self.port_hints and p not in found_ports:
                    found_ports.append(p)
            except ValueError:
                pass

        if not found_ports:
            for match in _FALLBACK_PORT_RE.finditer(line):
                try:
                    p = int(match.group(1))
                    if 80 <= p <= 65535 and p not in self.port_hints and p not in found_ports:
                        found_ports.append(p)
                except ValueError:
                    pass

        for p in found_ports:
            self.port_hints.append(p)

    def to_summary(self) -> dict[str, Any]:
        """输出给 Agent 与前端的轻量化摘要字典"""
        running_seconds = round(time.time() - self.started_at, 1)
        return {
            "proc_id": self.proc_id,
            "workspace_id": self.workspace_id,
            "conversation_id": self.conversation_id,
            "command": self.command,
            "cwd": self.cwd,
            "pid": self.pid,
            "status": self.status,
            "exit_code": self.exit_code,
            "running_seconds": running_seconds,
            "port_hints": list(self.port_hints),
            "log_lines_count": len(self.log_buffer),
            "has_errors": len(self.error_ring) > 0,
        }


def _sanitize_line(raw: str) -> str:
    """剥离 ANSI 转义字符与多余的 \\r"""
    if not raw:
        return ""
    cleaned = _ANSI_ESCAPE_RE.sub("", raw)
    if "\r" in cleaned:
        segments = [s for s in cleaned.split("\r") if s]
        cleaned = segments[-1] if segments else ""
    return cleaned.strip("\n\r")


class BackgroundProcessManager:
    """
    后台进程托管管理器单例
    提供跨平台进程树启动、非阻塞输出泵、状态探测与级联销毁。
    """
    _instance: "BackgroundProcessManager | None" = None
    _lock = threading.Lock()

    def __new__(cls) -> "BackgroundProcessManager":
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._processes = {}
                cls._instance._registry_lock = threading.Lock()
            return cls._instance

    @classmethod
    def get_instance(cls) -> "BackgroundProcessManager":
        return cls()

    def _get_active_processes_for_workspace(self, workspace_id: str) -> list[ManagedProcess]:
        with self._registry_lock:
            return [
                p for p in self._processes.values()
                if p.workspace_id == workspace_id and p.status in ("starting", "running")
            ]

    def start_process(
        self,
        command: str,
        cwd: str,
        workspace_id: str = "default",
        conversation_id: str = "default",
        wait_seconds: int = 3,
    ) -> dict[str, Any]:
        """
        启动一个后台托管进程。
        
        Args:
            command: 启动 Shell 指令 (如 npm run dev, python -m uvicorn ...)
            cwd: 锁定的工作区根目录
            workspace_id: 归属工作区
            conversation_id: 归属会话
            wait_seconds: 启动后前置探活等待秒数（默认 3 秒）

        Returns:
            dict 包含 proc_id、status、port_hints、initial_logs 或 crashed 报错信息
        """
        cwd_path = Path(cwd).resolve()
        if not cwd_path.exists() or not cwd_path.is_dir():
            return {
                "success": False,
                "error": f"目标工作区目录不存在: {cwd}",
            }

        # 1. 检查单工作区并发上限
        active_procs = self._get_active_processes_for_workspace(workspace_id)
        if len(active_procs) >= MAX_PROCESSES_PER_WORKSPACE:
            active_ids = [p.proc_id for p in active_procs]
            return {
                "success": False,
                "error": f"当前工作区后台进程数已达上限 ({MAX_PROCESSES_PER_WORKSPACE} 个): {active_ids}。请先使用 stop_background_process 终止不需要的进程。",
            }

        proc_id = f"bg_{uuid.uuid4().hex[:8]}"

        # 2. 跨平台子进程组启动参数准备
        is_win = sys.platform.startswith("win")
        popen_kwargs: dict[str, Any] = {
            "cwd": str(cwd_path),
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,  # 合并输出流
            "stdin": subprocess.DEVNULL,
            "text": True,
            "bufsize": 1,
            "encoding": "utf-8",
            "errors": "replace",
        }

        if is_win:
            # Windows: 独立进程组，防止控制台 Ctrl+C 连带中断，且方便 taskkill /T 递归清理
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            popen_kwargs["shell"] = True
            shell_cmd = command
        else:
            # POSIX: 独立 session，拥有独立进程组 ID (pgid)
            popen_kwargs["start_new_session"] = True
            popen_kwargs["shell"] = True
            shell_cmd = command

        try:
            subproc = subprocess.Popen(shell_cmd, **popen_kwargs)
        except Exception as e:
            logger.error(f"[BackgroundProcessManager] 启动命令失败: {command}, err={e}")
            return {
                "success": False,
                "error": f"启动子进程异常: {e}",
            }

        managed = ManagedProcess(
            proc_id=proc_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            command=command,
            cwd=str(cwd_path),
            pid=subproc.pid,
            status="starting",
            process_handle=subproc,
        )

        with self._registry_lock:
            self._processes[proc_id] = managed

        # 3. 启动非阻塞后台日志抽取线程
        def _pump_output():
            try:
                if subproc.stdout:
                    for line in iter(subproc.stdout.readline, ""):
                        if not line:
                            break
                        sanitized = _sanitize_line(line)
                        if sanitized:
                            managed.add_log_line(sanitized)
            except Exception as read_err:
                logger.debug(f"[BackgroundProcess] {proc_id} 日志泵读取中断: {read_err}")
            finally:
                subproc.poll()
                ret = subproc.returncode
                managed.exit_code = ret
                if managed.status != "killed":
                    managed.status = "exited" if ret == 0 else "crashed"

        pump_thread = threading.Thread(
            target=_pump_output,
            name=f"bg-pump-{proc_id}",
            daemon=True,
        )
        pump_thread.start()

        # 4. 前置探活等待 wait_seconds，捕获早期致命报错
        actual_wait = max(1, min(wait_seconds, 15))
        time.sleep(actual_wait)

        # 5. 探活判定（状态机转换）
        poll_ret = subproc.poll()
        if poll_ret is not None:
            # 进程在等待期内已退出
            managed.exit_code = poll_ret
            if poll_ret == 0:
                managed.status = "exited"
                return {
                    "success": True,
                    "proc_id": proc_id,
                    "status": "exited",
                    "exit_code": 0,
                    "message": "命令已在短时间内正常执行完毕退出。",
                    "logs": list(managed.log_buffer),
                }
            else:
                managed.status = "crashed"
                err_lines = list(managed.error_ring) or list(managed.log_buffer)
                return {
                    "success": False,
                    "proc_id": proc_id,
                    "status": "crashed",
                    "exit_code": poll_ret,
                    "error": f"进程启动后立即异常退出 (exit code: {poll_ret})",
                    "diagnostics": err_lines[-30:],
                }

        # 依然在运行 -> 置为 running
        managed.status = "running"
        init_logs = list(managed.log_buffer)[-40:]

        return {
            "success": True,
            "proc_id": proc_id,
            "pid": managed.pid,
            "status": "running",
            "port_hints": list(managed.port_hints),
            "message": f"进程已成功在后台启动 (PID: {managed.pid})，正在持续监听与运行。",
            "initial_logs": init_logs,
        }

    def read_logs(
        self,
        proc_id: str,
        tail_lines: int = 80,
        only_errors: bool = False,
    ) -> dict[str, Any]:
        """
        读取指定后台进程的最新日志切片。
        
        Args:
            proc_id: 进程短 ID
            tail_lines: 获取末尾行数（默认 80 行）
            only_errors: 是否仅返回错误堆栈（极省 Token）
        """
        with self._registry_lock:
            managed = self._processes.get(proc_id)

        if not managed:
            return {
                "success": False,
                "error": f"未找到 ID 为 {proc_id} 的托管进程。",
            }

        # 更新状态
        if managed.process_handle:
            poll_ret = managed.process_handle.poll()
            if poll_ret is not None and managed.status == "running":
                managed.exit_code = poll_ret
                managed.status = "exited" if poll_ret == 0 else "crashed"

        if only_errors:
            lines = list(managed.error_ring)[-tail_lines:]
        else:
            lines = list(managed.log_buffer)[-tail_lines:]

        return {
            "success": True,
            "proc_id": proc_id,
            "status": managed.status,
            "exit_code": managed.exit_code,
            "port_hints": list(managed.port_hints),
            "only_errors": only_errors,
            "lines_count": len(lines),
            "logs": lines,
        }

    def list_processes(self, workspace_id: str | None = None) -> dict[str, Any]:
        """列出当前工作区或所有托管进程"""
        with self._registry_lock:
            all_procs = list(self._processes.values())

        if workspace_id:
            all_procs = [p for p in all_procs if p.workspace_id == workspace_id]

        # 刷新所有进程状态
        for p in all_procs:
            if p.process_handle and p.status in ("starting", "running"):
                poll_ret = p.process_handle.poll()
                if poll_ret is not None:
                    p.exit_code = poll_ret
                    p.status = "exited" if poll_ret == 0 else "crashed"

        summaries = [p.to_summary() for p in all_procs]
        return {
            "success": True,
            "count": len(summaries),
            "processes": summaries,
        }

    def stop_process(self, proc_id: str) -> dict[str, Any]:
        """
        强行终止指定托管进程及其整个子进程树。
        """
        with self._registry_lock:
            managed = self._processes.get(proc_id)

        if not managed:
            return {
                "success": False,
                "error": f"未找到 ID 为 {proc_id} 的托管进程。",
            }

        if managed.status in ("killed", "exited", "crashed"):
            return {
                "success": True,
                "proc_id": proc_id,
                "status": managed.status,
                "message": f"进程已处于终止状态 ({managed.status})，无需重复清理。",
            }

        pid = managed.pid
        is_win = sys.platform.startswith("win")
        logger.info(f"[BackgroundProcessManager] 正在级联终止进程: {proc_id} (PID: {pid})")

        try:
            if is_win:
                # Windows: taskkill /F /T /PID 递归清理子进程树（包括 node.exe / python.exe）
                kill_cmd = f"taskkill /F /T /PID {pid}"
                res = subprocess.run(
                    kill_cmd,
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=5,
                )
                if res.returncode != 0:
                    logger.debug(f"[BackgroundProcessManager] taskkill 返回非零: {res.stderr.decode('gbk', 'replace')}")
                    if managed.process_handle:
                        managed.process_handle.kill()
            else:
                # POSIX: 杀死整个独立会话进程组
                try:
                    pgid = os.getpgid(pid)
                    os.killpg(pgid, signal.SIGTERM)
                    # 等待 1.5 秒确认退出
                    for _ in range(15):
                        if managed.process_handle and managed.process_handle.poll() is not None:
                            break
                        time.sleep(0.1)
                    if managed.process_handle and managed.process_handle.poll() is None:
                        os.killpg(pgid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except Exception as posix_err:
                    logger.warning(f"[BackgroundProcessManager] POSIX killpg 失败: {posix_err}")
                    if managed.process_handle:
                        managed.process_handle.kill()

        except Exception as e:
            logger.error(f"[BackgroundProcessManager] 终止进程树异常: {proc_id}, {e}")
            if managed.process_handle:
                try:
                    managed.process_handle.kill()
                except Exception:
                    pass

        managed.status = "killed"
        managed.exit_code = -9
        return {
            "success": True,
            "proc_id": proc_id,
            "status": "killed",
            "message": f"已成功终止进程树 (PID: {pid})。",
        }

    def cleanup_workspace(self, workspace_id: str) -> int:
        """清理指定工作区下的所有活跃后台进程，返回终止的进程数"""
        active_procs = self._get_active_processes_for_workspace(workspace_id)
        count = 0
        for p in active_procs:
            try:
                self.stop_process(p.proc_id)
                count += 1
            except Exception as e:
                logger.error(f"清理工作区进程失败: {p.proc_id}, {e}")
        return count

    def cleanup_conversation(self, conversation_id: str) -> int:
        """清理指定会话下的所有活跃后台进程"""
        with self._registry_lock:
            procs = [
                p for p in self._processes.values()
                if p.conversation_id == conversation_id and p.status in ("starting", "running")
            ]
        count = 0
        for p in procs:
            try:
                self.stop_process(p.proc_id)
                count += 1
            except Exception as e:
                logger.error(f"清理会话进程失败: {p.proc_id}, {e}")
        return count

    def cleanup_all(self) -> int:
        """程序退出时终止所有仍存活的托管进程树"""
        with self._registry_lock:
            alive = [
                p for p in self._processes.values()
                if p.status in ("starting", "running")
            ]
        count = 0
        for p in alive:
            try:
                self.stop_process(p.proc_id)
                count += 1
            except Exception as e:
                logger.error(f"退出全局清理进程失败: {p.proc_id}, {e}")
        return count


# ─────────────────────────────────────────────────────────────
# 暴露给 Agent Runtime 的 4 个原子工具函数
# ─────────────────────────────────────────────────────────────

def start_background_process(
    command: str,
    wait_seconds: int = 3,
    workspace_id: str = "default",
    conversation_id: str = "default",
    cwd: str | None = None,
) -> dict[str, Any]:
    """
    异步启动一个后台长驻进程（如 Web 开发服务器、长期运行的监控或构建脚本）。
    
    Args:
        command: 要执行的启动命令行
        wait_seconds: 启动后探活等待秒数（默认 3 秒），若在此期间异常退出将直接报告错误堆栈
        workspace_id: 归属的工作区 ID
        conversation_id: 归属的会话 ID
        cwd: 锁定的物理执行目录路径（未指定则由 runtime 传入当前工作区根路径）
    """
    target_cwd = cwd or os.getcwd()
    manager = BackgroundProcessManager.get_instance()
    return manager.start_process(
        command=command,
        cwd=target_cwd,
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        wait_seconds=wait_seconds,
    )


def read_process_logs(
    proc_id: str,
    tail_lines: int = 80,
    only_errors: bool = False,
) -> dict[str, Any]:
    """
    读取指定后台进程的最新输出日志切片。
    
    Args:
        proc_id: 进程短 ID（由 start_background_process 返回，如 "bg_a1b2c3"）
        tail_lines: 获取末尾最新的日志行数（默认 80 行）
        only_errors: 是否开启仅返回错误堆栈（推荐在排查崩溃时开启以极度节省上下文 Token）
    """
    manager = BackgroundProcessManager.get_instance()
    return manager.read_logs(proc_id=proc_id, tail_lines=tail_lines, only_errors=only_errors)


def list_background_processes(workspace_id: str | None = None) -> dict[str, Any]:
    """
    列出当前工作区中所有托管的后台进程信息、运行状态及实时嗅探到的监听端口。
    """
    manager = BackgroundProcessManager.get_instance()
    return manager.list_processes(workspace_id=workspace_id)


def stop_background_process(proc_id: str) -> dict[str, Any]:
    """
    终止指定的后台进程及其派生的整个子进程树（彻底释放端口并释放系统资源）。
    
    Args:
        proc_id: 进程短 ID（如 "bg_a1b2c3"）
    """
    manager = BackgroundProcessManager.get_instance()
    return manager.stop_process(proc_id=proc_id)
