"""
流式聊天核心接口 (SSE Stream Endpoint) 与多模态上传
"""
import os
import json
import uuid
import shutil
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse, FileResponse
from ...schema.chat_schema import ChatStreamRequest, ToolApprovalRequest, StopChatRequest
from ...agent.runtime import agent_runtime
from ...agent.shell_executor import shell_executor
from ...core.logging_config import logger

router = APIRouter(prefix="/chat", tags=["Chat"])

# 本地附件持久化目录
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
UPLOADS_DIR = BASE_DIR / "data" / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


@router.post("/upload")
async def upload_attachment(file: UploadFile = File(...)):
    """
    接收前端上传或粘贴的图片/文档，保存到本地 data/uploads 目录，
    返回本地真实物理绝对路径与前端访问静态 URL，供多模态模型无损读取。
    """
    try:
        ext = Path(file.filename or "image.png").suffix or ".png"
        unique_name = f"{uuid.uuid4().hex[:12]}_{file.filename}"
        save_path = UPLOADS_DIR / unique_name

        with save_path.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        file_size_kb = save_path.stat().st_size / 1024
        size_str = f"{file_size_kb:.1f} KB" if file_size_kb < 1024 else f"{file_size_kb / 1024:.2f} MB"

        return {
            "name": file.filename or unique_name,
            "file_path": str(save_path.resolve()),
            "url": f"/api/v1/chat/uploads/{unique_name}",
            "mime_type": file.content_type or "image/png",
            "size_str": size_str,
        }
    except Exception as e:
        logger.error(f"上传文件保存失败: {e}")
        raise HTTPException(status_code=500, detail=f"保存文件失败: {str(e)}")


@router.get("/uploads/{filename}")
async def get_uploaded_attachment(filename: str):
    """提供本地上传图片的静态预览服务"""
    file_path = UPLOADS_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="附件不存在")
    return FileResponse(file_path)


@router.post("/stream")
async def chat_stream(req: ChatStreamRequest):
    """
    发起 SSE 流式对话端点（由微内核 AgentRuntime 驱动：支持三层记忆、ReAct 工具闭环与多账号 429 自动故障转移）
    """
    async def event_generator():
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
        ):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

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
    强制中断指定会话正在执行的本地命令子进程树。
    """
    killed_count = shell_executor.cancel_session_processes(req.conversation_id)
    return {
        "status": "ok",
        "conversation_id": req.conversation_id,
        "killed_processes": killed_count,
    }
