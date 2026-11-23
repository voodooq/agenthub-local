"""
流式聊天核心接口 (SSE Stream Endpoint) 与多模态上传
"""
import asyncio
import json
import uuid
from pathlib import Path
from typing import Any
from fastapi import APIRouter, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import StreamingResponse, FileResponse
from ...schema.chat_schema import ChatStreamRequest, ToolApprovalRequest, StopChatRequest
from ...agent.runtime import agent_runtime
from ...agent.shell_executor import shell_executor
from ...agent.swarm_manager import swarm_manager
from ...repository.agent_repository import AgentProfileRepository
from ...repository.conversation_repository import ConversationRepository
from ...repository.swarm_repository import SwarmRepository
from ...repository.workspace_repository import WorkspaceRepository
from ...core.config import settings
from ...core.logging_config import logger
from ...search.hype_worker import ForegroundChatTracker
from ...service.attachment_service import attachment_service, AttachmentValidationError

router = APIRouter(prefix="/chat", tags=["Chat"])

# NOTE: 统一锚定至 settings.db_path 同级目录，确保源码模式与 PyInstaller 冻结运行包路径一致
_DEFAULT_UPLOADS_DIR = Path(settings.db_path).parent / "uploads"
UPLOADS_DIR = _DEFAULT_UPLOADS_DIR
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


def get_uploads_dir() -> Path:
    """获取当前有效的附件存储目录（若运行时动态覆盖了 settings.db_path 或模块常量则自动适配）"""
    target = UPLOADS_DIR if UPLOADS_DIR != _DEFAULT_UPLOADS_DIR else (Path(settings.db_path).parent / "uploads")
    target.mkdir(parents=True, exist_ok=True)
    return target


@router.get("/upload-rules")
async def get_upload_rules():
    """返回当前支持的附件格式白名单、大小上限与单条消息数量限制"""
    return attachment_service.build_rules_summary()


@router.post("/upload")
async def upload_attachment(
    file: UploadFile = File(...),
    workspace_id: str | None = Form(default="default"),
):
    """
    接收前端上传或粘贴的图片/文档，经白名单、体积与文件头魔数校验后直接物理落盘至目标工作区沙盒附件目录，
    自动保留干净原名、内容去重并登记资产库，返回真实绝对路径、工作区相对路径与静态访问 URL。
    """
    raw_name = file.filename or "attachment.png"
    safe_name = attachment_service.sanitize_filename(raw_name)
    ext, category, _ = attachment_service.classify_extension(safe_name)
    if not category:
        raise HTTPException(
            status_code=400,
            detail=attachment_service.build_unsupported_reason(safe_name, ext),
        )

    max_bytes, limit_label = attachment_service.get_max_bytes_for_category(category)

    try:
        # 分块流式读取并提前拦截超大请求体，防止内存峰值溢出
        chunks: list[bytes] = []
        total_read = 0
        chunk_size = 64 * 1024
        while True:
            chunk = await file.read(chunk_size)
            if not chunk:
                break
            total_read += len(chunk)
            if total_read > max_bytes:
                actual_mb = total_read / (1024 * 1024)
                raise HTTPException(
                    status_code=413,
                    detail=f"文件「{safe_name}」体积超过 {limit_label} 上限（已接收超过 {actual_mb:.1f} MB），已终止上传。",
                )
            chunks.append(chunk)

        raw_bytes = b"".join(chunks)
        meta = attachment_service.validate_upload(
            filename=safe_name,
            raw_bytes=raw_bytes,
            content_type=file.content_type,
        )

        saved = await attachment_service.save_bytes_to_workspace_attachments(
            workspace_id=workspace_id,
            safe_name=meta["safe_name"],
            raw_bytes=raw_bytes,
            register_artifact=True,
        )

        return {
            "name": saved["stored_name"],
            "original_name": meta["safe_name"],
            "file_path": saved["file_path"],
            "rel_path": saved["rel_path"],
            "url": saved["url"],
            "workspace_id": saved["workspace_id"],
            "mime_type": meta["mime_type"],
            "size_str": meta["size_str"],
            "size_bytes": meta["size_bytes"],
            "category": meta["category"],
        }
    except AttachmentValidationError as val_err:
        raise HTTPException(status_code=val_err.status_code, detail=val_err.message)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"上传文件保存失败: {e}")
        raise HTTPException(status_code=500, detail=f"保存文件失败: {str(e)}")


