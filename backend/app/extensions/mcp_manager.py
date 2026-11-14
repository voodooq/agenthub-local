"""
MCP 客户端常驻连接池管理器 (MCPManager)
特性：
1. 兼容标准 mcp_servers.json 配置规范（同时支持本地 stdio 进程与远程 Streamable HTTP / Legacy SSE 协议）
2. 自动映射 python/python3 至当前虚拟环境解释器 (sys.executable)，支持本地自研与主流公共标准 MCP 接入
3. 配置文件 mtime 变更热重载 + 启动失败熔断缓存 + nextCursor 全量工具分页拉取
4. 防僵尸进程与 HTTP 连接泄漏：应用退出时调用异步 shutdown_all() 清理所有常驻 MCP 连接
"""
from abc import ABC, abstractmethod
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
import httpx

from ..core.config import settings
from ..core.logging_config import logger
from ..core.proxy_helper import get_system_proxy_url
from ..core.runtime_paths import (
    get_bundle_root,
    get_silent_subprocess_kwargs,
    get_writable_app_root,
    resolve_python_interpreter,
    seed_mcp_config,
)
from ..agent.shell_executor import LocalShellExecutor

LOCAL_BYPASS_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal"})
SUPPORTED_MCP_PROTOCOL_VERSIONS = ("2025-03-26", "2024-11-05")
_ENV_PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class _SessionExpiredError(Exception):
    """Streamable HTTP 会话失效（服务端返回 HTTP 404）需要重建握手"""


def is_valid_mcp_server_name(name: str) -> bool:
    """校验 MCP 服务标识是否合法（严禁含双下划线 __ 以免与 mcp__<server>__<tool> 冲突）。"""
    clean = (name or "").strip()
    return bool(clean) and "__" not in clean


