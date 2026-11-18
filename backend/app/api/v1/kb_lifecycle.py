"""
AgentHub Local v1.5.8 知识库生命周期治理、版本发布与专家沙盒隔离路由 (KB Lifecycle API)
覆盖：
1. 数据源连接 CRUD 与断连安全清理策略（模式 A 断连保库 vs 模式 B 连带销毁）；
2. 独立知识库资产管理、多版本快照、原子回滚与 Staging 转正；
3. 团队共享包导出发布、订阅拉取 (只读锁) 与 Fork 本地独立副本；
4. 专家多知识库绑定与优先级加权配置；
5. 检索命中率测试台 (Retrieval Playground) 实时验货。
"""
from pathlib import Path
import time
from typing import Any
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ...core.logging_config import logger
from ...kb.manager import KBLifecycleManager
from ...kb.package import KBPackageEngine
from ...kb.storage import KBStorageManager
from ...search.multi_kb_router import MultiKBRetrievalRouter

router = APIRouter(prefix="/kb", tags=["kb-lifecycle"])


# ==========================================
# 数据模型 Pydantic Schemas
# ==========================================

class DataSourcePayload(BaseModel):
    source_id: str = Field(..., description="数据源唯一ID")
    name: str = Field(..., description="数据源展示名称")
    source_type: str = Field(..., description="local_dir | wecom | webdav | oss")
    config: dict[str, Any] = Field(default_factory=dict, description="特定适配器连接配置")
    ignore_patterns: str = Field(default="~$*,*.tmp,.git/*", description="排除通配符规则")
    sync_mode: str = Field(default="auto_live", description="auto_live | staging_review")
    default_tier: int = Field(default=2, description="1=正式规程, 2=常规资料, 3=历史归档")


class KnowledgeBaseCreatePayload(BaseModel):
    kb_id: str = Field(..., description="知识库全局唯一ID")
    name: str = Field(..., description="知识库名称")
    description: str = Field(default="", description="知识库描述")
    source_id: str | None = Field(default=None, description="关联的数据源ID（可为空）")
    embedding_model: str = Field(default="null_offline_lexical", description="使用的向量模型ID")
    embedding_dim: int = Field(default=768, description="向量维度")
    initial_version: str = Field(default="v1.0.0", description="首发版本号")


class RollbackPayload(BaseModel):
    target_version: str = Field(..., description="要回滚至的目标版本号")


class PrunePayload(BaseModel):
    keep_count: int = Field(default=3, description="保留的历史版本数量")


class PublishPayload(BaseModel):
    version: str = Field(..., description="要发布的版本号")
    changelog: str = Field(default="", description="本次版本变更说明")


class PullPackagePayload(BaseModel):
    package_path: str = Field(..., description="本地或共享挂载的 .kbpack 文件路径")
    kb_name: str = Field(default="", description="导入后的知识库名称")
    as_readonly: bool = Field(default=True, description="是否锁定为订阅只读模式")


class ForkPayload(BaseModel):
    new_name: str = Field(default="", description="克隆后的本地新知识库名称")


class ExpertBindingItem(BaseModel):
    kb_id: str
    pinned_version: str = "latest"
    priority_rank: int = 1


class ExpertBindingsPayload(BaseModel):
    bindings: list[ExpertBindingItem]


class PlaygroundQueryPayload(BaseModel):
    query: str = Field(..., description="模拟测试查询关键词或问句")
    version: str | None = Field(default=None, description="可选锁定指定版本，缺省使用当前激活版本")
    top_k: int = Field(default=5, ge=1, le=20)


# ==========================================
# 1. 数据源连接管理
# ==========================================

@router.get("/sources")
async def list_sources() -> dict[str, Any]:
    """获取所有已配置的数据源列表。"""
    sources = await KBLifecycleManager.list_data_sources()
    return {"success": True, "data": sources}


@router.post("/sources")
async def create_source(payload: DataSourcePayload) -> dict[str, Any]:
    """新建或更新数据源连接。"""
    res = await KBLifecycleManager.create_data_source(
        source_id=payload.source_id,
        name=payload.name,
        source_type=payload.source_type,
        config=payload.config,
        ignore_patterns=payload.ignore_patterns,
        sync_mode=payload.sync_mode,
        default_tier=payload.default_tier,
    )
    return {"success": True, "data": res}


@router.delete("/sources/{source_id}")
async def delete_source(
    source_id: str,
    delete_kb: bool = Query(default=False, description="是否连带物理销毁关联的知识库快照"),
) -> dict[str, Any]:
    """删除数据源双模式决策接口：断连保库 (delete_kb=False) 或连带销毁 (delete_kb=True)。"""
    res = await KBLifecycleManager.remove_data_source(source_id, destroy_associated_kb=delete_kb)
    return res


# ==========================================
# 2. 知识库资产与多版本流转
# ==========================================

@router.get("/bases")
async def list_bases() -> dict[str, Any]:
    """获取所有知识库资产列表（含物理磁盘占用与切片统计）。"""
    kbs = await KBLifecycleManager.list_knowledge_bases()
    return {"success": True, "data": kbs}


@router.post("/bases")
async def create_base(payload: KnowledgeBaseCreatePayload) -> dict[str, Any]:
    """创建全新独立知识库资产并初始化存储。"""
    res = await KBLifecycleManager.create_knowledge_base(
        kb_id=payload.kb_id,
        name=payload.name,
        description=payload.description,
        source_id=payload.source_id,
        embedding_model=payload.embedding_model,
        embedding_dim=payload.embedding_dim,
        initial_version=payload.initial_version,
    )
    return {"success": True, "data": res}


