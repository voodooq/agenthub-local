"""
Skill 经验提炼提议与自进化审批接口 (Experience Evolution API)
"""
import asyncio
from typing import Literal
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ...repository.workspace_repository import WorkspaceRepository
from ...skills.experience_distiller import skill_experience_service

router = APIRouter(prefix="/experiences", tags=["Experience Evolution"])


class ExperienceProposalPreviewRequest(BaseModel):
    """切换应用模式或更新目标 Skill 时实时重算 Diff 与草案正文请求体"""

    apply_mode: Literal["update_existing", "create_new"] = Field(
        "update_existing",
        description="应用模式：迭代更新已有 Skill 或另存新建独立 Skill",
    )
    target_skill_id: str | None = Field(
        None,
        description="当 apply_mode=update_existing 时指定的目标 Skill ID",
    )
    target_scope: Literal["workspace", "global"] | None = Field(
        None,
        description="可选作用域：工作区私有 (workspace) 或全局底座 (global)",
    )


class ExperienceProposalApplyRequest(BaseModel):
    """确认应用经验提议请求体"""

    edited_content: str | None = Field(None, description="用户在 Diff 预览中编辑修订后的正文文本")
    target_scope: Literal["workspace", "global"] | None = Field(
        None, description="可选覆盖目标作用域：工作区私有 (workspace) 或全局底座 (global)"
    )
    target_skill_name: str | None = Field(
        None, description="可选重命名新建 Skill ID（仅 create_new 生效）"
    )
    apply_mode: Literal["update_existing", "create_new"] | None = Field(
        None, description="可选覆盖应用模式：迭代更新已有 Skill 或另存为新 Skill"
    )
    target_skill_id: str | None = Field(
        None, description="迭代更新已有 Skill 时指定的目标 Skill ID"
    )
    next_version: str | None = Field(
        None, description="可选指定更新后的语义化版本号（如 1.1.0）"
    )


@router.get("/proposals")
async def list_experience_proposals(
    workspace_id: str | None = Query(None, description="按工作区过滤"),
    conversation_id: str | None = Query(None, description="按会话过滤"),
    status: str | None = Query("pending", description="状态过滤: pending/applied/dismissed 或留空查全部"),
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    """查询待审批或历史已处理的经验自进化提议列表"""
    clean_status = status.strip() if status and status.strip().lower() != "all" else None
    items = await skill_experience_service.list_proposals(
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        status=clean_status,
        limit=limit,
    )
    ws_root = await WorkspaceRepository.get_root_path_by_id(workspace_id) if workspace_id else None
    available_skills = await asyncio.to_thread(
        skill_experience_service.list_available_target_skills,
        ws_root,
    )
    return {"proposals": items, "total": len(items), "available_skills": available_skills}


@router.post("/proposals/{proposal_id}/preview")
async def preview_experience_proposal(
    proposal_id: str,
    req: ExperienceProposalPreviewRequest,
) -> dict:
    """当用户切换更新目标 Skill 或另存新建模式时，实时预览对应 Diff 与最新版本信息"""
    try:
        preview = await skill_experience_service.preview_proposal(
            proposal_id,
            apply_mode=req.apply_mode,
            target_skill_id=req.target_skill_id,
            target_scope=req.target_scope,
        )
        return {"success": True, "preview": preview}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"预览经验提议失败: {e}")


@router.post("/proposals/{proposal_id}/apply")
async def apply_experience_proposal(
    proposal_id: str,
    req: ExperienceProposalApplyRequest | None = None,
) -> dict:
    """
    经用户确认 Diff 后，将经验提议正式写入 Skill 目录（迭代升级 SKILL.md 与版本号或新建四元组）
    并立即同步 SQLite FTS5 与刷新内存缓存。
    """
    body = req or ExperienceProposalApplyRequest()
    try:
        updated = await skill_experience_service.apply_proposal(
            proposal_id,
            edited_content=body.edited_content,
            target_scope=body.target_scope,
            target_skill_name=body.target_skill_name,
            apply_mode=body.apply_mode,
            target_skill_id=body.target_skill_id,
            next_version=body.next_version,
        )
        return {"success": True, "proposal": updated}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"应用经验提议失败: {e}")


@router.post("/proposals/{proposal_id}/dismiss")
async def dismiss_experience_proposal(proposal_id: str) -> dict:
    """忽略并归档经验提议"""
    updated = await skill_experience_service.dismiss_proposal(proposal_id)
    if not updated:
        raise HTTPException(status_code=404, detail=f"未找到经验提议: {proposal_id}")
    return {"success": True, "proposal": updated}
