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
from ...vfs.sandbox_provider import SandboxVFSProvider
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
    elif ptype == "sandbox":
        return SandboxVFSProvider(
            mount_id=mount_id,
            mount_name=mount_name,
            workspace_id=config.get("workspace_id", ""),
            root_path=config.get("root_path", ""),
            sub_path=config.get("sub_path", ""),
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
    task_id: str | None = Field(default=None, description="前端传入的任务 ID，用于轮询实时训练进度")


# 知识库增量训练实时进度追踪（内存态，毫秒级轮询读取）
_SYNC_PROGRESS: dict[str, dict[str, Any]] = {}
_SYNC_LOCK = asyncio.Lock()


def _update_sync_progress(
    task_id: str,
    *,
    running: bool,
    percent: float,
    stage: str,
    current_file: str = "",
    processed_files: int = 0,
    total_files: int = 0,
    new_chunks: int = 0,
) -> None:
    """更新指定训练任务的实时进度快照。"""
    snapshot = {
        "task_id": task_id,
        "running": running,
        "percent": round(max(0.0, min(100.0, percent)), 1),
        "stage": stage,
        "current_file": current_file,
        "processed_files": processed_files,
        "total_files": total_files,
        "new_chunks": new_chunks,
        "updated_at": time.time(),
    }
    _SYNC_PROGRESS[task_id] = snapshot
    _SYNC_PROGRESS["latest"] = snapshot


@router.get("/sync/progress")
async def get_sync_progress(task_id: str | None = Query(default=None, description="训练任务 ID")) -> dict[str, Any]:
    """查询知识库增量训练的实时百分比与当前处理文件阶段。"""
    key = task_id or "latest"
    data = _SYNC_PROGRESS.get(key) or _SYNC_PROGRESS.get("latest") or {
        "task_id": key,
        "running": False,
        "percent": 0.0,
        "stage": "待启动",
        "current_file": "",
        "processed_files": 0,
        "total_files": 0,
        "new_chunks": 0,
    }
    return {"success": True, "data": data}


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


def _format_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    else:
        return f"{size / (1024 * 1024):.1f} MB"


@router.get("/mounts/{mount_id}/files")
async def list_mount_remote_files(
    mount_id: str,
    recursive: bool = Query(default=True, description="是否递归遍历子文件夹"),
) -> dict[str, Any]:
    """实时读取指定云端挂载源的远程空间文件列表，便于排查文件可达性与格式。"""
    mounts = _load_mounts()
    target = next((m for m in mounts if m["mount_id"] == mount_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="未找到该挂载源")

    provider = create_cloud_provider(
        mount_id=target["mount_id"],
        mount_name=target["mount_name"],
        provider_type=target["provider_type"],
        config=target.get("config", {}),
        authority_tier=target.get("authority_tier", 1),
    )
    if not provider:
        raise HTTPException(status_code=400, detail="不支持的提供商类型")

    try:
        files_meta = await provider.list_files(recursive=recursive)
        result_files = []
        for f in files_meta:
            ext = Path(f.title).suffix.lower()
            is_supported = ext in (".pdf", ".docx", ".doc", ".xlsx", ".xls", ".md", ".txt", ".json", ".csv") or f.is_dir
            result_files.append({
                "remote_uri": f.remote_uri,
                "title": f.title,
                "size": f.size,
                "size_formatted": _format_bytes(f.size),
                "is_dir": f.is_dir,
                "mtime": f.mtime,
                "mime_type": f.mime_type or (ext[1:] if ext else "unknown"),
                "supported": is_supported,
            })

        return {
            "success": True,
            "mount_id": mount_id,
            "mount_name": target["mount_name"],
            "provider_type": target["provider_type"],
            "total_count": len(result_files),
            "files": result_files,
        }
    except Exception as e:
        logger.warning(f"获取挂载文件列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"读取空间文件失败: {str(e)}")


@router.delete("/mounts/{mount_id}")
async def delete_cloud_mount(mount_id: str) -> dict[str, Any]:
    """删除指定的云端挂载源配置。"""
    mounts = _load_mounts()
    new_mounts = [m for m in mounts if m["mount_id"] != mount_id]
    _save_mounts(new_mounts)
    return {"success": True, "message": f"挂载源 {mount_id} 已移除"}


async def run_incremental_sync(
    *,
    mount_id: str | None = None,
    workspace_root: str = ".",
    task_id: str | None = None,
    source_tag: str = "manual",
) -> dict[str, Any]:
    """执行挂载源增量同步（支持新增、修改重切与已删除文件切片自动清理，带并发锁保护）。"""
    eff_task_id = task_id or f"sync_{int(time.time() * 1000)}"
    if _SYNC_LOCK.locked() and source_tag == "auto":
        return {"success": True, "skipped": True, "message": "已有同步任务正在执行，跳过本次自动巡检"}

    async with _SYNC_LOCK:
        _update_sync_progress(
            eff_task_id,
            running=True,
            percent=2.0,
            stage="正在扫描挂载源文档列表...",
        )

        mounts = _load_mounts()
        if mount_id:
            mounts = [m for m in mounts if m["mount_id"] == mount_id]

        if not mounts:
            _update_sync_progress(eff_task_id, running=False, percent=0.0, stage="未找到有效挂载源")
            return {"success": False, "message": "未找到匹配的有效挂载源"}

        engine = HybridSearchEngine(workspace_root)
        sync_mgr = CloudVFSSyncManager(workspace_root)
        total_new_chunks = 0
        total_removed_files = 0
        total_removed_chunks = 0
        sync_warnings: list[str] = []
        affected_kb_ids: set[str] = set()

        # 阶段 1：扫描所有挂载源，识别新增/修改文件（modified）与已删除文件（removed_uris）
        mount_plans: list[tuple[dict[str, Any], Any, list[Any], list[str]]] = []
        total_mounts = max(1, len(mounts))

        for m_idx, m in enumerate(mounts):
            _update_sync_progress(
                eff_task_id,
                running=True,
                percent=2.0 + 10.0 * (m_idx / total_mounts),
                stage=f"正在扫描挂载源：{m['mount_name']}...",
            )
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
                if not files:
                    if m["provider_type"] == "wecom":
                        sync_warnings.append(f"【{m['mount_name']}】未读取到文件，请检查 SpaceId 是否正确且自建应用拥有该空间权限。")
                    elif m["provider_type"] == "webdav":
                        sync_warnings.append(f"【{m['mount_name']}】未读取到文件，请确认挂载根路径下是否存在支持切片的文档 (PDF/DOCX/TXT/MD)。")
                    elif m["provider_type"] == "oss":
                        sync_warnings.append(f"【{m['mount_name']}】未读取到文件，请检查存储桶与前缀配置。")
                    elif m["provider_type"] == "sandbox":
                        sync_warnings.append(f"【{m['mount_name']}】沙盒目录下未扫描到支持切片的文档 (PDF/DOCX/TXT/MD/CSV/XLSX)。")
                # NOTE: 过滤纯目录条目后再执行去重兜底，防止目录或重复路径条目干扰文档计数
                dedup_seen: set[tuple[str, int]] = set()
                deduped_files: list = []
                for _f in files:
                    if getattr(_f, "is_dir", False) or not str(getattr(_f, "title", "") or "").strip():
                        continue
                    _fp = (_f.title.lower(), _f.size)
                    if _fp not in dedup_seen:
                        dedup_seen.add(_fp)
                        deduped_files.append(_f)
                    else:
                        logger.warning(f"训练去重：跳过重复文件 [{_f.title}] size={_f.size}")
                files = deduped_files

                modified = sync_mgr.filter_modified_files(m["mount_id"], files)
                # 仅在 list_files 正常返回时才检测被删除文件，防止接口异常误删
                removed_uris = sync_mgr.find_removed_uris(m["mount_id"], files)

                # 防呆自愈：若独立知识库目录/切片已被物理清空或尚未建库，但水位线仍残留导致 modified 为空，则自动重置水位线并全量重训
                if files and not modified:
                    from ...kb.storage import KBStorageManager
                    kb_id_chk = f"kb_{m['mount_id']}"
                    kb_dir_chk = KBStorageManager.get_kb_dir(kb_id_chk) / "versions"
                    has_any_chunks = False
                    if kb_dir_chk.exists():
                        for ver_db in kb_dir_chk.glob("*/index.db"):
                            try:
                                import sqlite3 as _s3
                                with _s3.connect(str(ver_db)) as _c:
                                    _tbl = _c.execute(
                                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='hybrid_chunks'"
                                    ).fetchone()
                                    if _tbl and int(_c.execute("SELECT COUNT(*) FROM hybrid_chunks").fetchone()[0] or 0) > 0:
                                        has_any_chunks = True
                                        break
                            except Exception:
                                pass
                    if not has_any_chunks:
                        logger.info(f"挂载源 [{m['mount_name']}] 目标知识库为空/已销毁，自动重置同步水位线以执行全量重建")
                        sync_mgr.clear_sync_state(m["mount_id"])
                        modified = list(files)
                        removed_uris = []

                mount_plans.append((m, provider, modified, removed_uris))
            except Exception as prov_err:
                logger.warning(f"挂载源 {m['mount_name']} 增量拉取失败: {prov_err}")
                sync_warnings.append(f"【{m['mount_name']}】拉取异常: {prov_err}")

        # 阶段 2：先清理已删除/待覆盖文件的旧切片，再执行新增与变更文件的切片向量化（12.0% -> 86.0%）
        total_files = sum(len(mod) for _, _, mod, _ in mount_plans)
        processed_files = 0

        from ...kb.storage import KBStorageManager

        for m, provider, modified, removed_uris in mount_plans:
            kb_id = f"kb_{m['mount_id']}"
            # 2.1 清理已在源端删除的文件切片与水位线
            if removed_uris:
                removed_cnt = KBStorageManager.delete_chunks_by_remote_uris(
                    kb_id=kb_id,
                    remote_uris=removed_uris,
                    version="v1.0.0",
                )
                sync_mgr.delete_sync_state_uris(m["mount_id"], removed_uris)
                total_removed_files += len(removed_uris)
                total_removed_chunks += removed_cnt
                affected_kb_ids.add(kb_id)
                logger.info(
                    f"[KBSync] 挂载源 [{m['mount_name']}] 检测到 {len(removed_uris)} 个文件已删除，已同步清理 {removed_cnt} 个旧切片"
                )

            # 2.2 对内容有变化的文件先删旧切片再重切写入，防止文档变短后尾部残留旧切片
            modified_uris = [f.remote_uri for f in modified if f.remote_uri]
            if modified_uris:
                KBStorageManager.delete_chunks_by_remote_uris(
                    kb_id=kb_id,
                    remote_uris=modified_uris,
                    version="v1.0.0",
                )

            mount_new_chunks: list[dict[str, Any]] = []
            for f in modified:
                current_pct = 12.0 + (74.0 * (processed_files / total_files) if total_files > 0 else 74.0)
                _update_sync_progress(
                    eff_task_id,
                    running=True,
                    percent=current_pct,
                    stage=f"正在切片与向量化 ({processed_files + 1}/{total_files})",
                    current_file=f.title,
                    processed_files=processed_files,
                    total_files=total_files,
                    new_chunks=total_new_chunks,
                )
                try:
                    stream = await provider.download_stream(f.remote_uri)
                    chunks = DocumentStreamExtractor.extract_chunks(stream, f.title, f.remote_uri, authority_tier=f.authority_tier)
                    if chunks:
                        await engine.upsert_chunks(chunks)
                        mount_new_chunks.extend(chunks)
                        total_new_chunks += len(chunks)
                    sync_mgr.update_sync_state(m["mount_id"], f)
                except Exception as file_err:
                    logger.warning(f"同步单个文件失败 ({f.title}): {file_err}")
                finally:
                    processed_files += 1
                    done_pct = 12.0 + (74.0 * (processed_files / total_files) if total_files > 0 else 74.0)
                    _update_sync_progress(
                        eff_task_id,
                        running=True,
                        percent=done_pct,
                        stage=f"正在切片与向量化 ({processed_files}/{total_files})",
                        current_file=f.title,
                        processed_files=processed_files,
                        total_files=total_files,
                        new_chunks=total_new_chunks,
                    )

            if mount_new_chunks:
                affected_kb_ids.add(kb_id)
                KBStorageManager.upsert_chunks_to_version_db(
                    kb_id=kb_id,
                    version="v1.0.0",
                    chunks=mount_new_chunks,
                )

        # 阶段 3：若有新增、变更或删除，触发元数据同步与后处理增强（86.0% -> 98.0%）
        if total_new_chunks > 0 or total_removed_chunks > 0 or affected_kb_ids:
            _update_sync_progress(
                eff_task_id,
                running=True,
                percent=88.0,
                stage="正在更新知识库索引与图谱增强...",
                processed_files=processed_files,
                total_files=total_files,
                new_chunks=total_new_chunks,
            )
            try:
                from ...kb.manager import KBLifecycleManager
                await KBLifecycleManager.auto_adopt_mounted_kbs()
                if total_new_chunks > 0:
                    await KBLifecycleManager.auto_post_process_trained_kbs(list(affected_kb_ids))
            except Exception as e:
                logger.debug(f"同步后自动增强流水线: {e}")

        _update_sync_progress(
            eff_task_id,
            running=False,
            percent=100.0,
            stage="增量训练与切片已完成",
            processed_files=processed_files,
            total_files=total_files,
            new_chunks=total_new_chunks,
        )

        if total_new_chunks == 0 and total_removed_files == 0 and sync_warnings:
            return {
                "success": True,
                "message": f"增量同步完成，新写入/更新 0 个规程切片。\n提示: {'; '.join(sync_warnings)}",
                "new_chunks": 0,
                "removed_files": 0,
                "removed_chunks": 0,
            }

        summary_parts = [f"新写入/更新 {total_new_chunks} 个切片"]
        if total_removed_files > 0:
            summary_parts.append(f"清理已删除文件 {total_removed_files} 个（移除旧切片 {total_removed_chunks} 个）")
        return {
            "success": True,
            "message": f"增量同步完成，{'，'.join(summary_parts)}",
            "new_chunks": total_new_chunks,
            "removed_files": total_removed_files,
            "removed_chunks": total_removed_chunks,
        }


@router.post("/sync")
async def trigger_sync(req: SyncRequest) -> dict[str, Any]:
    """触发指定挂载源或全部云盘的水位线增量同步。"""
    return await run_incremental_sync(
        mount_id=req.mount_id,
        workspace_root=req.workspace_root,
        task_id=req.task_id,
        source_tag="manual",
    )


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


