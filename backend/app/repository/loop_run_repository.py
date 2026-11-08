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
        初始化创建一条任务循环记录
        """
        loop_id = f"loop_{uuid.uuid4().hex[:12]}"
        now = datetime.now().isoformat()
        initial_scratchpad = f"# 任务目标：{goal_prompt}\n\n- [ ] 步骤 1: 需求拆解与环境探测\n"

        async with db_manager.get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO task_loop_runs (
                    id, workspace_id, conversation_id, goal_prompt, scratchpad_md,
                    loop_status, current_iteration, max_iterations, pending_approval_call,
                    error_fingerprints, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'planning', 0, ?, NULL, '[]', ?, ?);
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
            loop_status="planning",
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
    async def get_active_loop_by_workspace(workspace_id: str) -> TaskLoopResponse | None:
        """
        获取当前工作区中正在运行或等待审批的任务循环
        """
        async with db_manager.get_connection() as conn:
            async with conn.execute(
                """
                SELECT id, workspace_id, conversation_id, goal_prompt, scratchpad_md,
                       loop_status, current_iteration, max_iterations, pending_approval_call,
                       error_fingerprints, created_at, updated_at
                FROM task_loop_runs 
                WHERE workspace_id = ? AND loop_status IN ('planning', 'executing', 'verifying', 'reflecting', 'waiting_approval')
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

