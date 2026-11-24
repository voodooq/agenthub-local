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
    turbo_mode: bool = Field(
        default=False,
        description="是否开启全力极速模式；默认 False 采用温和低占用模式，为其他业务预留充足 CPU 与 I/O 资源",
    )


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


async def _resolve_kb_active_version(kb_id: str) -> str:
    """获取指定知识库当前激活的物理版本号（未建库时默认 v1.0.0）。"""
    try:
        from ...repository.db import db_manager

        async with db_manager.get_connection() as conn:
            cur = await conn.execute(
                "SELECT active_version, current_version FROM knowledge_bases WHERE kb_id = ? LIMIT 1",
                (kb_id,),
            )
            row = await cur.fetchone()
            if row:
                ver = str(row["active_version"] or row["current_version"] or "").strip()
                if ver:
                    return ver
    except Exception:
        pass
    return "v1.0.0"


async def _prepare_incremental_version(kb_id: str, has_changes: bool) -> tuple[str, str | None]:
    """为增量训练准备目标版本号；已有库且存在文件变更时自动复制快照并递增版本号。"""
    current_ver = await _resolve_kb_active_version(kb_id)
    if not has_changes:
        return current_ver, None

    from ...kb.storage import KBStorageManager
    from ...kb.version_helper import get_next_version
    from ...repository.db import db_manager

    cur_db = KBStorageManager.get_version_db_path(kb_id, current_ver)
    if not cur_db.exists() or (KBStorageManager.get_body_chunk_count(kb_id, current_ver) or 0) <= 0:
        return current_ver, None

    existing_versions: list[str] = [current_ver]
    try:
        vers_dir = KBStorageManager.get_kb_dir(kb_id) / "versions"
        if vers_dir.exists():
            for d in vers_dir.iterdir():
                if d.is_dir() and d.name.startswith("v"):
                    existing_versions.append(d.name)
        async with db_manager.get_connection() as conn:
            cur = await conn.execute("SELECT version FROM kb_versions WHERE kb_id = ?", (kb_id,))
            for r in await cur.fetchall():
                if r["version"]:
                    existing_versions.append(str(r["version"]))
    except Exception:
        pass

    next_ver = get_next_version(existing_versions, bump="patch")
    KBStorageManager.copy_version_snapshot(kb_id, source_version=current_ver, target_version=next_ver)
    return next_ver, current_ver


async def _commit_incremental_version(
    kb_id: str,
    new_ver: str,
    prev_ver: str,
    new_chunks_cnt: int,
    removed_chunks_cnt: int,
) -> None:
    """增量变更落盘成功后，原子切换 active_version 并登记历史版本记录。"""
    from ...kb.storage import KBStorageManager
    from ...repository.db import db_manager
    import uuid as _uuid

    changelog = f"增量同步：新增/更新 {new_chunks_cnt} 个切片，清理 {removed_chunks_cnt} 个旧切片"
    now_ts = time.time()
    body_cnt = KBStorageManager.get_body_chunk_count(kb_id, new_ver) or 0
    disk_sz = KBStorageManager.get_kb_disk_size(kb_id)

    try:
        async with db_manager.get_connection() as conn:
            await conn.execute(
                """
                UPDATE knowledge_bases
                SET active_version = ?, current_version = ?, chunk_count = ?, disk_size_bytes = ?, updated_at = ?
                WHERE kb_id = ?
                """,
                (new_ver, new_ver, body_cnt, disk_sz, now_ts, kb_id),
            )
            await conn.execute(
                """
                INSERT OR REPLACE INTO kb_versions (
                    kb_id, version, status, changelog, chunk_count, triple_count,
                    disk_size_bytes, created_by, created_at, is_published
                ) VALUES (?, ?, 'active', ?, ?, 0, ?, 'auto_sync', ?, 1)
                """,
                (kb_id, new_ver, changelog, body_cnt, disk_sz, now_ts),
            )
            await conn.commit()
        # 同步登记到版本快照表 kb_version_snapshots，供「版本管理与回滚」抽屉实时展示、比对与回滚
        try:
            from ...kb.exchange import KBExchangeEngine

            await KBExchangeEngine.create_snapshot(
                kb_id=kb_id,
                version_tag=new_ver,
                snapshot_type="release",
                change_summary=changelog,
                parent_version_tag=prev_ver,
            )
        except Exception as snap_err:
            logger.debug(f"[KBSync] 生成增量快照记录 {kb_id} ({new_ver}) 跳过: {snap_err}")

        logger.info(f"[KBSync] 知识库 {kb_id} 增量训练完成，版本已自动递增: {prev_ver} -> {new_ver}")
    except Exception as exc:
        logger.warning(f"[KBSync] 提交增量版本指针 {kb_id} ({new_ver}) 异常: {exc}")


