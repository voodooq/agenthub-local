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
import shutil
import tempfile
import time
from typing import Any
from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ...core.logging_config import logger
from ...kb.manager import KBLifecycleManager
from ...kb.package import KBPackageEngine
from ...kb.storage import KBStorageManager
from ...kb.exchange import KBExchangeEngine
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


def _clean_workspace_chunks_and_sync_state(target_mount_id: str | None = None) -> int:
    """清理工作区中的云端切片、图谱三元组与增量同步水位线（防止删库后被 auto_adopt 误复活）。

    Args:
        target_mount_id: 指定清理的挂载源 ID，为 None 时清空全部云端切片与水位线。
    """
    candidate_dbs: list[Path] = []
    default_index = Path(".agenthub") / "index.db"
    if default_index.exists():
        candidate_dbs.append(default_index)

    workspaces_dir = Path("data") / "workspaces"
    if workspaces_dir.exists():
        for ws in workspaces_dir.iterdir():
            idx = ws / ".agenthub" / "index.db"
            if idx.exists():
                candidate_dbs.append(idx)

    cleaned_chunks = 0
    import sqlite3
    for db_p in candidate_dbs:
        try:
            with sqlite3.connect(str(db_p)) as conn:
                cur = conn.cursor()
                if target_mount_id:
                    # 单个挂载源模式：清理包含该 mount_id 的切片与水位线
                    pattern = f"%{target_mount_id}%"
                    cur.execute("SELECT id FROM hybrid_chunks WHERE remote_uri LIKE ?", (pattern,))
                    bad_ids = [r[0] for r in cur.fetchall()]
                    if bad_ids:
                        cleaned_chunks += len(bad_ids)
                        ph = ",".join("?" for _ in bad_ids)
                        cur.execute(f"DELETE FROM hybrid_chunks WHERE id IN ({ph})", bad_ids)
                        try:
                            cur.execute(f"DELETE FROM hybrid_chunks_fts WHERE chunk_id IN ({ph})", bad_ids)
                        except Exception:
                            pass
                    # 清理水位线
                    try:
                        cur.execute("DELETE FROM cloud_vfs_sync_state WHERE mount_id = ?", (target_mount_id,))
                    except Exception:
                        pass
                else:
                    # 全量清理模式：清除所有云端切片、图谱三元组与水位线
                    try:
                        cur.execute("SELECT count(*) FROM hybrid_chunks WHERE remote_uri LIKE 'webdav://%' OR remote_uri LIKE 'oss://%' OR remote_uri LIKE 'wecom://%'")
                        cleaned_chunks += cur.fetchone()[0]
                        cur.execute("DELETE FROM hybrid_chunks WHERE remote_uri LIKE 'webdav://%' OR remote_uri LIKE 'oss://%' OR remote_uri LIKE 'wecom://%'")
                        cur.execute("DELETE FROM hybrid_chunks_fts WHERE remote_uri LIKE 'webdav://%' OR remote_uri LIKE 'oss://%' OR remote_uri LIKE 'wecom://%'")
                    except Exception:
                        pass
                    # 清空工作区图谱三元组
                    try:
                        cur.execute("DELETE FROM knowledge_triples")
                    except Exception:
                        pass
                    # 清空全部水位线
                    try:
                        cur.execute("DELETE FROM cloud_vfs_sync_state")
                    except Exception:
                        pass

                conn.commit()
                # VACUUM 释放工作区数据库磁盘空间
                try:
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                    conn.execute("VACUUM;")
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"清理工作区 [{db_p}] 切片失败: {e}")

    return cleaned_chunks


@router.delete("/bases/{kb_id}")
async def delete_base(kb_id: str) -> dict[str, Any]:
    """彻底物理销毁指定知识库及所有历史版本切片、三元组与文件。"""
    from ...repository.db import db_manager
    source_id = None
    async with db_manager.get_connection() as conn:
        cursor = await conn.execute("SELECT source_id FROM knowledge_bases WHERE kb_id = ?", (kb_id,))
        row = await cursor.fetchone()
        if row:
            source_id = row["source_id"]

    # 1. 物理删除独立存储目录与文件
    KBStorageManager.physically_destroy_kb(kb_id)

    # 2. 清理工作区切片与增量同步水位线（防止重新被 auto_adopt 误纳管复活）
    cleaned = _clean_workspace_chunks_and_sync_state(target_mount_id=source_id)

    # 3. 清理数据库元数据
    async with db_manager.get_connection() as conn:
        await conn.execute("DELETE FROM kb_versions WHERE kb_id = ?", (kb_id,))
        await conn.execute("DELETE FROM expert_kb_bindings WHERE kb_id = ?", (kb_id,))
        await conn.execute("DELETE FROM knowledge_bases WHERE kb_id = ?", (kb_id,))
        await conn.commit()

    return {
        "success": True,
        "message": f"知识库 {kb_id} 及其所有版本切片、三元组与物理文件已彻底销毁（已清理 {cleaned} 个工作区切片并重置同步水位线）",
    }


