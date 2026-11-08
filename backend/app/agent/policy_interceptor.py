"""
三级敏感操作拦截网关 (PolicyInterceptor)
分级规则：
- 🟢 L0 (Safe / Read-only): 读操作、扫描、无副作用查询 -> 静默放行
- 🟡 L1 (Workspace Write / Snapshot): 工作区目录内受控写入 -> 自动快照备份后放行
- 🔴 L2 (Critical / Out-of-bounds): 跨目录越界、危险删除、破坏性 Shell、敏感网络外发 -> 挂起并要求 HITL 审批
"""
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any
from ..core.logging_config import logger


class PolicyDecision:
    """
    拦截判定结论
    """
    def __init__(
        self,
        allowed: bool,
        level: str = "L0",
        risk_reason: str = "",
        snapshot_path: str | None = None,
        require_approval: bool = False,
    ):
        self.allowed = allowed
        self.level = level
        self.risk_reason = risk_reason
        self.snapshot_path = snapshot_path
        self.require_approval = require_approval


class PolicyInterceptor:
    """
    执行策略拦截网关
    """
    def __init__(self, workspace_root: str, security_level: str = "standard"):
        self.workspace_root = Path(workspace_root).resolve()
        self.security_level = security_level
        # 任务白名单：已被用户批准放行的工具及特征 (tool_name:hash)
        self.session_whitelists: set[str] = set()

    def add_to_whitelist(self, tool_name: str, args_signature: str | None = None) -> None:
        """
        将指定工具或调用特征加入当前会话白名单
        """
        key = f"{tool_name}:{args_signature}" if args_signature else tool_name
        self.session_whitelists.add(key)
        logger.info(f"安全网关已将操作加入白名单: {key}")

    def evaluate(self, tool_name: str, arguments: dict[str, Any]) -> PolicyDecision:
        """
        评估本次工具调用的安全等级
        """
        # 0. 检查是否在白名单中
        if tool_name in self.session_whitelists:
            return PolicyDecision(allowed=True, level="L0", risk_reason="在任务白名单中")

        # 1. 判别只读安全工具 (L0)
        read_only_tools = {
            "read_file", "search_files", "list_dir", "get_file_info", 
            "fts_search", "web_search", "inspect_code", "ask_question"
        }
        if tool_name in read_only_tools:
            return PolicyDecision(allowed=True, level="L0")

        # 2. 判别文件系统写操作
        file_write_tools = {"write_file", "edit_file", "save_file", "create_file", "append_file"}
        if tool_name in file_write_tools:
            raw_path = arguments.get("path") or arguments.get("file_path") or arguments.get("Path")
            if not raw_path:
                return PolicyDecision(
                    allowed=False, level="L2", risk_reason="未指定文件写入路径", require_approval=True
                )

            target_path = Path(raw_path)
            if not target_path.is_absolute():
                target_path = (self.workspace_root / target_path).resolve()
            else:
                target_path = target_path.resolve()

            # 跨工作区越界检查 (L2)
            try:
                target_path.relative_to(self.workspace_root)
            except ValueError:
                return PolicyDecision(
                    allowed=False,
                    level="L2",
                    risk_reason=f"写操作尝试跨越工作区边界: {target_path} 不在 {self.workspace_root} 内",
                    require_approval=True,
                )

            # 属于工作区内受控写 (L1) -> 自动创建轻量安全快照并放行
            snapshot_path = None
            if target_path.exists() and target_path.is_file():
                snapshot_path = self._create_snapshot(target_path)

            return PolicyDecision(
                allowed=True,
                level="L1",
                risk_reason="工作区内安全受控写入（已快照备份）",
                snapshot_path=snapshot_path,
            )

        # 3. 判别删除与清空等不可逆破坏性工具 (L2)
        dangerous_file_tools = {"delete_file", "remove_file", "delete_dir", "clean_dir"}
        if tool_name in dangerous_file_tools:
            return PolicyDecision(
                allowed=False,
                level="L2",
                risk_reason=f"尝试执行不可逆物理删除工具: {tool_name}",
                require_approval=True,
            )

        # 4. 判别系统命令执行工具 (Shell / Bash / Terminal)
        exec_tools = {"run_command", "execute_command", "bash", "terminal", "shell"}
        if tool_name in exec_tools:
            cmd = arguments.get("command") or arguments.get("CommandLine") or ""
            cmd_lower = cmd.lower()

            # 危险 Shell 指令特征库
            danger_patterns = [
                "rm -rf", "del /f", "rmdir /s", "drop database", "truncate", 
                "format ", "mkfs", "shutdown", "reboot", "curl -x post", "wget --post"
            ]
            for pat in danger_patterns:
                if pat in cmd_lower:
                    return PolicyDecision(
                        allowed=False,
                        level="L2",
                        risk_reason=f"命令中包含高危指令特征 [{pat}]",
                        require_approval=True,
                    )

            # 偏高安全等级下，任何 Shell 首次执行均需确认
            if self.security_level in ("high", "strict"):
                return PolicyDecision(
                    allowed=False,
                    level="L2",
                    risk_reason=f"严格模式下执行 Shell 命令需审批: {cmd[:60]}...",
                    require_approval=True,
                )

            # 标准模式下普通命令 (如 git status, pytest, npm test, python script.py) 视为 L1 放行
            return PolicyDecision(allowed=True, level="L1", risk_reason="普通构建与测试命令受控执行")

        # 默认兜底策略：未知外部工具归为 L2 需人工确认
        return PolicyDecision(
            allowed=False,
            level="L2",
            risk_reason=f"未知或自定义工具调用需人工授权: {tool_name}",
            require_approval=True,
        )

    def _create_snapshot(self, target_file: Path) -> str | None:
        """
        在工作区 .agenthub/snapshots/ 目录下为待修改文件创建时间戳快照
        """
        try:
            snapshot_dir = self.workspace_root / ".agenthub" / "snapshots"
            snapshot_dir.mkdir(parents=True, exist_ok=True)
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            unique_suffix = uuid.uuid4().hex[:6]
            backup_filename = f"{target_file.stem}_{timestamp}_{unique_suffix}{target_file.suffix}"
            dest_path = snapshot_dir / backup_filename
            shutil.copy2(target_file, dest_path)
            logger.info(f"已创建文件修改快照: {dest_path}")
            return str(dest_path)
        except Exception as e:
            logger.warning(f"创建快照失败: {e}")
            return None

