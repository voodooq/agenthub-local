"""
TaskLoopRun 数据仓储层
实现任务自主循环状态的持久化、检索与并发更新
"""
import json
import uuid
from datetime import datetime
from ..core.logging_config import logger
from ..repository.db import db_manager
from ..schema.loop_schema import PendingApprovalCall, TaskLoopResponse


class TaskLoopRepository:
    """
    任务自主循环数据访问对象
    """

    @staticmethod
    async def create_loop(
        workspace_id: str,
        conversation_id: str,
        goal_prompt: str,
        max_iterations: int = 10,
    ) -> TaskLoopResponse:
        """
        初始化创建一条任务循环记录，并自动归档当前工作区历史遗留的未完结任务
        """
        loop_id = f"loop_{uuid.uuid4().hex[:12]}"
        now = datetime.now().isoformat()
        initial_scratchpad = (
            f"# 任务目标：{goal_prompt}\n\n"
            f"## 执行计划 (DoD 驱动)\n"
            f"- [x] 1. 目标与工作区上下文审查 (初始化就绪)\n"
            f"- [/] 2. 核心逻辑执行与测试用例验证 (第 0 轮待命)\n"
            f"- [ ] 3. 客观标准验收 (DoD 判定与代码格式校验)\n"
            f"- [ ] 4. 产出物归档到工作区资产库\n"
        )

        async with db_manager.get_connection() as conn:
            # 自动将旧的未完结活跃任务标记为 completed，避免工作区孤儿假死悬挂
            await conn.execute(
                """
                UPDATE task_loop_runs
                SET loop_status = 'completed', updated_at = ?
                WHERE workspace_id = ? AND loop_status IN ('planning', 'executing', 'verifying', 'reflecting');
                """,
                (now, workspace_id),
            )
            await conn.execute(
                """
                INSERT INTO task_loop_runs (
                    id, workspace_id, conversation_id, goal_prompt, scratchpad_md,
                    loop_status, current_iteration, max_iterations, pending_approval_call,
                    error_fingerprints, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'executing', 0, ?, NULL, '[]', ?, ?);
                """,
                (
                    loop_id,
                    workspace_id,
                    conversation_id,
                    goal_prompt,
                    initial_scratchpad,
                    max_iterations,
                    now,
                    now,
                ),
            )
            await conn.commit()

        logger.info(f"成功初始化任务自主循环: {loop_id} (工作区: {workspace_id})")
        return TaskLoopResponse(
            id=loop_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            goal_prompt=goal_prompt,
            scratchpad_md=initial_scratchpad,
            loop_status="executing",
            current_iteration=0,
            max_iterations=max_iterations,
            pending_approval_call=None,
            error_fingerprints=[],
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    async def get_loop(loop_id: str) -> TaskLoopResponse | None:
        """
        根据 ID 查询任务循环
        """
        async with db_manager.get_connection() as conn:
            async with conn.execute(
                """
                SELECT id, workspace_id, conversation_id, goal_prompt, scratchpad_md,
                       loop_status, current_iteration, max_iterations, pending_approval_call,
                       error_fingerprints, created_at, updated_at
                FROM task_loop_runs WHERE id = ?;
                """,
                (loop_id,),
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None

                pending_call = None
                if row["pending_approval_call"]:
                    try:
                        pending_call = PendingApprovalCall(**json.loads(row["pending_approval_call"]))
                    except Exception as e:
                        logger.warning(f"解析 pending_approval_call 失败: {e}")

                fingerprints = []
                if row["error_fingerprints"]:
                    try:
                        fingerprints = json.loads(row["error_fingerprints"])
                    except Exception:
                        fingerprints = []

                return TaskLoopResponse(
                    id=row["id"],
                    workspace_id=row["workspace_id"],
                    conversation_id=row["conversation_id"],
                    goal_prompt=row["goal_prompt"],
                    scratchpad_md=row["scratchpad_md"],
                    loop_status=row["loop_status"],
                    current_iteration=row["current_iteration"],
                    max_iterations=row["max_iterations"],
                    pending_approval_call=pending_call,
                    error_fingerprints=fingerprints,
                    created_at=str(row["created_at"]),
                    updated_at=str(row["updated_at"]),
                )

    @staticmethod
    async def get_active_loop_by_workspace(
        workspace_id: str,
        include_completed: bool = True,
    ) -> TaskLoopResponse | None:
        """
        获取当前工作区中正在运行、等待审批或最新已交付的任务循环
        """
        status_filter = (
            "('planning', 'executing', 'verifying', 'reflecting', 'waiting_approval', 'completed', 'failed')"
            if include_completed
            else "('planning', 'executing', 'verifying', 'reflecting', 'waiting_approval')"
        )
        async with db_manager.get_connection() as conn:
            async with conn.execute(
                f"""
                SELECT id, workspace_id, conversation_id, goal_prompt, scratchpad_md,
                       loop_status, current_iteration, max_iterations, pending_approval_call,
                       error_fingerprints, created_at, updated_at
                FROM task_loop_runs 
                WHERE workspace_id = ? AND loop_status IN {status_filter}
                ORDER BY updated_at DESC LIMIT 1;
                """,
                (workspace_id,),
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None

                pending_call = None
                if row["pending_approval_call"]:
                    try:
                        pending_call = PendingApprovalCall(**json.loads(row["pending_approval_call"]))
                    except Exception:
                        pass

                fingerprints = []
                if row["error_fingerprints"]:
                    try:
                        fingerprints = json.loads(row["error_fingerprints"])
                    except Exception:
                        pass

                return TaskLoopResponse(
                    id=row["id"],
                    workspace_id=row["workspace_id"],
                    conversation_id=row["conversation_id"],
                    goal_prompt=row["goal_prompt"],
                    scratchpad_md=row["scratchpad_md"],
                    loop_status=row["loop_status"],
                    current_iteration=row["current_iteration"],
                    max_iterations=row["max_iterations"],
                    pending_approval_call=pending_call,
                    error_fingerprints=fingerprints,
                    created_at=str(row["created_at"]),
                    updated_at=str(row["updated_at"]),
                )

    @staticmethod
    async def record_tool_step(
        loop_id: str,
        tool_name: str,
        summary: str,
        is_success: bool = True,
    ) -> TaskLoopResponse | None:
        """
        在微内核具身执行工具时，动态向记事板追加工具执行轨迹并递增轮次
        """
        loop = await TaskLoopRepository.get_loop(loop_id)
        if not loop:
            return None

        next_iter = loop.current_iteration + 1
        status_icon = "x" if is_success else "!"
        clean_sum = (summary or "").strip().replace("\n", " ")[:120]
        step_log_line = f"  - [{status_icon}] 轮次 {next_iter} [{tool_name}]: `{clean_sum}`"

        cur_pad = loop.scratchpad_md or f"# 任务目标：{loop.goal_prompt}\n"
        if "## 执行计划 (DoD 驱动)" not in cur_pad:
            cur_pad = (
                f"# 任务目标：{loop.goal_prompt}\n\n"
                f"## 执行计划 (DoD 驱动)\n"
                f"- [x] 1. 目标与工作区上下文审查 (已就绪)\n"
                f"- [/] 2. 核心逻辑执行与测试用例验证 (第 {next_iter} 轮执行中)\n"
                f"{step_log_line}\n"
                f"- [ ] 3. 客观标准验收 (DoD 判定与代码格式校验)\n"
                f"- [ ] 4. 产出物归档到工作区资产库\n"
            )
        else:
            # 在步骤 2 下方插入最新执行行
            if "- [/] 2. 核心逻辑执行" in cur_pad:
                # 保持进行中并注入日志
                target_str = "- [/] 2. 核心逻辑执行与测试用例验证"
                replacement_str = f"- [/] 2. 核心逻辑执行与测试用例验证 (第 {next_iter} 轮推进中)\n{step_log_line}"
                # 如果已经带有轮次标记，做正则或安全替换
                lines = cur_pad.splitlines()
                new_lines = []
                inserted = False
                for line in lines:
                    if line.startswith("- [/] 2. 核心逻辑执行"):
                        new_lines.append(f"- [/] 2. 核心逻辑执行与测试用例验证 (第 {next_iter} 轮推进中)")
                        new_lines.append(step_log_line)
                        inserted = True
                    else:
                        new_lines.append(line)
                if not inserted:
                    new_lines.append(step_log_line)
                cur_pad = "\n".join(new_lines)
            else:
                cur_pad += f"\n{step_log_line}"

        await TaskLoopRepository.update_loop_status(
            loop_id=loop_id,
            status="executing",
            scratchpad_md=cur_pad,
            iteration=next_iter,
        )
        return await TaskLoopRepository.get_loop(loop_id)

    @staticmethod
    async def complete_loop(
        loop_id: str,
        summary: str = "模型已完成自主闭环并输出最终结论",
    ) -> TaskLoopResponse | None:
        """
        标记任务完成 (DoD 验收全部打勾通过并归档)
        """
        loop = await TaskLoopRepository.get_loop(loop_id)
        if not loop:
            return None

        cur_pad = loop.scratchpad_md or ""
        lines = cur_pad.splitlines()
        new_lines = []
        for line in lines:
            if line.startswith("- [/] 2.") or line.startswith("- [ ] 2."):
                new_lines.append(f"- [x] 2. 核心逻辑执行与测试用例验证 (已完成，共计 {loop.current_iteration} 轮)")
            elif line.startswith("- [ ] 3.") or line.startswith("- [/] 3."):
                new_lines.append(f"- [x] 3. 客观标准验收 (DoD 判定通过: {summary[:80]})")
            elif line.startswith("- [ ] 4.") or line.startswith("- [/] 4."):
                new_lines.append(f"- [x] 4. 产出物归档到工作区资产库 (验收完毕)")
            else:
                new_lines.append(line)

        final_pad = "\n".join(new_lines) if new_lines else (
            f"# 任务完成归档\n\n"
            f"- [x] 1. 目标与工作区上下文审查 (通过)\n"
            f"- [x] 2. 核心逻辑执行与测试用例验证 (通过)\n"
            f"- [x] 3. 客观标准验收 (DoD 判定通过: {summary[:80]})\n"
            f"- [x] 4. 产出物归档到工作区资产库 (已交付)\n"
        )

        await TaskLoopRepository.update_loop_status(
            loop_id=loop_id,
            status="completed",
            scratchpad_md=final_pad,
        )
        return await TaskLoopRepository.get_loop(loop_id)

    @staticmethod
    async def archive_loop(loop_id: str) -> bool:
        """
        人工或系统归档已交付或终止的任务 (状态置为 idle)
        """
        now = datetime.now().isoformat()
        async with db_manager.get_connection() as conn:
            await conn.execute(
                "UPDATE task_loop_runs SET loop_status = 'idle', updated_at = ? WHERE id = ?;",
                (now, loop_id),
            )
            await conn.commit()
        return True

    @staticmethod
    async def update_loop_status(
        loop_id: str,
        status: str,
        scratchpad_md: str | None = None,
        iteration: int | None = None,
        pending_approval: dict | None = None,
        error_fingerprints: list[str] | None = None,
    ) -> bool:
        """
        更新任务循环的状态、草稿记事板或拦截审批项
        """
        now = datetime.now().isoformat()
        fields = ["loop_status = ?", "updated_at = ?"]
        values = [status, now]

        if scratchpad_md is not None:
            fields.append("scratchpad_md = ?")
            values.append(scratchpad_md)

        if iteration is not None:
            fields.append("current_iteration = ?")
            values.append(iteration)

        if pending_approval is not None:
            fields.append("pending_approval_call = ?")
            values.append(json.dumps(pending_approval, ensure_ascii=False))
        elif status not in ("waiting_approval",):
            # 状态脱离 waiting_approval 时清空 pending_approval_call
            fields.append("pending_approval_call = NULL")

        if error_fingerprints is not None:
            fields.append("error_fingerprints = ?")
            values.append(json.dumps(error_fingerprints, ensure_ascii=False))

        values.append(loop_id)

        async with db_manager.get_connection() as conn:
            await conn.execute(
                f"UPDATE task_loop_runs SET {', '.join(fields)} WHERE id = ?;",
                tuple(values),
            )
            await conn.commit()
        return True

