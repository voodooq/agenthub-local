"""
会话与消息管理接口
"""
import json
import uuid
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from ...schema.conversation_schema import ConversationCreate, ConversationUpdate, MessageResponse
from ...repository.conversation_repository import ConversationRepository
from ...repository.message_repository import MessageRepository
from ...repository.workspace_repository import WorkspaceRepository

router = APIRouter(prefix="/conversations", tags=["Conversations"])


class AgentStateUpdateRequest(BaseModel):
    """更新会话的 Agent 状态（场景模式、无痕开关、工作草稿纸、CWD）"""
    scene_mode: str | None = None
    incognito: bool | None = None
    working_memory_json: str | None = None
    clear_working_memory: bool = False
    current_cwd: str | None = None
    workspace_id: str | None = None


@router.get("")
async def list_conversations(workspace_id: str | None = None):
    """获取会话列表，支持按工作区过滤"""
    return await ConversationRepository.get_all(workspace_id=workspace_id)


@router.post("")
async def create_conversation(req: ConversationCreate):
    """创建新会话，自动绑定工作区与 CWD"""
    conv_id = str(uuid.uuid4())
    target_cwd = None
    if req.workspace_id:
        ws = await WorkspaceRepository.get_by_id(req.workspace_id)
        if ws and ws.get("root_path"):
            target_cwd = ws["root_path"]

    await ConversationRepository.create({
        "id": conv_id,
        "title": req.title,
        "model_name": req.model_name,
        "workspace_id": req.workspace_id or "default",
        "current_cwd": target_cwd,
    })
    return {"id": conv_id, "title": req.title, "workspace_id": req.workspace_id or "default"}


@router.get("/{conv_id}")
async def get_conversation_detail(conv_id: str):
    """获取单个会话详情（含场景模式、无痕状态与工作草稿纸）"""
    conv = await ConversationRepository.get_by_id(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="会话不存在")
    try:
        wm = json.loads(conv.working_memory_json or "{}")
    except Exception:
        wm = {}
    return {
        "id": conv.id,
        "title": conv.title,
        "model_name": conv.model_name,
        "scene_mode": conv.scene_mode,
        "incognito": bool(conv.incognito),
        "working_memory": wm,
        "current_cwd": conv.current_cwd,
        "workspace_id": getattr(conv, "workspace_id", "default"),
        "updated_at": conv.updated_at,
    }


@router.put("/{conv_id}/agent-state")
async def update_conversation_agent_state(conv_id: str, req: AgentStateUpdateRequest):
    """更新会话的 Agent 状态（切换场景模式、无痕模式或一键清空工作草稿纸）"""
    wm_json = "{}" if req.clear_working_memory else req.working_memory_json
    await ConversationRepository.update_agent_state(
        conv_id=conv_id,
        scene_mode=req.scene_mode,
        incognito=req.incognito,
        working_memory_json=wm_json,
        current_cwd=req.current_cwd,
    )
    conv = await ConversationRepository.get_by_id(conv_id)
    try:
        wm = json.loads(conv.working_memory_json or "{}") if conv else {}
    except Exception:
        wm = {}
    return {
        "status": "ok",
        "scene_mode": conv.scene_mode if conv else req.scene_mode,
        "incognito": bool(conv.incognito) if conv else bool(req.incognito),
        "working_memory": wm,
        "current_cwd": conv.current_cwd if conv else req.current_cwd,
    }


@router.get("/{conv_id}/messages", response_model=list[MessageResponse])
async def get_conversation_messages(conv_id: str):
    """获取会话历史消息列表"""
    messages = await MessageRepository.get_by_conversation(conv_id)
    return [
        MessageResponse(
            id=m.id or 0,
            conversation_id=m.conversation_id,
            role=m.role,
            content=m.content,
            executed_by_account=m.executed_by_account,
            token_count=m.token_count,
            created_at=m.created_at,
            turn_id=m.turn_id or 1,
            compressed_content=m.compressed_content,
            tool_calls_json=m.tool_calls_json,
            tool_call_id=m.tool_call_id,
        )
        for m in messages
    ]


@router.post("/cleanup-duplicates")
async def cleanup_duplicate_conversations():
    """智能清理重复标题会话与消息为空的冗余会话"""
    cleaned_count = await ConversationRepository.cleanup_duplicates()
    return {"message": f"成功清理 {cleaned_count} 个冗余或重复会话", "cleaned_count": cleaned_count}


@router.patch("/{conv_id}")
async def update_conversation(conv_id: str, req: ConversationUpdate):
    """重命名、置顶或移动会话到其他工作区"""
    await ConversationRepository.update(conv_id, title=req.title, is_pinned=req.is_pinned)
    if req.workspace_id:
        await ConversationRepository.move_to_workspace(conv_id, req.workspace_id)
    return {"message": "更新成功"}


@router.delete("/{conv_id}")
async def delete_conversation(conv_id: str):
    """删除会话"""
    success = await ConversationRepository.delete(conv_id)
    if not success:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"message": "删除成功"}
