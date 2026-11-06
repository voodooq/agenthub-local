"""
MCP 客户端常驻连接池管理器 (MCPManager)
特性：
1. 兼容标准 mcp_servers.json 配置规范（支持 stdio 本地进程与远程端点配置）
2. 懒加载常驻进程池（MCP Connection Pool）：首次调用时拉起子进程并缓存 JSON-RPC 会话，避免每次对话重启耗时 2~5s
3. 防僵尸进程泄漏：在 FastAPI lifespan 退出或桌面端关闭时调用 shutdown_all() 递归强杀所有 MCP 进程树
"""
import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..core.logging_config import logger
from ..agent.shell_executor import LocalShellExecutor


class _StdioMCPConnection:
    """单个 stdio 模式 MCP Server 的常驻 JSON-RPC 2.0 连接封装"""

    def __init__(self, name: str, command: str, args: list[str], env: dict[str, str] | None = None) -> None:
        self.name = name
        self.command = command
        self.args = args
        self.custom_env = env or {}
        self.proc: subprocess.Popen | None = None
        self._req_id = 0
        self._lock = asyncio.Lock()
        self.tools_cache: list[dict[str, Any]] = []

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    async def ensure_started(self) -> bool:
        """懒加载拉起常驻 MCP 子进程并完成 JSON-RPC initialize 握手"""
        if self.is_alive():
            return True

        merged_env = os.environ.copy()
        merged_env.update(self.custom_env)

        # 在 Windows 上优先解析完整可执行路径（如 npx -> npx.cmd），防 WinError 2
        resolved_cmd = shutil.which(self.command) or self.command
        cmd_list = [resolved_cmd] + self.args

        # 若目标命令不存在，优雅跳过并提示
        if not shutil.which(self.command):
            logger.info(f"MCP Server [{self.name}] 依赖命令 `{self.command}` 未在系统 PATH 中找到，已跳过拉起。")
            return False

        try:
            self.proc = subprocess.Popen(
                cmd_list,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=merged_env,
                shell=(sys.platform == "win32" and resolved_cmd.lower().endswith((".cmd", ".bat"))),
                start_new_session=(sys.platform != "win32"),
            )
            # 发送 MCP 标准 initialize 握手
            init_resp = await self._send_rpc(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "GeminiHub-Local", "version": "2.0.0"},
                },
                timeout=10.0,
            )
            if init_resp is not None:
                # 拉取工具列表缓存
                tools_resp = await self._send_rpc("tools/list", {}, timeout=10.0)
                if isinstance(tools_resp, dict) and "tools" in tools_resp:
                    self.tools_cache = tools_resp["tools"]
                return True
        except Exception as e:
            logger.warning(f"拉起 MCP Server [{self.name}] 失败: {e}")
            self.close()
        return False

    async def _send_rpc(self, method: str, params: dict[str, Any], timeout: float = 15.0) -> Any:
        if not self.is_alive() or not self.proc or not self.proc.stdin or not self.proc.stdout:
            return None

        async with self._lock:
            self._req_id += 1
            req_id = self._req_id
            payload = json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}) + "\n"

            def _io_step() -> str:
                assert self.proc and self.proc.stdin and self.proc.stdout
                self.proc.stdin.write(payload.encode("utf-8"))
                self.proc.stdin.flush()
                line = self.proc.stdout.readline()
                return line.decode("utf-8", errors="replace").strip()

            try:
                resp_line = await asyncio.wait_for(asyncio.to_thread(_io_step), timeout=timeout)
                if resp_line:
                    parsed = json.loads(resp_line)
                    return parsed.get("result")
            except Exception as e:
                logger.warning(f"MCP [{self.name}] RPC ({method}) 通信异常: {e}")
            return None

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        if not await self.ensure_started():
            return f"Error: MCP Server [{self.name}] 未就绪或启动失败。"
        res = await self._send_rpc("tools/call", {"name": tool_name, "arguments": arguments}, timeout=30.0)
        if res is None:
            return f"Error: 调用 MCP 工具 [{tool_name}] 超时或无响应。"
        return json.dumps(res, ensure_ascii=False)

    def close(self) -> None:
        """销毁常驻子进程及其整个进程树"""
        if self.proc:
            pid = self.proc.pid
            LocalShellExecutor.kill_process_tree(pid)
            self.proc = None


