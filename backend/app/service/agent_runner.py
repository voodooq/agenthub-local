
class _CompatSubprocess:
    """Windows 下 SelectorEventLoop 不支持异步子进程时的透明兼容适配器"""
    def __init__(self, cmd: list[str], env: dict[str, str], cwd: str, stdin_bytes: bytes | None = None) -> None:
        import subprocess
        import threading
        from ..core.runtime_paths import get_silent_subprocess_kwargs

        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE if stdin_bytes is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=cwd,
            **get_silent_subprocess_kwargs(),
        )
        self.pid = self._proc.pid
        self.returncode: int | None = None
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._loop = asyncio.get_running_loop()
        self.stdout = self
        self.stderr = self

        if stdin_bytes is not None and self._proc.stdin is not None:
            def _feed_stdin() -> None:
                assert self._proc.stdin is not None
                try:
                    self._proc.stdin.write(stdin_bytes)
                    self._proc.stdin.flush()
                except Exception:
                    pass
                finally:
                    try:
                        self._proc.stdin.close()
                    except Exception:
                        pass

            threading.Thread(target=_feed_stdin, daemon=True).start()

        def _pump_stdout():
            assert self._proc.stdout is not None
            for line in iter(self._proc.stdout.readline, b""):
                self._loop.call_soon_threadsafe(self._queue.put_nowait, line)
            self._proc.stdout.close()
            self._loop.call_soon_threadsafe(self._queue.put_nowait, None)

        threading.Thread(target=_pump_stdout, daemon=True).start()

    async def readline(self) -> bytes:
        val = await self._queue.get()
        if val is None:
            return b""
        return val

    async def read(self) -> bytes:
        if self._proc.stderr:
            return await asyncio.to_thread(self._proc.stderr.read)
        return b""

    async def wait(self) -> int:
        self.returncode = await asyncio.to_thread(self._proc.wait)
        return self.returncode

    def kill(self) -> None:
        try:
            self._proc.kill()
        except Exception:
            pass

"""
Gemini / Antigravity CLI 子进程执行器（Agent Runner）
优先使用 Google Antigravity 官方 CLI (agy)，无缝回退兼容 gemini CLI。
实现流式输出、超时保护、注入防护。
本程序不接触任何 OAuth 令牌，所有认证由 CLI 自己处理。
"""
import asyncio
import inspect
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import AsyncGenerator, Any

from ..core.logging_config import logger
from ..core.config import settings
from ..core.runtime_paths import get_silent_subprocess_kwargs

# 单个请求子进程的超时时间（秒）
PROCESS_TIMEOUT_SECONDS = 180
# 单次会话最多允许的 chunk 块数与累计字符上限（兼顾细粒度代码流式写盘与恶意无限流防护）
MAX_CHUNKS_PER_SESSION = 30000
MAX_STREAM_CHARS_PER_SESSION = 300_000
# 异步子进程 stdout/stderr 单行缓冲区上限（16 MiB，避免大型 stream-json 结果行触发 64 KiB LimitOverrunError）
SUBPROCESS_STREAM_LIMIT = 16 * 1024 * 1024


def _check_stream_safety_limit(chunk_count: int, char_count: int) -> str | None:
    """检查单次 CLI 会话流是否超过块数或字符安全上限，超出时返回用户可读错误说明。"""
    if chunk_count >= MAX_CHUNKS_PER_SESSION:
        return "响应块数量超过安全上限，请求已终止（单步输出过长时建议改用多步 write_local_file 拆分写入）"
    if char_count >= MAX_STREAM_CHARS_PER_SESSION:
        return "单次响应输出字符数超过安全上限，请求已终止（建议将超大内容拆分多步写入）"
    return None
# 活跃 CLI 子进程注册表：conversation_id -> set[proc]
_ACTIVE_CLI_PROCESSES: dict[str, set[Any]] = {}


