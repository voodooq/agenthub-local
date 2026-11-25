"""
业务语义本体与只读 SQL 查询 API 路由（对接 NVIDIA Auto Ontology）
"""
from __future__ import annotations

from typing import Any
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ...sql.auto_ontology_client import (
    AutoOntologyClient,
    get_auto_ontology_config,
    save_auto_ontology_config,
)

router = APIRouter(prefix="/ontology", tags=["AutoOntology"])


class AutoOntologyConfigRequest(BaseModel):
    base_url: str = Field(..., description="Auto Ontology 网关访问地址（如 https://ontology.example.com）")
    api_key: str | None = Field(default="", description="x-api-key API Token（留空或传掩码串则保留原密钥）")
    timeout_seconds: int = Field(default=300, ge=15, le=900)
    enabled: bool = Field(default=True)
    target_db: str | None = Field(default=None, description="默认目标业务库名（fencinginfo / equestrianinfo）")


class AutoOntologyTestRequest(BaseModel):
    profile_id: str | None = None
    base_url: str | None = None
    api_key: str | None = None


class AutoOntologyAskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    conversation_id: str | None = None
    target_db: str | None = Field(default=None, description="目标业务库名（fencinginfo / equestrianinfo）")


@router.get("/config")
async def get_ontology_config() -> dict[str, Any]:
    """获取脱敏后的 Auto Ontology 连接配置。"""
    cfg = await get_auto_ontology_config(masked=True)
    return {"success": True, "data": cfg}


@router.put("/config")
async def update_ontology_config(req: AutoOntologyConfigRequest) -> dict[str, Any]:
    """保存 Auto Ontology 连接地址与 API Token。"""
    try:
        cfg = await save_auto_ontology_config(
            base_url=req.base_url,
            api_key=req.api_key,
            timeout_seconds=req.timeout_seconds,
            enabled=req.enabled,
            target_db=req.target_db,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"success": True, "data": cfg, "message": "Auto Ontology 配置已保存"}


@router.post("/test")
async def test_ontology_connection(req: AutoOntologyTestRequest | None = None) -> dict[str, Any]:
    """测试 Auto Ontology 服务连通性与 API Token 有效性（支持按数据工作台空间测试）。"""
    saved_url = ""
    saved_key = ""
    if req and req.profile_id:
        from ...repository.schema_repository import SchemaProfileRepository

        prof = await SchemaProfileRepository.get_profile(req.profile_id, masked=False)
        if prof:
            saved_url = str(prof.get("ontology_base_url") or "")
            saved_key = str(prof.get("ontology_api_key") or "")

    if not saved_url or not saved_key:
        global_saved = await get_auto_ontology_config(masked=False)
        saved_url = saved_url or global_saved["base_url"]
        saved_key = saved_key or global_saved["api_key"]

    base_url = (req.base_url if req and req.base_url else saved_url).strip()
    incoming_key = (req.api_key if req and req.api_key else "").strip()
    # 若传入的是脱敏串或空，则回退到已保存明文密钥
    if not incoming_key or "..." in incoming_key:
        api_key = saved_key
    else:
        api_key = incoming_key

    if not base_url or not api_key:
        raise HTTPException(status_code=400, detail="请先填写 Auto Ontology 服务地址与 API Token")

    try:
        client = AutoOntologyClient(base_url=base_url, api_key=api_key)
        res = await client.test_connection()
        return {"success": True, "data": res}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"连通性测试失败: {str(e)}")


@router.get("/terms")
async def list_ontology_terms(
    q: str = Query(default="", description="按术语名称、描述或 Schema 过滤"),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    """检索 Auto Ontology 已编译的业务术语字典。"""
    client = await AutoOntologyClient.from_saved_config()
    if not client:
        raise HTTPException(status_code=400, detail="尚未启用或配置 Auto Ontology 连接")
    try:
        terms = await client.search_terms(keyword=q, limit=limit)
        return {"success": True, "data": terms}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"检索业务术语失败: {str(e)}")


@router.post("/ask")
async def ask_ontology_question(req: AutoOntologyAskRequest) -> dict[str, Any]:
    """通过 Auto Ontology 执行自然语言业务数据查询（返回文字回答、SQL 与结果行）。"""
    try:
        client = await AutoOntologyClient.from_saved_config(target_db=req.target_db)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not client:
        raise HTTPException(status_code=400, detail="尚未启用或配置 Auto Ontology 连接")
    try:
        res = await client.ask_question(
            question=req.question,
            conversation_id=req.conversation_id,
            target_db=req.target_db,
        )
        return {"success": True, "data": res}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"业务数据查询失败: {str(e)}")

