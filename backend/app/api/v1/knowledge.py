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
async def get_knowledge_status(workspace_root: str = Query(default=".", description="工作区路径")):
    """获取当前工作区混合检索切片、三元组与云盘挂载状态。"""
    try:
        engine = HybridSearchEngine(workspace_root)
        with engine._get_connection() as conn:
            chunk_cnt = conn.execute("SELECT COUNT(*) as c FROM hybrid_chunks").fetchone()["c"]
            triple_cnt = conn.execute("SELECT COUNT(*) as c FROM knowledge_triples").fetchone()["c"]
            cloud_cnt = conn.execute("SELECT COUNT(DISTINCT mount_id) as c FROM cloud_vfs_sync_state").fetchone()["c"]

        mounts = _load_mounts()
        return {
            "success": True,
            "data": {
                "workspace_root": str(Path(workspace_root).resolve()),
                "total_chunks": chunk_cnt,
                "total_triples": triple_cnt,
                "synced_cloud_mounts": cloud_cnt,
                "configured_mounts": len(mounts),
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取知识库状态异常: {str(e)}")


@router.get("/mounts")
async def list_cloud_mounts():
    """获取所有已配置的云盘虚拟挂载源。"""
    return {"success": True, "data": _load_mounts()}


@router.post("/mounts")
async def save_cloud_mount(payload: CloudMountPayload):
    """保存或更新云端知识库挂载源。"""
    mounts = _load_mounts()
    # 替换已有或新增
    mounts = [m for m in mounts if m["mount_id"] != payload.mount_id]
    mounts.append(payload.model_dump())
    _save_mounts(mounts)
    return {"success": True, "message": f"挂载源「{payload.mount_name}」配置已保存"}


@router.post("/mounts/test")
async def test_cloud_mount(payload: CloudMountPayload):
    """测试指定云端挂载源的连通性。"""
    cfg = payload.config
    ptype = payload.provider_type.lower()
    provider = None

    try:
        if ptype == "wecom":
            provider = WeComDriveProvider(
                mount_id=payload.mount_id,
                mount_name=payload.mount_name,
                corpid=cfg.get("corpid", ""),
                corpsecret=cfg.get("corpsecret", ""),
                space_id=cfg.get("space_id", ""),
                authority_tier=payload.authority_tier,
            )
        elif ptype == "webdav":
            provider = WebDAVProvider(
                mount_id=payload.mount_id,
                mount_name=payload.mount_name,
                base_url=cfg.get("base_url", ""),
                username=cfg.get("username", ""),
                password=cfg.get("password", ""),
                root_path=cfg.get("root_path", "/"),
                authority_tier=payload.authority_tier,
            )
        elif ptype == "oss":
            provider = OSSProvider(
                mount_id=payload.mount_id,
                mount_name=payload.mount_name,
                endpoint=cfg.get("endpoint", ""),
                bucket=cfg.get("bucket", ""),
                access_key=cfg.get("access_key", ""),
                secret_key=cfg.get("secret_key", ""),
                prefix=cfg.get("prefix", ""),
                authority_tier=payload.authority_tier,
            )
        else:
            return {"success": False, "message": f"未知提供商类型: {ptype}"}

        ok, msg = await provider.test_connection()
        return {"success": ok, "message": msg}
    except Exception as e:
        return {"success": False, "message": f"测试连通性异常: {str(e)}"}


@router.delete("/mounts/{mount_id}")
async def delete_cloud_mount(mount_id: str):
    """删除指定的云端挂载源配置。"""
    mounts = _load_mounts()
    new_mounts = [m for m in mounts if m["mount_id"] != mount_id]
    _save_mounts(new_mounts)
    return {"success": True, "message": f"挂载源 {mount_id} 已移除"}


@router.post("/sync")
async def trigger_sync(req: SyncRequest):
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
        ptype = m["provider_type"].lower()
        cfg = m.get("config", {})
        provider = None
        if ptype == "wecom":
            provider = WeComDriveProvider(m["mount_id"], m["mount_name"], cfg.get("corpid", ""), cfg.get("corpsecret", ""), cfg.get("space_id", ""), authority_tier=m.get("authority_tier", 1))
        elif ptype == "webdav":
            provider = WebDAVProvider(m["mount_id"], m["mount_name"], cfg.get("base_url", ""), cfg.get("username", ""), cfg.get("password", ""), cfg.get("root_path", "/"), authority_tier=m.get("authority_tier", 2))
        elif ptype == "oss":
            provider = OSSProvider(m["mount_id"], m["mount_name"], cfg.get("endpoint", ""), cfg.get("bucket", ""), cfg.get("access_key", ""), cfg.get("secret_key", ""), cfg.get("prefix", ""), authority_tier=m.get("authority_tier", 1))

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
):
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