@router.post("/bases/purge-all")
async def purge_all_bases() -> dict[str, Any]:
    """一键物理彻底销毁系统所有独立知识库物理文件、切片、图谱三元组并重置增量水位线。"""
    # 1. 物理销毁全部独立存储目录（物理文件全部删除）
    dir_cnt = KBStorageManager.physically_destroy_all_kbs()

    # 2. 清理所有工作区中的云端切片、图谱三元组与全部增量同步水位线
    cleaned_chunks = _clean_workspace_chunks_and_sync_state(target_mount_id=None)

    # 3. 清空全局元数据表
    from ...repository.db import db_manager
    async with db_manager.get_connection() as conn:
        cursor = await conn.execute("SELECT count(*) FROM knowledge_bases")
        kb_count = (await cursor.fetchone())[0]
        await conn.execute("DELETE FROM kb_versions")
        await conn.execute("DELETE FROM expert_kb_bindings")
        await conn.execute("DELETE FROM knowledge_bases")
        await conn.commit()

    return {
        "success": True,
        "message": f"已彻底物理销毁全部 {kb_count} 个独立知识库物理文件（共清理 {dir_cnt} 个物理目录、{cleaned_chunks} 个工作区切片与所有图谱三元组，并已重置同步水位线）",
    }


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


@router.get("/bases/{kb_id}/chunks")
async def list_kb_chunks(
    kb_id: str,
    version: str | None = Query(default=None, description="版本号，缺省为当前激活版本"),
    page: int = Query(default=1, ge=1, description="页码"),
    page_size: int = Query(default=20, ge=1, le=100, description="每页切片数"),
    search: str | None = Query(default=None, description="可选搜索关键词过滤"),
) -> dict[str, Any]:
    """分页查看指定独立知识库中的规程切片与分块详情。"""
    kbs = await KBLifecycleManager.list_knowledge_bases()
    target_kb = next((k for k in kbs if k["kb_id"] == kb_id), None)
    if not target_kb:
        raise HTTPException(status_code=404, detail=f"知识库不存在: {kb_id}")

    target_ver = version or target_kb["active_version"]
    db_p = KBStorageManager.get_version_db_path(kb_id, target_ver)
    if not db_p.exists():
        return {
            "success": True,
            "kb_id": kb_id,
            "name": target_kb["name"],
            "version": target_ver,
            "total_count": 0,
            "page": page,
            "page_size": page_size,
            "chunks": [],
        }

    import sqlite3
    conn = sqlite3.connect(str(db_p))
    conn.row_factory = sqlite3.Row
    try:
        where_clause = ""
        params: list[Any] = []
        if search and search.strip():
            kw = f"%{search.strip()}%"
            where_clause = "WHERE content LIKE ? OR title LIKE ?"
            params.extend([kw, kw])

        # 统计总数
        count_sql = f"SELECT count(*) FROM hybrid_chunks {where_clause}"
        cursor = conn.execute(count_sql, params)
        total_count = cursor.fetchone()[0]

        # 分页查询
        offset = (page - 1) * page_size
        query_sql = f"""
        SELECT id, remote_uri, title, content, authority_tier, mtime, created_at
        FROM hybrid_chunks
        {where_clause}
        ORDER BY rowid ASC
        LIMIT ? OFFSET ?
        """
        cursor = conn.execute(query_sql, params + [page_size, offset])
        rows = cursor.fetchall()

        chunks_data = []
        for r in rows:
            content_str = r["content"] or ""
            chunks_data.append({
                "id": str(r["id"]),
                "remote_uri": r["remote_uri"],
                "title": r["title"],
                "content": content_str,
                "authority_tier": r["authority_tier"],
                "mtime": r["mtime"],
                "created_at": r["created_at"],
                "char_count": len(content_str),
            })

        return {
            "success": True,
            "kb_id": kb_id,
            "name": target_kb["name"],
            "version": target_ver,
            "total_count": total_count,
            "page": page,
            "page_size": page_size,
            "chunks": chunks_data,
        }
    finally:
        conn.close()


