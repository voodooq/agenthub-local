"""
任务自主循环 (Task Loop) 与三级审批接口路由
"""
from fastapi import APIRouter, HTTPException
from ...agent.loop_runner import TaskLoopRunner
from ...core.logging_config import logger
from ...repository.loop_run_repository import TaskLoopRepository
from ...schema.loop_schema import (
    TaskLoopApprovalAction,
    TaskLoopControlAction,
    TaskLoopCreateRequest,
    TaskLoopResponse,
)

router = APIRouter(prefix="/loop", tags=["Task Loop & HITL Gate"])


@router.post("/start", response_model=TaskLoopResponse)
async def start_loop(req: TaskLoopCreateRequest) -> TaskLoopResponse:
    """
    发起一个新的长程自主循环任务
    """
    try:
        loop_resp = await TaskLoopRepository.create_loop(
            workspace_id=req.workspace_id,
            conversation_id=req.conversation_id,
            goal_prompt=req.goal_prompt,
            max_iterations=req.max_iterations,
        )
        runner = await TaskLoopRunner.load(loop_resp.id)
        if runner:
            await runner.step_plan(req.goal_prompt)
        
        updated = await TaskLoopRepository.get_loop(loop_resp.id)
        return updated or loop_resp
    except Exception as e:
        logger.error(f"启动任务循环异常: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{loop_id}", response_model=TaskLoopResponse)
async def get_loop_detail(loop_id: str) -> TaskLoopResponse:
    """
    获取指定任务循环的当前执行状态、DAG 记事板与审批拦截信息
    """
    loop_data = await TaskLoopRepository.get_loop(loop_id)
    if not loop_data:
        raise HTTPException(status_code=404, detail="未找到指定的任务循环")
    return loop_data


@router.get("/workspace/{workspace_id}/active", response_model=TaskLoopResponse | None)
async def get_active_loop_by_workspace(workspace_id: str) -> TaskLoopResponse | None:
    """
    获取指定工作区中正在执行或等待审批的活跃任务循环
    """
    return await TaskLoopRepository.get_active_loop_by_workspace(workspace_id)


@router.post("/{loop_id}/action")
async def handle_loop_action(
    loop_id: str,
    action_data: TaskLoopApprovalAction | TaskLoopControlAction,
) -> dict:
    """
    处理人工审批反馈 (approve_once, approve_all, modify_args, reject) 或控制指令 (pause, resume, abort)
    """
    runner = await TaskLoopRunner.load(loop_id)
    if not runner:
        raise HTTPException(status_code=404, detail="未找到指定的任务循环")

    # 1. 审批动作
    if hasattr(action_data, "action") and action_data.action in (
        "approve_once", "approve_all", "modify_args", "reject"
    ):
        approval_req: TaskLoopApprovalAction = action_data  # type: ignore
        result = await runner.handle_approval(
            action=approval_req.action,
            modified_args=approval_req.modified_arguments,
            guidance=approval_req.user_guidance,
        )
        return result

    # 2. 控制指令
    ctrl_action = getattr(action_data, "action", "")
    if ctrl_action == "pause":
        await TaskLoopRepository.update_loop_status(loop_id, "paused")
        return {"success": True, "action": "pause", "message": "循环已暂停"}
    elif ctrl_action == "resume":
        await TaskLoopRepository.update_loop_status(loop_id, "executing")
        return {"success": True, "action": "resume", "message": "循环已恢复执行"}
    elif ctrl_action == "abort":
        await TaskLoopRepository.update_loop_status(loop_id, "failed")
        return {"success": True, "action": "abort", "message": "循环已被人工强行终止"}
    elif ctrl_action == "complete":
        await runner.step_verify(True, "用户人工确认任务圆满达成并验收通过")
        return {"success": True, "action": "complete", "message": "任务已顺利完成并归档"}

    raise HTTPException(status_code=400, detail="未知的操作类型")

