"""
FTS5 全局全文检索接口
"""
from fastapi import APIRouter, Query
from ...repository.search_repository import SearchRepository

router = APIRouter(prefix="/search", tags=["Search"])


@router.get("")
async def search_full_text(q: str = Query(..., min_length=1, description="检索关键词")):
    """
    基于 FTS5 虚拟表进行全局关键词检索
    支持毫秒级定位中英历史对话，返回上下文高亮片段
    """
    return await SearchRepository.search_messages(q)
