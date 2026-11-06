"""
系统健康检查路由
"""
from fastapi import APIRouter
from ...core.config import settings

router = APIRouter()


@router.get("/health")
async def health_check():
    """返回当前服务健康状态与版本"""
    return {
        "status": "healthy",
        "app_name": settings.app_name,
        "version": settings.app_version
    }
