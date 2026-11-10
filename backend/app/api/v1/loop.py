"""
任务自主循环 (Task Loop) 与三级审批接口路由
"""
from fastapi import APIRouter, HTTPException
import asyncio
from ...agent.loop_runner import TaskLoopRunner
from ...core.logging_config import logger
from ...extensions.skill_loader import skill_loader
from ...memory.ledger import event_ledger
from ...repository.conversation_repository import ConversationRepository
from ...repository.loop_run_repository import TaskLoopRepository
from ...repository.message_repository import MessageRepository
from ...repository.workspace_repository import WorkspaceRepository
from ...schema.loop_schema import (
    TaskLoopApprovalAction,
    TaskLoopControlAction,
    TaskLoopCreateRequest,
    TaskLoopResponse,
)
from ...skills.experience_distiller import (
    ExecutionTrace,
    experience_distiller,
    skill_experience_service,
)

router = APIRouter(prefix="/loop", tags=["Task Loop & HITL Gate"])


async def _record_loop_intervention(
    loop_id: str,
    action: str,
    detail: dict,
) -> None:
    """在非无痕会话下将用户暂停/纠偏/改参等介入行为追加至 event_ledger"""
    try:
        loop_data = await TaskLoopRepository.get_loop(loop_id)
        if not loop_data:
            return
        conv = await ConversationRepository.get_by_id(loop_data.conversation_id)
        if conv and bool(conv.incognito):
            return
        await event_ledger.append_event(
            workspace_id=loop_data.workspace_id,
            conversation_id=loop_data.conversation_id,
            turn_id=max(1, int(loop_data.current_iteration or 1)),
            loop_id=loop_id,
            event_type="user_intervention",
            actor="user",
            payload={"action": action, **detail},
            incognito=False,
        )
    except Exception as e:
        logger.debug(f"记录 Loop 人工介入账本跳过: {e}")


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
async def get_active_loop_by_workspace(
    workspace_id: str,
    include_completed: bool = True,
) -> TaskLoopResponse | None:
    """
    获取指定工作区中正在执行或最新交付的任务循环
    """
    return await TaskLoopRepository.get_active_loop_by_workspace(workspace_id, include_completed=include_completed)