def extract_origin(url: str) -> str:
    """提取 URL 的同源标识（scheme://netloc，统一小写）。"""
    parsed = urlparse((url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def is_same_origin(url_a: str, url_b: str) -> bool:
    """判断两个 URL 是否严格同源（同协议 + 同主机名与端口）。"""
    origin_a = extract_origin(url_a)
    origin_b = extract_origin(url_b)
    return bool(origin_a and origin_b and origin_a == origin_b)


def should_bypass_proxy(target_url: str) -> bool:
    """判断目标 URL 主机是否为本地回环或容器宿主机映射地址（此类地址直连免代理）。"""
    host = (urlparse((target_url or "").strip()).hostname or "").strip().lower()
    return host in LOCAL_BYPASS_HOSTS


def build_httpx_client_kwargs(
    target_url: str,
    timeout: httpx.Timeout | float,
    *,
    follow_redirects: bool = False,
) -> dict[str, Any]:
    """构建适配本地免代理与系统代理的 httpx.AsyncClient 初始化参数。"""
    kwargs: dict[str, Any] = {
        "timeout": timeout,
        "follow_redirects": follow_redirects,
    }
    if should_bypass_proxy(target_url):
        # NOTE: 显式关闭 trust_env，防止环境变量中的全局 HTTP_PROXY 劫持本地/容器宿主机回环请求
        kwargs["trust_env"] = False
    else:
        proxy_url = get_system_proxy_url()
        if proxy_url:
            kwargs["proxy"] = proxy_url
    return kwargs


def expand_env_placeholders(headers: dict[str, Any] | None) -> tuple[dict[str, str], list[str]]:
    """展开请求头中的 ${ENV_VAR} 占位符，返回 (展开后字典, 缺失环境变量列表)。"""
    if not isinstance(headers, dict):
        return {}, []
    expanded: dict[str, str] = {}
    missing_vars: list[str] = []

    for raw_k, raw_v in headers.items():
        k = str(raw_k).strip()
        if not k:
            continue
        val_str = str(raw_v if raw_v is not None else "")

        def _replace_match(m: re.Match[str]) -> str:
            var_name = m.group(1)
            if var_name in os.environ:
                return os.environ[var_name]
            if var_name not in missing_vars:
                missing_vars.append(var_name)
            return m.group(0)

        expanded[k] = _ENV_PLACEHOLDER_RE.sub(_replace_match, val_str)

    return expanded, missing_vars


def extract_mcp_servers_map(raw_payload: Any) -> dict[str, Any]:
    """兼容从标准 mcpServers、VS Code servers 或平铺字典中提取服务映射。"""
    if not isinstance(raw_payload, dict):
        return {}
    if isinstance(raw_payload.get("mcpServers"), dict):
        return raw_payload["mcpServers"]
    if isinstance(raw_payload.get("servers"), dict):
        return raw_payload["servers"]
    return raw_payload


def normalize_transport_name(raw_transport: str | None, *, has_url: bool, has_command: bool) -> tuple[str, bool]:
    """将主流生态的 transport/type 字段归一化为 stdio | streamable-http | sse，并返回是否为自动推断。"""
    t = (raw_transport or "").strip().lower()
    if t in ("streamable-http", "streamablehttp", "streamable_http", "http"):
        return "streamable-http", False
    if t == "sse":
        return "sse", False
    if t == "stdio":
        return "stdio", False
    if has_url and not has_command:
        return "streamable-http", True
    return "stdio", False


def parse_mcp_server_entry(srv_name: str, raw_cfg: dict[str, Any]) -> dict[str, Any]:
    """将单条 MCP 配置归一化为内部标准结构，兼容多种社区与历史字段写法。"""
    command = str(raw_cfg.get("command") or "").strip()
    url = str(raw_cfg.get("url") or raw_cfg.get("serverUrl") or "").strip()
    raw_transport = str(raw_cfg.get("transport") or raw_cfg.get("type") or "").strip()
    transport, transport_inferred = normalize_transport_name(
        raw_transport,
        has_url=bool(url),
        has_command=bool(command),
    )

    default_timeout = 60.0 if transport in ("streamable-http", "sse") else 35.0
    raw_timeout = raw_cfg.get("timeout_seconds", raw_cfg.get("timeout", default_timeout))
    try:
        timeout_seconds = max(5.0, min(600.0, float(raw_timeout)))
    except (TypeError, ValueError):
        timeout_seconds = default_timeout

    raw_headers = raw_cfg.get("headers")
    headers: dict[str, str] = {}
    if isinstance(raw_headers, dict):
        headers = {str(k).strip(): str(v) for k, v in raw_headers.items() if str(k).strip()}

    raw_upload_ep = str(raw_cfg.get("upload_endpoint") or "").strip()
    upload_endpoint = raw_upload_ep if raw_upload_ep.startswith(("http://", "https://")) else ""
    upload_file_field = str(raw_cfg.get("upload_file_field") or "file").strip() or "file"

    raw_origins = raw_cfg.get("download_origins")
    download_origins: list[str] = []
    if isinstance(raw_origins, list):
        for item in raw_origins:
            origin = extract_origin(str(item))
            if origin and origin not in download_origins:
                download_origins.append(origin)

    raw_env = raw_cfg.get("env")
    env_dict = {str(k): str(v) for k, v in raw_env.items()} if isinstance(raw_env, dict) else {}

    return {
        "name": srv_name,
        "transport": transport,
        "transport_inferred": transport_inferred,
        "command": command,
        "args": [str(a) for a in (raw_cfg.get("args") or [])],
        "env": env_dict,
        "url": url,
        "headers": headers,
        "timeout_seconds": timeout_seconds,
        "upload_endpoint": upload_endpoint,
        "upload_file_field": upload_file_field,
        "download_origins": download_origins,
        "enabled": bool(raw_cfg.get("enabled", False)),
        "description": str(raw_cfg.get("description") or "").strip(),
    }


def normalize_mcp_tool_definitions(raw_tools: Any) -> list[dict[str, Any]]:
    """规范化 tools/list 返回的工具条目：对 description=null 与 inputSchema=null 做安全兜底。"""
    if not isinstance(raw_tools, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for item in raw_tools:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        schema = item.get("inputSchema")
        safe_schema = dict(schema) if isinstance(schema, dict) else {"type": "object", "properties": {}}
        if "properties" not in safe_schema or not isinstance(safe_schema.get("properties"), dict):
            safe_schema["type"] = safe_schema.get("type") or "object"
            safe_schema["properties"] = {}
        norm_item = dict(item)
        norm_item["name"] = name
        norm_item["description"] = str(item.get("description") or "")
        norm_item["inputSchema"] = safe_schema
        cleaned.append(norm_item)
    return cleaned


def extract_mcp_tool_result(res: Any) -> str:
    """统一抽取 MCP tools/call 结果（兼容 text/resource/resource_link/image/audio 与 structuredContent），严禁注入大 Base64。"""
    if res is None:
        return "null"

    if isinstance(res, dict):
        parts: list[str] = []
        raw_content = res.get("content")
        if isinstance(raw_content, list):
            for item in raw_content:
                if not isinstance(item, dict):
                    parts.append(str(item))
                    continue
                ctype = str(item.get("type") or "").strip().lower()
                if ctype == "text":
                    txt = item.get("text", "")
                    if txt:
                        parts.append(str(txt))
                elif ctype in ("image", "audio"):
                    mime = item.get("mimeType") or "application/octet-stream"
                    data_len = len(str(item.get("data") or ""))
                    approx_bytes = (data_len * 3) // 4
                    uri_hint = f", uri={item['uri']}" if item.get("uri") else ""
                    parts.append(f"[{ctype}: mime={mime}, approx_bytes={approx_bytes}{uri_hint}]")
                elif ctype == "resource":
                    resource = item.get("resource") if isinstance(item.get("resource"), dict) else item
                    r_uri = str(resource.get("uri") or "")
                    r_mime = str(resource.get("mimeType") or "text/plain")
                    if "text" in resource and resource.get("text") is not None:
                        header = f"[resource: {r_uri} ({r_mime})]" if r_uri else f"[resource ({r_mime})]"
                        parts.append(f"{header}\n{resource.get('text')}")
                    else:
                        blob_len = len(str(resource.get("blob") or ""))
                        approx_bytes = (blob_len * 3) // 4
                        parts.append(f"[resource: {r_uri or 'binary'} ({r_mime}), approx_bytes={approx_bytes}]")
                elif ctype == "resource_link":
                    r_uri = str(item.get("uri") or "")
                    r_name = str(item.get("name") or item.get("title") or r_uri)
                    r_mime = str(item.get("mimeType") or "")
                    mime_part = f" ({r_mime})" if r_mime else ""
                    parts.append(f"[resource_link: {r_name} -> {r_uri}{mime_part}]")
                else:
                    parts.append(json.dumps(item, ensure_ascii=False))

        structured = res.get("structuredContent")
        if structured is not None:
            structured_str = json.dumps(structured, ensure_ascii=False)
            # 当服务端未同步渲染在 text 中或 content 为空时补充结构化数据
            if not parts or structured_str not in "\n".join(parts):
                parts.append(structured_str)

        if parts:
            joined = "\n".join(parts)
            if res.get("isError") and not joined.startswith("Error:"):
                return f"Error: {joined}"
            return joined

        if res.get("isError"):
            return f"Error: {json.dumps(res, ensure_ascii=False)}"

    return json.dumps(res, ensure_ascii=False)


class BaseMCPConnection(ABC):
    """MCP 连接器统一抽象基类"""

    def __init__(self, name: str) -> None:
        self.name = name
        self.tools_cache: list[dict[str, Any]] = []
        self._last_error: str | None = None

    @abstractmethod
    def is_alive(self) -> bool:
        """返回当前连接是否处于可调用就绪状态。"""

    @abstractmethod
    async def ensure_started(self) -> bool:
        """确保连接已初始化并拉取可用工具列表。"""

    @abstractmethod
    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """执行指定远端/子进程工具并返回可读字符串。"""

    @abstractmethod
    def matches_config(self, normalized_cfg: dict[str, Any]) -> bool:
        """判断当前连接是否仍与最新配置一致（不一致则触发热重启）。"""

    @abstractmethod
    async def close(self) -> None:
        """异步关闭并清理连接资源。"""


class _StdioMCPConnection(BaseMCPConnection):
    """单个 stdio 模式 MCP Server 的常驻 JSON-RPC 2.0 连接封装"""

    DEFAULT_INIT_TIMEOUT_SECONDS = 30.0
    DEFAULT_LIST_TIMEOUT_SECONDS = 20.0
    DEFAULT_CALL_TIMEOUT_SECONDS = 35.0

    def __init__(
        self,
        name: str,
        command: str,
        args: list[str],
        env: dict[str, str] | None = None,
        cwd: str | Path | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        super().__init__(name)
        self.command = command
        self.args = args
        self.custom_env = env or {}
        self.cwd = str(cwd) if cwd else None
        self.timeout_seconds = float(timeout_seconds or self.DEFAULT_CALL_TIMEOUT_SECONDS)
        self.proc: subprocess.Popen | None = None
        self._req_id = 0
        self._lock = asyncio.Lock()

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def matches_config(self, normalized_cfg: dict[str, Any]) -> bool:
        return (
            normalized_cfg.get("transport") == "stdio"
            and normalized_cfg.get("command") == self.command
            and normalized_cfg.get("args") == self.args
            and normalized_cfg.get("env", {}) == self.custom_env
            and abs(float(normalized_cfg.get("timeout_seconds", self.DEFAULT_CALL_TIMEOUT_SECONDS)) - self.timeout_seconds) < 0.01
        )

    def _resolve_executable(self) -> str | None:
        """解析启动命令的真实可执行文件路径，优先将 python 映射至可用外部/虚拟环境解释器"""
        cmd_lower = self.command.strip().lower()
        if cmd_lower in ("python", "python3", "python.exe"):
            return resolve_python_interpreter()

        if not getattr(sys, "frozen", False):
            scripts_dir = Path(sys.executable).parent
            for ext in (".exe", ".cmd", ".bat", ""):
                candidate = scripts_dir / f"{self.command}{ext}"
                if candidate.exists():
                    return str(candidate)

        return shutil.which(self.command)

    async def ensure_started(self) -> bool:
        """懒加载拉起常驻 MCP 子进程并完成 JSON-RPC initialize 握手与分页工具发现"""
        if self.is_alive():
            return True

        resolved_cmd = self._resolve_executable()
        if not resolved_cmd:
            self._last_error = f"启动命令 `{self.command}` 未找到"
            logger.info(f"MCP Server [{self.name}] 启动命令 `{self.command}` 未找到，已自动熔断跳过。")
            return False

        merged_env = os.environ.copy()
        merged_env["PYTHONIOENCODING"] = "utf-8"
        merged_env.update(self.custom_env)
        cmd_list = [resolved_cmd] + [str(a) for a in self.args]

        try:
            self.proc = subprocess.Popen(
                cmd_list,
                cwd=self.cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=merged_env,
                shell=(sys.platform == "win32" and resolved_cmd.lower().endswith((".cmd", ".bat"))),
                start_new_session=(sys.platform != "win32"),
                **get_silent_subprocess_kwargs(),
            )
            init_resp = await self._send_rpc(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "AgentHub-Local", "version": settings.app_version},
                },
                timeout=self.DEFAULT_INIT_TIMEOUT_SECONDS,
            )
            if init_resp is not None:
                await self._send_notification("notifications/initialized", {})
                collected_tools: list[dict[str, Any]] = []
                cursor: str | None = None
                for _ in range(20):
                    list_params = {"cursor": cursor} if cursor else {}
                    tools_resp = await self._send_rpc(
                        "tools/list",
                        list_params,
                        timeout=self.DEFAULT_LIST_TIMEOUT_SECONDS,
                    )
                    if not isinstance(tools_resp, dict):
                        break
                    page_tools = tools_resp.get("tools")
                    if isinstance(page_tools, list):
                        collected_tools.extend(t for t in page_tools if isinstance(t, dict))
                    next_cursor = tools_resp.get("nextCursor")
                    if not next_cursor:
                        break
                    cursor = str(next_cursor)

                self.tools_cache = normalize_mcp_tool_definitions(collected_tools)
                logger.info(f"MCP Server [{self.name}] 已就绪，挂载工具数: {len(self.tools_cache)}")
                return True
        except Exception as e:
            self._last_error = str(e)
            logger.warning(f"拉起 MCP Server [{self.name}] 失败: {e}")

        await self.close()
        return False

    async def _send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        """发送无需响应的单向 JSON-RPC 2.0 通知"""
        if not self.is_alive() or not self.proc or not self.proc.stdin:
            return
        payload = json.dumps({"jsonrpc": "2.0", "method": method, "params": params or {}}) + "\n"

        def _write_notif() -> None:
            assert self.proc and self.proc.stdin
            self.proc.stdin.write(payload.encode("utf-8"))
            self.proc.stdin.flush()

        async with self._lock:
            try:
                await asyncio.wait_for(asyncio.to_thread(_write_notif), timeout=5.0)
            except Exception as e:
                logger.debug(f"MCP [{self.name}] 发送通知 ({method}) 异常: {e}")

    async def _send_rpc(self, method: str, params: dict[str, Any], timeout: float = 15.0) -> Any:
        self._last_error = None
        if not self.is_alive() or not self.proc or not self.proc.stdin or not self.proc.stdout:
            self._last_error = "子进程未处于存活状态"
            return None

        async with self._lock:
            self._req_id += 1
            req_id = self._req_id
            payload = json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}) + "\n"

            def _io_step() -> dict[str, Any] | None:
                assert self.proc and self.proc.stdin and self.proc.stdout
                self.proc.stdin.write(payload.encode("utf-8"))
                self.proc.stdin.flush()
                for _ in range(100):
                    raw_line = self.proc.stdout.readline()
                    if not raw_line:
                        break
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not (line.startswith("{") and line.endswith("}")):
                        continue
                    try:
                        parsed_obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if (
                        isinstance(parsed_obj, dict)
                        and parsed_obj.get("jsonrpc") == "2.0"
                        and parsed_obj.get("id") == req_id
                    ):
                        return parsed_obj
                return None

            try:
                parsed = await asyncio.wait_for(asyncio.to_thread(_io_step), timeout=timeout)
                if parsed is None:
                    self._last_error = "未收到匹配的 JSON-RPC 响应（可能进程已退出或输出非协议内容）"
                    return None
                if "error" in parsed and parsed["error"]:
                    err_obj = parsed["error"]
                    err_msg = err_obj.get("message") if isinstance(err_obj, dict) else str(err_obj)
                    self._last_error = str(err_msg or "MCP Server 返回 JSON-RPC error")
                    logger.warning(f"MCP [{self.name}] RPC ({method}) 返回错误: {self._last_error}")
                    return None
                return parsed.get("result")
            except asyncio.TimeoutError:
                self._last_error = f"RPC ({method}) 等待响应超时 (>{timeout:.0f}s)"
                logger.warning(f"MCP [{self.name}] {self._last_error}")
            except Exception as e:
                self._last_error = str(e)
                logger.warning(f"MCP [{self.name}] RPC ({method}) 通信异常: {e}")
            return None

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        if not await self.ensure_started():
            reason = f" ({self._last_error})" if self._last_error else ""
            return f"Error: MCP Server [{self.name}] 未就绪或启动失败{reason}。"
        res = await self._send_rpc(
            "tools/call",
            {"name": tool_name, "arguments": arguments},
            timeout=self.timeout_seconds,
        )
        if res is None:
            if not self.is_alive():
                await self.close()
            detail = self._last_error or "超时或无响应"
            return f"Error: 调用 MCP 工具 [{tool_name}] 失败: {detail}。"

        return extract_mcp_tool_result(res)

    async def close(self) -> None:
        """销毁常驻子进程及其整个进程树"""
        if self.proc:
            pid = self.proc.pid
            LocalShellExecutor.kill_process_tree(pid)
            self.proc = None