@router.get("/uploads/ws/{workspace_id}/{filename}")
async def get_workspace_uploaded_attachment(workspace_id: str, filename: str):
    """提供工作区沙盒附件的静态下载与预览服务（严格防目录穿越）"""
    file_path = await attachment_service.resolve_workspace_attachment_file(workspace_id, filename)
    if not file_path:
        raise HTTPException(status_code=404, detail="工作区沙盒中未找到该附件")
    return FileResponse(file_path)


@router.get("/uploads/{filename}")
async def get_uploaded_attachment(filename: str):
    """兼容历史全局 uploads 目录与遗留 backend/data/uploads 目录的静态预览服务"""
    safe_filename = Path(filename).name
    if not safe_filename or safe_filename != filename or safe_filename in (".", ".."):
        raise HTTPException(status_code=400, detail="非法文件名")

    candidates = [
        get_uploads_dir() / safe_filename,
        Path(__file__).resolve().parent.parent.parent.parent / "data" / "uploads" / safe_filename,
        Path(__file__).resolve().parent.parent.parent / "data" / "uploads" / safe_filename,
    ]
    for cand in candidates:
        if cand.exists() and cand.is_file():
            return FileResponse(cand)
    raise HTTPException(status_code=404, detail="附件不存在")


# SSE 等待上游期间的保活心跳周期（需远小于前端 90s idle_timeout 看门狗）
SSE_HEARTBEAT_INTERVAL_SECONDS = 15.0