class _AsyncLineReader:
    """兼容 asyncio.StreamReader 与兼容适配器的安全异步按行读取器。

    对于原生 asyncio.StreamReader，改用固定块 read() + 内存换行切分，
    彻底避免单行超长 NDJSON 超过 StreamReader.limit 时抛出 LimitOverrunError/ValueError。
    """

    def __init__(self, stream: Any, chunk_size: int = 65536) -> None:
        self._stream = stream
        self._chunk_size = chunk_size
        self._buffer = bytearray()
        self._eof = False
        self._use_chunked = isinstance(stream, asyncio.StreamReader)

    async def readline(self) -> bytes:
        """异步读取下一行原始字节数据（包含末尾换行符，EOF 时返回空字节串）。"""
        if not self._use_chunked:
            return await self._stream.readline()

        while True:
            nl_idx = self._buffer.find(b"\n")
            if nl_idx != -1:
                line = bytes(self._buffer[: nl_idx + 1])
                del self._buffer[: nl_idx + 1]
                return line
            if self._eof:
                if self._buffer:
                    line = bytes(self._buffer)
                    self._buffer.clear()
                    return line
                return b""
            chunk = await self._stream.read(self._chunk_size)
            if not chunk:
                self._eof = True
            else:
                self._buffer.extend(chunk)


def _kill_process_tree(proc: Any) -> None:
    """安全跨平台强杀 CLI 子进程及其派生子进程树（兼容测试 Mock 对象不误杀系统 PID）"""
    if proc is None:
        return
    if getattr(proc, "returncode", None) is not None:
        return

    pid = getattr(proc, "pid", None)
    is_real_proc = isinstance(proc, _CompatSubprocess) or type(proc).__module__.startswith("asyncio.")
    if sys.platform == "win32" and is_real_proc and isinstance(pid, int) and pid > 0:
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
                **get_silent_subprocess_kwargs(),
            )
        except Exception:
            pass

    try:
        proc.kill()
    except Exception:
        pass


def cancel_cli_processes(conversation_id: str) -> int:
    """强制终止指定会话关联的所有活跃 CLI 子进程树，并返回已触发终止的进程数"""
    if not conversation_id:
        return 0
    procs = list(_ACTIVE_CLI_PROCESSES.get(conversation_id, ()))
    killed = 0
    for proc in procs:
        if getattr(proc, "returncode", None) is None:
            _kill_process_tree(proc)
            killed += 1
    return killed

from ..core.proxy_helper import get_system_proxy_env

# 白名单：只允许传递给子进程的环境变量 key（含代理与 Windows 系统核心目录变量）
_ALLOWED_ENV_KEYS = {
    "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "PATH", "USERPROFILE", "HOME", "USERNAME", "USERDOMAIN",
    "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA",
    "PROGRAMFILES", "PROGRAMFILES(X86)", "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)", "PUBLIC",
    "TEMP", "TMP",
    "PATHEXT", "COMSPEC", "OS",
    "NODE_PATH",          # Node.js 需要
    "GEMINI_CLI_HOME",    # 本程序注入
    "GEMINI_CLI_TRUST_WORKSPACE",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy",
}


def _find_cli_binary() -> tuple[str, str]:
    """
    探测可用的 CLI 可执行文件。
    优先查找 Antigravity CLI (agy)，若不存在则降级查找 gemini CLI。
    Returns:
        (binary_path, cli_type) 其中 cli_type 为 'agy' 或 'gemini'
    """
    # 1. 优先在系统 PATH 中寻找 agy
    agy_in_path = shutil.which("agy") or shutil.which("agy.exe")
    if agy_in_path:
        return agy_in_path, "agy"

    # 2. 检查常见安装位置 (~/.gemini/bin/agy.exe 或 ~/.gemini/bin/agy)
    default_home = Path.home() / ".gemini" / "bin"
    for candidate in [default_home / "agy.exe", default_home / "agy"]:
        if candidate.exists() and os.access(candidate, os.X_OK | os.R_OK):
            return str(candidate), "agy"

    # 3. 降级寻找 gemini
    gemini_in_path = shutil.which("gemini") or shutil.which("gemini.cmd") or shutil.which("gemini.ps1")
    if gemini_in_path:
        return gemini_in_path, "gemini"

    # 兜底默认回退
    return "gemini", "gemini"