class _RemoteHttpMCPConnection(BaseMCPConnection):
    """远程 HTTP MCP 客户端连接器（原生兼容 Streamable HTTP 与 Legacy HTTP + SSE）"""

    DEFAULT_TIMEOUT_SECONDS = 60.0

    def __init__(
        self,
        name: str,
        transport: str,
        url: str,
        headers: dict[str, str] | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        upload_endpoint: str = "",
        upload_file_field: str = "file",
        download_origins: list[str] | None = None,
        transport_inferred: bool = False,
    ) -> None:
        super().__init__(name)
        self.transport = "sse" if transport == "sse" else "streamable-http"
        self._configured_transport = self.transport
        self.transport_inferred = transport_inferred
        self.url = url.strip()
        self.raw_headers = dict(headers or {})
        self.timeout_seconds = max(5.0, float(timeout_seconds or self.DEFAULT_TIMEOUT_SECONDS))
        self.upload_endpoint = upload_endpoint.strip()
        self.upload_file_field = (upload_file_field or "file").strip() or "file"
        self.download_origins = list(download_origins or [])

        self.session_id: str | None = None
        self.protocol_version: str | None = None
        self.client: httpx.AsyncClient | None = None

        self._initialized = False
        self._closed = False
        self._req_id = 0
        self._lock = asyncio.Lock()

        # Legacy SSE 专用状态
        self._post_endpoint: str | None = None
        self._sse_task: asyncio.Task[None] | None = None
        self._sse_ready_event: asyncio.Event | None = None
        self._sse_alive = False
        self._pending_futures: dict[Any, asyncio.Future[dict[str, Any]]] = {}

    def is_alive(self) -> bool:
        """判断远程连接状态；无状态 HTTP 初始化后保持就绪，SSE 额外要求流监听仍存活。"""
        if self._closed or not self._initialized or not self.client or self.client.is_closed:
            return False
        if self.transport == "sse":
            return (
                self._sse_alive
                and self._sse_task is not None
                and not self._sse_task.done()
                and bool(self._post_endpoint)
            )
        return True

    def matches_config(self, normalized_cfg: dict[str, Any]) -> bool:
        return (
            normalized_cfg.get("transport") == self._configured_transport
            and normalized_cfg.get("url") == self.url
            and normalized_cfg.get("headers", {}) == self.raw_headers
            and abs(float(normalized_cfg.get("timeout_seconds", self.DEFAULT_TIMEOUT_SECONDS)) - self.timeout_seconds) < 0.01
            and normalized_cfg.get("upload_endpoint", "") == self.upload_endpoint
            and normalized_cfg.get("upload_file_field", "file") == self.upload_file_field
            and normalized_cfg.get("download_origins", []) == self.download_origins
        )

    def _build_resolved_headers(self, *, extra: dict[str, str] | None = None) -> dict[str, str]:
        expanded, missing_vars = expand_env_placeholders(self.raw_headers)
        if missing_vars:
            logger.warning(f"MCP [{self.name}] 存在未设置的环境变量占位符: {', '.join(missing_vars)}")
        if extra:
            expanded.update(extra)
        return expanded

    def _ensure_client(self) -> httpx.AsyncClient:
        if self.client is None or self.client.is_closed:
            timeout_cfg = httpx.Timeout(self.timeout_seconds, connect=min(10.0, self.timeout_seconds))
            kwargs = build_httpx_client_kwargs(self.url, timeout_cfg, follow_redirects=False)
            self.client = httpx.AsyncClient(**kwargs)
        return self.client

    async def ensure_started(self) -> bool:
        """完成远程 MCP 握手与分页工具发现（支持未显式指定协议时的 Streamable HTTP -> SSE 探测回退）。"""
        if self.is_alive():
            return True

        async with self._lock:
            if self.is_alive():
                return True

            self._closed = False
            if self.transport == "sse":
                return await self._start_sse()

            ok, suggest_sse = await self._start_streamable_http()
            if ok:
                return True

            if suggest_sse and self.transport_inferred:
                logger.info(f"MCP [{self.name}] Streamable HTTP 端点不匹配，自动回退尝试 Legacy SSE 握手")
                await self._reset_transport_state()
                self.transport = "sse"
                return await self._start_sse()

            return False

    async def _reset_transport_state(self) -> None:
        self._initialized = False
        self.session_id = None
        self.protocol_version = None
        self._post_endpoint = None
        self._sse_alive = False
        if self._sse_task and not self._sse_task.done():
            self._sse_task.cancel()
            try:
                await self._sse_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        self._sse_task = None
        for fut in list(self._pending_futures.values()):
            if not fut.done():
                fut.cancel()
        self._pending_futures.clear()
        if self.client and not self.client.is_closed:
            await self.client.aclose()
        self.client = None

    async def _read_streamable_sse_response(
        self,
        resp: httpx.Response,
        expected_id: Any,
    ) -> dict[str, Any] | None:
        """逐行流式读取 Streamable HTTP 返回的 text/event-stream，命中目标 id 即提前退出。"""
        data_lines: list[str] = []
        fallback_msg: dict[str, Any] | None = None

        def _flush() -> dict[str, Any] | None:
            nonlocal fallback_msg
            if not data_lines:
                return None
            joined = "\n".join(data_lines).strip()
            data_lines.clear()
            if not joined or joined == "[DONE]":
                return None
            try:
                obj = json.loads(joined)
            except json.JSONDecodeError:
                return None
            if not isinstance(obj, dict):
                return None
            if expected_id is not None and obj.get("id") == expected_id:
                return obj
            if fallback_msg is None and ("result" in obj or "error" in obj):
                fallback_msg = obj
            return None

        async for raw_line in resp.aiter_lines():
            line = raw_line.rstrip("\r")
            if not line:
                matched = _flush()
                if matched is not None:
                    return matched
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip(" "))

        matched = _flush()
        return matched if matched is not None else fallback_msg

    async def _post_streamable_rpc(
        self,
        method: str,
        params: dict[str, Any],
        *,
        is_notification: bool = False,
        timeout_override: float | None = None,
    ) -> Any:
        self._last_error = None
        client = self._ensure_client()

        extra_headers: dict[str, str] = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if self.session_id:
            extra_headers["Mcp-Session-Id"] = self.session_id
        if self.protocol_version and method != "initialize":
            extra_headers["MCP-Protocol-Version"] = self.protocol_version

        req_headers = self._build_resolved_headers(extra=extra_headers)
        req_id: int | None = None
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params}
        if not is_notification:
            self._req_id += 1
            req_id = self._req_id
            body["id"] = req_id

        timeout_val = timeout_override or self.timeout_seconds
        req_timeout = httpx.Timeout(timeout_val, connect=min(10.0, timeout_val))

        async with client.stream("POST", self.url, headers=req_headers, json=body, timeout=req_timeout) as resp:
            if resp.status_code in (401, 403):
                self._last_error = f"远程 MCP 鉴权失败 (HTTP {resp.status_code})，请检查 headers / Authorization / API Key"
                return None

            if resp.status_code == 404 and self.session_id and method != "initialize":
                raise _SessionExpiredError("远端 MCP Session 已失效 (HTTP 404)")

            if resp.status_code == 405:
                self._last_error = "远程 MCP 服务返回 HTTP 405 Method Not Allowed（该端点可能仅支持 Legacy SSE 模式）"
                return None

            if resp.status_code >= 400:
                raw_err = await resp.aread()
                snippet = raw_err.decode("utf-8", errors="replace")[:300].strip()
                self._last_error = f"HTTP {resp.status_code}: {snippet or resp.reason_phrase}"
                return None

            if method == "initialize":
                sid = resp.headers.get("mcp-session-id") or resp.headers.get("Mcp-Session-Id")
                self.session_id = sid.strip() if sid and sid.strip() else None

            if is_notification:
                # NOTE: MCP Streamable HTTP 规范中通知常返回 HTTP 202 Accepted 空响应体
                await resp.aread()
                return True

            content_type = (resp.headers.get("content-type") or "").lower()
            if "text/event-stream" in content_type:
                parsed = await self._read_streamable_sse_response(resp, expected_id=req_id)
            else:
                raw_bytes = await resp.aread()
                if not raw_bytes.strip():
                    self._last_error = f"RPC ({method}) 收到空响应体 (HTTP {resp.status_code})"
                    return None
                text_body = raw_bytes.decode("utf-8", errors="replace")
                stripped = text_body.lstrip()
                if stripped.startswith("event:") or stripped.startswith("data:"):
                    # 兼容未正确标注 Content-Type 的 SSE 响应体
                    parsed = self._parse_sse_text_fallback(text_body, expected_id=req_id)
                else:
                    try:
                        parsed = json.loads(text_body)
                    except Exception as e:
                        self._last_error = f"解析 JSON 响应失败: {e}"
                        return None

        if not isinstance(parsed, dict):
            self._last_error = f"RPC ({method}) 响应格式异常"
            return None

        if "error" in parsed and parsed["error"]:
            err_obj = parsed["error"]
            err_msg = err_obj.get("message") if isinstance(err_obj, dict) else str(err_obj)
            self._last_error = str(err_msg or "MCP Server 返回 JSON-RPC error")
            return None

        return parsed.get("result")

    def _parse_sse_text_fallback(self, raw_body: str, expected_id: Any) -> dict[str, Any] | None:
        data_lines: list[str] = []
        fallback_msg: dict[str, Any] | None = None
        for raw_line in raw_body.splitlines() + [""]:
            line = raw_line.rstrip("\r")
            if not line:
                if data_lines:
                    joined = "\n".join(data_lines).strip()
                    data_lines.clear()
                    if joined and joined != "[DONE]":
                        try:
                            obj = json.loads(joined)
                            if isinstance(obj, dict):
                                if expected_id is not None and obj.get("id") == expected_id:
                                    return obj
                                if fallback_msg is None and ("result" in obj or "error" in obj):
                                    fallback_msg = obj
                        except json.JSONDecodeError:
                            pass
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip(" "))
        return fallback_msg

    async def _start_streamable_http(self) -> tuple[bool, bool]:
        """执行 Streamable HTTP 握手与分页拉取，返回 (是否成功, 是否建议回退 SSE)。"""
        if not self.url.startswith(("http://", "https://")):
            self._last_error = f"非法远程 URL: {self.url}"
            return False, False

        init_resp: Any = None
        for idx, proto_ver in enumerate(SUPPORTED_MCP_PROTOCOL_VERSIONS):
            try:
                init_resp = await self._post_streamable_rpc(
                    "initialize",
                    {
                        "protocolVersion": proto_ver,
                        "capabilities": {},
                        "clientInfo": {"name": "AgentHub-Local", "version": settings.app_version},
                    },
                    timeout_override=min(30.0, self.timeout_seconds),
                )
            except httpx.HTTPError as e:
                self._last_error = f"连接远程 MCP 异常: {e}"
                logger.warning(f"MCP [{self.name}] {self._last_error}")
                return False, False
            except Exception as e:
                self._last_error = str(e)
                logger.warning(f"MCP [{self.name}] 初始化异常: {e}")
                return False, False

            if init_resp is not None:
                negotiated = init_resp.get("protocolVersion") if isinstance(init_resp, dict) else None
                self.protocol_version = str(negotiated or proto_ver)
                break

            err_lower = (self._last_error or "").lower()
            if "405" in err_lower or "method not allowed" in err_lower:
                return False, True
            is_version_err = any(k in err_lower for k in ("version", "protocol", "unsupported"))
            if not is_version_err or idx == len(SUPPORTED_MCP_PROTOCOL_VERSIONS) - 1:
                suggest_sse = ("404" in err_lower and self.url.rstrip("/").lower().endswith("/sse"))
                return False, suggest_sse

        if init_resp is None:
            return False, False

        try:
            await self._post_streamable_rpc("notifications/initialized", {}, is_notification=True, timeout_override=10.0)
            tools = await self._paginate_tools_streamable()
            self.tools_cache = tools
            self._initialized = True
            logger.info(
                f"远程 MCP [{self.name}] (streamable-http, proto={self.protocol_version}, "
                f"session={'stateless' if not self.session_id else 'active'}) 已就绪，工具数: {len(tools)}"
            )
            return True, False
        except Exception as e:
            self._last_error = f"拉取远程工具列表失败: {e}"
            logger.warning(f"MCP [{self.name}] {self._last_error}")
            return False, False

    async def _paginate_tools_streamable(self) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(20):
            params = {"cursor": cursor} if cursor else {}
            resp = await self._post_streamable_rpc("tools/list", params, timeout_override=min(30.0, self.timeout_seconds))
            if not isinstance(resp, dict):
                break
            page = resp.get("tools")
            if isinstance(page, list):
                collected.extend(t for t in page if isinstance(t, dict))
            next_cursor = resp.get("nextCursor")
            if not next_cursor:
                break
            cursor = str(next_cursor)
        return normalize_mcp_tool_definitions(collected)

    async def _start_sse(self) -> bool:
        """启动 Legacy HTTP + SSE 长连接监听与双向 RPC 通道。"""
        if not self.url.startswith(("http://", "https://")):
            self._last_error = f"非法远程 SSE URL: {self.url}"
            return False

        self._post_endpoint = None
        self._sse_alive = False
        self._sse_ready_event = asyncio.Event()
        self._ensure_client()
        self._sse_task = asyncio.create_task(self._sse_reader_loop())

        wait_timeout = min(20.0, self.timeout_seconds)
        try:
            await asyncio.wait_for(self._sse_ready_event.wait(), timeout=wait_timeout)
        except asyncio.TimeoutError:
            self._last_error = f"等待 SSE 服务返回 endpoint 超时 (>{wait_timeout:.0f}s)"
            await self._reset_transport_state()
            return False

        if not self._post_endpoint or not self._sse_alive:
            await self._reset_transport_state()
            return False

        init_resp: Any = None
        for idx, proto_ver in enumerate(SUPPORTED_MCP_PROTOCOL_VERSIONS):
            init_resp = await self._send_sse_rpc(
                "initialize",
                {
                    "protocolVersion": proto_ver,
                    "capabilities": {},
                    "clientInfo": {"name": "AgentHub-Local", "version": settings.app_version},
                },
                timeout=min(30.0, self.timeout_seconds),
            )
            if init_resp is not None:
                negotiated = init_resp.get("protocolVersion") if isinstance(init_resp, dict) else None
                self.protocol_version = str(negotiated or proto_ver)
                break
            err_lower = (self._last_error or "").lower()
            is_version_err = any(k in err_lower for k in ("version", "protocol", "unsupported"))
            if not is_version_err or idx == len(SUPPORTED_MCP_PROTOCOL_VERSIONS) - 1:
                await self._reset_transport_state()
                return False

        await self._send_sse_notification("notifications/initialized", {})
        collected: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(20):
            params = {"cursor": cursor} if cursor else {}
            resp = await self._send_sse_rpc("tools/list", params, timeout=min(30.0, self.timeout_seconds))
            if not isinstance(resp, dict):
                break
            page = resp.get("tools")
            if isinstance(page, list):
                collected.extend(t for t in page if isinstance(t, dict))
            next_cursor = resp.get("nextCursor")
            if not next_cursor:
                break
            cursor = str(next_cursor)

        self.tools_cache = normalize_mcp_tool_definitions(collected)
        self._initialized = True
        logger.info(f"远程 MCP [{self.name}] (sse, proto={self.protocol_version}) 已就绪，工具数: {len(collected)}")
        return True

    async def _sse_reader_loop(self) -> None:
        """后台监听 GET <url> 的 SSE 事件流，处理 endpoint 与 JSON-RPC 消息。"""
        assert self._sse_ready_event is not None
        client = self._ensure_client()
        # NOTE: SSE 长流空闲等待期间不得被 read timeout 掐断，统一设为 read=None，仅限制 connect/write
        stream_timeout = httpx.Timeout(connect=min(10.0, self.timeout_seconds), read=None, write=15.0, pool=10.0)
        sse_headers = self._build_resolved_headers(extra={"Accept": "text/event-stream", "Cache-Control": "no-cache"})

        try:
            async with client.stream("GET", self.url, headers=sse_headers, timeout=stream_timeout) as resp:
                if resp.status_code in (301, 302, 303, 307, 308):
                    loc = resp.headers.get("location", "")
                    self._last_error = f"SSE 请求拒绝重定向 (HTTP {resp.status_code} -> {loc})，以防鉴权凭证泄露"
                    self._sse_ready_event.set()
                    return
                if resp.status_code in (401, 403):
                    self._last_error = f"远程 SSE MCP 鉴权失败 (HTTP {resp.status_code})"
                    self._sse_ready_event.set()
                    return
                if resp.status_code >= 400:
                    self._last_error = f"SSE 连接失败 (HTTP {resp.status_code})"
                    self._sse_ready_event.set()
                    return

                self._sse_alive = True
                event_name = ""
                data_lines: list[str] = []

                def _dispatch_event() -> bool:
                    nonlocal event_name, data_lines
                    if not data_lines:
                        event_name = ""
                        return True
                    payload_str = "\n".join(data_lines).strip()
                    ev = (event_name or "message").strip().lower()
                    event_name = ""
                    data_lines = []

                    if ev == "endpoint" and not self._post_endpoint:
                        raw_ep = payload_str
                        if raw_ep.startswith("{"):
                            try:
                                ep_obj = json.loads(raw_ep)
                                if isinstance(ep_obj, dict) and ep_obj.get("endpoint"):
                                    raw_ep = str(ep_obj["endpoint"])
                            except json.JSONDecodeError:
                                pass
                        resolved_post_url = urljoin(self.url, raw_ep)
                        if not is_same_origin(resolved_post_url, self.url):
                            self._last_error = f"SSE endpoint 跨源拦截: {resolved_post_url} 与主地址 {self.url} 不同源"
                            self._sse_ready_event.set()
                            return False
                        self._post_endpoint = resolved_post_url
                        self._sse_ready_event.set()
                        return True

                    if payload_str and payload_str != "[DONE]":
                        try:
                            msg = json.loads(payload_str)
                        except json.JSONDecodeError:
                            return True
                        if isinstance(msg, dict) and "id" in msg:
                            fut = self._pending_futures.pop(msg["id"], None)
                            if fut and not fut.done():
                                fut.set_result(msg)
                    return True

                async for raw_line in resp.aiter_lines():
                    if self._closed:
                        break
                    line = raw_line.rstrip("\r")
                    if not line:
                        if not _dispatch_event():
                            break
                        continue
                    if line.startswith(":"):
                        continue
                    if line.startswith("event:"):
                        event_name = line[6:].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].lstrip(" "))
                _dispatch_event()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._last_error = f"SSE 流读取中断: {e}"
            logger.debug(f"MCP [{self.name}] {self._last_error}")
        finally:
            self._sse_alive = False
            if not self._sse_ready_event.is_set():
                self._sse_ready_event.set()
            for _, fut in list(self._pending_futures.items()):
                if not fut.done():
                    fut.set_exception(RuntimeError(self._last_error or "SSE 连接已断开"))
            self._pending_futures.clear()

    async def _send_sse_notification(self, method: str, params: dict[str, Any]) -> None:
        if not self._post_endpoint or not self._sse_alive:
            return
        client = self._ensure_client()
        post_headers = self._build_resolved_headers(extra={"Content-Type": "application/json"})
        body = {"jsonrpc": "2.0", "method": method, "params": params}
        try:
            await client.post(self._post_endpoint, headers=post_headers, json=body, timeout=10.0)
        except Exception as e:
            logger.debug(f"MCP [{self.name}] SSE 发送通知 ({method}) 异常: {e}")

    async def _send_sse_rpc(self, method: str, params: dict[str, Any], timeout: float) -> Any:
        self._last_error = None
        if not self._post_endpoint or not self._sse_alive:
            self._last_error = "SSE 通道未处于存活状态"
            return None

        client = self._ensure_client()
        self._req_id += 1
        req_id = self._req_id
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending_futures[req_id] = fut

        post_headers = self._build_resolved_headers(extra={"Content-Type": "application/json", "Accept": "application/json"})
        body = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}

        try:
            resp = await client.post(
                self._post_endpoint,
                headers=post_headers,
                json=body,
                timeout=httpx.Timeout(timeout, connect=min(10.0, timeout)),
            )
            if resp.status_code in (301, 302, 303, 307, 308):
                self._pending_futures.pop(req_id, None)
                self._last_error = f"SSE POST 拒绝重定向 (HTTP {resp.status_code})，防止凭证泄露"
                return None
            if resp.status_code in (401, 403):
                self._pending_futures.pop(req_id, None)
                self._last_error = f"远程 SSE MCP 鉴权失败 (HTTP {resp.status_code})"
                return None
            if resp.status_code >= 400:
                self._pending_futures.pop(req_id, None)
                self._last_error = f"SSE POST 错误 HTTP {resp.status_code}: {(resp.text or '')[:200]}"
                return None

            if (resp.content or b"").strip():
                try:
                    direct_obj = resp.json()
                    if isinstance(direct_obj, dict) and direct_obj.get("id") == req_id and (
                        "result" in direct_obj or "error" in direct_obj
                    ):
                        self._pending_futures.pop(req_id, None)
                        if direct_obj.get("error"):
                            err_obj = direct_obj["error"]
                            self._last_error = err_obj.get("message") if isinstance(err_obj, dict) else str(err_obj)
                            return None
                        return direct_obj.get("result")
                except Exception:
                    pass

            msg = await asyncio.wait_for(fut, timeout=timeout)
            if "error" in msg and msg["error"]:
                err_obj = msg["error"]
                self._last_error = err_obj.get("message") if isinstance(err_obj, dict) else str(err_obj)
                return None
            return msg.get("result")
        except asyncio.TimeoutError:
            self._pending_futures.pop(req_id, None)
            self._last_error = f"SSE RPC ({method}) 等待响应超时 (>{timeout:.0f}s)"
            return None
        except Exception as e:
            self._pending_futures.pop(req_id, None)
            self._last_error = str(e)
            return None

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        if not await self.ensure_started():
            reason = f" ({self._last_error})" if self._last_error else ""
            return f"Error: 远程 MCP Server [{self.name}] 未就绪或连接失败{reason}。"

        if self.transport == "sse":
            res = await self._send_sse_rpc(
                "tools/call",
                {"name": tool_name, "arguments": arguments},
                timeout=self.timeout_seconds,
            )
            if res is None:
                detail = self._last_error or "超时或连接中断"
                return f"Error: 调用远程 MCP 工具 [{tool_name}] 失败: {detail}。"
            return extract_mcp_tool_result(res)

        try:
            res = await self._post_streamable_rpc(
                "tools/call",
                {"name": tool_name, "arguments": arguments},
            )
        except _SessionExpiredError:
            logger.info(f"MCP [{self.name}] 会话已失效 (HTTP 404)，自动清理会话并重新握手重试")
            await self._reset_transport_state()
            if not await self.ensure_started():
                reason = f" ({self._last_error})" if self._last_error else ""
                return f"Error: 远程 MCP [{tool_name}] 重连失败{reason}。"
            try:
                res = await self._post_streamable_rpc(
                    "tools/call",
                    {"name": tool_name, "arguments": arguments},
                )
            except Exception as retry_err:
                return f"Error: 调用远程 MCP 工具 [{tool_name}] 重试失败: {retry_err}"
        except Exception as e:
            return f"Error: 调用远程 MCP 工具 [{tool_name}] 异常: {e}"

        if res is None:
            detail = self._last_error or "超时或无响应"
            return f"Error: 调用远程 MCP 工具 [{tool_name}] 失败: {detail}。"
        return extract_mcp_tool_result(res)

    async def test_connection(self) -> dict[str, Any]:
        """执行一次独立连通性探测，返回工具列表与耗时信息后即关闭连接。"""
        t0 = time.monotonic()
        try:
            ok = await self.ensure_started()
            latency_ms = round((time.monotonic() - t0) * 1000, 1)
            tool_names = [str(t.get("name") or "") for t in self.tools_cache if t.get("name")]
            return {
                "ok": ok,
                "server": self.name,
                "transport": self.transport,
                "protocol_version": self.protocol_version,
                "tool_count": len(tool_names),
                "tools": tool_names,
                "latency_ms": latency_ms,
                "error": None if ok else (self._last_error or "连接或握手失败"),
            }
        finally:
            await self.close()

    async def close(self) -> None:
        """关闭连接：若有 Streamable Session 则发 DELETE 清理，并释放 AsyncClient/SSE 协程。"""
        self._closed = True
        if self.transport == "streamable-http" and self.session_id and self.client and not self.client.is_closed:
            try:
                del_headers = self._build_resolved_headers(extra={"Mcp-Session-Id": self.session_id})
                await self.client.delete(self.url, headers=del_headers, timeout=3.0)
            except Exception:
                pass
        await self._reset_transport_state()


