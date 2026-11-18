"""
云端知识库挂载、混合检索索引状态与图谱可视化 API (Knowledge Router)
"""
import asyncio
from pathlib import Path
import time
from typing import Any
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ...core.logging_config import logger
from ...search.hybrid_engine import HybridSearchEngine
from ...search.light_graph_rag import LightGraphRAGEngine
from ...vfs.base import CloudVFSSyncManager
from ...vfs.wecom_drive import WeComDriveProvider
from ...vfs.webdav_provider import WebDAVProvider
from ...vfs.oss_provider import OSSProvider
from ...vfs.document_extractor import DocumentStreamExtractor

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

# 挂载配置持久化目录
MOUNTS_CONFIG_PATH = Path.home() / ".agenthub" / "cloud_mounts.json"


def _load_mounts() -> list[dict[str, Any]]:
    if not MOUNTS_CONFIG_PATH.exists():
        return []
    try:
        import json
        return json.loads(MOUNTS_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_mounts(mounts: list[dict[str, Any]]) -> None:
    MOUNTS_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    import json
    MOUNTS_CONFIG_PATH.write_text(json.dumps(mounts, ensure_ascii=False, indent=2), encoding="utf-8")


def create_cloud_provider(
    mount_id: str,
    mount_name: str,
    provider_type: str,
    config: dict[str, Any],
    authority_tier: int = 1,
) -> Any:
    """统一创建对应类型的云盘 VFS 提供商适配器实例。"""
    ptype = provider_type.lower()
    if ptype == "wecom":
        return WeComDriveProvider(
            mount_id=mount_id,
            mount_name=mount_name,
            corpid=config.get("corpid", ""),
            corpsecret=config.get("corpsecret", ""),
            space_id=config.get("space_id", ""),
            authority_tier=authority_tier,
        )
    elif ptype == "webdav":
        return WebDAVProvider(
            mount_id=mount_id,
            mount_name=mount_name,
            base_url=config.get("base_url", ""),
            username=config.get("username", ""),
            password=config.get("password", ""),
            root_path=config.get("root_path", "/"),
            authority_tier=authority_tier,
        )
    elif ptype == "oss":
        return OSSProvider(
            mount_id=mount_id,
            mount_name=mount_name,
            endpoint=config.get("endpoint", ""),
            bucket=config.get("bucket", ""),
            access_key=config.get("access_key", ""),
            secret_key=config.get("secret_key", ""),
            prefix=config.get("prefix", ""),
            authority_tier=authority_tier,
        )
    return None


class CloudMountPayload(BaseModel):
    mount_id: str = Field(..., description="挂载唯一标识")
    mount_name: str = Field(..., description="展示名称")
    provider_type: str = Field(..., description="wecom | webdav | oss")
    authority_tier: int = Field(default=1, description="1=正式发布库, 2=工作区当前, 3=草稿/归档")
    config: dict[str, Any] = Field(default_factory=dict, description="特定适配器连接配置")


class SyncRequest(BaseModel):
    workspace_root: str = Field(default=".", description="当前工作区路径")
    mount_id: str | None = Field(default=None, description="指定挂载源 ID，若为空则同步全部")


@router.get("/status")
async def get_knowledge_status(
    workspace_root: str = Query(default=".", description="工作区路径")
) -> dict[str, Any]:
    """获取当前工作区混合检索切片、三元组与云盘挂载状态。"""
    try:
        engine = HybridSearchEngine(workspace_root)
        stats = engine.get_knowledge_stats()
        mounts = _load_mounts()
        return {
            "success": True,
            "data": {
                "workspace_root": str(Path(workspace_root).resolve()),
                "total_chunks": stats["total_chunks"],
                "total_triples": stats["total_triples"],
                "synced_cloud_mounts": stats["synced_cloud_mounts"],
                "configured_mounts": len(mounts),
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取知识库状态异常: {str(e)}")


@router.get("/mounts")
async def list_cloud_mounts() -> dict[str, Any]:
    """获取所有已配置的云盘虚拟挂载源。"""
    return {"success": True, "data": _load_mounts()}


@router.post("/mounts")
async def save_cloud_mount(payload: CloudMountPayload) -> dict[str, Any]:
    """保存或更新云端知识库挂载源。"""
    mounts = _load_mounts()
    # 替换已有或新增
    mounts = [m for m in mounts if m["mount_id"] != payload.mount_id]
    mounts.append(payload.model_dump())
    _save_mounts(mounts)
    return {"success": True, "message": f"挂载源「{payload.mount_name}」配置已保存"}


@router.post("/mounts/test")
async def test_cloud_mount(payload: CloudMountPayload) -> dict[str, Any]:
    """测试指定云端挂载源的连通性。"""
    provider = create_cloud_provider(
        mount_id=payload.mount_id,
        mount_name=payload.mount_name,
        provider_type=payload.provider_type,
        config=payload.config,
        authority_tier=payload.authority_tier,
    )
    if not provider:
        return {"success": False, "message": f"未知提供商类型: {payload.provider_type}"}

    try:
        ok, msg = await provider.test_connection()
        return {"success": ok, "message": msg}
    except Exception as e:
        return {"success": False, "message": f"测试连通性异常: {str(e)}"}


@router.delete("/mounts/{mount_id}")
async def delete_cloud_mount(mount_id: str) -> dict[str, Any]:
    """删除指定的云端挂载源配置。"""
    mounts = _load_mounts()
    new_mounts = [m for m in mounts if m["mount_id"] != mount_id]
    _save_mounts(new_mounts)
    return {"success": True, "message": f"挂载源 {mount_id} 已移除"}


@router.post("/sync")
async def trigger_sync(req: SyncRequest) -> dict[str, Any]:
    """触发指定挂载源或全部云盘的水位线增量同步。"""
    mounts = _load_mounts()
    if req.mount_id:
        mounts = [m for m in mounts if m["mount_id"] == req.mount_id]

    if not mounts:
        return {"success": False, "message": "未找到匹配的有效挂载源"}

    engine = HybridSearchEngine(req.workspace_root)
    sync_mgr = CloudVFSSyncManager(req.workspace_root)
    total_new_chunks = 0

    for m in mounts:
        provider = create_cloud_provider(
            mount_id=m["mount_id"],
            mount_name=m["mount_name"],
            provider_type=m["provider_type"],
            config=m.get("config", {}),
            authority_tier=m.get("authority_tier", 1),
        )
        if not provider:
            continue

        try:
            files = await provider.list_files(recursive=True)
            modified = sync_mgr.filter_modified_files(m["mount_id"], files)
            for f in modified:
                try:
                    stream = await provider.download_stream(f.remote_uri)
                    chunks = DocumentStreamExtractor.extract_chunks(stream, f.title, f.remote_uri, authority_tier=f.authority_tier)
                    if chunks:
                        await engine.upsert_chunks(chunks)
                        total_new_chunks += len(chunks)
                    sync_mgr.update_sync_state(m["mount_id"], f)
                except Exception as file_err:
                    logger.warning(f"同步单个文件失败 ({f.title}): {file_err}")
        except Exception as prov_err:
            logger.warning(f"挂载源 {m['mount_name']} 增量拉取失败: {prov_err}")

    return {
        "success": True,
        "message": f"增量同步完成，新写入/更新 {total_new_chunks} 个规程切片",
        "new_chunks": total_new_chunks,
    }


@router.get("/graph")
async def get_knowledge_graph_preview(
    seed: str = Query(..., description="起点实体"),
    max_hops: int = Query(default=2, ge=1, le=3),
    workspace_root: str = Query(default=".", description="工作区路径"),
    pod_id: str = Query(default="global"),
) -> dict[str, Any]:
    """获取知识图谱可视化节点与边数据。"""
    engine = LightGraphRAGEngine(workspace_root)
    edges = engine.query_subgraph(seed, max_hops=max_hops, pod_id=pod_id)

    # 提取唯一节点
    nodes_set = set()
    for e in edges:
        nodes_set.add(e["subject"])
        nodes_set.add(e["object"])

    nodes = [{"id": n, "label": n} for n in sorted(list(nodes_set))]
    return {
        "success": True,
        "seed": seed,
        "nodes": nodes,
        "edges": edges,
    }
    """获取知识图谱可视化节点与边数据。"""
    engine = LightGraphRAGEngine(workspace_root)
    edges = engine.query_subgraph(seed, max_hops=max_hops, pod_id=pod_id)

    # 提取唯一节点
    nodes_set = set()
    for e in edges:
        nodes_set.add(e["subject"])
        nodes_set.add(e["object"])

    nodes = [{"id": n, "label": n} for n in sorted(list(nodes_set))]
    return {
        "success": True,
        "seed": seed,
        "nodes": nodes,
        "edges": edges,
    }

