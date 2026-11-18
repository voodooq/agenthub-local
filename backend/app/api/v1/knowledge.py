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
SHARED_SPACES_PATH = Path.home() / ".agenthub" / "shared_wecom_spaces.json"


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


def _load_shared_spaces() -> list[dict[str, Any]]:
    if not SHARED_SPACES_PATH.exists():
        return []
    try:
        import json
        return json.loads(SHARED_SPACES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_shared_spaces(spaces: list[dict[str, Any]]) -> None:
    SHARED_SPACES_PATH.parent.mkdir(parents=True, exist_ok=True)
    import json
    SHARED_SPACES_PATH.write_text(json.dumps(spaces, ensure_ascii=False, indent=2), encoding="utf-8")


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
    sync_warnings: list[str] = []

    for m in mounts:
        m_cfg = m.get("config", {})
        if m["provider_type"] == "wecom" and not m_cfg.get("space_id"):
            sync_warnings.append(f"【{m['mount_name']}】未配置微盘 SpaceId，企微无法识别目标空间。")

        provider = create_cloud_provider(
            mount_id=m["mount_id"],
            mount_name=m["mount_name"],
            provider_type=m["provider_type"],
            config=m_cfg,
            authority_tier=m.get("authority_tier", 1),
        )
        if not provider:
            continue

        try:
            files = await provider.list_files(recursive=True)
            if not files and m["provider_type"] == "wecom":
                sync_warnings.append(f"【{m['mount_name']}】未读取到文件，请检查 SpaceId 是否正确且自建应用拥有该空间权限。")
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
            sync_warnings.append(f"【{m['mount_name']}】拉取异常: {prov_err}")

    if total_new_chunks == 0 and sync_warnings:
        return {
            "success": True,
            "message": f"增量同步完成，新写入/更新 0 个规程切片。\n提示: {'; '.join(sync_warnings)}",
            "new_chunks": 0,
        }

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


class CreateWeComSpaceRequest(BaseModel):
    corpid: str = Field(..., description="企业ID")
    corpsecret: str = Field(..., description="自建应用Secret")
    space_name: str = Field(default="AgentHub-规程知识库", description="微盘空间名称")
    admin_userid: str = Field(default="", description="指定的管理员企业微信UserID")
    operator_role: str = Field(default="admin", description="操作者角色：admin 或 member")
    allow_all_departments: bool = Field(default=True, description="是否全员可读写")


@router.post("/wecom/create_space")
async def create_wecom_space(req: CreateWeComSpaceRequest) -> dict[str, Any]:
    """通过企微 API 创建共享空间，并内置权限管控。"""
    if req.operator_role != "admin":
        raise HTTPException(
            status_code=403,
            detail="权限不足：仅管理员具备新建微盘空间权限，普通成员请直接选用已有共享空间",
        )

    prov = WeComDriveProvider(
        mount_id="tmp_create",
        mount_name=req.space_name,
        corpid=req.corpid,
        corpsecret=req.corpsecret,
    )
    ok, space_id, msg = await prov.create_space(
        space_name=req.space_name,
        admin_userid=req.admin_userid,
        allow_all_departments=req.allow_all_departments,
    )
    if not ok:
        return {"success": False, "message": msg}

    # 自动沉淀登记到共享空间清单
    spaces = _load_shared_spaces()
    existing = [s for s in spaces if s.get("space_id") == space_id]
    if not existing:
        spaces.insert(0, {
            "space_id": space_id,
            "space_name": req.space_name,
            "corpid": req.corpid,
            "corpsecret": req.corpsecret,  # 保留凭据供普通成员免配直接一键挂载
            "created_at": time.time(),
            "admin_userid": req.admin_userid or "企业管理员",
            "share_token": f"wecom://{space_id}",
        })
        _save_shared_spaces(spaces)

    return {
        "success": True,
        "space_id": space_id,
        "space_name": req.space_name,
        "share_token": f"wecom://{space_id}",
        "message": f"微盘空间【{req.space_name}】创建成功！应用已获得完全管理员权限。",
    }


@router.get("/wecom/shared_spaces")
async def get_shared_wecom_spaces() -> dict[str, Any]:
    """获取所有已创建或可供普通用户直接导入的微盘共享空间列表。"""
    spaces = _load_shared_spaces()
    # 脱敏输出，但保留挂载所需的核心信息
    safe_spaces = []
    for s in spaces:
        safe_spaces.append({
            "space_id": s.get("space_id"),
            "space_name": s.get("space_name"),
            "corpid": s.get("corpid"),
            "has_secret": bool(s.get("corpsecret")),
            "created_at": s.get("created_at"),
            "admin_userid": s.get("admin_userid"),
            "share_token": s.get("share_token"),
        })
    return {"success": True, "data": safe_spaces}


class QuickMountSharedSpaceRequest(BaseModel):
    space_id: str
    mount_name: str = ""
    authority_tier: int = 1


@router.post("/wecom/mount_shared")
async def mount_shared_space(req: QuickMountSharedSpaceRequest) -> dict[str, Any]:
    """普通成员免密免配一键直接挂载已存在的企业微盘共享空间。"""
    spaces = _load_shared_spaces()
    target = next((s for s in spaces if s.get("space_id") == req.space_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="未找到对应的共享空间配置，请联系管理员重新分享")

    mount_name = req.mount_name.strip() or target.get("space_name", "微盘共享库")
    mount_id = f"mnt_shared_{int(time.time())}"

    mounts = _load_mounts()
    # 避免重复挂载相同 space_id
    for m in mounts:
        if m.get("provider_type") == "wecom" and m.get("config", {}).get("space_id") == req.space_id:
            return {"success": True, "message": "该微盘空间已经挂载在知识库中，无需重复添加"}

    new_mount = {
        "mount_id": mount_id,
        "mount_name": mount_name,
        "provider_type": "wecom",
        "authority_tier": req.authority_tier,
        "config": {
            "corpid": target.get("corpid", ""),
            "corpsecret": target.get("corpsecret", ""),
            "space_id": req.space_id,
        },
    }
    mounts.append(new_mount)
    _save_mounts(mounts)

    return {
        "success": True,
        "message": f"成功快速挂载微盘空间【{mount_name}】，普通成员可立即点击一键增量同步",
        "mount_id": mount_id,
    }

