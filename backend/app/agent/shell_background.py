"""
后台长耗时命令与大包下载管理器 (BackgroundCommandManager)
负责：
1. 重型下载/安装/构建命令的自动识别与包管理器防超时环境变量注入；
2. 进程生命周期托管、实时流式日志落盘与增量 tail 读取；
3. 双层活跃度探针（任务日志字节增量 + 缓存/工作目录活跃字节增量），精准区分“慢速下载”与“真正卡死/断网”；
4. 与 LocalShellExecutor.cancel_session_processes 联动清理，杜绝孤儿进程。
"""
import asyncio
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.logging_config import logger
from ..core.runtime_paths import get_silent_subprocess_kwargs

# 重型下载/安装/构建命令默认前台观察窗口（秒）：超时未完成则无缝转后台，不杀进程
DEFAULT_HEAVY_OBSERVE_SECONDS: float = 15.0
# 连续无任何日志与缓存目录增长的停滞告警阈值（秒）
DEFAULT_STAGNANT_THRESHOLD_SECONDS: float = 75.0
# check_background_command 单次安全等待上限（秒）
MAX_CHECK_WAIT_SECONDS: float = 60.0
# 单个观察目录最多扫描的条目数，避免扫描超大 node_modules/.venv 阻塞事件循环
_MAX_SCAN_ENTRIES_PER_DIR: int = 400
_MAX_SCAN_DEPTH: int = 3

# 识别需要长耗时下载/编译的重型命令特征（排除 pip list / npm -v 等只读查询）
_HEAVY_COMMAND_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\buv\s+(?:sync|pip\s+install|add|venv|tool\s+install)\b", re.I),
    re.compile(r"\b(?:pip|pip3)\s+(?:install|download|wheel)\b", re.I),
    re.compile(r"\bpython(?:\.exe)?\s+-m\s+pip\s+(?:install|download|wheel)\b", re.I),
    re.compile(r"\b(?:npm|pnpm|yarn|bun)\s+(?:install|i|ci|add|update|build)\b", re.I),
    re.compile(r"\bgit\s+(?:clone|fetch|pull|submodule\s+update)\b", re.I),
    re.compile(r"\b(?:curl|wget)\b", re.I),
    re.compile(r"\b(?:Invoke-WebRequest|iwr)\b", re.I),
    re.compile(r"\b(?:cargo|rustup)\s+(?:build|install|update|fetch)\b", re.I),
    re.compile(r"\bdocker\s+(?:pull|build|push)\b", re.I),
    re.compile(r"\b(?:conda|mamba)\s+(?:install|create|update)\b", re.I),
)

# 致命网络/断网报错指纹（用于辅助识别断网或源不可达）
_NETWORK_ERROR_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"Could not resolve host", re.I), "DNS 解析失败 (Could not resolve host)"),
    (
        re.compile(r"(?:NameResolutionError|Temporary failure in name resolution|getaddrinfo failed)", re.I),
        "DNS 域名解析失败",
    ),
    (
        re.compile(r"(?:ConnectionError|ConnectionRefusedError|ConnectionResetError|Connection aborted)", re.I),
        "网络连接异常或被远端重置",
    ),
    (
        re.compile(r"(?:UNEXPECTED_EOF|EOF occurred in violation of protocol)", re.I),
        "TLS/HTTP 连接意外断开 (UNEXPECTED_EOF)",
    ),
    (re.compile(r"(?:ReadTimeoutError|ConnectTimeoutError|timed out|ETIMEDOUT)", re.I), "网络请求超时"),
    (re.compile(r"(?:ENOTFOUND|ECONNREFUSED|ECONNRESET|EAI_AGAIN)", re.I), "底层网络 socket 不可达"),
    (re.compile(r"(?:TLS handshake timeout|CERTIFICATE_VERIFY_FAILED)", re.I), "TLS 握手超时或证书校验失败"),
    (re.compile(r"(?:error:\s*Failed to fetch|Failed to download)", re.I), "包下载请求失败"),
)

_ANSI_ESCAPE_RE = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]"
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
    r"|\x1b[@-Z\\-_]"
)


def is_heavy_command(command: str) -> bool:
    """判断命令是否为重型下载、依赖安装或长耗时构建指令。"""
    cmd_clean = (command or "").strip()
    if not cmd_clean:
        return False
    return any(pattern.search(cmd_clean) for pattern in _HEAVY_COMMAND_PATTERNS)


