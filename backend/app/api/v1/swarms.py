"""
原生蜂群协作接口 (Swarms API & SSE)
提供蜂群会话查询、消息板增量拉取、人工插话、单成员终止及实时 SSE 事件流。
"""
import asyncio
import json
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ...agent.swarm_manager import swarm_manager
from ...repository.swarm_repository import SwarmRepository

router = APIRouter(prefix="/swarms", tags=["swarms"])

SSE_HEARTBEAT_INTERVAL_SECONDS = 15.0


class SwarmPostMessageRequest(BaseModel):
    """向蜂群消息板发帖请求体（支持人工插话或指定发送者）"""

    text: str = Field(..., min_length=1, max_length=8000, description="消息正文，支持 @成员名 唤醒")
    sender: str = Field(default="user", max_length=100, description="发送者标识，默认 user")
    kind: str = Field(default="message", max_length=32, description="消息类型")


@router.get("/by-session/{session_id}")
async def get_swarm_by_session(session_id: str):
    """按主会话 ID 获取当前关联的 Swarm 及其成员和历史消息。"""
    swarm = await SwarmRepository.get_by_session(session_id)
    if not swarm:
        return {"swarm": None, "members": [], "messages": []}
    members = await SwarmRepository.list_members(swarm["id"])
    messages, _ = await SwarmRepository.read_messages(swarm["id"], since=0, limit=100)
    return {"swarm": swarm, "members": members, "messages": messages}


@router.get("/{swarm_id}")
async def get_swarm_detail(swarm_id: str):
    """按 swarm_id 获取蜂群详情、成员状态与消息板记录。"""
    swarm = await SwarmRepository.get_swarm(swarm_id)
    if not swarm:
        raise HTTPException(status_code=404, detail="Swarm 不存在")
    members = await SwarmRepository.list_members(swarm_id)
    messages, _ = await SwarmRepository.read_messages(swarm_id, since=0, limit=100)
    return {"swarm": swarm, "members": members, "messages": messages}


@router.get("/{swarm_id}/messages")
async def get_swarm_messages(
    swarm_id: str,
    since: int = Query(default=0, ge=0, description="增量拉取起始 seq"),
    limit: int = Query(default=50, ge=1, le=200, description="最多返回条数"),
):
    """增量拉取 Swarm 消息板消息与最新 head 水位。"""
    swarm = await SwarmRepository.get_swarm(swarm_id)
    if not swarm:
        raise HTTPException(status_code=404, detail="Swarm 不存在")
    messages, head = await SwarmRepository.read_messages(swarm_id, since=since, limit=limit)
    return {"messages": messages, "head": head}


@router.post("/{swarm_id}/messages")
async def post_swarm_message(swarm_id: str, req: SwarmPostMessageRequest):
    """向 Swarm 消息板发帖（人工插话可直接 @专家名 唤醒对应 Worker）。"""
    swarm = await SwarmRepository.get_swarm(swarm_id)
    if not swarm:
        raise HTTPException(status_code=404, detail="Swarm 不存在")
    msg = await swarm_manager.post_message(
        swarm_id,
        sender=req.sender.strip() or "user",
        text=req.text,
        kind=req.kind,
    )
    return {"message": msg, "head": msg["seq"]}


@router.post("/{swarm_id}/members/{name}/kill")
async def kill_swarm_member(swarm_id: str, name: str):
    """永久终止指定 Swarm 成员并级联清理其关联子进程。"""
    swarm = await SwarmRepository.get_swarm(swarm_id)
    if not swarm:
        raise HTTPException(status_code=404, detail="Swarm 不存在")
    member = await SwarmRepository.get_member(swarm_id, name)
    if not member:
        raise HTTPException(status_code=404, detail=f"Swarm 成员 `{name}` 不存在")
    killed = await swarm_manager.kill_member(swarm_id, name)
    return {"status": "ok", "member": killed}


@router.get("/{swarm_id}/stream")
async def stream_swarm_events(swarm_id: str, request: Request):
    """订阅指定 Swarm 的实时 SSE 事件流（swarm_update / swarm_message / agent_turn）。"""
    swarm = await SwarmRepository.get_swarm(swarm_id)
    if not swarm:
        raise HTTPException(status_code=404, detail="Swarm 不存在")

    async def event_generator():
        members = await SwarmRepository.list_members(swarm_id)
        init_payload = {
            "type": "swarm_update",
            "swarm_id": swarm_id,
            "swarm": swarm,
            "members": members,
        }
        yield f"data: {json.dumps(init_payload, ensure_ascii=False)}\n\n"

        sub_gen = swarm_manager.subscribe(swarm_id)
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(
                        sub_gen.__anext__(),
                        timeout=SSE_HEARTBEAT_INTERVAL_SECONDS,
                    )
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'type': 'heartbeat'}, ensure_ascii=False)}\n\n"
                    continue
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            await sub_gen.aclose()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