@router.post("/{loop_id}/action")
async def handle_loop_action(
    loop_id: str,
    action_data: TaskLoopApprovalAction | TaskLoopControlAction,
) -> dict:
    """
    处理人工审批反馈 (approve_once, approve_all, modify_args, reject) 或控制指令 (pause, resume, abort, complete, archive)
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
        if result.get("success") and approval_req.action in ("modify_args", "reject"):
            await _record_loop_intervention(
                loop_id,
                approval_req.action,
                {
                    "guidance": approval_req.user_guidance or "",
                    "modified_arguments": approval_req.modified_arguments or {},
                },
            )
        return result

    # 2. 控制指令
    ctrl_action = getattr(action_data, "action", "")
    if ctrl_action == "pause":
        await TaskLoopRepository.update_loop_status(loop_id, "paused")
        await _record_loop_intervention(loop_id, "pause", {"guidance": "用户暂停自主执行进行人工检查"})
        return {"success": True, "action": "pause", "message": "循环已暂停"}
    elif ctrl_action == "resume":
        await TaskLoopRepository.update_loop_status(loop_id, "executing")
        await _record_loop_intervention(loop_id, "resume", {"guidance": "用户检查后恢复自主执行"})
        return {"success": True, "action": "resume", "message": "循环已恢复执行"}
    elif ctrl_action == "abort":
        await TaskLoopRepository.update_loop_status(loop_id, "failed")
        return {"success": True, "action": "abort", "message": "循环已被人工强行终止"}
    elif ctrl_action == "complete":
        await runner.step_verify(True, "用户人工确认任务圆满达成并验收通过")
        return {"success": True, "action": "complete", "message": "任务已顺利完成并归档"}
    elif ctrl_action == "archive":
        await TaskLoopRepository.archive_loop(loop_id)
        return {"success": True, "action": "archive", "message": "任务已归档收起"}

    raise HTTPException(status_code=400, detail="未知的操作类型")


@router.post("/{loop_id}/distill-skill")
async def distill_loop_to_skill(loop_id: str) -> dict:
    """
    从已执行/已交付的任务 Loop 轨迹（账本 + scratchpad.md）手动强触发经验提炼，
    生成一条 pending 状态的 experience_proposal 供前端预览 Diff 并确认沉淀为可复用 Skill。
    """
    loop_data = await TaskLoopRepository.get_loop(loop_id)
    if not loop_data:
        raise HTTPException(status_code=404, detail="未找到指定的任务循环")

    conv = await ConversationRepository.get_by_id(loop_data.conversation_id)
    if conv and bool(conv.incognito):
        raise HTTPException(status_code=400, detail="无痕模式下禁止沉淀会话执行经验")

    ws_root = await WorkspaceRepository.get_root_path_by_id(loop_data.workspace_id)
    events = await event_ledger.get_trace_for_distillation(
        loop_data.conversation_id,
        loop_id=loop_id,
        limit=150,
    )
    active_skills: list[str] = []
    recovered_cnt = 0
    had_intervention = False
    tool_steps = 0
    artifacts: list[str] = []

    for evt in events:
        etype = evt.get("event_type")
        evt_payload = evt.get("payload") if isinstance(evt.get("payload"), dict) else {}
        sid = str(evt.get("skill_id") or "").strip()
        if sid and sid not in active_skills:
            active_skills.append(sid)
        for p_sid in (evt_payload.get("active_skill_ids") or []):
            clean_p_sid = str(p_sid or "").strip()
            if clean_p_sid and clean_p_sid not in active_skills:
                active_skills.append(clean_p_sid)
        if etype == "tool_executed":
            tool_steps += 1
        elif etype == "tool_error_recovered":
            recovered_cnt += 1
        elif etype == "user_intervention":
            had_intervention = True
        elif etype == "artifact_produced":
            p = str(evt_payload.get("file_path") or "").strip()
            if p and p not in artifacts:
                artifacts.append(p)

    # 回溯 Loop 目标文案与同会话近期用户输入，确保多轮微调优化已有 Skill 时不会丢失关联目标
    if loop_data.goal_prompt:
        try:
            goal_matched = await asyncio.to_thread(
                skill_loader.resolve_slash_commands,
                loop_data.goal_prompt,
                ws_root,
            )
            for gm in goal_matched:
                g_sid = str(gm.get("id") or "").strip()
                if g_sid and g_sid not in active_skills:
                    active_skills.append(g_sid)
        except Exception:
            pass

    if not active_skills:
        try:
            recent_msgs = await MessageRepository.get_by_conversation(loop_data.conversation_id, limit=40)
            for m in reversed(recent_msgs):
                if getattr(m, "role", "") != "user":
                    continue
                msg_matched = await asyncio.to_thread(
                    skill_loader.resolve_slash_commands,
                    getattr(m, "content", "") or "",
                    ws_root,
                )
                for mm in msg_matched:
                    m_sid = str(mm.get("id") or "").strip()
                    if m_sid and m_sid not in active_skills:
                        active_skills.append(m_sid)
                if active_skills:
                    break
        except Exception as msg_err:
            logger.debug(f"回溯会话近期用户指令关联 Skill 跳过: {msg_err}")

    trace = ExecutionTrace(
        workspace_id=loop_data.workspace_id,
        conversation_id=loop_data.conversation_id,
        loop_id=loop_data.id,
        scene_mode=(conv.scene_mode if conv else "agent") or "agent",
        incognito=False,
        user_goal=loop_data.goal_prompt or "",
        active_skill_ids=active_skills,
        recovered_error_count=recovered_cnt,
        had_user_intervention=had_intervention,
        total_tool_steps=max(tool_steps, int(loop_data.current_iteration or 0)),
        circuit_breaker_triggered=(loop_data.loop_status == "failed"),
        final_success=True,
        final_summary=loop_data.scratchpad_md or "",
        produced_artifacts=artifacts,
        workspace_root=ws_root,
    )
    proposal = await experience_distiller.distill_and_propose(trace, force=True)
    if not proposal:
        raise HTTPException(status_code=400, detail="未能从当前任务轨迹中生成有效经验提议")
    proposal["available_skills"] = await asyncio.to_thread(
        skill_experience_service.list_available_target_skills,
        ws_root,
    )
    return {"success": True, "proposal": proposal}