def inject_package_manager_env(env: dict[str, str]) -> dict[str, str]:
    """为子进程环境变量注入主流包管理器防超时、并发与非交互保护（尊重已有显式变量）。"""
    env.setdefault("UV_HTTP_TIMEOUT", "600")
    env.setdefault("UV_CONCURRENT_DOWNLOADS", "4")
    env.setdefault("PIP_DEFAULT_TIMEOUT", "300")
    env.setdefault("NPM_CONFIG_FETCH_TIMEOUT", "300000")
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def detect_network_error(text: str) -> str | None:
    """检测日志文本中是否命中致命网络/断网异常指纹。"""
    if not text:
        return None
    for pattern, reason in _NETWORK_ERROR_PATTERNS:
        if pattern.search(text):
            return reason
    return None


def _decode_bytes(raw_bytes: bytes) -> str:
    """UTF-8 / GBK 容错解码。"""
    if not raw_bytes:
        return ""
    try:
        return raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return raw_bytes.decode("gbk")
        except UnicodeDecodeError:
            return raw_bytes.decode("utf-8", errors="replace")


def _sanitize_text(raw_text: str) -> str:
    """剥离 ANSI 控制符并折叠进度条覆盖行。"""
    if not raw_text:
        return ""
    cleaned = _ANSI_ESCAPE_RE.sub("", raw_text).replace("\r\n", "\n")
    lines: list[str] = []
    blank_run = 0
    for line in cleaned.split("\n"):
        if "\r" in line:
            parts = [seg for seg in line.split("\r") if seg != ""]
            line = parts[-1] if parts else ""
        if not line.strip():
            blank_run += 1
            if blank_run <= 2:
                lines.append("")
        else:
            blank_run = 0
            lines.append(line)
    return "\n".join(lines)


def resolve_default_watch_dirs(cwd: str, env: dict[str, str] | None = None) -> list[Path]:
    """收集用于判断下载活跃度的缓存与工作目录列表。"""
    source_env = env or os.environ
    candidates: list[Path] = []
    cwd_path = Path(cwd)
    candidates.append(cwd_path)
    candidates.append(cwd_path / ".venv")
    candidates.append(cwd_path / "node_modules")

    for env_key in ("UV_CACHE_DIR", "PIP_CACHE_DIR", "npm_config_cache"):
        val = source_env.get(env_key)
        if val:
            candidates.append(Path(val))

    if sys.platform == "win32":
        local_app_data = source_env.get("LOCALAPPDATA")
        if local_app_data:
            candidates.append(Path(local_app_data) / "uv" / "cache")
            candidates.append(Path(local_app_data) / "pip" / "Cache")
    else:
        home = Path.home()
        candidates.append(home / ".cache" / "uv")
        candidates.append(home / ".cache" / "pip")

    # 去重并保留真实存在的目录
    unique_dirs: list[Path] = []
    seen: set[str] = set()
    for p in candidates:
        try:
            resolved = str(p.resolve())
        except OSError:
            resolved = str(p)
        if resolved not in seen and p.is_dir():
            seen.add(resolved)
            unique_dirs.append(p)
    return unique_dirs


def measure_watch_dirs_footprint(watch_dirs: list[Path], since_ts: float) -> tuple[int, int]:
    """
    采样观察目录在任务启动后的活跃字节总和与最新修改时间戳。
    限制单目录扫描深度与条目数，确保探针耗时极低且不阻塞。
    """
    active_bytes = 0
    latest_mtime_ns = 0
    since_ns = int((since_ts - 1.0) * 1_000_000_000)

    for root in watch_dirs:
        if not root.is_dir():
            continue
        scanned = 0
        stack: list[tuple[Path, int]] = [(root, 0)]
        while stack and scanned < _MAX_SCAN_ENTRIES_PER_DIR:
            current_dir, depth = stack.pop()
            try:
                with os.scandir(current_dir) as it:
                    for entry in it:
                        scanned += 1
                        if scanned > _MAX_SCAN_ENTRIES_PER_DIR:
                            break
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                if depth < _MAX_SCAN_DEPTH and entry.name not in (".git", "__pycache__"):
                                    stack.append((Path(entry.path), depth + 1))
                            elif entry.is_file(follow_symlinks=False):
                                st = entry.stat(follow_symlinks=False)
                                if st.st_mtime_ns >= since_ns:
                                    active_bytes += st.st_size
                                    if st.st_mtime_ns > latest_mtime_ns:
                                        latest_mtime_ns = st.st_mtime_ns
                        except OSError:
                            continue
            except OSError:
                continue

    return active_bytes, latest_mtime_ns


