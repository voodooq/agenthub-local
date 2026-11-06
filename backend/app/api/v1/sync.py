"""
历史数据同步接口
"""
from fastapi import APIRouter, UploadFile, File, Body
from ...service.sync_service import sync_service

router = APIRouter(prefix="/sync", tags=["Sync"])


@router.post("/takeout")
async def import_takeout(file: UploadFile = File(...)):
    """上传并导入 Google Takeout MyActivity.json 文件"""
    content = await file.read()
    return await sync_service.import_takeout_json(content.decode("utf-8", errors="ignore"))


@router.post("/share-link")
async def clone_share_link(url: str = Body(..., embed=True)):
    """解析公开分享链接 g.co/gemini/share/xxx 并克隆为本地会话"""
    conv_id = await sync_service.clone_share_link(url)
    return {"message": "克隆成功", "conversation_id": conv_id}


@router.post("/web")
async def sync_web_bookmarklet(payload: dict = Body(...)):
    """接收浏览器油猴或书签脚本直接推送的当前对话数据"""
    title = payload.get("title", "来自网页端书签同步")
    messages = payload.get("messages", [])
    
    # 动态入库
    import uuid
    from ...repository.conversation_repository import ConversationRepository
    from ...repository.message_repository import MessageRepository

    conv_id = str(uuid.uuid4())
    await ConversationRepository.create({
        "id": conv_id,
        "title": title,
        "source_type": "web_sync",
        "source_account": "Bookmarklet"
    })

    for msg in messages:
        await MessageRepository.save_message(
            conversation_id=conv_id,
            role=msg.get("role", "user"),
            content=msg.get("content", ""),
            executed_by_account="Bookmarklet"
        )

    return {"message": "网页同步成功", "conversation_id": conv_id, "count": len(messages)}