@router.delete("/bases/{kb_id}")
async def delete_base(kb_id: str) -> dict[str, Any]:
    """彻底物理销毁指定知识库及所有历史版本。"""
    KBStorageManager.physically_destroy_kb(kb_id)
    from ...repository.db import db_manager
    async with db_manager.get_connection() as conn:
        await conn.execute("DELETE FROM kb_versions WHERE kb_id = ?", (kb_id,))
        await conn.execute("DELETE FROM expert_kb_bindings WHERE kb_id = ?", (kb_id,))
        await conn.execute("DELETE FROM knowledge_bases WHERE kb_id = ?", (kb_id,))
        await conn.commit()
    return {"success": True, "message": f"知识库 {kb_id} 及其所有版本已彻底物理删除"}


@router.get("/bases/{kb_id}/versions")
async def list_versions(kb_id: str) -> dict[str, Any]:
    """获取知识库版本快照列表与 Changelog。"""
    versions = await KBLifecycleManager.list_kb_versions(kb_id)
    return {"success": True, "data": versions}


@router.post("/bases/{kb_id}/rollback")
async def rollback_kb(kb_id: str, payload: RollbackPayload) -> dict[str, Any]:
    """原子切换 active_version 指针至指定历史版本 (< 50ms)。"""
    try:
        res = await KBLifecycleManager.rollback_version(kb_id, payload.target_version)
        return res
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/bases/{kb_id}/promote-staging")
async def promote_staging(kb_id: str) -> dict[str, Any]:
    """将 Staging 预览草稿版本正式转正为生效版本。"""
    try:
        res = await KBLifecycleManager.promote_staging_version(kb_id)
        return res
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/bases/{kb_id}/prune")
async def prune_kb(kb_id: str, payload: PrunePayload) -> dict[str, Any]:
    """执行知识库磁盘瘦身清理，释放旧版本快照空间。"""
    try:
        res = await KBLifecycleManager.prune_versions(kb_id, keep_count=payload.keep_count)
        return res
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ==========================================
# 3. 团队共享发布 (Publish) 与订阅拉取 (Pull)
# ==========================================

@router.post("/bases/{kb_id}/publish")
async def publish_kb_package(kb_id: str, payload: PublishPayload) -> dict[str, Any]:
    """将指定版本打包导出为标准 .kbpack 团队共享包。"""
    try:
        pkg_file = KBPackageEngine.export_package(kb_id, payload.version)
        return {
            "success": True,
            "kb_id": kb_id,
            "version": payload.version,
            "package_path": str(pkg_file),
            "filename": pkg_file.name,
            "size_bytes": pkg_file.stat().st_size,
            "message": f"成功导出共享分发包: {pkg_file.name}",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"打包失败: {str(e)}")


@router.post("/shared/pull")
async def pull_shared_package(payload: PullPackagePayload) -> dict[str, Any]:
    """一键拉取并订阅远端/共享的 .kbpack 知识库（自动施加只读锁）。"""
    pkg_p = Path(payload.package_path)
    if not pkg_p.exists():
        raise HTTPException(status_code=404, detail=f"指定的知识库包文件不存在: {payload.package_path}")

    try:
        res = await KBPackageEngine.import_and_subscribe_package(
            pkg_path=pkg_p,
            kb_name=payload.kb_name,
            as_readonly=payload.as_readonly,
        )
        return res
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"导入知识库包失败: {str(e)}")


@router.post("/bases/{kb_id}/fork")
async def fork_kb(kb_id: str, payload: ForkPayload) -> dict[str, Any]:
    """将订阅只读库克隆为本地独立可编辑知识库副本。"""
    try:
        res = await KBPackageEngine.fork_as_local_editable(kb_id, payload.new_name)
        return res
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Fork 失败: {str(e)}")


# ==========================================
# 4. 专家绑定路由与检索测试台 (Playground)
# ==========================================

@router.get("/experts/{expert_id}/bindings")
async def get_expert_bindings(expert_id: str) -> dict[str, Any]:
    """获取指定专家绑定的知识库清单。"""
    kbs = await KBLifecycleManager.get_expert_kbs(expert_id)
    return {"success": True, "data": kbs}


@router.put("/experts/{expert_id}/bindings")
async def save_expert_bindings(expert_id: str, payload: ExpertBindingsPayload) -> dict[str, Any]:
    """保存专家绑定的知识库清单及优先级顺序。"""
    items = [b.model_dump() for b in payload.bindings]
    res = await KBLifecycleManager.bind_expert_kbs(expert_id, items)
    return {"success": True, "data": res, "message": f"成功为专家绑定 {len(items)} 个知识库"}


@router.post("/bases/{kb_id}/playground")
async def test_retrieval_playground(kb_id: str, payload: PlaygroundQueryPayload) -> dict[str, Any]:
    """检索命中率测试台：在指定知识库内执行模拟检索，返回切片得分与详情。"""
    # 获取目标知识库版本
    kbs = await KBLifecycleManager.list_knowledge_bases()
    target_kb = next((k for k in kbs if k["kb_id"] == kb_id), None)
    if not target_kb:
        raise HTTPException(status_code=404, detail=f"知识库不存在: {kb_id}")

    version = payload.version or target_kb["active_version"]
    db_p = KBStorageManager.get_version_db_path(kb_id, version)
    if not db_p.exists():
        raise HTTPException(status_code=400, detail=f"该版本索引库未就绪: {version}")

    chunks = await MultiKBRetrievalRouter._search_single_index_db(
        db_path=db_p,
        query=payload.query,
        top_k=payload.top_k,
        kb_id=kb_id,
        kb_name=target_kb["name"],
        version=version,
        priority_weight=1.0,
        include_archived=True,
    )

    return {
        "success": True,
        "kb_id": kb_id,
        "name": target_kb["name"],
        "version": version,
        "query": payload.query,
        "total_hits": len(chunks),
        "chunks": chunks,
    }

