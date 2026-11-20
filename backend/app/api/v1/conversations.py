"""
会话与消息管理接口
"""
import html
import json
import secrets
import uuid
from datetime import datetime
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from ...repository.db import db_manager
from ...schema.conversation_schema import (
    BranchConversationRequest,
    ConversationCreate,
    ConversationUpdate,
    MessageResponse,
)
from ...repository.conversation_repository import ConversationRepository
from ...repository.message_repository import MessageRepository
from ...repository.token_usage_repository import TokenUsageRepository
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
async def list_conversations(
    workspace_id: str | None = None,
    all_workspaces: bool = Query(default=False, description="是否返回全部工作区下的会话供左侧项目树渲染"),
):
    """获取会话列表，支持按工作区过滤或返回全量工作区会话"""
    return await ConversationRepository.get_all(
        workspace_id=workspace_id,
        include_all_workspaces=all_workspaces,
    )


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


@router.get("/share/{token}", response_class=HTMLResponse)
async def view_shared_conversation(token: str):
    """只读公开访问已生成的会话分享快照页面"""
    async with db_manager.get_connection() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS conversation_shares (
                token TEXT PRIMARY KEY,
                conversation_id TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                snapshot_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        cursor = await conn.execute(
            "SELECT title, snapshot_json, created_at FROM conversation_shares WHERE token = ?;",
            (token,),
        )
        row = await cursor.fetchone()
    if not row:
        return HTMLResponse(
            content="<h3>分享链接不存在或已被撤销</h3>",
            status_code=404,
        )

    title_raw, snapshot_json, created_at = row[0], row[1], row[2]
    safe_title = html.escape(str(title_raw or "AgentHub 对话分享"))
    try:
        items = json.loads(snapshot_json or "[]")
    except Exception:
        items = []

    blocks: list[str] = []
    for item in items:
        role = item.get("role", "")
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        is_user = role == "user"
        safe_body = html.escape(content).replace("\n", "<br/>")
        blocks.append(
            f'<section class="msg {"msg-user" if is_user else "msg-ai"}">'
            f'<div class="role">{"提问" if is_user else "AgentHub"}</div>'
            f'<div class="body">{safe_body}</div></section>'
        )

    html_doc = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>{safe_title}</title>
  <style>
    :root {{ color-scheme: light dark; }}
    body {{ margin: 0; padding: 32px 16px; font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif; background: #131314; color: #e6ece9; line-height: 1.7; }}
    @media (prefers-color-scheme: light) {{ body {{ background: #f8fafc; color: #1f2421; }} .msg-user {{ background: #e8f5f0 !important; color: #064e3b !important; }} .meta {{ color: #64748b !important; }} }}
    .wrap {{ max-width: 780px; margin: 0 auto; }}
    h1 {{ font-size: 20px; margin: 0 0 6px; font-weight: 600; }}
    .meta {{ font-size: 12px; color: #8c9b95; margin-bottom: 28px; }}
    .msg {{ margin-bottom: 20px; }}
    .msg-user {{ background: #282a2c; padding: 12px 16px; border-radius: 16px; margin-left: auto; max-width: 85%; }}
    .msg-ai {{ padding: 4px 0; }}
    .role {{ font-size: 11px; opacity: 0.65; margin-bottom: 4px; font-weight: 600; }}
    .body {{ word-break: break-word; font-size: 15px; }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>{safe_title}</h1>
    <div class="meta">只读分享快照 · 创建于 {html.escape(str(created_at)[:19])}</div>
    {"".join(blocks) or "<p>暂无可展示的对话内容。</p>"}
  </div>
</body>
</html>"""
    return HTMLResponse(content=html_doc)


@router.get("/{conv_id}/share")
async def get_conversation_share_status(conv_id: str):
    """查询指定会话是否已生成只读分享链接"""
    async with db_manager.get_connection() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS conversation_shares (
                token TEXT PRIMARY KEY,
                conversation_id TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                snapshot_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        cursor = await conn.execute(
            "SELECT token, created_at FROM conversation_shares WHERE conversation_id = ?;",
            (conv_id,),
        )
        row = await cursor.fetchone()
    if not row:
        return {"token": None}
    return {"token": row[0], "created_at": row[1]}


@router.post("/{conv_id}/share")
async def create_conversation_share(conv_id: str):
    """为指定会话生成或更新只读分享快照链接"""
    conv = await ConversationRepository.get_by_id(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="会话不存在")
    messages = await MessageRepository.get_by_conversation(conv_id)
    snapshot = [
        {"role": m.role, "content": m.content}
        for m in messages
        if m.role in ("user", "model", "assistant") and (m.content or "").strip()
    ]
    token = secrets.token_urlsafe(12)
    now = datetime.now().isoformat()

    async with db_manager.get_connection() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS conversation_shares (
                token TEXT PRIMARY KEY,
                conversation_id TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                snapshot_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        await conn.execute(
            "DELETE FROM conversation_shares WHERE conversation_id = ?;",
            (conv_id,),
        )
        await conn.execute(
            "INSERT INTO conversation_shares (token, conversation_id, title, snapshot_json, created_at) VALUES (?, ?, ?, ?, ?);",
            (token, conv_id, conv.title or "AgentHub 对话分享", json.dumps(snapshot, ensure_ascii=False), now),
        )
        await conn.commit()

    return {"token": token, "share_url": f"/api/v1/conversations/share/{token}"}


@router.delete("/{conv_id}/share")
async def revoke_conversation_share(conv_id: str):
    """撤销指定会话的只读分享链接"""
    async with db_manager.get_connection() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS conversation_shares (
                token TEXT PRIMARY KEY,
                conversation_id TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                snapshot_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        await conn.execute(
            "DELETE FROM conversation_shares WHERE conversation_id = ?;",
            (conv_id,),
        )
        await conn.commit()
    return {"revoked": True}


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
        "parent_conversation_id": getattr(conv, "parent_conversation_id", None),
        "branched_from_message_id": getattr(conv, "branched_from_message_id", None),
        "branched_from_turn_id": getattr(conv, "branched_from_turn_id", None),
        "updated_at": conv.updated_at,
    }


@router.post("/{conv_id}/branch")
async def branch_conversation(conv_id: str, req: BranchConversationRequest | None = None):
    """从当前会话的指定消息或轮次创建分叉分支会话"""
    body = req or BranchConversationRequest()
    new_conv_id = str(uuid.uuid4())
    branched = await ConversationRepository.branch_conversation(
        source_conv_id=conv_id,
        new_conv_id=new_conv_id,
        up_to_message_id=body.up_to_message_id,
        up_to_turn_id=body.up_to_turn_id,
        custom_title=body.title,
    )
    if not branched:
        raise HTTPException(status_code=404, detail="源会话不存在，无法创建分支")
    return branched


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
async def get_conversation_messages(
    conv_id: str,
    limit: int | None = Query(default=None, ge=1, description="可选截取最近 N 条消息；默认返回完整会话历史"),
):
    """获取会话历史消息列表（批量回显每轮末条 AI 回复的持久化 Token 水表）"""
    messages = await MessageRepository.get_by_conversation(conv_id, limit=limit)
    turn_stats_map = await TokenUsageRepository.get_turn_summaries_map(conv_id)

    # 定位每个 turn_id 最后一条 AI 交付消息的索引位置，避免单轮多步重复显示多个水表
    last_model_idx_by_turn: dict[int, int] = {}
    for idx, m in enumerate(messages):
        if m.role in ("model", "assistant"):
            last_model_idx_by_turn[m.turn_id or 1] = idx

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
            token_stats=(
                turn_stats_map.get(m.turn_id or 1)
                if last_model_idx_by_turn.get(m.turn_id or 1) == idx
                else None
            ),
        )
        for idx, m in enumerate(messages)
    ]


@router.get("/{conv_id}/token-stats")
async def get_conversation_token_stats(conv_id: str):
    """获取当前会话完整 Token 消耗、KV Cache 命中率、省流统计与逐轮性能趋势（服务 /stats 看板）"""
    conv = await ConversationRepository.get_by_id(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="会话不存在")
    return await TokenUsageRepository.get_conversation_detailed_stats(conv_id)


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
