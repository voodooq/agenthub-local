"""
流式聊天核心接口 (SSE Stream Endpoint) 与多模态上传
"""
import json
import uuid
from pathlib import Path
from fastapi import APIRouter, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import StreamingResponse, FileResponse
from ...schema.chat_schema import ChatStreamRequest, ToolApprovalRequest, StopChatRequest
from ...agent.runtime import agent_runtime
from ...agent.shell_executor import shell_executor
from ...core.config import settings
from ...core.logging_config import logger
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


@router.post("/stream")
async def chat_stream(req: ChatStreamRequest, request: Request):
    """
    发起 SSE 流式对话端点（由微内核 AgentRuntime 驱动：支持三层记忆、ReAct 工具闭环与多账号 429 自动故障转移）
    """
    async def event_generator():
        completed = False
        active_conv_id = req.conversation_id
        try:
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
            ):
                if event.get("type") == "conv_init" and event.get("conversation_id"):
                    active_conv_id = str(event["conversation_id"])
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            completed = True
        finally:
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