@router.post("/bases/{kb_id}/extract-graph")
async def extract_kb_graph(
    kb_id: str,
    version: str | None = Query(default=None, description="版本号，缺省为当前激活版本"),
    force_rebuild: bool = Query(default=False, description="是否强制全量重构图谱（缺省 False 为增量水位线模式）"),
) -> dict[str, Any]:
    """触发对指定知识库中未抽取的切片进行增量抽取，或全量重构知识图谱三元组。"""
    kbs = await KBLifecycleManager.list_knowledge_bases()
    target_kb = next((k for k in kbs if k["kb_id"] == kb_id), None)
    if not target_kb:
        raise HTTPException(status_code=404, detail=f"知识库不存在: {kb_id}")

    target_ver = version or target_kb["active_version"]
    new_extracted, total = KBStorageManager.extract_and_save_triples_for_kb(kb_id, target_ver, force_rebuild=force_rebuild)

    from ...repository.db import db_manager
    async with db_manager.get_connection() as conn:
        await conn.execute(
            "UPDATE knowledge_bases SET triple_count = ?, updated_at = ? WHERE kb_id = ?",
            (total, time.time(), kb_id),
        )
        await conn.execute(
            "UPDATE kb_versions SET triple_count = ? WHERE kb_id = ? AND version = ?",
            (total, kb_id, target_ver),
        )
        await conn.commit()

    if new_extracted == 0 and not force_rebuild:
        msg = f"【{target_kb['name']}】所有切片均已完成图谱构建（共 {total} 条关系边），未重复消耗算力。"
    else:
        msg = f"成功为【{target_kb['name']}】增量抽取/强化印证 {new_extracted} 条关系边，当前总计 {total} 条"

    return {
        "success": True,
        "kb_id": kb_id,
        "name": target_kb["name"],
        "version": target_ver,
        "new_extracted": new_extracted,
        "triple_count": total,
        "message": msg,
    }


@router.get("/bases/{kb_id}/triples")
async def list_kb_triples(
    kb_id: str,
    version: str | None = Query(default=None, description="版本号，缺省为当前激活版本"),
    page: int = Query(default=1, ge=1, description="页码"),
    page_size: int = Query(default=30, ge=1, le=100, description="每页三元组数"),
    search: str | None = Query(default=None, description="可选搜索关键词过滤（主体/谓词/客体）"),
) -> dict[str, Any]:
    """分页查看指定独立知识库中的知识图谱三元组资产清单（优先按置信度/验证频次降序）。"""
    kbs = await KBLifecycleManager.list_knowledge_bases()
    target_kb = next((k for k in kbs if k["kb_id"] == kb_id), None)
    if not target_kb:
        raise HTTPException(status_code=404, detail=f"知识库不存在: {kb_id}")

    target_ver = version or target_kb["active_version"]
    db_p = KBStorageManager.get_version_db_path(kb_id, target_ver)
    if not db_p.exists():
        return {
            "success": True,
            "kb_id": kb_id,
            "name": target_kb["name"],
            "version": target_ver,
            "total_count": 0,
            "page": page,
            "page_size": page_size,
            "triples": [],
        }

    import sqlite3
    conn = sqlite3.connect(str(db_p))
    conn.row_factory = sqlite3.Row
    try:
        where_clause = ""
        params: list[Any] = []
        if search and search.strip():
            kw = f"%{search.strip()}%"
            where_clause = "WHERE subject LIKE ? OR predicate LIKE ? OR object LIKE ?"
            params.extend([kw, kw, kw])

        count_sql = f"SELECT count(*) FROM knowledge_triples {where_clause}"
        cursor = conn.execute(count_sql, params)
        total_count = cursor.fetchone()[0]

        offset = (page - 1) * page_size
        query_sql = f"""
        SELECT id, subject, predicate, object, chunk_id, confidence, created_at
        FROM knowledge_triples
        {where_clause}
        ORDER BY confidence DESC, id ASC
        LIMIT ? OFFSET ?
        """
        cursor = conn.execute(query_sql, params + [page_size, offset])
        rows = cursor.fetchall()

        triples_data = [
            {
                "id": r["id"],
                "subject": r["subject"],
                "predicate": r["predicate"],
                "object": r["object"],
                "chunk_id": r["chunk_id"],
                "confidence": r["confidence"] if "confidence" in r.keys() else 1.0,
                "created_at": r["created_at"],
            }
            for r in rows
        ]

        return {
            "success": True,
            "kb_id": kb_id,
            "name": target_kb["name"],
            "version": target_ver,
            "total_count": total_count,
            "page": page,
            "page_size": page_size,
            "triples": triples_data,
        }
    finally:
        conn.close()


