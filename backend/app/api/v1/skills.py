"""
技能 (Skills) 检索与工作区沙盒管理路由
"""
import asyncio
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from ...core.logging_config import logger
from ...repository.workspace_repository import WorkspaceRepository
from ...skills.skill_service import SkillService

router = APIRouter(prefix="/skills", tags=["Skills & MCP"])


class WorkspaceSkillCreateRequest(BaseModel):
    workspace_id: str = Field(..., description="归属工作区 ID")
    name: str = Field(..., description="技能英文标识")
    command: str = Field(..., description="呼出快捷指令 (如 /test)")
    description: str = Field(..., description="技能简要说明")
    sop_content: str = Field(..., description="Markdown 格式 SOP 执行规程")


@router.get("")
async def list_skills(
    workspace_id: str | None = Query(None),
    include_content: bool = Query(False, description="是否一并返回完整 SOP 正文，列表页默认 False 仅返摘要"),
    refresh: bool = Query(False, description="是否强制绕过列表短窗口快照缓存即时重扫磁盘"),
):
    """
    获取可用技能列表（若提供 workspace_id 则合并该工作区的 L2 专属技能与 L1 底座技能）。
    在线程池执行磁盘扫描，避免大量技能文件 I/O 阻塞异步事件循环。
    """
    ws_root = None
    if workspace_id:
        ws_root = await WorkspaceRepository.get_root_path_by_id(workspace_id)

    service = SkillService(workspace_root=ws_root)
    skills = await asyncio.to_thread(
        service.list_all_skills,
        include_content=include_content,
        force_refresh=refresh,
    )
    return {"skills": skills, "total": len(skills)}


@router.get("/{name}")
async def get_skill_detail(name: str, workspace_id: str | None = Query(None)):
    """
    按需获取单个指定技能的 SOP 明文规程
    """
    ws_root = None
    if workspace_id:
        ws_root = await WorkspaceRepository.get_root_path_by_id(workspace_id)

    service = SkillService(workspace_root=ws_root)
    detail = await asyncio.to_thread(service.get_skill_detail, name)
    if not detail:
        raise HTTPException(status_code=404, detail=f"未找到技能: {name}")
    return detail


@router.post("")
async def create_workspace_skill(req: WorkspaceSkillCreateRequest):
    """
    在工作区物理路径下创建或覆盖 L2 专属技能 (<workspace>/skills/<name>/SKILL.md)
    """
    ws_root = await WorkspaceRepository.get_root_path_by_id(req.workspace_id)
    if not ws_root:
        raise HTTPException(status_code=404, detail="工作区不存在")

    service = SkillService(workspace_root=ws_root)
    try:
        saved = await asyncio.to_thread(
            service.save_workspace_skill,
            skill_name=req.name,
            command=req.command,
            description=req.description,
            sop_content=req.sop_content,
        )
        return {"success": True, "skill": saved}
    except Exception as e:
        logger.error(f"创建工作区技能失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{name}")
async def delete_workspace_skill(name: str, workspace_id: str = Query(..., description="工作区 ID")):
    """
    删除指定工作区私有专属技能
    """
    ws_root = await WorkspaceRepository.get_root_path_by_id(workspace_id)
    if not ws_root:
        raise HTTPException(status_code=404, detail="工作区不存在")

    service = SkillService(workspace_root=ws_root)
    deleted = await asyncio.to_thread(service.delete_workspace_skill, name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"未找到可删除的工作区专属技能: {name}")
    return {"success": True, "deleted": name}