def _build_safe_env(cli_home_dir: str | None = None) -> dict[str, str]:
    """
    构建子进程用的精简环境变量字典。
    只传递白名单变量，然后注入 GEMINI_CLI_HOME。
    防止主进程敏感环境变量（如 tokens/secrets）泄漏到子进程。
    """
    base_env = {k: v for k, v in os.environ.items() if k.upper() in _ALLOWED_ENV_KEYS}
    # 自动补充 Windows 注册表或环境系统代理
    base_env.update(get_system_proxy_env())
    if cli_home_dir and str(cli_home_dir).strip().lower() not in ("", "default", "none"):
        base_env["GEMINI_CLI_HOME"] = cli_home_dir
    base_env["GEMINI_CLI_TRUST_WORKSPACE"] = "true"
    return base_env


def _sanitize_prompt(prompt: str) -> str:
    """
    对用户 prompt 做基础安全截断保护。
    若超过 64000 字符，保留头部系统/工具定义及尾部最新工具输出与用户问题，折叠中间冗长历史。
    """
    max_chars = 64000
    if len(prompt) > max_chars:
        logger.warning(f"用户 prompt 超过 {max_chars} 字符（当前 {len(prompt)}），保留头尾并折叠中间历史")
        marker = "\n...[中间历史上下文过长已截断]...\n"
        head_len = 30000
        tail_len = max_chars - head_len - len(marker)
        prompt = f"{prompt[:head_len]}{marker}{prompt[-tail_len:]}"
    return prompt


def resolve_agy_model_flag(model: str | None, thinking_level: str | None = None) -> str:
    """
    将前端模型标识与思考等级解析为 Antigravity CLI (agy) 支持的真实 --model 参数。
    """
    level = (thinking_level or "high").lower()
    if level not in ("low", "medium", "high"):
        level = "high"

    if not model:
        return f"gemini-3.8-flash-{level}"

    m_lower = model.strip().lower()

    # GPT-OSS 120B 仅支持 medium
    if "gpt" in m_lower or "120b" in m_lower:
        return "gpt-oss-120b-medium"

    explicit_suffix = None
    for sfx in ("-low", "-medium", "-high"):
        if m_lower.endswith(sfx):
            explicit_suffix = sfx[1:]
            break

    effective_level = level if thinking_level else (explicit_suffix or level)

    if "argon" in m_lower or "2.5-pro" in m_lower or "3.1-pro" in m_lower:
        return f"gemini-4-argon-{effective_level}"
    if "opus" in m_lower:
        return f"claude-opus-5-5-{effective_level}"
    if "sonnet" in m_lower:
        return f"claude-sonnet-5-5-{effective_level}"
    if "3.8-flash" in m_lower or "flash" in m_lower:
        return f"gemini-3.8-flash-{effective_level}"

    return model.strip()


