"""
专家配置管理接口 (Agents API)
提供官方预置专家查询及自定义专家的持久化增删改查能力
"""
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ...repository.agent_repository import AgentProfileRepository

router = APIRouter(prefix="/agents", tags=["agents"])


class AgentCreateRequest(BaseModel):
    """创建自定义专家请求体"""

    id: str | None = Field(None, description="可选自定义 ID，留空则自动生成")
    name: str = Field(..., min_length=1, max_length=120, description="专家名称")
    description: str = Field("", max_length=1000, description="专家职责简介")
    base_model: str = Field("", max_length=120, description="默认底座模型（留空表示跟随当前会话模型）")
    context_prompt: str = Field("", max_length=8000, description="专家角色系统提示词")
    skill_ids: list[str] = Field(default_factory=list, description="挂载的技能 ID 列表")
    allowed_tools: list[str] = Field(default_factory=list, description="允许调用的工具白名单")
    allowed_write_dirs: list[str] = Field(default_factory=list, description="允许写入的相对目录白名单")
    tools_count: int | None = Field(None, ge=0, description="可用工具数量展示值")
    status: Literal["active", "draft"] = Field("active", description="专家状态")


class AgentUpdateRequest(BaseModel):
    """更新自定义专家请求体"""

    name: str | None = Field(None, min_length=1, max_length=120, description="专家名称")
    description: str | None = Field(None, max_length=1000, description="专家职责简介")
    base_model: str | None = Field(None, max_length=120, description="默认底座模型")
    context_prompt: str | None = Field(None, max_length=8000, description="专家角色系统提示词")
    skill_ids: list[str] | None = Field(None, description="挂载的技能 ID 列表")
    allowed_tools: list[str] | None = Field(None, description="允许调用的工具白名单")
    allowed_write_dirs: list[str] | None = Field(None, description="允许写入的相对目录白名单")
    tools_count: int | None = Field(None, ge=0, description="可用工具数量展示值")
    status: Literal["active", "draft"] | None = Field(None, description="专家状态")


@router.get("/")
@router.get("")
async def list_agents(
    ownership: Literal["official", "custom"] | None = Query(None, description="按归属过滤"),
    status: Literal["active", "draft"] | None = Query(None, description="按状态过滤"),
) -> dict:
    """获取全部专家列表（官方专家 + 用户自定义专家）"""
    items = await AgentProfileRepository.list_agents(ownership=ownership, status=status)
    return {"agents": items, "total": len(items)}


@router.get("/{agent_id}")
async def get_agent(agent_id: str) -> dict:
    """获取单个专家配置详情"""
    item = await AgentProfileRepository.get_agent(agent_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"未找到专家: {agent_id}")
    return {"agent": item}


@router.post("/")
@router.post("")
async def create_agent(req: AgentCreateRequest) -> dict:
    """新建自定义专家并持久化至数据库"""
    try:
        created = await AgentProfileRepository.create_agent(req.model_dump())
        return {"success": True, "agent": created}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        if "UNIQUE constraint failed" in str(e):
            raise HTTPException(status_code=409, detail="专家 ID 已存在")
        raise HTTPException(status_code=500, detail=f"创建专家失败: {e}")


@router.put("/{agent_id}")
async def update_agent(agent_id: str, req: AgentUpdateRequest) -> dict:
    """更新自定义专家配置；官方内置专家禁止修改并返回 403"""
    try:
        payload = req.model_dump(exclude_unset=True)
        updated = await AgentProfileRepository.update_agent(agent_id, payload)
        if not updated:
            raise HTTPException(status_code=404, detail=f"未找到专家: {agent_id}")
        return {"success": True, "agent": updated}
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{agent_id}")
async def delete_agent(agent_id: str) -> dict:
    """删除自定义专家配置；官方内置专家禁止删除并返回 403"""
    try:
        deleted = await AgentProfileRepository.delete_agent(agent_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"未找到专家: {agent_id}")
        return {"success": True, "deleted_id": agent_id}
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
