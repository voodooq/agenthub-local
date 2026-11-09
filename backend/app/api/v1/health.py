"""
系统健康检查路由
"""
from fastapi import APIRouter
from ...core.config import settings, load_version_metadata

router = APIRouter()


@router.get("/health")
async def health_check():
    """返回当前服务健康状态与实时版本配置"""
    file_ver, file_edition = load_version_metadata()
    # 若运行时更新了根目录 version.json，实时反映最新值（若测试覆写了 settings.app_version 则优先保持兼容）
    current_ver = file_ver if (file_ver and file_ver != "0.0.0") else settings.app_version
    current_edition = file_edition or settings.app_edition
    return {
        "status": "healthy",
        "app_name": settings.app_name,
        "version": current_ver,
        "edition": current_edition,
    }
