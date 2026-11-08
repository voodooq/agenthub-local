"""
技能 (Skills) 检索与工作区沙盒管理路由
"""
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
async def list_skills(workspace_id: str | None = Query(None)):
    """
    获取可用技能列表（若提供 workspace_id 则合并该工作区的 L2 专属技能与 L1 底座技能）
    """
    ws_root = None
    if workspace_id:
        ws = await WorkspaceRepository.get_by_id(workspace_id)
        if ws:
            ws_root = ws["root_path"]

    service = SkillService(workspace_root=ws_root)
    skills = service.list_all_skills()
    return {"skills": skills, "total": len(skills)}


@router.get("/{name}")
async def get_skill_detail(name: str, workspace_id: str | None = Query(None)):
    """
    获取指定技能的 SOP 明文规程
    """
    ws_root = None
    if workspace_id:
        ws = await WorkspaceRepository.get_by_id(workspace_id)
        if ws:
            ws_root = ws["root_path"]

    service = SkillService(workspace_root=ws_root)
    detail = service.get_skill_detail(name)
    if not detail:
        raise HTTPException(status_code=404, detail=f"未找到技能: {name}")
    return detail


@router.post("")
async def create_workspace_skill(req: WorkspaceSkillCreateRequest):
    """
    在工作区物理路径下创建或覆盖 L2 专属技能 (<workspace>/.agenthub/skills/<name>/SKILL.md)
    """
    ws = await WorkspaceRepository.get_by_id(req.workspace_id)
    if not ws:
        raise HTTPException(status_code=404, detail="工作区不存在")

    service = SkillService(workspace_root=ws["root_path"])
    try:
        saved = service.save_workspace_skill(
            skill_name=req.name,
            command=req.command,
            description=req.description,
            sop_content=req.sop_content,
        )
        return {"success": True, "skill": saved}
    except Exception as e:
        logger.error(f"创建工作区技能失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))