async def test_cli_model_availability(
    cli_home_dir: str | None,
    model: str,
    thinking_level: str = "medium",
    timeout_seconds: float = 25.0,
) -> dict[str, Any]:
    """
    针对指定 CLI 账号目录与模型发起真实轻量探测，验证该账号是否真正可调用该模型。
    """
    import time
    env = _build_safe_env(cli_home_dir)
    cli_bin, cli_type = _find_cli_binary()

    if cli_type == "agy":
        model_flag = resolve_agy_model_flag(model, thinking_level)
        cmd = [
            cli_bin,
            "-p", "reply ok",
            "--model", model_flag,
            "--output-format", "json",
            "--print-timeout", f"{max(5, int(timeout_seconds))}s",
            "--disable-slash-commands",
        ]
    else:
        model_flag = model
        cmd = [cli_bin, "-o", "json", "-m", model_flag, "reply ok"]

    cwd = str(Path.home())
    start_time = time.time()

    try:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=SUBPROCESS_STREAM_LIMIT,
                env=env,
                cwd=cwd,
                **get_silent_subprocess_kwargs(),
            )
        except NotImplementedError:
            proc = _CompatSubprocess(cmd, env, cwd)

        async def _collect_output() -> tuple[str, str, int]:
            out_lines: list[str] = []
            assert proc.stdout is not None
            line_reader = _AsyncLineReader(proc.stdout)
            while True:
                line = await line_reader.readline()
                if not line:
                    break
                out_lines.append(line.decode("utf-8", errors="replace").strip())
            stderr_bytes = await proc.stderr.read() if proc.stderr else b""
            code = await proc.wait()
            return "\n".join(out_lines), stderr_bytes.decode("utf-8", errors="replace"), code

        try:
            stdout_text, stderr_text, exit_code = await asyncio.wait_for(
                _collect_output(),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            _kill_process_tree(proc)
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except Exception:
                pass
            return {
                "available": False,
                "model_flag": model_flag,
                "latency_ms": round((time.time() - start_time) * 1000),
                "error": f"探测超时 (>{int(timeout_seconds)}s)",
            }

        latency_ms = round((time.time() - start_time) * 1000)

        # 解析 JSON 响应确认非 ERROR
        if exit_code == 0 and stdout_text:
            for line in reversed(stdout_text.splitlines()):
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    try:
                        data = json.loads(line)
                        if data.get("status") == "ERROR" or data.get("error"):
                            return {
                                "available": False,
                                "model_flag": model_flag,
                                "latency_ms": latency_ms,
                                "error": str(data.get("error") or "模型返回错误状态")[:160],
                            }
                        return {
                            "available": True,
                            "model_flag": model_flag,
                            "latency_ms": latency_ms,
                            "error": None,
                        }
                    except Exception:
                        pass
            return {
                "available": True,
                "model_flag": model_flag,
                "latency_ms": latency_ms,
                "error": None,
            }

        err_detail = (stderr_text or stdout_text or f"退出码 {exit_code}").strip()
        return {
            "available": False,
            "model_flag": model_flag,
            "latency_ms": latency_ms,
            "error": err_detail[:160],
        }
    except Exception as e:
        return {
            "available": False,
            "model_flag": model_flag,
            "latency_ms": round((time.time() - start_time) * 1000),
            "error": str(e)[:160],
        }


async def run_cli_stream(
    cli_home_dir: str,
    prompt: str,
    model: str | None = None,
    thinking_level: str | None = None,
    allowed_tools: list[str] | None = None,
    approval_mode: str = "default",
    work_dir: str | None = None,
    system_prompt: str | None = None,
    conversation_id: str | None = None,
    cancel_event: asyncio.Event | None = None,
    allow_native_tools: bool = False,
) -> AsyncGenerator[dict[str, Any], None]:
    """
    以子进程方式调用 Antigravity / Gemini CLI，逐行解析 stream-json 事件并 yield。

    Args:
        cli_home_dir: 账号专属 GEMINI_CLI_HOME 目录
        prompt: 用户输入（已由调用方净化）
        model: 模型名称（可选，传 --model）
        thinking_level: 思考深度等级 ('low' | 'medium' | 'high')
        allowed_tools: 自动放行的工具列表（白名单）
        approval_mode: 'default'（需人工确认）或 'auto_edit'（代码编辑自动批准）
        work_dir: 子进程工作目录（白名单目录之一）
        system_prompt: 系统指令覆盖（通过前置文本传入）
        conversation_id: 会话标识（用于注册取消句柄）
        cancel_event: 异步取消信号
        allow_native_tools: 是否允许 CLI 放行自带原生工具；在 AgentHub 宿主托管链路上默认 False，防止绕过宿主审计

    Yields:
        解析后的事件字典，type 字段为：
        init / content / thinking / tool_request / tool_result / error / done / timeout /
        rate_limited / auth_error
    """
    prompt = _sanitize_prompt(prompt)
    env = _build_safe_env(cli_home_dir)
    cli_bin, cli_type = _find_cli_binary()

    # 根据 CLI 类型组装命令参数；长 prompt 严禁直传 Windows 命令行以防触碰 CreateProcessW 32767 字符上限 ([WinError 206])
    safe_mode = "default" if approval_mode == "yolo" else approval_mode
    stdin_payload_bytes: bytes | None = None

    if cli_type == "agy":
        cmd: list[str] = [
            cli_bin,
            "--output-format", "stream-json",
            "--input-format", "stream-json",
            "--disable-slash-commands",
        ]
        # agy 必须显式传递合法的 --model 参数
        model_flag = resolve_agy_model_flag(model, thinking_level)
        cmd.extend(["--model", model_flag])
        # NOTE: 仅在显式放行 CLI 原生工具且处于 auto_edit 模式时才跳过权限确认，避免 agy 旁路 AgentHub 工具体系
        if allow_native_tools and safe_mode == "auto_edit":
            cmd.append("--dangerously-skip-permissions")
        # NOTE: agy --input-format stream-json 从 stdin 读取 {"event":"user","message":{...}}，彻底免疫 Windows 命令行长度限制
        stdin_payload_bytes = (
            json.dumps(
                {
                    "event": "user",
                    "message": {"role": "user", "content": prompt},
                },
                ensure_ascii=False,
            )
            + "\n"
        ).encode("utf-8")
    else:
        # 旧版 gemini CLI 参数：超长文本回退 stdin 管道传参
        cmd: list[str] = [cli_bin, "-o", "stream-json"]
        if model:
            cmd.extend(["-m", model])
        if allowed_tools:
            for tool in allowed_tools:
                cmd.extend(["--allowed-tools", tool])
        cmd.extend(["--approval-mode", safe_mode])
        if len(prompt) > 20000:
            stdin_payload_bytes = prompt.encode("utf-8")
        else:
            cmd.append(prompt)

    cwd = work_dir or str(Path.home())
    logger.info(
        f"启动 {cli_type.upper()} CLI 子进程 ({cli_bin}): alias={Path(cli_home_dir).name} "
        f"model={model or 'default'} cwd={cwd} prompt_chars={len(prompt)} stdin_mode={stdin_payload_bytes is not None} pid=pending"
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE if stdin_payload_bytes is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=SUBPROCESS_STREAM_LIMIT,
            env=env,
            cwd=cwd,
            **get_silent_subprocess_kwargs(),
        )
    except NotImplementedError:
        logger.info("当前环境不支持 asyncio 原生子进程传输，自动切换至 Windows 线程兼容适配器")
        proc = _CompatSubprocess(cmd, env, cwd, stdin_bytes=stdin_payload_bytes)

    # 向异步子进程 stdin 写入载荷并发送 EOF，通知 CLI 单轮输入结束
    if stdin_payload_bytes is not None and not isinstance(proc, _CompatSubprocess):
        proc_stdin = proc.stdin
        if proc_stdin is not None:
            try:
                proc_stdin.write(stdin_payload_bytes)
                drain_fn = getattr(proc_stdin, "drain", None)
                if callable(drain_fn):
                    drain_res = drain_fn()
                    if inspect.isawaitable(drain_res):
                        await drain_res
                write_eof_fn = getattr(proc_stdin, "write_eof", None)
                can_eof_fn = getattr(proc_stdin, "can_write_eof", None)
                if callable(write_eof_fn) and (not callable(can_eof_fn) or can_eof_fn()):
                    write_eof_fn()
                close_fn = getattr(proc_stdin, "close", None)
                if callable(close_fn):
                    close_fn()
            except Exception as stdin_err:
                logger.warning(f"向 {cli_type.upper()} CLI 写入 stdin 载荷异常: {stdin_err}")

    logger.info(f"{cli_type.upper()} CLI 子进程已启动 PID={proc.pid}")
    if conversation_id:
        _ACTIVE_CLI_PROCESSES.setdefault(conversation_id, set()).add(proc)
    chunk_count = 0
    accumulated_chars = 0
    accumulated_text = ""

    try:
        async def _read_lines() -> AsyncGenerator[str | dict, None]:
            """从 stdout 逐行读取 NDJSON，支持主动取消与超时保护"""
            assert proc.stdout is not None
            line_reader = _AsyncLineReader(proc.stdout)
            while True:
                if cancel_event and cancel_event.is_set():
                    _kill_process_tree(proc)
                    yield {"type": "cancelled", "message": "已中断执行"}
                    return

                read_task = asyncio.create_task(line_reader.readline())
                cancel_task = asyncio.create_task(cancel_event.wait()) if cancel_event else None
                wait_tasks = {read_task, cancel_task} if cancel_task else {read_task}
                try:
                    done, pending = await asyncio.wait(
                        wait_tasks,
                        timeout=PROCESS_TIMEOUT_SECONDS,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for t in pending:
                        t.cancel()
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)

                    if not done:
                        _kill_process_tree(proc)
                        yield {"type": "timeout", "message": "子进程响应超时，已终止"}
                        return

                    if cancel_task and cancel_task in done and cancel_event and cancel_event.is_set():
                        _kill_process_tree(proc)
                        yield {"type": "cancelled", "message": "已中断执行"}
                        return

                    line = read_task.result()
                except asyncio.CancelledError:
                    read_task.cancel()
                    if cancel_task:
                        cancel_task.cancel()
                    _kill_process_tree(proc)
                    raise

                if not line:
                    break
                yield line.decode("utf-8", errors="replace").strip()

        async for raw in _read_lines():
            if isinstance(raw, dict):
                # timeout / cancelled 事件直接透传并退出
                yield raw
                return
            if not raw:
                continue
            limit_error = _check_stream_safety_limit(chunk_count, accumulated_chars)
            if limit_error:
                logger.warning(
                    f"单次会话流输出超过安全上限 (chunks={chunk_count}, chars={accumulated_chars})，终止子进程"
                )
                _kill_process_tree(proc)
                yield {"type": "error", "message": limit_error}
                return
            try:
                event: dict[str, Any] = json.loads(raw)
            except json.JSONDecodeError:
                # 非 JSON 行，忽略
                continue

            # 1. 适配 Antigravity CLI (agy) 事件格式
            if "event" in event:
                evt_name = event.get("event")
                if evt_name == "step_update":
                    step_update = event.get("step_update", {})
                    thinking_delta = (
                        step_update.get("thinking_delta")
                        or step_update.get("thinking")
                        or ""
                    )
                    if thinking_delta:
                        yield {"type": "thinking", "text": thinking_delta}

                    # 多字段兼容提取文本（text_delta / text / content / response）
                    text_delta = (
                        step_update.get("text_delta")
                        or step_update.get("text")
                        or step_update.get("content")
                        or ""
                    )
                    if text_delta:
                        chunk_count += 1
                        accumulated_chars += len(text_delta)
                        accumulated_text += text_delta
                        yield {"type": "content", "text": text_delta}

                    # 工具请求（实测 agy 实际推送 step_type == "tool" 及 tool_info，兼容历史 "tool_call"）
                    step_type = step_update.get("step_type")
                    step_state = str(step_update.get("state") or "").upper()
                    tool_info = step_update.get("tool_info")
                    if (
                        step_type in ("tool", "tool_call")
                        or "tool_call" in step_update
                        or isinstance(tool_info, dict)
                    ):
                        t_info_dict = tool_info if isinstance(tool_info, dict) else {}
                        t_name = (
                            step_update.get("tool_name")
                            or t_info_dict.get("name")
                            or "system_tool"
                        )
                        t_input = (
                            step_update.get("tool_input")
                            or t_info_dict.get("parameters")
                            or {}
                        )
                        if step_state == "DONE" and step_type == "tool":
                            yield {
                                "type": "tool_result",
                                "tool_name": t_name,
                                "output": t_info_dict.get("output", ""),
                            }
                        else:
                            yield {
                                "type": "tool_request",
                                "tool_name": t_name,
                                "tool_input": t_input,
                                "requires_approval": True,
                            }
                elif evt_name == "result":
                    res = event.get("result", {})
                    if res.get("status") == "ERROR" or res.get("error"):
                        err_msg = res.get("error") or "CLI 执行异常失败"
                        logger.error(f"agy 返回 ERROR 状态: {err_msg}")
                        if "copyrighted works" in err_msg.lower() or "safety filter" in err_msg.lower():
                            friendly_msg = (
                                "内容触发了 Google 官方安全策略与版权材料审查（Safety Filter: Copyrighted Works）。"
                                "生成的部分文本因与现有公开文献或已有著作高度重合而被上游过滤阻断。"
                                "建议优化提示词（例如要求「用自己的语言改写转述」、「列出核心要点」或「按步骤分批生成」），避免直接输出大段原文。"
                            )
                            yield {"type": "error", "message": friendly_msg}
                            return
                        yield {"type": "error", "message": err_msg}
                        return
                    # NOTE: agy 非增量结尾优先填充 response 字段，其次回退 text/content
                    fallback_text = (
                        res.get("response")
                        or res.get("text")
                        or res.get("content")
                        or ""
                    )
                    if chunk_count == 0 and fallback_text:
                        chunk_count += 1
                        accumulated_chars += len(fallback_text)
                        accumulated_text += fallback_text
                        yield {"type": "content", "text": fallback_text}
                    denied_actions = res.get("denied_actions") or []
                    has_xml_tool_markup = (
                        "<tool_call" in accumulated_text.lower()
                        or "dsml" in accumulated_text.lower()
                    )
                    if denied_actions and not has_xml_tool_markup:
                        denied_names = ", ".join(
                            str(a.get("display_name") or a.get("action") or "native_tool")
                            for a in denied_actions
                            if isinstance(a, dict)
                        ) or "native_tool"
                        logger.warning(f"agy CLI 原生工具被无头安全策略拦截: {denied_names}")
                        yield {
                            "type": "error",
                            "error_code": "cli_native_tool_denied",
                            "denied_tools": denied_names,
                            "message": (
                                f"CLI 引擎尝试绕过宿主直接调用内置工具 ({denied_names}) 已被安全策略拦截（非弹窗审批项），"
                                "请使用 AgentHub 标准 <tool_call> XML 标签输出工具调用。"
                            ),
                        }
                        return
                    yield {
                        "type": "done",
                        "usage": res.get("usage", {}),
                        "exit_code": 0,
                    }
                    return
                elif evt_name == "error":
                    err_msg = event.get("error", {}).get("message", "CLI 执行出错")
                    logger.error(f"agy 返回错误事件: {err_msg}")
                    if "copyrighted works" in err_msg.lower() or "safety filter" in err_msg.lower():
                        friendly_msg = (
                            "内容触发了 Google 官方安全策略与版权材料审查（Safety Filter: Copyrighted Works）。"
                            "生成的部分文本因与现有公开文献或已有著作高度重合而被上游过滤阻断。"
                            "建议优化提示词（例如要求「用自己的语言改写转述」、「列出核心要点」或「按步骤分批生成」），避免直接输出大段原文。"
                        )
                        yield {"type": "error", "message": friendly_msg}
                        return
                    yield {"type": "error", "message": err_msg}

            # 2. 适配 Gemini CLI 事件格式
            event_type = event.get("type", "")

            if event_type == "content":
                text_piece = str(event.get("content", event.get("text", "")) or "")
                chunk_count += 1
                accumulated_chars += len(text_piece)
                accumulated_text += text_piece
                yield {"type": "content", "text": text_piece}

            elif event_type == "message":
                chunk_count += 1
                parts = event.get("message", {}).get("parts", [])
                for part in parts:
                    if isinstance(part, dict) and "text" in part:
                        text_piece = str(part["text"] or "")
                        accumulated_chars += len(text_piece)
                        accumulated_text += text_piece
                        yield {"type": "content", "text": text_piece}

            elif event_type == "tool_use":
                yield {
                    "type": "tool_request",
                    "tool_name": event.get("tool_name", ""),
                    "tool_input": event.get("tool_input", {}),
                    "requires_approval": True,
                }

            elif event_type == "tool_result":
                yield {
                    "type": "tool_result",
                    "tool_name": event.get("tool_name", ""),
                    "output": event.get("output", ""),
                }

            elif event_type == "error":
                error_msg = event.get("error", {}).get("message", str(event))
                logger.error(f"CLI 返回错误事件: {error_msg}")
                yield {"type": "error", "message": error_msg}

            elif event_type == "result":
                usage = event.get("usage", {})
                yield {
                    "type": "done",
                    "usage": usage,
                    "exit_code": event.get("exitCode", 0),
                }
                return

        # 进程已无更多输出但没有收到 result 事件（异常退出场景）
        stderr_data = b""
        if proc.stderr:
            try:
                stderr_data = await asyncio.wait_for(proc.stderr.read(), timeout=5)
            except asyncio.TimeoutError:
                pass
        stderr_text = stderr_data.decode("utf-8", errors="replace") if stderr_data else ""

        exit_code = await proc.wait()
        if exit_code != 0:
            logger.error(f"CLI 子进程异常退出 exit_code={exit_code} stderr={stderr_text[:500]}")
            # 检测 429 / quota exhausted 关键字，向上报告限流信号
            if "429" in stderr_text or "quota" in stderr_text.lower() or "exhausted" in stderr_text.lower():
                yield {"type": "rate_limited", "message": "账号额度已用尽或触发限流，将触发切号"}
            elif (
                "auth" in stderr_text.lower()
                or "login" in stderr_text.lower()
                or "unauthenticated" in stderr_text.lower()
                or "ineligibletier" in stderr_text.lower()
            ):
                yield {"type": "auth_error", "message": f"账号认证受限或失效: {stderr_text[:120]}"}
            else:
                yield {"type": "error", "message": f"CLI 进程退出码 {exit_code}，stderr: {stderr_text[:200]}"}
        else:
            if chunk_count == 0:
                yield {"type": "content", "text": "已成功执行指令并完成系统级上下文分析。"}
            yield {"type": "done", "usage": {}, "exit_code": 0}

    except asyncio.CancelledError:
        _kill_process_tree(proc)
        raise
    except Exception as e:
        logger.error(f"agent_runner 异常: {e}", exc_info=True)
        yield {"type": "error", "message": str(e)}
    finally:
        if conversation_id and conversation_id in _ACTIVE_CLI_PROCESSES:
            _ACTIVE_CLI_PROCESSES[conversation_id].discard(proc)
            if not _ACTIVE_CLI_PROCESSES[conversation_id]:
                _ACTIVE_CLI_PROCESSES.pop(conversation_id, None)
        # 确保子进程资源及派生进程树被彻底回收
        if getattr(proc, "returncode", None) is None:
            _kill_process_tree(proc)
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except Exception:
                pass
        logger.info(f"CLI 子进程 PID={proc.pid} 已回收")
