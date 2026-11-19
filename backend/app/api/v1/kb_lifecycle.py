"""
AgentHub Local v1.5.8 知识库生命周期治理、版本发布与专家沙盒隔离路由 (KB Lifecycle API)
覆盖：
1. 数据源连接 CRUD 与断连安全清理策略（模式 A 断连保库 vs 模式 B 连带销毁）；
2. 独立知识库资产管理、多版本快照、原子回滚与 Staging 转正；
3. 团队共享包导出发布、订阅拉取 (只读锁) 与 Fork 本地独立副本；
4. 专家多知识库绑定与优先级加权配置；
5. 检索命中率测试台 (Retrieval Playground) 实时验货。
"""
import asyncio
from pathlib import Path
import shutil
import sqlite3
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
from ...kb.exchange import KBExchangeEngine, get_snapshots_dir
from ...kb.upgrade_service import KBUpgradeService
from ...search.hype_worker import HyPEQuestionWorker
from ...search.multi_kb_router import MultiKBRetrievalRouter
from ...repository.db import db_manager

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
    domain_type: str = Field(default="general", description="业务领域: fencing | equestrian | rugby | general | custom")
    rule_profile_json: str = Field(default="", description="自定义规则模板 JSON 字符串")


class DomainUpdatePayload(BaseModel):
    domain_type: str = Field(default="general", description="业务领域类型")
    rule_profile_json: str = Field(default="", description="自定义规则模板配置 JSON")


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


class PublishTogglePayload(BaseModel):
    version: str = Field(default="v1.0.0", description="版本号")
    publish: bool = Field(default=True, description="True=发布为正式版, False=设为测试维护状态")


class PlaygroundQueryPayload(BaseModel):
    query: str = Field(..., description="模拟测试查询关键词或问句")
    version: str | None = Field(default=None, description="可选锁定指定版本，缺省使用当前激活版本")
    top_k: int = Field(default=5, ge=1, le=20)
    simulate_chat: bool = Field(default=True, description="是否使用大模型以自然语言模拟专家回答")


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
async def list_bases(
    published_only: bool = Query(default=False, description="是否仅返回已发布正式版本的知识库"),
) -> dict[str, Any]:
    """获取知识库资产列表（含物理磁盘占用与切片统计）。"""
    kbs = await KBLifecycleManager.list_knowledge_bases(published_only=published_only)
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
        domain_type=payload.domain_type,
        rule_profile_json=payload.rule_profile_json,
    )
    return {"success": True, "data": res}


@router.put("/bases/{kb_id}/domain")
async def update_base_domain(kb_id: str, payload: DomainUpdatePayload) -> dict[str, Any]:
    """更新知识库的所属业务领域及规则模板配置。"""
    ok = await KBLifecycleManager.update_knowledge_base_domain(
        kb_id=kb_id,
        domain_type=payload.domain_type,
        rule_profile_json=payload.rule_profile_json,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="未找到目标知识库")
    return {"success": True, "message": f"知识库 [{kb_id}] 领域模板已更新为 {payload.domain_type}"}


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
                    try:
                        cur.execute("DELETE FROM hybrid_chunks WHERE remote_uri LIKE ?", (pattern,))
                        if cur.rowcount > 0:
                            cleaned_chunks += cur.rowcount
                    except Exception as chunk_err:
                        logger.debug(f"清理工作区 hybrid_chunks 跳过: {chunk_err}")

                    try:
                        cur.execute("DELETE FROM hybrid_chunks_fts WHERE remote_uri LIKE ?", (pattern,))
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

    # 2. 彻底销毁所有历史快照记录与磁盘 .ahkb 归档包
    await KBExchangeEngine.destroy_all_snapshots_for_kb(kb_id)

    # 3. 清理工作区切片与增量同步水位线（防止重新被 auto_adopt 误纳管复活）
    cleaned = _clean_workspace_chunks_and_sync_state(target_mount_id=source_id)

    # 4. 清理数据库元数据
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

    # 3. 彻底物理清理所有持久化快照归档包
    snapshots_dir = get_snapshots_dir()
    if snapshots_dir.exists():
        for f in snapshots_dir.glob("*.ahkb"):
            try:
                f.unlink()
            except Exception:
                pass

    # 4. 清空全局元数据表
    from ...repository.db import db_manager
    async with db_manager.get_connection() as conn:
        cursor = await conn.execute("SELECT count(*) FROM knowledge_bases")
        row = await cursor.fetchone()
        kb_count = int(row[0]) if row else 0
        await conn.execute("DELETE FROM kb_versions")
        await conn.execute("DELETE FROM kb_version_snapshots")
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
    try:
        res = await KBLifecycleManager.bind_expert_kbs(expert_id, items)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"success": True, "data": res, "message": f"成功为专家绑定 {len(items)} 个知识库"}


