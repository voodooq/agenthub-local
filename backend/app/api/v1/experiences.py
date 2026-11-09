"""
Skill 经验提炼提议与自进化审批接口 (Experience Evolution API)
"""
from typing import Literal
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ...skills.experience_distiller import skill_experience_service

router = APIRouter(prefix="/experiences", tags=["Experience Evolution"])


class ExperienceProposalApplyRequest(BaseModel):
    """确认应用经验提议请求体"""

    edited_content: str | None = Field(None, description="用户在 Diff 预览中编辑修订后的正文文本")
    target_scope: Literal["workspace", "global"] | None = Field(
        None, description="可选覆盖目标作用域：工作区私有 (workspace) 或全局底座 (global)"
    )
    target_skill_name: str | None = Field(
        None, description="可选重命名新建 Skill ID（仅 proposal_type=new_skill 生效）"
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
    return {"proposals": items, "total": len(items)}


@router.post("/proposals/{proposal_id}/apply")
async def apply_experience_proposal(
    proposal_id: str,
    req: ExperienceProposalApplyRequest | None = None,
) -> dict:
    """
    经用户确认 Diff 后，将经验提议正式写入 Skill 目录（knowledge/experience_playbook.md 或新建四元组）
    并立即同步 SQLite FTS5 与刷新内存缓存。
    """
    body = req or ExperienceProposalApplyRequest()
    try:
        updated = await skill_experience_service.apply_proposal(
            proposal_id,
            edited_content=body.edited_content,
            target_scope=body.target_scope,
            target_skill_name=body.target_skill_name,
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