@router.post("/stream")
async def chat_stream(req: ChatStreamRequest, request: Request):
    """
    发起 SSE 流式对话端点（由微内核 AgentRuntime 驱动：支持三层记忆、ReAct 工具闭环与多账号 429 自动故障转移）
    """
    async def event_generator():
        from ...schema.intent_router import FollowupStreamBuffer

        ForegroundChatTracker.enter()
        completed = False
        active_conv_id = req.conversation_id
        queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        followup_stream_buf = FollowupStreamBuffer(hold_chars=32)

        stripped_msg = (req.message or "").strip()
        is_slash_swarm = stripped_msg.lower().startswith("/swarm")
        effective_msg = stripped_msg[6:].strip() if is_slash_swarm else req.message
        if is_slash_swarm and not effective_msg:
            effective_msg = "请统筹协调蜂群成员完成当前任务。"

        has_target_agents = bool(req.target_agent_ids and len(req.target_agent_ids) > 0)
        # 严格门控：仅在显式开启 swarm_mode 或输入 /swarm 指令时才进入多智能体蜂群协作编排
        # 常规单专家问答（无论是在沙盒发问还是使用 @专家名）一律以目标专家身份在当前会话直连推理，确保答复始终在发问正下方
        use_swarm = bool(req.swarm_mode or is_slash_swarm)

        async def _producer() -> None:
            swarm_sub_task: asyncio.Task[None] | None = None
            try:
                if not use_swarm:
                    async for event in agent_runtime.chat_stream(
                        conversation_id=req.conversation_id,
                        user_message=req.message,
                        model_name=req.model_name,
                        thinking_level=req.thinking_level,
                        system_override=req.system_override,
                        temperature=req.temperature,
                        context_rounds=req.context_rounds,
                        attachments=req.attachments,
                        scene_mode=req.scene_mode,
                        incognito=req.incognito,
                        workspace_id=req.workspace_id,
                        execution_strategy=req.execution_strategy,
                        clarify_mode=req.clarify_mode,
                        approved_plan_steps=req.approved_plan_steps,
                        confirmed_facts=req.confirmed_facts,
                        inquiry_confirmed=req.inquiry_confirmed,
                        inquiry_action=req.inquiry_action,
                        is_disconnected=request.is_disconnected,
                        agent_id=req.agent_id or (req.target_agent_ids[0] if req.target_agent_ids else None),
                        grounding_config=req.grounding_config,
                        is_retry=req.is_retry,
                        force_live=req.force_live,
                        caller_channel=req.caller_channel,
                        simulated_role=req.simulated_role,
                        simulated_scope=req.simulated_scope,
                    ):
                        await queue.put(("event", event))
                    await queue.put(("done", None))
                    return

                # ── Swarm 统一收口模式（支持 /swarm 蜂群编排与 @专家名 轻量点名） ──
                conv_id = req.conversation_id or str(uuid.uuid4())
                is_new_conv = False
                ws_id = req.workspace_id or "default"
                target_cwd: str | None = None
                try:
                    ws = await WorkspaceRepository.get_by_id(ws_id)
                    if ws and ws.get("root_path"):
                        target_cwd = ws["root_path"]
                except Exception:
                    pass

                existing_conv = await ConversationRepository.get_by_id(conv_id)
                if not existing_conv:
                    is_new_conv = True
                    await ConversationRepository.create({
                        "id": conv_id,
                        "title": (effective_msg or "Swarm 协作").split("\n")[0][:24],
                        "model_name": req.model_name,
                        "scene_mode": req.scene_mode or "agent",
                        "incognito": 1 if req.incognito else 0,
                        "workspace_id": ws_id,
                        "current_cwd": target_cwd,
                    })

                swarm = await swarm_manager.get_or_create_swarm(
                    session_id=conv_id,
                    swarm_id=req.swarm_id,
                    workspace_id=ws_id,
                    cwd=target_cwd,
                    default_model=req.model_name,
                )
                eff_swarm_id = swarm["id"]

                await queue.put((
                    "event",
                    {
                        "type": "conv_init",
                        "conversation_id": conv_id,
                        "is_new": is_new_conv,
                        "swarm_id": eff_swarm_id,
                    },
                ))
                await queue.put((
                    "event",
                    {
                        "type": "swarm_update",
                        "swarm_id": eff_swarm_id,
                        "swarm": swarm,
                        "members": await SwarmRepository.list_members(eff_swarm_id),
                    },
                ))

                # 订阅蜂群实时事件并合流到当前 SSE 队列
                active_worker_names: set[str] = set()
                finished_worker_names: set[str] = set()
                workers_done_event = asyncio.Event()
                worker_reply_chunks: list[str] = []

                async def _forward_swarm_events() -> None:
                    sub = swarm_manager.subscribe(eff_swarm_id)
                    try:
                        async for sw_evt in sub:
                            await queue.put(("event", sw_evt))
                            if sw_evt.get("type") == "agent_turn":
                                inner = sw_evt.get("event") or {}
                                m_name = str(sw_evt.get("member_name") or "")
                                in_type = inner.get("type")
                                # 若是点名专家直通模式，将 Worker 的思考过程与文本内容实时回传给前端主会话
                                if not (req.swarm_mode or is_slash_swarm) and in_type in ("content", "thought", "status", "tool_call", "tool_result"):
                                    if in_type == "content" and inner.get("text"):
                                        worker_reply_chunks.append(inner["text"])
                                    await queue.put(("event", inner))
                                if in_type in ("done", "error") and m_name in active_worker_names:
                                    finished_worker_names.add(m_name)
                                    if active_worker_names and active_worker_names.issubset(finished_worker_names):
                                        workers_done_event.set()
                    finally:
                        await sub.aclose()

                swarm_sub_task = asyncio.create_task(_forward_swarm_events())

                # 若传入 target_agent_ids，直接注册并唤醒对应专家 Worker
                if has_target_agents:
                    mention_tags: list[str] = []
                    for aid in req.target_agent_ids or []:
                        profile = await AgentProfileRepository.get_agent(aid)
                        m_name = aid
                        if profile and profile.get("name"):
                            # 使用稳定无空格标识作为成员名，兼容 @mention 解析
                            m_name = aid
                        active_worker_names.add(m_name)
                        await swarm_manager.add_member(
                            eff_swarm_id,
                            name=m_name,
                            brief="",
                            role="worker",
                            agent_id=aid,
                            workspace_id=ws_id,
                            cwd=target_cwd,
                        )
                        mention_tags.append(f"@{m_name}")

                    await swarm_manager.post_message(
                        eff_swarm_id,
                        sender="user",
                        text=f"{' '.join(mention_tags)} {effective_msg}".strip(),
                        kind="message",
                    )

                # 若同时开启了 swarm_mode（或 /swarm）且未仅限单次点名，由 Coordinator 主控运行
                if req.swarm_mode or is_slash_swarm or not has_target_agents:
                    async for event in agent_runtime.chat_stream(
                        conversation_id=conv_id,
                        user_message=effective_msg,
                        model_name=req.model_name,
                        thinking_level=req.thinking_level,
                        system_override=req.system_override,
                        temperature=req.temperature,
                        context_rounds=req.context_rounds,
                        attachments=req.attachments,
                        scene_mode=req.scene_mode or "agent",
                        incognito=req.incognito,
                        workspace_id=ws_id,
                        execution_strategy=req.execution_strategy,
                        clarify_mode=req.clarify_mode,
                        approved_plan_steps=req.approved_plan_steps,
                        confirmed_facts=req.confirmed_facts,
                        inquiry_confirmed=req.inquiry_confirmed,
                        inquiry_action=req.inquiry_action,
                        is_disconnected=request.is_disconnected,
                        swarm_mode=True,
                        swarm_id=eff_swarm_id,
                        swarm_role="coordinator",
                    ):
                        await queue.put(("event", event))
                else:
                    # 轻量 @专家点名直通模式：等待被点名专家 Worker 完成当前轮次
                    try:
                        await asyncio.wait_for(workers_done_event.wait(), timeout=300.0)
                    except asyncio.TimeoutError:
                        pass
                    # 将 Worker 生成的完整回复保存到主会话中，确保主会话发问正下方历史可查
                    if worker_reply_chunks:
                        full_reply = "".join(worker_reply_chunks)
                        max_turn = await MessageRepository.get_max_turn_id(conv_id)
                        await MessageRepository.save_message(
                            conversation_id=conv_id,
                            role="assistant",
                            content=full_reply,
                            turn_id=max_turn,
                            model_name=req.model_name or "swarm",
                        )
                    await queue.put(("event", {"type": "done", "swarm_id": eff_swarm_id}))

                await queue.put(("done", None))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await queue.put(("error", exc))
            finally:
                if swarm_sub_task and not swarm_sub_task.done():
                    swarm_sub_task.cancel()
                    try:
                        await swarm_sub_task
                    except BaseException:
                        pass

        producer_task = asyncio.create_task(_producer())
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    kind, payload = await asyncio.wait_for(
                        queue.get(),
                        timeout=SSE_HEARTBEAT_INTERVAL_SECONDS,
                    )
                except asyncio.TimeoutError:
                    if producer_task.done() and queue.empty():
                        break
                    # NOTE: 周期性推送轻量心跳帧，防止慢工具或长推理等待超过前端 90s 无数据看门狗触发误杀
                    yield f"data: {json.dumps({'type': 'heartbeat'}, ensure_ascii=False)}\n\n"
                    continue

                if kind == "done":
                    completed = True
                    break
                if kind == "error":
                    raise payload

                event = payload
                if event.get("type") == "conv_init" and event.get("conversation_id"):
                    active_conv_id = str(event["conversation_id"])
                # 流式尾部滑窗缓冲：杜绝 <!-- FOLLOWUP_SUGGESTIONS: [...] --> 闪烁给前端
                ev_type = event.get("type")
                if ev_type == "chunk" and isinstance(event.get("content"), str):
                    safe_chunk = followup_stream_buf.feed(event["content"])
                    if not safe_chunk:
                        continue
                    event = {**event, "content": safe_chunk}
                elif ev_type in ("start", "stream_reset", "done"):
                    flushed_text, _ = followup_stream_buf.flush()
                    if flushed_text and ev_type == "done":
                        yield f"data: {json.dumps({'type': 'chunk', 'content': flushed_text}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            ForegroundChatTracker.exit()
            if not producer_task.done():
                producer_task.cancel()
                try:
                    await producer_task
                except BaseException:
                    pass
            # 客户端非正常断连或生成器提前关闭时，触发后端执行流与遗留子进程兜底清理
            if not completed and active_conv_id:
                agent_runtime.cancel_session_stream(active_conv_id)
                shell_executor.cancel_session_processes(active_conv_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@router.post("/tool-approval")
async def resolve_tool_approval(req: ToolApprovalRequest):
    """
    接收前端用户对灰名单终端命令的审批决定（允许 / 拒绝），唤醒挂起的 Agent 协程。
    """
    resolved = shell_executor.resolve_approval(req.call_id, req.approved)
    return {
        "status": "ok",
        "call_id": req.call_id,
        "approved": req.approved,
        "resolved": resolved,
    }


@router.post("/stop")
async def stop_chat_execution(req: StopChatRequest):
    """
    强制中断指定会话正在进行的流式生成、CLI 子进程及本地命令子进程树。
    """
    stream_cancelled = agent_runtime.cancel_session_stream(req.conversation_id)
    killed_count = shell_executor.cancel_session_processes(req.conversation_id)
    return {
        "status": "ok",
        "conversation_id": req.conversation_id,
        "stream_cancelled": stream_cancelled,
        "killed_processes": killed_count,
    }
