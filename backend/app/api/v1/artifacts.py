"""
工作区资产库 (Artifacts) 接口路由
"""
import os
import subprocess
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from ...core.logging_config import logger
from ...repository.artifact_repository import ArtifactRepository
from ...repository.db import db_manager

router = APIRouter(prefix="/artifacts", tags=["Workspace Artifacts"])


class ArtifactRegisterRequest(BaseModel):
    workspace_id: str
    file_path: str
    file_name: str
    file_type: str = Field("document", description="document, code, media, report")
    file_size: int = 0
    conversation_id: str | None = None
    source: str = "ai_generated"
    summary: str | None = None


@router.get("")
async def list_artifacts(
    workspace_id: str = Query(..., description="工作区 ID"),
    file_type: str | None = Query(None, description="资产分类: document / code / media / report"),
):
    """
    获取指定工作区的资产列表
    """
    items = await ArtifactRepository.list_artifacts(workspace_id, file_type)
    return {"artifacts": items, "total": len(items)}


@router.post("")
async def register_artifact(req: ArtifactRegisterRequest):
    """
    登记一件新资产
    """
    item = await ArtifactRepository.register_artifact(
        workspace_id=req.workspace_id,
        file_path=req.file_path,
        file_name=req.file_name,
        file_type=req.file_type,
        file_size=req.file_size,
        conversation_id=req.conversation_id,
        source=req.source,
        summary=req.summary,
    )
    return item


@router.post("/{artifact_id}/open")
async def open_artifact_in_explorer(artifact_id: str):
    """
    在 Windows 原生资源管理器中定位或打开该物理文件
    """
    async with db_manager.get_connection() as conn:
        async with conn.execute(
            "SELECT file_path FROM workspace_artifacts WHERE id = ?;", (artifact_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="未找到指定的资产记录")
            file_path = row["file_path"]

    target = Path(file_path).resolve()
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"文件不存在: {file_path}")

    try:
        # Windows 原生打开定位
        if os.name == "nt":
            subprocess.Popen(f'explorer /select,"{str(target)}"', shell=True)
        return {"success": True, "path": str(target)}
    except Exception as e:
        logger.error(f"打开资源管理器定位失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