@dataclass
class BackgroundJob:
    """后台运行命令任务实体。"""

    job_id: str
    session_id: str | None
    command: str
    cwd: str
    process: subprocess.Popen[bytes]
    log_path: Path
    temp_script_path: str | None
    watch_dirs: list[Path]
    started_at: float
    last_activity_at: float
    stagnant_threshold: float = DEFAULT_STAGNANT_THRESHOLD_SECONDS
    last_log_size: int = 0
    last_dir_footprint: tuple[int, int] = (0, 0)
    read_offset: int = 0
    exit_code: int | None = None
    finished_at: float | None = None
    cancelled: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _log_thread: threading.Thread | None = field(default=None, repr=False)

    def is_finished(self) -> bool:
        """判断进程与日志读取线程是否均已结束。"""
        with self._lock:
            return self.exit_code is not None


class BackgroundCommandManager:
    """后台命令/长耗时下载进程池管理器。"""

    def __init__(
        self,
        *,
        kill_tree_fn: Any,
        cwd_marker: str = "__GH_CWD_MARKER__=",
        stagnant_threshold: float = DEFAULT_STAGNANT_THRESHOLD_SECONDS,
    ) -> None:
        self._kill_tree_fn = kill_tree_fn
        self._cwd_marker = cwd_marker
        self._default_stagnant_threshold = stagnant_threshold
        self._jobs: dict[str, BackgroundJob] = {}
        self._jobs_lock = threading.Lock()

    def start_job(
        self,
        *,
        command: str,
        exec_args: list[str],
        cwd: str,
        env: dict[str, str],
        session_id: str | None = None,
        temp_script_path: str | None = None,
        allow_gui_window: bool = False,
        watch_dirs: list[Path] | None = None,
        stagnant_threshold: float | None = None,
    ) -> BackgroundJob:
        """拉起子进程并启动实时流式日志采集线程。"""
        job_id = f"bg_{uuid.uuid4().hex[:8]}"
        fd, log_name = tempfile.mkstemp(prefix=f"agenthub_{job_id}_", suffix=".log")
        os.close(fd)
        log_path = Path(log_name)

        proc = subprocess.Popen(
            exec_args,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=(sys.platform != "win32"),
            **get_silent_subprocess_kwargs(allow_gui=allow_gui_window),
        )

        now = time.monotonic()
        effective_watch_dirs = watch_dirs if watch_dirs is not None else resolve_default_watch_dirs(cwd, env)
        initial_footprint = measure_watch_dirs_footprint(effective_watch_dirs, now)

        job = BackgroundJob(
            job_id=job_id,
            session_id=session_id,
            command=command,
            cwd=cwd,
            process=proc,
            log_path=log_path,
            temp_script_path=temp_script_path,
            watch_dirs=effective_watch_dirs,
            started_at=now,
            last_activity_at=now,
            stagnant_threshold=(
                stagnant_threshold if stagnant_threshold is not None else self._default_stagnant_threshold
            ),
            last_dir_footprint=initial_footprint,
        )

        def _pump_output() -> None:
            try:
                if proc.stdout is not None:
                    stdout_fd = proc.stdout.fileno()
                    with open(log_path, "ab") as log_fp:
                        while True:
                            # 使用 os.read 直接读取底层管道可用字节，避免 BufferedReader.read(4096) 凑满缓冲才返回
                            chunk = os.read(stdout_fd, 4096)
                            if not chunk:
                                break
                            log_fp.write(chunk)
                            log_fp.flush()
            except Exception as pump_err:
                logger.debug(f"[BackgroundCommand] 日志采集异常 (job_id={job_id}): {pump_err}")
            finally:
                if proc.stdout is not None:
                    try:
                        proc.stdout.close()
                    except OSError:
                        pass
                rc = proc.wait()
                with job._lock:
                    job.exit_code = int(rc)
                    job.finished_at = time.monotonic()
                if temp_script_path:
                    try:
                        os.unlink(temp_script_path)
                    except OSError:
                        pass

        thread = threading.Thread(target=_pump_output, name=f"bg-log-{job_id}", daemon=True)
        job._log_thread = thread
        with self._jobs_lock:
            self._jobs[job_id] = job
        thread.start()
        return job

    def get_job(self, job_id: str) -> BackgroundJob | None:
        """根据 job_id 获取后台任务对象。"""
        with self._jobs_lock:
            return self._jobs.get(job_id)

    def _read_incremental_and_tail(self, job: BackgroundJob, tail_lines: int = 40) -> tuple[str, str]:
        """读取任务自上次查询以来的增量日志，以及整体尾部快照。"""
        if not job.log_path.is_file():
            return "", ""

        try:
            raw_all = job.log_path.read_bytes()
        except OSError:
            return "", ""

        with job._lock:
            offset = min(job.read_offset, len(raw_all))
            delta_bytes = raw_all[offset:]
            job.read_offset = len(raw_all)

        delta_text = self._clean_log_text(_decode_bytes(delta_bytes))[0]
        full_cleaned, _ = self._clean_log_text(_decode_bytes(raw_all))
        all_lines = [ln for ln in full_cleaned.splitlines() if ln.strip()]
        tail_text = "\n".join(all_lines[-max(1, tail_lines) :]) if all_lines else ""
        return delta_text, tail_text

    def _clean_log_text(self, text: str) -> tuple[str, str | None]:
        """过滤 CLIXML、剥离尾部 CWD 标记并执行 ANSI 降噪。"""
        if not text:
            return "", None
        cleaned = re.sub(r"#<\s*CLIXML[\s\S]*?</Objs>", "", text, flags=re.IGNORECASE)
        extracted_cwd: str | None = None
        if self._cwd_marker in cleaned:
            parts = cleaned.rsplit(self._cwd_marker, 1)
            cleaned = parts[0].rstrip()
            candidate = parts[1].strip().splitlines()[0].strip() if parts[1].strip() else ""
            if candidate and os.path.isdir(candidate):
                extracted_cwd = candidate
        return _sanitize_text(cleaned).strip(), extracted_cwd

    def evaluate_status(self, job: BackgroundJob) -> dict[str, Any]:
        """
        基于“进程存活 + 日志字节增量 + 缓存/工作目录活跃度”判定当前任务健康状态。
        """
        now = time.monotonic()
        try:
            log_size = job.log_path.stat().st_size if job.log_path.is_file() else 0
        except OSError:
            log_size = job.last_log_size

        dir_footprint = measure_watch_dirs_footprint(job.watch_dirs, job.started_at)
        log_grown = log_size > job.last_log_size
        dir_grown = dir_footprint != job.last_dir_footprint

        with job._lock:
            if log_grown or dir_grown:
                job.last_activity_at = now
                job.last_log_size = log_size
                job.last_dir_footprint = dir_footprint
            exit_code = job.exit_code
            cancelled = job.cancelled
            last_act = job.last_activity_at
            elapsed = now - job.started_at
            idle_seconds = max(0.0, now - last_act)

        if exit_code is not None:
            if cancelled:
                status = "cancelled"
            elif exit_code == 0:
                status = "completed"
            else:
                status = "failed"
            return {
                "status": status,
                "exit_code": exit_code,
                "elapsed_seconds": round(elapsed, 1),
                "idle_seconds": round(idle_seconds, 1),
                "log_bytes": log_size,
                "active_dir_bytes": dir_footprint[0],
                "is_active": False,
                "network_error": None,
            }

        # 进程仍在运行：读取日志尾部做致命网络指纹辅助探测
        tail_snippet = ""
        try:
            if job.log_path.is_file():
                raw = job.log_path.read_bytes()
                tail_snippet = _decode_bytes(raw[-4096:])
        except OSError:
            pass

        net_err = detect_network_error(tail_snippet)
        # 若刚产生新活动（日志或目录刚增长），仅把网络指纹作为观察，不立即误判断网
        if net_err and idle_seconds >= 5.0:
            status = "network_error"
        elif idle_seconds >= job.stagnant_threshold:
            status = "stagnant"
        else:
            status = "running"

        return {
            "status": status,
            "exit_code": None,
            "elapsed_seconds": round(elapsed, 1),
            "idle_seconds": round(idle_seconds, 1),
            "log_bytes": log_size,
            "active_dir_bytes": dir_footprint[0],
            "is_active": log_grown or dir_grown,
            "network_error": net_err,
        }

    async def wait_initial_or_detach(
        self,
        job: BackgroundJob,
        *,
        observe_seconds: float = DEFAULT_HEAVY_OBSERVE_SECONDS,
    ) -> dict[str, Any]:
        """
        前台观察窗口等待：若在 observe_seconds 内完成则返回完整同步结果；
        若仍未结束则无缝转入后台，不杀进程，返回 job_id 与初始日志快照。
        """
        deadline = time.monotonic() + max(0.0, observe_seconds)
        while time.monotonic() < deadline:
            if job.is_finished():
                break
            await asyncio.sleep(min(0.25, max(0.01, deadline - time.monotonic())))

        if job.is_finished():
            raw_all = job.log_path.read_bytes() if job.log_path.is_file() else b""
            cleaned_out, new_cwd = self._clean_log_text(_decode_bytes(raw_all))
            rc = job.exit_code if job.exit_code is not None else 0
            self._cleanup_finished_job(job.job_id)
            return {
                "output": cleaned_out or ("(执行成功，无控制台输出)" if rc == 0 else f"命令执行结束，退出码: {rc}"),
                "exit_code": rc,
                "new_cwd": new_cwd or job.cwd,
                "timed_out": False,
                "background": False,
            }

        delta_text, tail_text = self._read_incremental_and_tail(job, tail_lines=30)
        preview = delta_text or tail_text or "(已启动，暂未产生控制台输出，正在后台运行/下载中)"
        state = self.evaluate_status(job)
        output = (
            f"[已自动转入后台运行] 命令在 {observe_seconds:.1f}s 观察窗口内尚未结束，已托管至后台继续执行（未强杀进程）。\n"
            f"- job_id: `{job.job_id}`\n"
            f"- pid: {job.process.pid}\n"
            f"- 状态: `{state['status']}` | 日志字节: {state['log_bytes']} | 缓存目录活跃字节: {state['active_dir_bytes']}\n"
            f"- 初始输出:\n{preview}\n\n"
            f"请调用 `check_background_command(job_id=\"{job.job_id}\", wait_seconds=20)` 持续跟踪进度；"
            f"只要状态为 `running`（下载或缓存仍在增长）请继续耐心等待，切勿随意强杀。"
        )
        return {
            "output": output,
            "exit_code": 0,
            "new_cwd": job.cwd,
            "timed_out": False,
            "background": True,
            "job_id": job.job_id,
            "status": state["status"],
        }

    async def check_job(
        self,
        job_id: str,
        *,
        wait_seconds: float = 20.0,
        tail_lines: int = 40,
    ) -> dict[str, Any]:
        """
        安全等待一小段时间并检查后台任务最新状态与增量日志。
        任务若在等待期间结束，会立即提前唤醒返回。
        """
        job = self.get_job(job_id)
        if job is None:
            return {
                "job_id": job_id,
                "status": "not_found",
                "exit_code": -1,
                "output": f"Error: 未找到后台任务 `{job_id}`（可能已结束并被清理或 ID 不存在）。",
            }

        capped_wait = min(max(0.0, float(wait_seconds)), MAX_CHECK_WAIT_SECONDS)
        if capped_wait > 0 and not job.is_finished():
            deadline = time.monotonic() + capped_wait
            while time.monotonic() < deadline:
                if job.is_finished():
                    break
                await asyncio.sleep(min(0.5, max(0.05, deadline - time.monotonic())))

        state = self.evaluate_status(job)
        delta_text, tail_text = self._read_incremental_and_tail(job, tail_lines=tail_lines)
        status = state["status"]
        new_cwd: str | None = None

        if job.is_finished():
            raw_all = job.log_path.read_bytes() if job.log_path.is_file() else b""
            _, new_cwd = self._clean_log_text(_decode_bytes(raw_all))

        log_section = delta_text or tail_text or "(本轮暂无新增控制台输出)"
        header = (
            f"[后台任务状态] job_id=`{job_id}` | status=`{status}` | "
            f"已运行={state['elapsed_seconds']}s | 距上次活跃={state['idle_seconds']}s | "
            f"日志={state['log_bytes']}B | 缓存活跃={state['active_dir_bytes']}B"
        )

        if status == "completed":
            output = f"{header}\n退出码: 0（任务已成功完成）\n--- 最新日志 ---\n{log_section}"
            self._cleanup_finished_job(job_id)
        elif status == "failed":
            output = (
                f"Error: {header}\n退出码: {state['exit_code']}（后台命令执行失败）\n"
                f"--- 错误日志尾部 ---\n{tail_text or log_section}"
            )
            self._cleanup_finished_job(job_id)
        elif status == "cancelled":
            output = f"{header}\n任务已被手动取消。\n--- 最后日志 ---\n{tail_text or log_section}"
            self._cleanup_finished_job(job_id)
        elif status == "network_error":
            output = (
                f"{header}\n[网络异常警告]: 检测到网络错误指纹 `{state['network_error']}`，"
                f"且已连续 {state['idle_seconds']}s 无新进展。建议调用 `cancel_background_command` 终止后检查网络或切换镜像源。\n"
                f"--- 最新日志 ---\n{log_section}"
            )
        elif status == "stagnant":
            output = (
                f"{header}\n[停滞告警]: 日志与缓存目录已连续 {state['idle_seconds']}s 无字节增长，进程可能卡死或等待输入。"
                f"若确认卡死，请调用 `cancel_background_command(job_id=\"{job_id}\")` 终止并更换方案。\n"
                f"--- 最新日志 ---\n{log_section}"
            )
        else:
            activity_hint = "（活跃下载/输出中）" if state["is_active"] else "（进程存活，等待远程响应/写入）"
            output = (
                f"{header} {activity_hint}\n"
                f"--- 增量日志 ---\n{log_section}\n\n"
                f"提示：任务仍在正常推进，请继续调用 `check_background_command(job_id=\"{job_id}\", wait_seconds=20)` 跟踪至完成。"
            )

        return {
            "job_id": job_id,
            "status": status,
            "exit_code": state["exit_code"],
            "elapsed_seconds": state["elapsed_seconds"],
            "idle_seconds": state["idle_seconds"],
            "log_bytes": state["log_bytes"],
            "active_dir_bytes": state["active_dir_bytes"],
            "network_error": state["network_error"],
            "new_cwd": new_cwd or job.cwd,
            "output": output,
        }

    async def cancel_job(self, job_id: str) -> dict[str, Any]:
        """主动终止后台任务及其整个子进程树。"""
        job = self.get_job(job_id)
        if job is None:
            return {
                "job_id": job_id,
                "status": "not_found",
                "output": f"Error: 未找到后台任务 `{job_id}`。",
            }

        with job._lock:
            job.cancelled = True
            pid = job.process.pid

        if not job.is_finished():
            await asyncio.to_thread(self._kill_tree_fn, pid)
            # 等待日志线程收割退出状态
            for _ in range(10):
                if job.is_finished():
                    break
                await asyncio.sleep(0.05)

        _, tail_text = self._read_incremental_and_tail(job, tail_lines=20)
        self._cleanup_finished_job(job_id)
        return {
            "job_id": job_id,
            "status": "cancelled",
            "output": f"已终止后台任务 `{job_id}` (PID={pid})。\n--- 终止前日志尾部 ---\n{tail_text or '(无输出)'}",
        }

    def cancel_session_jobs(self, session_id: str) -> int:
        """联动清理指定会话下所有仍在运行的后台任务。"""
        with self._jobs_lock:
            targets = [job for job in self._jobs.values() if job.session_id == session_id]

        cancelled_count = 0
        for job in targets:
            with job._lock:
                job.cancelled = True
                pid = job.process.pid
            if not job.is_finished():
                self._kill_tree_fn(pid)
                cancelled_count += 1
            self._cleanup_finished_job(job.job_id)
        return cancelled_count

    def _cleanup_finished_job(self, job_id: str) -> None:
        """从活动池移除已结束任务并清理临时日志文件。"""
        with self._jobs_lock:
            job = self._jobs.pop(job_id, None)
        if job is None:
            return
        try:
            if job.log_path.is_file():
                job.log_path.unlink()
        except OSError:
            pass