class MCPManager:
    """全局 MCP 客户端常驻连接池"""

    def __init__(self, config_path: str | Path | None = None) -> None:
        base_dir = Path(__file__).resolve().parent.parent.parent.parent
        self.config_path = Path(config_path) if config_path else (base_dir / "data" / "mcp_servers.json")
        self._connections: dict[str, _StdioMCPConnection] = {}
        self._ensure_default_config()

    def _ensure_default_config(self) -> None:
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            if not self.config_path.exists():
                default_cfg = {
                    "mcpServers": {
                        "filesystem-example": {
                            "command": "npx",
                            "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
                            "enabled": False,
                            "description": "官方本地文件系统 MCP 示例（按需开启）",
                        }
                    }
                }
                self.config_path.write_text(
                    json.dumps(default_cfg, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
        except Exception as e:
            logger.warning(f"初始化 mcp_servers.json 失败: {e}")

    def load_config(self) -> dict[str, Any]:
        if not self.config_path.exists():
            return {"mcpServers": {}}
        try:
            return json.loads(self.config_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"读取 mcp_servers.json 失败: {e}")
            return {"mcpServers": {}}

    def save_config(self, cfg: dict[str, Any]) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")

    async def get_enabled_tools(self) -> list[dict[str, Any]]:
        """获取所有已启用 MCP Server 暴露的工具 Schema"""
        cfg = self.load_config().get("mcpServers", {})
        all_tools: list[dict[str, Any]] = []

        for srv_name, srv_cfg in cfg.items():
            if not isinstance(srv_cfg, dict) or not srv_cfg.get("enabled", False):
                # 若被禁用且之前有连接，及时关闭回收
                if srv_name in self._connections:
                    self._connections[srv_name].close()
                    del self._connections[srv_name]
                continue

            conn = self._connections.get(srv_name)
            if not conn:
                conn = _StdioMCPConnection(
                    name=srv_name,
                    command=srv_cfg.get("command", ""),
                    args=srv_cfg.get("args", []),
                    env=srv_cfg.get("env", {}),
                )
                self._connections[srv_name] = conn

            if await conn.ensure_started():
                for t in conn.tools_cache:
                    all_tools.append({
                        "name": f"mcp__{srv_name}__{t.get('name')}",
                        "description": t.get("description", f"MCP Tool from {srv_name}"),
                        "parameters": t.get("inputSchema", {"type": "object", "properties": {}}),
                    })
        return all_tools

    async def call_mcp_tool(self, namespaced_name: str, arguments: dict[str, Any]) -> str:
        """路由并调用形如 mcp__<server>__<tool> 的工具"""
        parts = namespaced_name.split("__", 2)
        if len(parts) != 3 or parts[0] != "mcp":
            return f"Error: 非法的 MCP 工具名格式 '{namespaced_name}'"
        _, srv_name, raw_tool_name = parts
        conn = self._connections.get(srv_name)
        if not conn:
            await self.get_enabled_tools()
            conn = self._connections.get(srv_name)
        if not conn:
            return f"Error: MCP Server '{srv_name}' 未启用或不存在。"
        return await conn.call_tool(raw_tool_name, arguments)

    def shutdown_all(self) -> None:
        """关闭所有常驻 MCP 子进程，防止僵尸进程泄漏"""
        for name, conn in list(self._connections.items()):
            try:
                conn.close()
                logger.info(f"已安全销毁常驻 MCP 进程 [{name}]")
            except Exception as e:
                logger.debug(f"关闭 MCP [{name}] 异常: {e}")
        self._connections.clear()


mcp_manager = MCPManager()