# ==========================================
# 6. v1.5.9 资产交换与版本快照治理 API
# ==========================================

@router.get("/{kb_id}/export")
async def export_kb_bundle(
    kb_id: str,
    version: str | None = Query(default=None, description="版本号，缺省为当前激活版本"),
    author: str = Query(default="AgentHub User", description="导出人"),
    change_summary: str = Query(default="", description="版本变更说明"),
):
    """将指定版本的知识库打包导出为标准 .ahkb 归档文件。"""
    kbs = await KBLifecycleManager.list_knowledge_bases()
    target_kb = next((k for k in kbs if k["kb_id"] == kb_id), None)
    if not target_kb:
        raise HTTPException(status_code=404, detail=f"知识库不存在: {kb_id}")

    target_ver = version or target_kb.get("active_version") or "v1.0.0"
    try:
        pkg_path, md5_val, manifest = KBExchangeEngine.export_bundle(
            kb_id=kb_id,
            version=target_ver,
            author=author,
            change_summary=change_summary,
        )
        return FileResponse(
            path=str(pkg_path),
            filename=pkg_path.name,
            media_type="application/zip",
            headers={
                "X-Content-MD5": md5_val,
                "X-KB-Version": target_ver,
                "Access-Control-Expose-Headers": "X-Content-MD5, X-KB-Version",
            },
        )
    except Exception as e:
        logger.error(f"导出 .ahkb 失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"导出知识库包失败: {str(e)}")


@router.post("/import")
async def import_kb_bundle(
    file: UploadFile = File(..., description=".ahkb 资产包文件"),
    conflict_strategy: str = Form(default="ask", description="ask | backup_and_overwrite | import_as_new"),
    target_kb_name: str = Form(default="", description="导入后的知识库名称覆盖"),
) -> dict[str, Any]:
    """导入 .ahkb 知识库包，支持 MD5 幂等跳过、冲突询问与自动快照备份覆盖。"""
    if not file.filename or not (file.filename.endswith(".ahkb") or file.filename.endswith(".zip")):
        raise HTTPException(status_code=400, detail="仅支持上传 .ahkb 知识库资产包")

    # 写入临时文件
    suffix = Path(file.filename).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp_path = Path(tmp.name)
        shutil.copyfileobj(file.file, tmp)

    try:
        result = await KBExchangeEngine.import_bundle(
            bundle_path=tmp_path,
            conflict_strategy=conflict_strategy,
            target_kb_name=target_kb_name,
        )
        return {"success": True, "data": result}
    except Exception as e:
        logger.error(f"导入 .ahkb 失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"导入知识库资产包失败: {str(e)}")
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass


@router.get("/{kb_id}/versions")
async def list_kb_versions(kb_id: str) -> dict[str, Any]:
    """获取指定知识库的所有历史快照记录与自动备份列表。"""
    snapshots = await KBExchangeEngine.list_snapshots(kb_id)
    return {"success": True, "data": snapshots}


@router.get("/{kb_id}/versions/diff")
async def diff_kb_versions(
    kb_id: str,
    base_snapshot: str = Query(..., description="基准快照 ID"),
    target_snapshot: str = Query(..., description="目标快照 ID"),
) -> dict[str, Any]:
    """比对两个快照版本之间的文档增删改差异。"""
    try:
        diff_res = await KBExchangeEngine.compute_snapshot_diff(base_snapshot, target_snapshot)
        return {"success": True, "data": diff_res}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{kb_id}/versions/{snapshot_id}/rollback")
async def rollback_kb_snapshot(kb_id: str, snapshot_id: str) -> dict[str, Any]:
    """将知识库安全回滚至指定快照（回滚前自动生成 auto_backup）。"""
    try:
        res = await KBExchangeEngine.rollback_to_snapshot(kb_id, snapshot_id)
        return {"success": True, "data": res}
    except Exception as e:
        logger.error(f"知识库回滚失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"回滚失败: {str(e)}")