async def run_incremental_sync(
    *,
    mount_id: str | None = None,
    workspace_root: str = ".",
    task_id: str | None = None,
    source_tag: str = "manual",
    turbo_mode: bool = False,
) -> dict[str, Any]:
    """执行挂载源增量同步（默认温和节流保护 CPU/I/O，仅在显式开启 turbo_mode 时全力并发）。"""
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

        # 阶段 1：温和控制挂载源扫描并发（默认 2 并发，全力模式 4 并发），避免瞬间打满网络与 CPU
        mount_plans: list[tuple[dict[str, Any], Any, list[Any], list[str]]] = []
        scan_sem = asyncio.Semaphore(4 if turbo_mode else 2)

        async def _scan_single_mount(m: dict[str, Any]) -> tuple[dict[str, Any], Any, list[Any], list[str]] | None:
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
                return None

            async with scan_sem:
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
                    removed_uris = sync_mgr.find_removed_uris(m["mount_id"], files)

                    # 防呆自愈：若当前激活版本独立知识库目录/切片已被物理清空或尚未建库，自动重置水位线全量重建
                    if files and not modified:
                        from ...kb.storage import KBStorageManager
                        kb_id_chk = f"kb_{m['mount_id']}"
                        target_ver_chk = await _resolve_kb_active_version(kb_id_chk)
                        ver_db = KBStorageManager.get_version_db_path(kb_id_chk, target_ver_chk)
                        has_any_chunks = False
                        if ver_db.exists():
                            try:
                                import sqlite3 as _s3
                                with _s3.connect(str(ver_db)) as _c:
                                    _tbl = _c.execute(
                                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='hybrid_chunks'"
                                    ).fetchone()
                                    if _tbl and int(_c.execute("SELECT COUNT(*) FROM hybrid_chunks").fetchone()[0] or 0) > 0:
                                        has_any_chunks = True
                            except Exception:
                                pass
                        if not has_any_chunks:
                            logger.info(f"挂载源 [{m['mount_name']}] 目标知识库 ({target_ver_chk}) 为空/已销毁，自动重置同步水位线以执行全量重建")
                            sync_mgr.clear_sync_state(m["mount_id"])
                            modified = list(files)
                            removed_uris = []

                    return (m, provider, modified, removed_uris)
                except Exception as prov_err:
                    logger.warning(f"挂载源 {m['mount_name']} 增量拉取失败: {prov_err}")
                    sync_warnings.append(f"【{m['mount_name']}】拉取异常: {prov_err}")
                    return None

        _update_sync_progress(
            eff_task_id,
            running=True,
            percent=4.0,
            stage=f"正在并发扫描 {len(mounts)} 个挂载源目录...",
        )
        scan_results = await asyncio.gather(*[_scan_single_mount(m) for m in mounts])
        mount_plans = [r for r in scan_results if r is not None]

        # 阶段 2：先清理已删除/待覆盖文件的旧切片，再执行新增与变更文件的切片向量化（12.0% -> 86.0%）
        total_files = sum(len(mod) for _, _, mod, _ in mount_plans)
        processed_files = 0

        from ...kb.storage import KBStorageManager

        for m, provider, modified, removed_uris in mount_plans:
            kb_id = f"kb_{m['mount_id']}"
            has_mount_changes = bool(modified or removed_uris)
            target_ver, prev_ver = await _prepare_incremental_version(kb_id, has_mount_changes)
            mount_removed_cnt = 0
            # 2.1 清理已在源端删除的文件切片与水位线
            if removed_uris:
                removed_cnt = KBStorageManager.delete_chunks_by_remote_uris(
                    kb_id=kb_id,
                    remote_uris=removed_uris,
                    version=target_ver,
                )
                sync_mgr.delete_sync_state_uris(m["mount_id"], removed_uris)
                total_removed_files += len(removed_uris)
                total_removed_chunks += removed_cnt
                mount_removed_cnt += removed_cnt
                affected_kb_ids.add(kb_id)
                logger.info(
                    f"[KBSync] 挂载源 [{m['mount_name']}] 检测到 {len(removed_uris)} 个文件已删除，已同步清理 {target_ver} 的 {removed_cnt} 个旧切片"
                )

            # 2.2 对内容有变化的文件先删旧切片再重切写入，防止文档变短后尾部残留旧切片
            modified_uris = [f.remote_uri for f in modified if f.remote_uri]
            if modified_uris:
                KBStorageManager.delete_chunks_by_remote_uris(
                    kb_id=kb_id,
                    remote_uris=modified_uris,
                    version=target_ver,
                )

            mount_new_chunks: list[dict[str, Any]] = []
            if modified:
                # 温和模式严格限制单文件流式解析 + 礼让 CPU 时间片，全力模式才放开 4 并发
                file_sem = asyncio.Semaphore(4 if turbo_mode else 1)

                async def _process_single_file(f: Any) -> tuple[Any, list[dict[str, Any]]]:
                    async with file_sem:
                        try:
                            stream = await provider.download_stream(f.remote_uri)
                            chunks = await asyncio.to_thread(
                                DocumentStreamExtractor.extract_chunks,
                                stream,
                                f.title,
                                f.remote_uri,
                                authority_tier=f.authority_tier,
                            )
                            if not turbo_mode:
                                # 主动让出 80ms 时间片，确保前台对话/IM/Web 接口随时响应流畅不卡顿
                                await asyncio.sleep(0.08)
                            return f, chunks or []
                        except Exception as file_err:
                            logger.warning(f"同步单个文件失败 ({f.title}): {file_err}")
                            return f, []

                tasks = [asyncio.create_task(_process_single_file(f)) for f in modified]
                mode_label = "全力并发" if turbo_mode else "温和节能"
                for fut in asyncio.as_completed(tasks):
                    f_obj, f_chunks = await fut
                    if f_chunks:
                        mount_new_chunks.extend(f_chunks)
                        total_new_chunks += len(f_chunks)
                        # NOTE: 仅当文件成功切片时才更新水位线，避免解析失败的文件被误记为已同步，
                        # 导致下次增量训练跳过该文件、知识库永久缺失对应内容
                        sync_mgr.update_sync_state(m["mount_id"], f_obj)
                    else:
                        logger.debug(f"[KBSync] 文件切片为空，跳过水位线更新: {f_obj.title}")
                    processed_files += 1
                    done_pct = 12.0 + (78.0 * (processed_files / total_files) if total_files > 0 else 78.0)
                    _update_sync_progress(
                        eff_task_id,
                        running=True,
                        percent=done_pct,
                        stage=f"正在{mode_label}切片与入库 ({processed_files}/{total_files})",
                        current_file=f_obj.title,
                        processed_files=processed_files,
                        total_files=total_files,
                        new_chunks=total_new_chunks,
                    )

                if mount_new_chunks:
                    await engine.upsert_chunks(mount_new_chunks)

            if mount_new_chunks:
                affected_kb_ids.add(kb_id)
                KBStorageManager.upsert_chunks_to_version_db(
                    kb_id=kb_id,
                    version=target_ver,
                    chunks=mount_new_chunks,
                )

            if prev_ver:
                if mount_new_chunks or mount_removed_cnt > 0:
                    await _commit_incremental_version(
                        kb_id=kb_id,
                        new_ver=target_ver,
                        prev_ver=prev_ver,
                        new_chunks_cnt=len(mount_new_chunks),
                        removed_chunks_cnt=mount_removed_cnt,
                    )
                else:
                    # 若待更新文件均未产生有效切片且未删除任何切片，清理空副本保持原版本不动
                    try:
                        import shutil as _shutil

                        empty_dir = KBStorageManager.get_version_dir(kb_id, target_ver)
                        if empty_dir.exists() and target_ver != prev_ver:
                            _shutil.rmtree(empty_dir, ignore_errors=True)
                    except Exception:
                        pass

        # 阶段 3：若有新增、变更或删除，轻量纳管主库并把重度图谱/口语增强移入后台异步执行，主流程立即完成
        if total_new_chunks > 0 or total_removed_chunks > 0 or affected_kb_ids:
            _update_sync_progress(
                eff_task_id,
                running=True,
                percent=94.0,
                stage="正在更新知识库版本索引并调度后台图谱增强...",
                processed_files=processed_files,
                total_files=total_files,
                new_chunks=total_new_chunks,
            )
            try:
                from ...kb.manager import KBLifecycleManager

                await KBLifecycleManager.auto_adopt_mounted_kbs(only_missing=True)
                if total_new_chunks > 0 and affected_kb_ids:
                    asyncio.create_task(
                        KBLifecycleManager.auto_post_process_trained_kbs(list(affected_kb_ids))
                    )
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
        turbo_mode=req.turbo_mode,
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