@router.post("/bases/{kb_id}/publish-toggle")
@router.post("/{kb_id}/publish-toggle")
async def toggle_kb_version_publish(kb_id: str, payload: PublishTogglePayload) -> dict[str, Any]:
    """切换知识库当前版本的发布状态：发布为正式版或转为测试维护中。"""
    try:
        res = await KBLifecycleManager.set_version_publish_status(
            kb_id=kb_id, version=payload.version, publish=payload.publish
        )
        return res
    except Exception as e:
        logger.error(f"切换知识库发布状态异常: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/bases/{kb_id}/playground")
@router.post("/{kb_id}/playground")
async def test_retrieval_playground(kb_id: str, payload: PlaygroundQueryPayload) -> dict[str, Any]:
    """检索命中率测试台：在指定知识库内执行模拟检索，支持使用大模型以自然语言模拟专家作答检验。"""
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

    # 自然语言模拟专家回答生成
    simulated_answer = ""
    grounding_level = "strong"

    if payload.simulate_chat and chunks:
        from ...service.engine_pool import engine_pool
        context_parts = []
        for i, c in enumerate(chunks[:4]):
            title = c.get("title") or "规程文档"
            section = f" ({c.get('section_path')})" if c.get("section_path") else ""
            content = c.get("content") or ""
            context_parts.append(f"【依据 {i+1}】《{title}》{section}\n{content}")

        context_text = "\n\n".join(context_parts)
        prompt = f"""你是一个专业严谨的规程问答与知识库测试助手。请研读以下从知识库召回的规程切片依据，用通俗清晰、准确严谨的自然语言回答提问。

要求：
1. 优先严格依据所提供的规程切片作答，列出具体条款标准、金额、限制或执行流程；
2. 若依据不足以完整解答，请明确指出“依据未涉及的部分”，严禁凭空猜测；
3. 输出结构：先直接给出结论，再分点列出详细依据。

【知识库召回依据】：
{context_text}

【用户自然语言提问】：
{payload.query}
"""
        try:
            async for evt in engine_pool.execute_stream_with_failover(
                conversation_id="playground_sim_chat",
                user_prompt=prompt,
                model_name="",
                thinking_level="low",
                temperature=0.3,
                pre_built_history=[],
                task_role="light",
            ):
                if evt.get("type") == "chunk":
                    simulated_answer += evt.get("content", "")
                elif evt.get("type") == "done":
                    simulated_answer = evt.get("full_content", simulated_answer)
                    break
        except Exception as sim_err:
            logger.warning(f"自然语言模拟回答生成异常: {sim_err}")
            simulated_answer = f"【依据命中成功】已成功召回 {len(chunks)} 条相关规程切片，首条切片得分 {chunks[0].get('score', 0):.2f}。"

        if not simulated_answer and chunks:
            top_snippet = chunks[0].get("content", "").strip()[:180]
            title = chunks[0].get("title") or "规程文档"
            simulated_answer = (
                f"【依据命中成功】已成功召回 {len(chunks)} 条相关规程切片依据。\n"
                f"首要依据《{title}》显示：\n{top_snippet}...\n\n"
                f"（可在下方【召回证据切片】中展开阅读完整原文并核验事实依据）"
            )
    elif payload.simulate_chat and not chunks:
        simulated_answer = "抱歉，在当前测试的知识库版本中，未检索到与该自然语言提问相关的规程条款依据。建议检查版本是否已训练切片或调整提问方式。"
        grounding_level = "none"

    return {
        "success": True,
        "kb_id": kb_id,
        "name": target_kb["name"],
        "version": version,
        "is_published": bool(target_kb.get("is_published")),
        "query": payload.query,
        "total_hits": len(chunks),
        "simulated_answer": simulated_answer,
        "grounding_status": {
            "level": grounding_level if chunks else "none",
            "top_score": chunks[0].get("score", 0) if chunks else 0,
            "is_grounded": len(chunks) > 0,
            "used_chunks_count": len(chunks),
        },
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
    reveal: bool = Query(default=False, description="是否在系统文件管理器中直接高亮选中导出的资产包"),
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
        if reveal:
            try:
                from .artifacts import _reveal_in_file_manager
                _reveal_in_file_manager(pkg_path)
            except Exception as rev_err:
                logger.warning(f"导出后定位文件夹跳过: {rev_err}")

        from urllib.parse import quote
        safe_local_path = quote(str(pkg_path))
        return FileResponse(
            path=str(pkg_path),
            filename=pkg_path.name,
            media_type="application/zip",
            headers={
                "X-Content-MD5": md5_val,
                "X-KB-Version": target_ver,
                "X-Local-File-Path": safe_local_path,
                "Access-Control-Expose-Headers": "X-Content-MD5, X-KB-Version, X-Local-File-Path",
            },
        )
    except Exception as e:
        logger.error(f"导出 .ahkb 失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"导出知识库包失败: {str(e)}")


class RevealKbFilePayload(BaseModel):
    file_path: str


@router.post("/reveal")
async def reveal_kb_file(payload: RevealKbFilePayload) -> dict[str, Any]:
    """在宿主操作系统的文件资源管理器中打开并高亮定位指定物理路径的文件。"""
    from urllib.parse import unquote
    raw_path = unquote(payload.file_path).strip()
    target = Path(raw_path)
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"目标物理文件不存在: {raw_path}")
    try:
        from .artifacts import _reveal_in_file_manager
        _reveal_in_file_manager(target)
        return {"success": True, "path": str(target)}
    except Exception as e:
        logger.error(f"打开资源管理器失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"打开文件管理器失败: {str(e)}")


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


@router.delete("/{kb_id}/versions/{snapshot_id}")
@router.delete("/bases/{kb_id}/versions/{snapshot_id}")
async def delete_kb_snapshot(kb_id: str, snapshot_id: str) -> dict[str, Any]:
    """删除指定的历史快照归档及物理归档文件（正在生效使用的版本不可删除）。"""
    try:
        res = await KBExchangeEngine.delete_snapshot(kb_id, snapshot_id)
        return {"success": True, "data": res}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(f"删除快照失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"删除快照失败: {str(e)}")


@router.get("/{kb_id}/hierarchy-status")
async def get_kb_hierarchy_status(kb_id: str, version: str = "v1.0.0") -> dict[str, Any]:
    """检查知识库切片的分层索引状态（是否为老旧扁平切片）。"""
    try:
        data = await KBUpgradeService.check_hierarchy_status(kb_id, version)
        return {"success": True, "data": data}
    except Exception as e:
        logger.error(f"检查知识库分层索引状态失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{kb_id}/upgrade-to-parent-child")
async def upgrade_kb_to_parent_child(kb_id: str, version: str = "v1.0.0") -> dict[str, Any]:
    """一键将老旧扁平知识库升级为父子分层结构并调度 HyPE 口语增强。"""
    try:
        res = await KBUpgradeService.upgrade_kb_to_parent_child(kb_id, version)
        return {"success": True, "data": res}
    except Exception as e:
        logger.error(f"升级知识库为分层索引失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{kb_id}/enhancement-status")
async def get_kb_enhancement_status(kb_id: str, version: str = "v1.0.0") -> dict[str, Any]:
    """获取指定知识库后台口语化问答与规则抽取的进度状态。"""
    try:
        data = await HyPEQuestionWorker.get_enhancement_status(kb_id, version)
        return {"success": True, "data": data}
    except Exception as e:
        logger.error(f"查询知识库增强进度失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/bases/{kb_id}/oralized-results")
@router.get("/{kb_id}/oralized-results")
async def get_kb_oralized_results(
    kb_id: str,
    version: str | None = Query(default=None, description="版本号，缺省为当前激活版本"),
    keyword: str | None = Query(default=None, description="关键词搜索"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    """获取指定知识库口语化提炼与刚性规则抽取的详细结果列表。"""
    if not isinstance(version, str):
        version = None
    if not isinstance(keyword, str):
        keyword = None
    if not isinstance(page, int):
        page = 1
    if not isinstance(page_size, int):
        page_size = 20

    kbs = await KBLifecycleManager.list_knowledge_bases()
    target_kb = next((k for k in kbs if k["kb_id"] == kb_id), None)
    if not target_kb:
        raise HTTPException(status_code=404, detail=f"知识库不存在: {kb_id}")

    target_ver = version or target_kb.get("active_version") or "v1.0.0"
    db_path = KBStorageManager.get_version_db_path(kb_id, target_ver)
    if not db_path.exists():
        return {
            "success": True,
            "kb_id": kb_id,
            "version": target_ver,
            "kb_name": target_kb["name"],
            "total_questions": 0,
            "total_parents": 0,
            "matched_parents": 0,
            "rules_extracted": 0,
            "status": "empty",
            "items": [],
            "page": page,
            "page_size": page_size,
        }

    def _query_oral_data():
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        try:
            # 1. 统计总体数据
            cur.execute("SELECT count(*) FROM hybrid_chunks WHERE chunk_role = 'parent'")
            total_parents = cur.fetchone()[0]

            cur.execute("SELECT count(*) FROM hybrid_chunks WHERE chunk_role = 'hype_question'")
            total_questions = cur.fetchone()[0]

            rules_count = 0
            try:
                cur.execute("SELECT count(*) FROM kb_structured_rules WHERE kb_id = ?", (kb_id,))
                rules_count = cur.fetchone()[0]
            except Exception:
                pass

            # 2. 查询父切片
            kw_filter = f"%{keyword.strip()}%" if keyword and keyword.strip() else None

            if kw_filter:
                cur.execute(
                    """
                    SELECT DISTINCT p.id, p.title, p.section_path, p.content, p.remote_uri
                    FROM hybrid_chunks p
                    LEFT JOIN hybrid_chunks q ON q.parent_chunk_id = p.id AND q.chunk_role = 'hype_question'
                    WHERE p.chunk_role = 'parent'
                      AND (p.title LIKE ? OR p.section_path LIKE ? OR p.content LIKE ? OR q.content LIKE ?)
                    ORDER BY p.id ASC
                    """,
                    (kw_filter, kw_filter, kw_filter, kw_filter),
                )
            else:
                cur.execute(
                    """
                    SELECT id, title, section_path, content, remote_uri
                    FROM hybrid_chunks
                    WHERE chunk_role = 'parent'
                    ORDER BY id ASC
                    """
                )
            parent_rows = cur.fetchall()
            matched_total_parents = len(parent_rows)

            offset = (page - 1) * page_size
            paged_parents = parent_rows[offset : offset + page_size]

            items = []
            for prow in paged_parents:
                pid = prow["id"]
                cur.execute(
                    "SELECT id, content FROM hybrid_chunks WHERE chunk_role = 'hype_question' AND parent_chunk_id = ? ORDER BY id ASC",
                    (pid,),
                )
                q_rows = cur.fetchall()
                oral_questions = [r["content"] for r in q_rows if r["content"]]

                rules = []
                try:
                    cur.execute(
                        "SELECT rule_name, rule_type, condition_expression, action_expression FROM kb_structured_rules WHERE kb_id = ? AND parent_chunk_id = ?",
                        (kb_id, pid),
                    )
                    r_rows = cur.fetchall()
                    for rr in r_rows:
                        rules.append({
                            "rule_name": rr["rule_name"],
                            "rule_type": rr["rule_type"],
                            "condition": rr["condition_expression"],
                            "action": rr["action_expression"],
                        })
                except Exception:
                    pass

                items.append({
                    "parent_chunk_id": pid,
                    "title": prow["title"] or "未命名规程文档",
                    "section_path": prow["section_path"] or "",
                    "remote_uri": prow["remote_uri"] or "",
                    "snippet": (prow["content"] or "")[:300] + ("..." if len(prow["content"] or "") > 300 else ""),
                    "full_content": prow["content"] or "",
                    "oral_questions": oral_questions,
                    "rules": rules,
                })

            return {
                "total_parents": total_parents,
                "matched_parents": matched_total_parents,
                "total_questions": total_questions,
                "rules_extracted": rules_count,
                "items": items,
            }
        finally:
            conn.close()

    res = await asyncio.to_thread(_query_oral_data)
    enhancement_status = await HyPEQuestionWorker.get_enhancement_status(kb_id, target_ver)

    return {
        "success": True,
        "kb_id": kb_id,
        "kb_name": target_kb["name"],
        "version": target_ver,
        "total_parents": res["total_parents"],
        "matched_parents": res["matched_parents"],
        "total_questions": res["total_questions"],
        "rules_extracted": res["rules_extracted"],
        "status": enhancement_status.get("status", "idle"),
        "hype_completed": enhancement_status.get("hype_completed", 0),
        "items": res["items"],
        "page": page,
        "page_size": page_size,
    }


@router.post("/{kb_id}/cleanup-garbled")
@router.post("/bases/{kb_id}/cleanup-garbled")
async def cleanup_garbled_chunks(
    kb_id: str,
    version: str | None = Query(default=None, description="指定版本，默认当前活跃版本"),
):
    """扫描指定知识库并级联清理乱码、损坏编码及无效碎片切片。"""
    from ...search.text_quality_gate import TextQualityGate
    from ...repository.db import db_manager

    kbs = await KBLifecycleManager.list_knowledge_bases()
    target_kb = next((k for k in kbs if k["kb_id"] == kb_id), None)
    if not target_kb:
        raise HTTPException(status_code=404, detail="知识库不存在")

    actual_version = version if isinstance(version, str) and version.strip() else None
    target_ver = actual_version or target_kb.get("active_version") or target_kb.get("current_version") or "v1.0.0"
    db_path = KBStorageManager.get_version_db_path(kb_id, target_ver)
    if not db_path.exists():
        raise HTTPException(status_code=404, detail=f"版本 [{target_ver}] 数据库文件不存在")

    def _execute_cleanup() -> dict[str, int]:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        try:
            # 1. 扫描所有 parent 切片
            cur.execute("SELECT id, content FROM hybrid_chunks WHERE chunk_role = 'parent'")
            parents = cur.fetchall()

            bad_parents: set[str] = set()
            for p in parents:
                is_ok, _ = TextQualityGate.is_chunk_acceptable(p["content"] or "", min_body_chars=20)
                if not is_ok:
                    bad_parents.add(p["id"])

            # 2. 扫描所有 child 切片 (属于坏 parent 或自身未通过门控)
            cur.execute("SELECT id, parent_chunk_id, content FROM hybrid_chunks WHERE chunk_role = 'child'")
            children = cur.fetchall()

            bad_children: set[str] = set()
            for c in children:
                pid = c["parent_chunk_id"]
                if pid in bad_parents:
                    bad_children.add(c["id"])
                    continue
                is_ok, _ = TextQualityGate.is_chunk_acceptable(c["content"] or "", min_body_chars=15)
                if not is_ok:
                    bad_children.add(c["id"])

            # 3. 扫描属于坏 parent 的 hype_question
            cur.execute("SELECT id, parent_chunk_id FROM hybrid_chunks WHERE chunk_role = 'hype_question'")
            hypes = cur.fetchall()
            bad_hypes: set[str] = {h["id"] for h in hypes if h["parent_chunk_id"] in bad_parents}

            all_bad_ids = list(bad_parents | bad_children | bad_hypes)
            if all_bad_ids:
                # 批量删除
                batch_size = 400
                for i in range(0, len(all_bad_ids), batch_size):
                    batch = all_bad_ids[i : i + batch_size]
                    q_marks = ",".join(["?"] * len(batch))
                    cur.execute(f"DELETE FROM hybrid_chunks WHERE id IN ({q_marks})", batch)
                    try:
                        cur.execute(f"DELETE FROM hybrid_chunks_fts WHERE chunk_id IN ({q_marks})", batch)
                    except Exception:
                        pass
                    try:
                        cur.execute(f"DELETE FROM knowledge_triples WHERE chunk_id IN ({q_marks})", batch)
                    except Exception:
                        pass

                conn.commit()
                try:
                    cur.execute("INSERT INTO hybrid_chunks_fts(hybrid_chunks_fts) VALUES('rebuild');")
                    conn.commit()
                except Exception:
                    pass

            # 统计剩余数量
            cur.execute("SELECT count(*) FROM hybrid_chunks")
            rem_chunks = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM knowledge_triples")
            rem_triples = cur.fetchone()[0]

            return {
                "removed_parents": len(bad_parents),
                "removed_children": len(bad_children),
                "removed_hype": len(bad_hypes),
                "remaining_chunks": rem_chunks,
                "remaining_triples": rem_triples,
            }
        finally:
            conn.close()

    res = await asyncio.to_thread(_execute_cleanup)

    # 同步更新主表 chunk_count
    async with db_manager.get_connection() as conn:
        await conn.execute(
            "UPDATE knowledge_bases SET chunk_count = ?, triple_count = ?, updated_at = CURRENT_TIMESTAMP WHERE kb_id = ?",
            (res["remaining_chunks"], res["remaining_triples"], kb_id),
        )

    total_removed = res["removed_parents"] + res["removed_children"] + res["removed_hype"]
    logger.info(
        f"[KBGarbledCleanup] 知识库 [{kb_id}] 版本 [{target_ver}] 乱码清理完毕: "
        f"清除父切片 {res['removed_parents']} 个, 子切片 {res['removed_children']} 个, 口语问答 {res['removed_hype']} 个"
    )

    return {
        "success": True,
        "kb_id": kb_id,
        "version": target_ver,
        "cleaned_count": total_removed,
        "message": f"成功清理 {total_removed} 个乱码切片（父切片: {res['removed_parents']}，子切片: {res['removed_children']}，口语索引: {res['removed_hype']}）",
        **res,
    }


@router.post("/{kb_id}/rebuild-hype")
@router.post("/bases/{kb_id}/rebuild-hype")
async def rebuild_kb_hype(
    kb_id: str,
    version: str | None = Query(default=None, description="指定版本，默认当前活跃版本"),
):
    """清空当前知识库已有的口语问答 (HyPE)，并基于新短口语规范重新全量提炼。"""
    from ...repository.db import db_manager

    kbs = await KBLifecycleManager.list_knowledge_bases()
    target_kb = next((k for k in kbs if k["kb_id"] == kb_id), None)
    if not target_kb:
        raise HTTPException(status_code=404, detail="知识库不存在")

    actual_version = version if isinstance(version, str) and version.strip() else None
    target_ver = actual_version or target_kb.get("active_version") or target_kb.get("current_version") or "v1.0.0"
    db_path = KBStorageManager.get_version_db_path(kb_id, target_ver)
    if not db_path.exists():
        raise HTTPException(status_code=404, detail=f"版本 [{target_ver}] 数据库文件不存在")

    def _clear_existing_hype() -> int:
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        try:
            cur.execute("SELECT count(*) FROM hybrid_chunks WHERE chunk_role = 'hype_question'")
            existing_count = cur.fetchone()[0]

            cur.execute("DELETE FROM hybrid_chunks WHERE chunk_role = 'hype_question'")
            # 重置曾因连续失败被熔断的父切片标记，允许手工重构时重新纳入提炼
            try:
                cur.execute("UPDATE hybrid_chunks SET skip_hype = 0 WHERE chunk_role = 'parent' AND skip_hype = 1")
            except Exception:
                pass
            conn.commit()
            try:
                cur.execute("INSERT INTO hybrid_chunks_fts(hybrid_chunks_fts) VALUES('rebuild');")
                conn.commit()
            except Exception:
                pass
            return existing_count
        finally:
            conn.close()

    cleared = await asyncio.to_thread(_clear_existing_hype)

    # 清空内存熔断记录并触发后台重新提炼
    HyPEQuestionWorker.reset_failed_cache(kb_id)
    HyPEQuestionWorker.schedule_background_hype(kb_id, target_ver)

    logger.info(f"[HyPERebuild] 知识库 [{kb_id}] 已清空旧废话口语切片 {cleared} 条，已触发后台重构提炼")

    return {
        "success": True,
        "kb_id": kb_id,
        "version": target_ver,
        "cleared_count": cleared,
        "message": f"已清空 {cleared} 条旧口语问答，新口语化提炼已在后台启动",
    }


class RenameKBRequest(BaseModel):
    name: str


@router.patch("/{kb_id}/rename")
@router.patch("/bases/{kb_id}/rename")
async def rename_knowledge_base(kb_id: str, req: RenameKBRequest) -> dict[str, Any]:
    """对知识库进行重命名，立即更新主数据库并返回更新后元数据。"""
    new_name = req.name.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="知识库名称不能为空")

    async with db_manager.get_connection() as conn:
        async with conn.execute(
            "SELECT * FROM knowledge_bases WHERE kb_id = ?", (kb_id,)
        ) as cur:
            row = await cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="知识库不存在")
            source_id = row["source_id"]

        now_ts = time.time()
        await conn.execute(
            "UPDATE knowledge_bases SET name = ?, updated_at = ? WHERE kb_id = ?",
            (new_name, now_ts, kb_id),
        )
        if source_id:
            try:
                await conn.execute(
                    "UPDATE kb_data_sources SET name = ? WHERE source_id = ?",
                    (new_name, source_id),
                )
            except Exception:
                pass
        await conn.commit()

    logger.info(f"[KBLifecycle] 知识库 [{kb_id}] 成功重命名并持久化提交为: {new_name}")
    return {"success": True, "data": {"kb_id": kb_id, "name": new_name}}




