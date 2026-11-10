
class _CompatSubprocess:
    """Windows 下 SelectorEventLoop 不支持异步子进程时的透明兼容适配器"""
    def __init__(self, cmd: list[str], env: dict[str, str], cwd: str) -> None:
        import subprocess
        import threading
        from ..core.runtime_paths import get_silent_subprocess_kwargs

        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
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
# 单次会话最多允许的 chunk 块数上限（防止恶意无限流）
MAX_CHUNKS_PER_SESSION = 5000
# 活跃 CLI 子进程注册表：conversation_id -> set[proc]
_ACTIVE_CLI_PROCESSES: dict[str, set[Any]] = {}


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

# 白名单：只允许传递给子进程的环境变量 key（含代理白名单）
_ALLOWED_ENV_KEYS = {
    "SYSTEMROOT", "PATH", "USERPROFILE", "HOME", "USERNAME",
    "APPDATA", "LOCALAPPDATA", "TEMP", "TMP",
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
    对用户 prompt 做基础安全检查（防止参数溢出）。
    若包含超长内容，保留前 64000 字符。
    """
    if len(prompt) > 64000:
        logger.warning("用户 prompt 超过 64000 字符，已做安全截断")
        prompt = prompt[:64000]
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
                env=env,
                cwd=cwd,
                **get_silent_subprocess_kwargs(),
            )
        except NotImplementedError:
            proc = _CompatSubprocess(cmd, env, cwd)

        async def _collect_output() -> tuple[str, str, int]:
            out_lines: list[str] = []
            assert proc.stdout is not None
            while True:
                line = await proc.stdout.readline()
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

    Yields:
        解析后的事件字典，type 字段为：
        init / content / tool_request / tool_result / error / done / timeout /
        rate_limited / auth_error
    """
    prompt = _sanitize_prompt(prompt)
    env = _build_safe_env(cli_home_dir)
    cli_bin, cli_type = _find_cli_binary()

    # 根据 CLI 类型组装命令参数
    safe_mode = "default" if approval_mode == "yolo" else approval_mode

    if cli_type == "agy":
        cmd: list[str] = [
            cli_bin,
            "-p", prompt,
            "--output-format", "stream-json",
            "--disable-slash-commands",
        ]
        # agy 必须显式传递合法的 --model 参数
        model_flag = resolve_agy_model_flag(model, thinking_level)
        cmd.extend(["--model", model_flag])
        if safe_mode == "auto_edit":
            cmd.append("--dangerously-skip-permissions")
    else:
        # 旧版 gemini CLI 参数
        cmd: list[str] = [cli_bin, "-o", "stream-json"]
        if model:
            cmd.extend(["-m", model])
        if allowed_tools:
            for tool in allowed_tools:
                cmd.extend(["--allowed-tools", tool])
        cmd.extend(["--approval-mode", safe_mode])
        cmd.append(prompt)

    cwd = work_dir or str(Path.home())
    logger.info(
        f"启动 {cli_type.upper()} CLI 子进程 ({cli_bin}): alias={Path(cli_home_dir).name} "
        f"model={model or 'default'} cwd={cwd} pid=pending"
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=cwd,
            **get_silent_subprocess_kwargs(),
        )
    except NotImplementedError:
        logger.info("当前环境不支持 asyncio 原生子进程传输，自动切换至 Windows 线程兼容适配器")
        proc = _CompatSubprocess(cmd, env, cwd)

    logger.info(f"{cli_type.upper()} CLI 子进程已启动 PID={proc.pid}")
    if conversation_id:
        _ACTIVE_CLI_PROCESSES.setdefault(conversation_id, set()).add(proc)
    chunk_count = 0

    try:
        async def _read_lines() -> AsyncGenerator[str | dict, None]:
            """从 stdout 逐行读取 NDJSON，支持主动取消与超时保护"""
            assert proc.stdout is not None
            while True:
                if cancel_event and cancel_event.is_set():
                    _kill_process_tree(proc)
                    yield {"type": "cancelled", "message": "已中断执行"}
                    return

                read_task = asyncio.create_task(proc.stdout.readline())
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
            if chunk_count >= MAX_CHUNKS_PER_SESSION:
                logger.warning("单次会话 chunk 数量超限，终止子进程")
                _kill_process_tree(proc)
                yield {"type": "error", "message": "响应块数量超过安全上限，请求已终止"}
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
                    # 多字段兼容提取文本（text_delta / text / content）
                    text_delta = (
                        step_update.get("text_delta")
                        or step_update.get("text")
                        or step_update.get("content")
                        or ""
                    )
                    if text_delta:
                        chunk_count += 1
                        yield {"type": "content", "text": text_delta}
                    # 工具请求
                    if step_update.get("step_type") == "tool_call" or "tool_call" in step_update:
                        yield {
                            "type": "tool_request",
                            "tool_name": step_update.get("tool_name", "system_tool"),
                            "tool_input": step_update.get("tool_input", {}),
                            "requires_approval": True,
                        }
                elif evt_name == "result":
                    res = event.get("result", {})
                    if res.get("status") == "ERROR" or res.get("error"):
                        err_msg = res.get("error") or "CLI 执行异常失败"
                        logger.error(f"agy 返回 ERROR 状态: {err_msg}")
                        yield {"type": "error", "message": err_msg}
                        return
                    fallback_text = res.get("text") or res.get("content") or ""
                    if chunk_count == 0 and fallback_text:
                        chunk_count += 1
                        yield {"type": "content", "text": fallback_text}
                    yield {
                        "type": "done",
                        "usage": res.get("usage", {}),
                        "exit_code": 0,
                    }
                    return
                elif evt_name == "error":
                    err_msg = event.get("error", {}).get("message", "CLI 执行出错")
                    logger.error(f"agy 返回错误事件: {err_msg}")
                    yield {"type": "error", "message": err_msg}

            # 2. 适配 Gemini CLI 事件格式
            event_type = event.get("type", "")

            if event_type == "content":
                chunk_count += 1
                yield {"type": "content", "text": event.get("content", event.get("text", ""))}

            elif event_type == "message":
                chunk_count += 1
                parts = event.get("message", {}).get("parts", [])
                for part in parts:
                    if isinstance(part, dict) and "text" in part:
                        yield {"type": "content", "text": part["text"]}

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