class MCPManager:
    """全局 MCP 客户端常驻连接池（支持热插拔重载、远程协议与失败冷却自愈）"""

    FAILURE_COOLDOWN_SECONDS = 20.0

    def __init__(self, config_path: str | Path | None = None) -> None:
        self.base_dir = get_writable_app_root()
        root_cfg = self.base_dir / "mcp_servers.json"
        data_cfg = self.base_dir / "data" / "mcp_servers.json"

        if config_path:
            self.config_path = Path(config_path)
        elif root_cfg.exists():
            self.config_path = root_cfg
        elif getattr(sys, "frozen", False):
            # 冻结桌面版默认释放配置文件于可写根目录，便于用户编辑与升级保留
            self.config_path = root_cfg
        else:
            self.config_path = data_cfg

        self._connections: dict[str, BaseMCPConnection] = {}
        # 记录失败时间戳（单调时钟），超过冷却期后允许自动重新探测，避免一次波动永久拉黑
        self._failed_servers: dict[str, float] = {}
        self._last_mtime: float = 0.0
        self._ensure_default_config()

    def _ensure_default_config(self) -> None:
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            if not self.config_path.exists():
                if seed_mcp_config(get_bundle_root(), self.config_path):
                    return
                default_cfg = {
                    "mcpServers": {
                        "playwright-cdp": {
                            "command": "npx",
                            "args": ["-y", "@playwright/mcp@latest", "--cdp-endpoint", "http://127.0.0.1:9222"],
                            "env": {},
                            "enabled": False,
                            "description": "接管本机已开启 --remote-debugging-port=9222 的 Edge/Chrome 浏览器（按需启用）",
                        }
                    }
                }
                self.config_path.write_text(
                    json.dumps(default_cfg, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
        except Exception as e:
            logger.warning(f"初始化 mcp_servers.json 失败: {e}")

    def _sync_config_mtime(self) -> None:
        """检测 mcp_servers.json 是否发生磁盘变更，若变更则清空失败熔断缓存并触发热重载"""
        if not self.config_path.exists():
            return
        try:
            current_mtime = self.config_path.stat().st_mtime
            if current_mtime != self._last_mtime:
                self._last_mtime = current_mtime
                self._failed_servers.clear()
        except Exception:
            pass

    def load_config(self) -> dict[str, Any]:
        if not self.config_path.exists():
            return {"mcpServers": {}}
        try:
            # NOTE: 兼容 Windows 记事本或 PowerShell 保存的带 UTF-8 BOM JSON 文件
            raw = json.loads(self.config_path.read_text(encoding="utf-8-sig"))
            if isinstance(raw, dict):
                return raw
            return {"mcpServers": {}}
        except Exception as e:
            logger.warning(f"读取 mcp_servers.json 失败: {e}")
            return {"mcpServers": {}}

    def save_config(self, cfg: dict[str, Any]) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        self._failed_servers.clear()
        self._sync_config_mtime()

    def _build_connection(self, srv_name: str, norm_cfg: dict[str, Any]) -> BaseMCPConnection:
        if norm_cfg["transport"] in ("streamable-http", "sse"):
            return _RemoteHttpMCPConnection(
                name=srv_name,
                transport=norm_cfg["transport"],
                url=norm_cfg["url"],
                headers=norm_cfg["headers"],
                timeout_seconds=norm_cfg["timeout_seconds"],
                upload_endpoint=norm_cfg["upload_endpoint"],
                upload_file_field=norm_cfg["upload_file_field"],
                download_origins=norm_cfg["download_origins"],
                transport_inferred=norm_cfg["transport_inferred"],
            )
        return _StdioMCPConnection(
            name=srv_name,
            command=norm_cfg["command"],
            args=norm_cfg["args"],
            env=norm_cfg["env"],
            cwd=self.base_dir,
            timeout_seconds=norm_cfg["timeout_seconds"],
        )

    def get_enabled_remote_file_config(
        self,
        srv_name: str,
        *,
        strict_env: bool = True,
    ) -> tuple[dict[str, Any] | None, str | None]:
        """获取指定已启用远程 MCP 的标准化协同配置与展开后的 Headers，失败返回 (None, error_msg)。"""
        clean_name = (srv_name or "").strip()
        if not clean_name:
            return None, "Error: 未指定 server_name。"

        cfg_map = extract_mcp_servers_map(self.load_config())
        raw_srv = cfg_map.get(clean_name)
        if not isinstance(raw_srv, dict):
            return None, f"Error: 未在 mcp_servers.json 中找到服务 '{clean_name}'。"
        if not raw_srv.get("enabled", False):
            return None, f"Error: MCP 服务 '{clean_name}' 当前未启用，请先在设置中启用该服务。"

        norm = parse_mcp_server_entry(clean_name, raw_srv)
        if norm["transport"] not in ("streamable-http", "sse") or not norm["url"]:
            return None, f"Error: MCP 服务 '{clean_name}' 不是远程 HTTP/SSE 服务或未配置 url。"

        expanded_headers, missing_vars = expand_env_placeholders(norm["headers"])
        if strict_env and missing_vars:
            return None, (
                f"Error: 远程 MCP 服务 '{clean_name}' 引用了未定义的环境变量: {', '.join(missing_vars)}，无法执行鉴权请求。"
            )

        norm["expanded_headers"] = expanded_headers
        norm["missing_env_vars"] = missing_vars
        return norm, None

    async def test_server_connection(self, srv_name: str, raw_cfg: dict[str, Any]) -> dict[str, Any]:
        """对单条 MCP 服务配置执行一次性真实握手与工具拉取探测。"""
        clean_name = (srv_name or "test-mcp").strip() or "test-mcp"
        if not is_valid_mcp_server_name(clean_name):
            return {
                "ok": False,
                "server": clean_name,
                "transport": "unknown",
                "tool_count": 0,
                "tools": [],
                "latency_ms": 0.0,
                "error": "MCP 服务名称不能为空且不能包含双下划线 `__`",
            }
        norm = parse_mcp_server_entry(clean_name, raw_cfg)
        if norm["transport"] == "stdio" and not norm["command"]:
            return {
                "ok": False,
                "server": clean_name,
                "transport": "stdio",
                "tool_count": 0,
                "tools": [],
                "latency_ms": 0.0,
                "error": "本地 stdio 模式必须提供启动命令 command",
            }
        if norm["transport"] in ("streamable-http", "sse") and not norm["url"].startswith(("http://", "https://")):
            return {
                "ok": False,
                "server": clean_name,
                "transport": norm["transport"],
                "tool_count": 0,
                "tools": [],
                "latency_ms": 0.0,
                "error": "远程 MCP 服务 url 必须以 http:// 或 https:// 开头",
            }

        conn = self._build_connection(clean_name, norm)
        if isinstance(conn, _RemoteHttpMCPConnection):
            return await conn.test_connection()

        t0 = time.monotonic()
        try:
            ok = await conn.ensure_started()
            latency_ms = round((time.monotonic() - t0) * 1000, 1)
            tools = [str(t.get("name") or "") for t in conn.tools_cache if t.get("name")]
            return {
                "ok": ok,
                "server": clean_name,
                "transport": "stdio",
                "tool_count": len(tools),
                "tools": tools,
                "latency_ms": latency_ms,
                "error": None if ok else (conn._last_error or "本地 MCP 进程启动或握手失败"),
            }
        finally:
            await conn.close()

    async def get_enabled_tools(self) -> list[dict[str, Any]]:
        """获取所有已启用 MCP Server 暴露的工具 Schema（支持热感知配置文件更新与多态连接）"""
        self._sync_config_mtime()
        cfg = extract_mcp_servers_map(self.load_config())
        all_tools: list[dict[str, Any]] = []

        # 清理已被移除或禁用的旧连接
        for existing_name in list(self._connections.keys()):
            srv_cfg = cfg.get(existing_name)
            if not isinstance(srv_cfg, dict) or not srv_cfg.get("enabled", False):
                await self._connections[existing_name].close()
                del self._connections[existing_name]

        for srv_name, srv_cfg in cfg.items():
            if not is_valid_mcp_server_name(srv_name):
                logger.warning(f"跳过非法 MCP 服务名称 '{srv_name}'（名称不能包含双下划线 `__`）")
                continue
            if not isinstance(srv_cfg, dict) or not srv_cfg.get("enabled", False):
                self._failed_servers.pop(srv_name, None)
                continue
            failed_at = self._failed_servers.get(srv_name)
            if failed_at is not None:
                if (time.monotonic() - failed_at) < self.FAILURE_COOLDOWN_SECONDS:
                    continue
                self._failed_servers.pop(srv_name, None)

            norm_cfg = parse_mcp_server_entry(srv_name, srv_cfg)
            conn = self._connections.get(srv_name)
            if conn and not conn.matches_config(norm_cfg):
                await conn.close()
                conn = None

            if not conn:
                conn = self._build_connection(srv_name, norm_cfg)
                self._connections[srv_name] = conn

            if await conn.ensure_started():
                self._failed_servers.pop(srv_name, None)
                for t in conn.tools_cache:
                    raw_tname = str(t.get("name") or "").strip()
                    if not raw_tname:
                        continue
                    schema = t.get("inputSchema")
                    safe_schema = schema if isinstance(schema, dict) else {"type": "object", "properties": {}}
                    if "properties" not in safe_schema or not isinstance(safe_schema.get("properties"), dict):
                        safe_schema = {**safe_schema, "type": safe_schema.get("type") or "object", "properties": {}}
                    all_tools.append({
                        "name": f"mcp__{srv_name}__{raw_tname}",
                        "description": str(t.get("description") or f"MCP Tool from {srv_name}"),
                        "parameters": safe_schema,
                    })
            else:
                self._failed_servers[srv_name] = time.monotonic()
                stale_conn = self._connections.pop(srv_name, None)
                if stale_conn:
                    await stale_conn.close()

        return all_tools

    async def call_mcp_tool(self, namespaced_name: str, arguments: dict[str, Any]) -> str:
        """路由并调用形如 mcp__<server>__<tool> 的工具"""
        parts = namespaced_name.split("__", 2)
        if len(parts) != 3 or parts[0] != "mcp":
            return f"Error: 非法的 MCP 工具名格式 '{namespaced_name}'"
        _, srv_name, raw_tool_name = parts
        conn = self._connections.get(srv_name)
        if not conn or not conn.is_alive():
            # NOTE: 当明确点名调用某 MCP 服务但此前曾短暂失败或掉线时，立即重置其冷却窗口尝试恢复
            self._failed_servers.pop(srv_name, None)
            await self.get_enabled_tools()
            conn = self._connections.get(srv_name)
        if not conn:
            return f"Error: MCP Server '{srv_name}' 未启用或启动失败。"
        return await conn.call_tool(raw_tool_name, arguments)

    def has_cooling_servers(self) -> bool:
        """是否存在处于失败冷却期、等待重连的 MCP 服务"""
        return bool(self._failed_servers)

    def clear_cooldowns(self) -> None:
        """清空所有失败冷却标记，允许立即重新探测已启用服务"""
        self._failed_servers.clear()

    async def shutdown_all(self) -> None:
        """异步关闭所有常驻 MCP 子进程与远程 HTTP 连接池，防止僵尸进程或套接字泄漏"""
        for name, conn in list(self._connections.items()):
            try:
                await conn.close()
                logger.info(f"已安全销毁常驻 MCP 连接 [{name}]")
            except Exception as e:
                logger.debug(f"关闭 MCP [{name}] 异常: {e}")
        self._connections.clear()


mcp_manager = MCPManager()
