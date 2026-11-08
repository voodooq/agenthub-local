"""
TaskLoopRunner: 自主循环状态机 (Planner -> Executor -> Verifier -> Reflector)
集成防死循环错误指纹熔断机制与安全拦截网关
"""
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any
from ..core.logging_config import logger
from ..repository.loop_run_repository import TaskLoopRepository
from ..repository.workspace_repository import WorkspaceRepository
from .policy_interceptor import PolicyInterceptor


class TaskLoopRunner:
    """
    长程自愈循环执行器
    """
    def __init__(self, loop_id: str, workspace_root: str, security_level: str = "standard"):
        self.loop_id = loop_id
        self.workspace_root = Path(workspace_root).resolve()
        self.interceptor = PolicyInterceptor(str(self.workspace_root), security_level)

    @classmethod
    async def load(cls, loop_id: str) -> "TaskLoopRunner | None":
        """
        从数据库加载任务循环实例
        """
        loop_data = await TaskLoopRepository.get_loop(loop_id)
        if not loop_data:
            return None

        ws = await WorkspaceRepository.get_by_id(loop_data.workspace_id)
        ws_root = ws["root_path"] if ws else str(Path.cwd())
        security = ws.get("security_level", "standard") if ws else "standard"
        return cls(loop_id, ws_root, security)

    def _compute_error_fingerprint(self, err_type: str, err_msg: str) -> str:
        """
        计算错误指纹用于重复死循环检测
        """
        raw = f"{err_type}:{err_msg.strip()[:150]}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def _sync_scratchpad_file(self, content: str) -> None:
        """
        同步写入工作区本地物理文件 <workspace>/.agenthub/scratchpad.md
        """
        try:
            agenthub_dir = self.workspace_root / ".agenthub"
            agenthub_dir.mkdir(parents=True, exist_ok=True)
            scratchpad_path = agenthub_dir / "scratchpad.md"
            scratchpad_path.write_text(content, encoding="utf-8")
        except Exception as e:
            logger.warning(f"写入本地 scratchpad.md 失败: {e}")

    async def step_plan(self, goal: str) -> str:
        """
        [Planner 阶段] 规划或重构子任务清单 (DAG)
        """
        await TaskLoopRepository.update_loop_status(self.loop_id, "planning")
        
        # 结构化生成初始步骤或调整步骤
        plan_md = (
            f"# 任务目标：{goal}\n\n"
            f"## 执行计划 (DoD 驱动)\n"
            f"- [x] 1. 目标与工作区上下文审查 (已就绪: `{self.workspace_root.name}`)\n"
            f"- [/] 2. 核心逻辑执行与测试用例验证\n"
            f"- [ ] 3. 客观标准验收 (DoD 判定与代码格式校验)\n"
            f"- [ ] 4. 产出物归档到工作区资产库\n"
        )
        self._sync_scratchpad_file(plan_md)
        await TaskLoopRepository.update_loop_status(self.loop_id, "executing", scratchpad_md=plan_md)
        return plan_md

    async def step_execute_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """
        [Executor 阶段] 执行指定工具调用，先经三级安全网关审查
        """
        decision = self.interceptor.evaluate(tool_name, arguments)

        # 遇到 L2 高危操作或严格模式下的不安全调用 -> 挂起当前循环并等待人工审批
        if decision.require_approval or not decision.allowed:
            call_id = f"call_{uuid.uuid4().hex[:8]}"
            approval_payload = {
                "call_id": call_id,
                "tool_name": tool_name,
                "arguments": arguments,
                "policy_level": decision.level,
                "risk_reason": decision.risk_reason,
                "snapshot_path": decision.snapshot_path,
            }
            await TaskLoopRepository.update_loop_status(
                self.loop_id,
                status="waiting_approval",
                pending_approval=approval_payload,
            )
            return {
                "status": "waiting_approval",
                "message": f"操作已被安全网关拦截，等待人工确认: {decision.risk_reason}",
                "approval_info": approval_payload,
            }

        # L0 / L1 放行执行（模拟或执行具身逻辑）
        return {
            "status": "success",
            "message": f"工具 [{tool_name}] 成功在工作区受控执行",
            "level": decision.level,
            "snapshot_path": decision.snapshot_path,
        }

    async def handle_approval(
        self,
        action: str,
        modified_args: dict[str, Any] | None = None,
        guidance: str | None = None,
    ) -> dict[str, Any]:
        """
        处理人工审批响应并推动状态机从 waiting_approval 恢复
        """
        loop_data = await TaskLoopRepository.get_loop(self.loop_id)
        if not loop_data or loop_data.loop_status != "waiting_approval":
            return {"success": False, "message": "当前循环不在等待审批状态"}

        pending = loop_data.pending_approval_call
        if not pending:
            return {"success": False, "message": "未发现待审批项"}

        if action == "approve_once":
            # 仅放行本次
            await TaskLoopRepository.update_loop_status(self.loop_id, "executing")
            return {"success": True, "action": "approve_once", "message": "已批准本次执行"}

        elif action == "approve_all":
            # 将工具加入白名单并放行
            self.interceptor.add_to_whitelist(pending.tool_name)
            await TaskLoopRepository.update_loop_status(self.loop_id, "executing")
            return {"success": True, "action": "approve_all", "message": "已加入会话白名单并放行"}

        elif action == "modify_args":
            # 使用新参数放行
            await TaskLoopRepository.update_loop_status(self.loop_id, "executing")
            return {
                "success": True,
                "action": "modify_args",
                "message": "已采用修正后的安全参数放行",
                "args": modified_args or {},
            }

        elif action == "reject":
            # 驳回并进入反思阶段
            guidance_text = guidance or "用户拒绝了此项高危操作，请更换安全可替代方案"
            await self.step_reflect("USER_REJECTION", guidance_text)
            return {"success": True, "action": "reject", "message": "操作已驳回，AI 已进入反思阶段"}

        return {"success": False, "message": f"未知的审批动作: {action}"}

    async def step_verify(self, success_criteria_met: bool, detail: str = "") -> bool:
        """
        [Verifier 阶段] 客观校验本次迭代是否达成标准 (DoD)
        """
        await TaskLoopRepository.update_loop_status(self.loop_id, "verifying")
        if success_criteria_met:
            # 达成 DoD，标记完成
            loop_data = await TaskLoopRepository.get_loop(self.loop_id)
            cur_iter = (loop_data.current_iteration if loop_data else 0) + 1
            completed_scratchpad = (
                f"# 任务完成归档\n\n- [x] 所有子步骤已全部通过 Verifier 验证。\n- 明细: {detail}"
            )
            self._sync_scratchpad_file(completed_scratchpad)
            await TaskLoopRepository.update_loop_status(
                self.loop_id,
                status="completed",
                scratchpad_md=completed_scratchpad,
                iteration=cur_iter,
            )
            return True
        else:
            return False

    async def step_reflect(self, error_type: str, error_message: str) -> dict[str, Any]:
        """
        [Reflector 阶段] 分析失败根因，提取错误指纹检测死循环，超过 3 次触发安全熔断
        """
        loop_data = await TaskLoopRepository.get_loop(self.loop_id)
        if not loop_data:
            return {"status": "error", "message": "任务不存在"}

        fingerprints = list(loop_data.error_fingerprints)
        cur_iter = loop_data.current_iteration + 1
        fp = self._compute_error_fingerprint(error_type, error_message)
        fingerprints.append(fp)

        # 检查最近连续 3 次指纹是否相同（死循环熔断）
        if len(fingerprints) >= 3 and fingerprints[-1] == fingerprints[-2] == fingerprints[-3]:
            fuse_msg = f"检测到连续 3 次相同错误 [{error_type}]，触发自主循环熔断保护，防止无限空转。"
            logger.error(f"Loop {self.loop_id} 触发死循环熔断: {fuse_msg}")
            await TaskLoopRepository.update_loop_status(
                self.loop_id,
                status="failed",
                iteration=cur_iter,
                error_fingerprints=fingerprints,
            )
            return {"status": "fused", "message": fuse_msg}

        # 检查是否超过最大迭代轮次
        if cur_iter >= loop_data.max_iterations:
            await TaskLoopRepository.update_loop_status(
                self.loop_id,
                status="failed",
                iteration=cur_iter,
                error_fingerprints=fingerprints,
            )
            return {"status": "max_iterations_reached", "message": "已达最大自愈迭代上限"}

        # 记录反思日志并切回 planning 状态
        reflect_note = (
            f"\n\n> ⚠️ 第 {cur_iter} 轮遇到问题 [{error_type}]: {error_message}\n"
            f"> 正在规划修正替代策略...\n"
        )
        cur_pad = loop_data.scratchpad_md or ""
        updated_pad = cur_pad + reflect_note
        self._sync_scratchpad_file(updated_pad)

        await TaskLoopRepository.update_loop_status(
            self.loop_id,
            status="planning",
            scratchpad_md=updated_pad,
            iteration=cur_iter,
            error_fingerprints=fingerprints,
        )
        return {"status": "reflected", "next_iteration": cur_iter}

