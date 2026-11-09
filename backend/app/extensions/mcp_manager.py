"""
MCP 客户端常驻连接池管理器 (MCPManager)
特性：
1. 兼容标准 mcp_servers.json 配置规范（支持 stdio 本地进程热插拔）
2. 自动映射 python/python3 至当前虚拟环境解释器 (sys.executable)，支持 Agent 自开发 Python MCP 零配置运行
3. 配置文件 mtime 变更热重载 + 启动失败熔断缓存（避免缺少依赖时每轮对话重复报错刷屏）
4. 防僵尸进程泄漏：在退出时调用 shutdown_all() 递归清理所有 MCP 子进程树
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

    def __init__(
        self,
        name: str,
        command: str,
        args: list[str],
        env: dict[str, str] | None = None,
        cwd: str | Path | None = None,
    ) -> None:
        self.name = name
        self.command = command
        self.args = args
        self.custom_env = env or {}
        self.cwd = str(cwd) if cwd else None
        self.proc: subprocess.Popen | None = None
        self._req_id = 0
        self._lock = asyncio.Lock()
        self.tools_cache: list[dict[str, Any]] = []

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _resolve_executable(self) -> str | None:
        """解析启动命令的真实可执行文件路径，优先将 python 映射至当前虚拟环境解释器"""
        cmd_lower = self.command.strip().lower()
        if cmd_lower in ("python", "python3", "python.exe"):
            return sys.executable

        scripts_dir = Path(sys.executable).parent
        for ext in (".exe", ".cmd", ".bat", ""):
            candidate = scripts_dir / f"{self.command}{ext}"
            if candidate.exists():
                return str(candidate)

        resolved = shutil.which(self.command)
        return resolved

    async def ensure_started(self) -> bool:
        """懒加载拉起常驻 MCP 子进程并完成 JSON-RPC initialize 握手"""
        if self.is_alive():
            return True

        resolved_cmd = self._resolve_executable()
        if not resolved_cmd:
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
            )
            init_resp = await self._send_rpc(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "AgentHub-Local", "version": "1.1.5"},
                },
                timeout=10.0,
            )
            if init_resp is not None:
                tools_resp = await self._send_rpc("tools/list", {}, timeout=10.0)
                if isinstance(tools_resp, dict) and "tools" in tools_resp:
                    self.tools_cache = tools_resp["tools"]
                logger.info(f"MCP Server [{self.name}] 已就绪，挂载工具数: {len(self.tools_cache)}")
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
                for _ in range(30):
                    raw_line = self.proc.stdout.readline()
                    if not raw_line:
                        break
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if line.startswith("{") and line.endswith("}") and '"jsonrpc"' in line:
                        return line
                return ""

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
        res = await self._send_rpc("tools/call", {"name": tool_name, "arguments": arguments}, timeout=35.0)
        if res is None:
            return f"Error: 调用 MCP 工具 [{tool_name}] 超时或无响应。"

        if isinstance(res, dict) and "content" in res and isinstance(res["content"], list):
            text_parts = [
                item.get("text", "")
                for item in res["content"]
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            if text_parts:
                return "\n".join(text_parts)

        return json.dumps(res, ensure_ascii=False)

    def close(self) -> None:
        """销毁常驻子进程及其整个进程树"""
        if self.proc:
            pid = self.proc.pid
            LocalShellExecutor.kill_process_tree(pid)
            self.proc = None


class MCPManager:
    """全局 MCP 客户端常驻连接池（支持热插拔重载与失败熔断）"""

    def __init__(self, config_path: str | Path | None = None) -> None:
        if getattr(sys, "frozen", False):
            self.base_dir = Path(sys.executable).resolve().parent
        else:
            self.base_dir = Path(__file__).resolve().parent.parent.parent.parent
        root_cfg = self.base_dir / "mcp_servers.json"
        data_cfg = self.base_dir / "data" / "mcp_servers.json"

        if config_path:
            self.config_path = Path(config_path)
        elif root_cfg.exists():
            self.config_path = root_cfg
        else:
            self.config_path = data_cfg

        self._connections: dict[str, _StdioMCPConnection] = {}
        self._failed_servers: set[str] = set()
        self._last_mtime: float = 0.0
        self._ensure_default_config()

    def _ensure_default_config(self) -> None:
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            if not self.config_path.exists():
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
            return json.loads(self.config_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"读取 mcp_servers.json 失败: {e}")
            return {"mcpServers": {}}

    def save_config(self, cfg: dict[str, Any]) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        self._failed_servers.clear()
        self._sync_config_mtime()

    async def get_enabled_tools(self) -> list[dict[str, Any]]:
        """获取所有已启用 MCP Server 暴露的工具 Schema（支持热感知配置文件更新）"""
        self._sync_config_mtime()
        cfg = self.load_config().get("mcpServers", {})
        all_tools: list[dict[str, Any]] = []

        # 清理已被移除或禁用的旧连接
        for existing_name in list(self._connections.keys()):
            srv_cfg = cfg.get(existing_name)
            if not isinstance(srv_cfg, dict) or not srv_cfg.get("enabled", False):
                self._connections[existing_name].close()
                del self._connections[existing_name]

        for srv_name, srv_cfg in cfg.items():
            if not isinstance(srv_cfg, dict) or not srv_cfg.get("enabled", False):
                continue
            if srv_name in self._failed_servers:
                continue

            conn = self._connections.get(srv_name)
            desired_cmd = str(srv_cfg.get("command", "")).strip()
            desired_args = [str(a) for a in (srv_cfg.get("args") or [])]
            # 若配置参数发生变更，重启该连接
            if conn and (conn.command != desired_cmd or conn.args != desired_args):
                conn.close()
                conn = None

            if not conn:
                conn = _StdioMCPConnection(
                    name=srv_name,
                    command=desired_cmd,
                    args=desired_args,
                    env=srv_cfg.get("env", {}),
                    cwd=self.base_dir,
                )
                self._connections[srv_name] = conn

            if await conn.ensure_started():
                for t in conn.tools_cache:
                    all_tools.append({
                        "name": f"mcp__{srv_name}__{t.get('name')}",
                        "description": t.get("description", f"MCP Tool from {srv_name}"),
                        "parameters": t.get("inputSchema", {"type": "object", "properties": {}}),
                    })
            else:
                self._failed_servers.add(srv_name)
                self._connections.pop(srv_name, None)

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
            return f"Error: MCP Server '{srv_name}' 未启用或启动失败。"
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
