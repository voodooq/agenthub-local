"""
AgentHub Local v1.5.8 知识库生命周期管理核心 (KB Lifecycle Manager)
核心功能：
1. 数据源注册与双模式安全清理（断连保库 frozen_snapshot vs 物理连带销毁）；
2. 知识库资产元数据维护、版本快照流转（staging -> ready / rollback）；
3. 专家角色与知识库绑定路由管理；
4. 检索命中率测试台 (Retrieval Playground) 执行。
"""
import asyncio
import json
from pathlib import Path
import time
from typing import Any
import aiosqlite

from ..core.logging_config import logger
from ..repository.db import db_manager
from .storage import KBStorageManager


class KBLifecycleManager:
    """知识库全生命周期治理服务。"""

    # ==========================================
    # 1. 数据源生命周期与断连保护
    # ==========================================

    @classmethod
    async def create_data_source(
        cls,
        source_id: str,
        name: str,
        source_type: str,
        config: dict[str, Any],
        ignore_patterns: str = "~$*,*.tmp,.git/*",
        sync_mode: str = "auto_live",
        default_tier: int = 2,
    ) -> dict[str, Any]:
        """注册并保存数据源连接。"""
        now = time.time()
        config_json = json.dumps(config, ensure_ascii=False)
        async with db_manager.get_connection() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO kb_data_sources (
                    source_id, name, source_type, config_json, ignore_patterns,
                    sync_mode, default_tier, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?)
                """,
                (source_id, name, source_type, config_json, ignore_patterns, sync_mode, default_tier, now),
            )
            await conn.commit()

        logger.info(f"成功注册数据源连接: {name} ({source_type}, ID: {source_id})")
        return {
            "source_id": source_id,
            "name": name,
            "source_type": source_type,
            "config": config,
            "sync_mode": sync_mode,
            "default_tier": default_tier,
            "status": "active",
        }

    @classmethod
    async def list_data_sources(cls) -> list[dict[str, Any]]:
        """获取所有已注册的数据源连接列表。"""
        async with db_manager.get_connection() as conn:
            async with conn.execute("SELECT * FROM kb_data_sources ORDER BY created_at DESC") as cursor:
                rows = await cursor.fetchall()
                results = []
                for r in rows:
                    try:
                        cfg = json.loads(r["config_json"])
                    except Exception:
                        cfg = {}
                    results.append({
                        "source_id": r["source_id"],
                        "name": r["name"],
                        "source_type": r["source_type"],
                        "config": cfg,
                        "ignore_patterns": r["ignore_patterns"],
                        "sync_mode": r["sync_mode"],
                        "default_tier": r["default_tier"],
                        "status": r["status"],
                        "last_sync_at": r["last_sync_at"],
                        "last_error": r["last_error"],
                        "created_at": r["created_at"],
                    })
                return results

    @classmethod
    async def remove_data_source(
        cls,
        source_id: str,
        destroy_associated_kb: bool = False,
    ) -> dict[str, Any]:
        """删除数据源双模式决策清理：
        - 模式 A (destroy_associated_kb=False): 断开连接，关联知识库置为 🔒 frozen_snapshot 保留快照供专家继续使用；
        - 模式 B (destroy_associated_kb=True): 物理销毁关联知识库与索引文件。
        """
        async with db_manager.get_connection() as conn:
            # 查找关联知识库
            async with conn.execute("SELECT kb_id, name FROM knowledge_bases WHERE source_id = ?", (source_id,)) as cur:
                associated_kbs = await cur.fetchall()

            if destroy_associated_kb:
                # 模式 B：连带物理彻底销毁
                for kb in associated_kbs:
                    kb_id = kb["kb_id"]
                    KBStorageManager.physically_destroy_kb(kb_id)
                    await conn.execute("DELETE FROM kb_versions WHERE kb_id = ?", (kb_id,))
                    await conn.execute("DELETE FROM expert_kb_bindings WHERE kb_id = ?", (kb_id,))
                    await conn.execute("DELETE FROM knowledge_bases WHERE kb_id = ?", (kb_id,))
                    logger.info(f"已彻底连带物理销毁知识库: {kb['name']} ({kb_id})")
                mode_desc = "已物理销毁关联知识库与所有版本索引"
            else:
                # 模式 A：断连保库，切换为已冻结快照
                for kb in associated_kbs:
                    kb_id = kb["kb_id"]
                    await conn.execute(
                        "UPDATE knowledge_bases SET source_id = NULL, ownership_type = 'frozen_snapshot', updated_at = ? WHERE kb_id = ?",
                        (time.time(), kb_id),
                    )
                    logger.info(f"知识库 {kb['name']} ({kb_id}) 已安全冻结为离线快照，解除数据源关联")
                mode_desc = "已保留已训练知识库快照，状态转为已冻结 (Frozen Snapshot)"

            # 删除数据源记录
            await conn.execute("DELETE FROM kb_data_sources WHERE source_id = ?", (source_id,))
            await conn.commit()

        return {
            "success": True,
            "source_id": source_id,
            "destroy_associated_kb": destroy_associated_kb,
            "message": f"数据源已移除。{mode_desc}",
        }

    # ==========================================
    # 2. 知识库资产与版本快照流转
    # ==========================================

    @classmethod
    async def create_knowledge_base(
        cls,
        kb_id: str,
        name: str,
        description: str = "",
        source_id: str | None = None,
        embedding_model: str = "null_offline_lexical",
        embedding_dim: int = 768,
        initial_version: str = "v1.0.0",
        ownership_type: str = "local",
        domain_type: str = "general",
        rule_profile_json: str = "",
    ) -> dict[str, Any]:
        """创建新知识库资产并初始化 v1.0.0 独立存储。"""
        now = time.time()
        # 初始化物理存储目录与空数据库
        db_path = KBStorageManager.get_version_db_path(kb_id, initial_version)
        KBStorageManager.init_version_db_schema(db_path)
        KBStorageManager.write_manifest(
            kb_id=kb_id,
            version=initial_version,
            embedding_model=embedding_model,
            embedding_dim=embedding_dim,
            chunk_count=0,
            triple_count=0,
            changelog="初始化首个版本库",
        )

        disk_size = KBStorageManager.get_kb_disk_size(kb_id)

        async with db_manager.get_connection() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO knowledge_bases (
                    kb_id, name, description, ownership_type, source_id,
                    active_version, staging_version, embedding_model, embedding_dim,
                    chunk_count, triple_count, disk_size_bytes, domain_type, rule_profile_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, '', ?, ?, 0, 0, ?, ?, ?, ?, ?)
                """,
                (
                    kb_id, name, description, ownership_type, source_id, initial_version,
                    embedding_model, embedding_dim, disk_size, domain_type, rule_profile_json, now, now
                ),
            )
            await conn.execute(
                """
                INSERT OR REPLACE INTO kb_versions (
                    kb_id, version, status, changelog, chunk_count, triple_count,
                    disk_size_bytes, created_by, created_at
                ) VALUES (?, ?, 'ready', '初始化版本', 0, 0, ?, 'local', ?)
                """,
                (kb_id, initial_version, disk_size, now),
            )
            await conn.commit()

        logger.info(f"成功创建知识库资产: {name} (ID: {kb_id}, 版本: {initial_version}, 领域: {domain_type})")
        return {
            "kb_id": kb_id,
            "name": name,
            "active_version": initial_version,
            "ownership_type": ownership_type,
            "domain_type": domain_type,
            "disk_size_bytes": disk_size,
        }

    @classmethod
    async def update_knowledge_base_domain(
        cls, kb_id: str, domain_type: str, rule_profile_json: str = ""
    ) -> bool:
        """更新知识库的所属业务领域及自定义规则模板 JSON。"""
        now = time.time()
        async with db_manager.get_connection() as conn:
            cur = await conn.execute(
                """
                UPDATE knowledge_bases
                SET domain_type = ?, rule_profile_json = ?, updated_at = ?
                WHERE kb_id = ?
                """,
                (domain_type, rule_profile_json, now, kb_id),
            )
            await conn.commit()
            return cur.rowcount > 0

    @classmethod
    async def auto_adopt_mounted_kbs(cls, *, only_missing: bool = False) -> None:
        """自动扫描云端挂载源并纳管已在工作区中训练切片的独立知识库资产（带 mtime 与已发布版本双重短路）。"""
        try:
            from ..api.v1.knowledge import _load_mounts
            mounts = _load_mounts()
            if not mounts:
                return

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

            max_db_mtime = 0.0
            for db_p in candidate_dbs:
                try:
                    max_db_mtime = max(max_db_mtime, db_p.stat().st_mtime)
                except OSError:
                    pass

            for m in mounts:
                m_id = m["mount_id"]
                kb_id = f"kb_{m_id}"
                m_name = m["mount_name"]
                p_type = m["provider_type"]
                cfg = m.get("config", {})

                # 双重短路检查：已存在且版本已升级或工作区 index.db 未变更时跳过磁盘扫描与写库
                async with db_manager.get_connection() as conn:
                    cursor = await conn.execute(
                        "SELECT kb_id, chunk_count, updated_at, active_version FROM knowledge_bases WHERE kb_id = ?",
                        (kb_id,),
                    )
                    existing_row = await cursor.fetchone()

                if existing_row:
                    if only_missing:
                        continue
                    existing_updated_at = float(existing_row["updated_at"] or 0.0)
                    existing_active_ver = str(existing_row["active_version"] or "v1.0.0")
                    if existing_active_ver != "v1.0.0" or (
                        max_db_mtime > 0 and existing_updated_at >= max_db_mtime
                    ):
                        logger.debug(f"挂载源 [{m_name}] ({kb_id}) 状态无变化，跳过重复纳管")
                        continue

                # 严格计算属于本挂载源的 valid_prefix 与 valid_subpath（防止跨挂载源或越界目录匹配）
                valid_prefix = ""
                valid_subpath = None
                if p_type == "webdav":
                    # WebDAV remote_uri 格式: webdav://{m_id}{rel_path}
                    valid_prefix = f"webdav://{m_id}"
                    root_p = cfg.get("root_path", "").strip()
                    if root_p and root_p != "/":
                        from urllib.parse import unquote
                        valid_subpath = "/" + unquote(root_p).strip("/")
                elif p_type == "oss":
                    bucket = cfg.get("bucket", "").strip()
                    prefix = cfg.get("prefix", "").strip()
                    ep = cfg.get("endpoint", "").strip()
                    if not prefix and ep:
                        from urllib.parse import urlparse
                        ep_p = urlparse(ep if "://" in ep else f"http://{ep}").path.strip("/")
                        if ep_p:
                            prefix = ep_p
                    clean_prefix = prefix.strip("/")
                    if clean_prefix:
                        valid_prefix = f"oss://{bucket}/{clean_prefix}"
                    else:
                        valid_prefix = f"oss://{bucket}/"
                elif p_type == "wecom":
                    space_id = cfg.get("space_id", "").strip()
                    valid_prefix = f"wecom://{space_id}/"
                else:
                    valid_prefix = f"{p_type}://{m_id}"

                # 1. 确保 kb_data_sources 中有记录
                async with db_manager.get_connection() as conn:
                    await conn.execute(
                        """
                        INSERT OR IGNORE INTO kb_data_sources (
                            source_id, name, source_type, config_json, ignore_patterns,
                            sync_mode, default_tier, status, created_at
                        ) VALUES (?, ?, ?, ?, '~$*,*.tmp,.git/*', 'auto_live', ?, 'active', ?)
                        """,
                        (m_id, m_name, p_type, json.dumps(cfg, ensure_ascii=False), m.get("authority_tier", 2), time.time()),
                    )
                    cursor = await conn.execute("SELECT kb_id, chunk_count FROM knowledge_bases WHERE kb_id = ?", (kb_id,))
                    row = await cursor.fetchone()
                    existing_count = row["chunk_count"] if row else 0
                    is_created = bool(row)

                # 2. 清理独立库中不属于该挂载源的切片（防止把历史测试或其他目录的切片串入）
                del_count, remaining, disk_sz = KBStorageManager.clean_invalid_chunks_for_kb(
                    kb_id=kb_id,
                    version="v1.0.0",
                    valid_prefix=valid_prefix,
                    valid_subpath=valid_subpath,
                )
                if del_count > 0:
                    logger.info(f"知识库 {m_name} ({kb_id}) 已隔离清洗 {del_count} 个越界/混淆切片，剩余 {remaining} 个有效切片")
                    now = time.time()
                    async with db_manager.get_connection() as conn:
                        await conn.execute(
                            "UPDATE knowledge_bases SET chunk_count = ?, disk_size_bytes = ?, updated_at = ? WHERE kb_id = ?",
                            (remaining, disk_sz, now, kb_id),
                        )
                        await conn.execute(
                            "UPDATE kb_versions SET chunk_count = ?, disk_size_bytes = ? WHERE kb_id = ? AND version = 'v1.0.0'",
                            (remaining, disk_sz, kb_id),
                        )
                        await conn.commit()
                    existing_count = remaining

                # 3. 从工作区 candidate_dbs 中严格收集属于该挂载源且符合目录的切片
                collected_chunks: list[dict[str, Any]] = []
                for db_p in candidate_dbs:
                    try:
                        import sqlite3 as s3
                        with s3.connect(str(db_p)) as c_conn:
                            c_conn.row_factory = s3.Row
                            cur = c_conn.cursor()
                            cur.execute("SELECT * FROM hybrid_chunks WHERE remote_uri LIKE ?", (f"{valid_prefix}%",))
                            rows = cur.fetchall()
                            for r in rows:
                                uri = r["remote_uri"]
                                is_ok = True
                                if valid_subpath and valid_subpath != "/":
                                    sub = uri[len(valid_prefix):]
                                    from urllib.parse import unquote
                                    clean_norm = "/" + unquote(sub).strip("/")
                                    if not clean_norm.startswith(valid_subpath):
                                        is_ok = False
                                if is_ok:
                                    collected_chunks.append(dict(r))
                    except Exception:
                        pass

                # 去重
                unique_chunks_map = {
                    str(c.get("chunk_id") or c.get("id") or f"{c.get('remote_uri')}_{i}"): c
                    for i, c in enumerate(collected_chunks)
                }
                chunks_list = list(unique_chunks_map.values())

                if chunks_list and (not is_created or existing_count < len(chunks_list)):
                    # 4. 若未创建则新建独立知识库（此时不持有任何外层 DB 锁）
                    if not is_created:
                        await cls.create_knowledge_base(
                            kb_id=kb_id,
                            name=m_name,
                            description=f"由云端挂载源 [{m_name}] ({p_type}) 自动构建的独立规程知识库",
                            source_id=m_id,
                            initial_version="v1.0.0",
                        )

                    # 5. 将切片写入专属版本的独立 index.db
                    total_chunks, disk_size = KBStorageManager.upsert_chunks_to_version_db(
                        kb_id=kb_id,
                        version="v1.0.0",
                        chunks=chunks_list,
                    )

                    # 6. 更新数据库元数据
                    now = time.time()
                    async with db_manager.get_connection() as conn:
                        await conn.execute(
                            """
                            UPDATE knowledge_bases
                            SET chunk_count = ?, disk_size_bytes = ?, updated_at = ?
                            WHERE kb_id = ?
                            """,
                            (total_chunks, disk_size, now, kb_id),
                        )
                        await conn.execute(
                            """
                            UPDATE kb_versions
                            SET chunk_count = ?, disk_size_bytes = ?
                            WHERE kb_id = ? AND version = 'v1.0.0'
                            """,
                            (total_chunks, disk_size, kb_id),
                        )
                        await conn.commit()
                    logger.info(f"成功自动纳管挂载源 [{m_name}] 为独立知识库 ({kb_id})：共纳入 {total_chunks} 个切片")
        except Exception as e:
            logger.warning(f"自动纳管知识库资产异常: {e}")

    @classmethod
    async def auto_post_process_trained_kbs(
        cls, kb_ids: list[str] | None = None
    ) -> dict[str, Any]:
        """每轮训练完知识库后自动执行后置增强流水线：
        1. 自动重构/建立父子关系 (Parent-Child Hierarchy)
        2. 自动抽取并持久化知识图谱三元组 (Knowledge Graph Triples)
        3. 自动触发并调度后台口语化问答提炼与刚性规则抽取 (HyPE & Rules)
        """
        from .upgrade_service import KBUpgradeService
        from .storage import KBStorageManager
        from ..search.hype_worker import HyPEQuestionWorker

        processed_summary: dict[str, Any] = {}

        target_ids = list(kb_ids) if kb_ids else []
        if not target_ids:
            try:
                async with db_manager.get_connection() as conn:
                    async with conn.execute("SELECT kb_id FROM knowledge_bases") as cur:
                        rows = await cur.fetchall()
                        target_ids = [r["kb_id"] for r in rows]
            except Exception as e:
                logger.warning(f"获取知识库列表失败: {e}")
                return processed_summary

        for kid in target_ids:
            try:
                active_ver = "v1.0.0"
                async with db_manager.get_connection() as conn:
                    async with conn.execute("SELECT active_version FROM knowledge_bases WHERE kb_id = ?", (kid,)) as cur:
                        row = await cur.fetchone()
                        if row and row["active_version"]:
                            active_ver = row["active_version"]

                summary_item: dict[str, Any] = {"kb_id": kid, "version": active_ver}

                # 1. 自动建立/重塑父子关系
                hierarchy_status = await KBUpgradeService.check_hierarchy_status(kid, active_ver)
                if hierarchy_status.get("needs_upgrade"):
                    logger.info(f"[AutoPipeline] 知识库 [{kid}] 缺少父子关系，开始自动重塑分层...")
                    upgrade_res = await KBUpgradeService.upgrade_kb_to_parent_child(kid, active_ver)
                    if isinstance(upgrade_res, dict) and upgrade_res.get("version"):
                        active_ver = str(upgrade_res["version"])
                        summary_item["version"] = active_ver
                    summary_item["hierarchy"] = "auto_upgraded"
                else:
                    summary_item["hierarchy"] = "already_hierarchical"

                # 2. 自动抽取构建知识图谱三元组（放到线程池中执行，避免阻塞异步事件循环）
                new_triples, total_triples = await asyncio.to_thread(
                    KBStorageManager.extract_and_save_triples_for_kb, kid, active_ver
                )
                summary_item["new_triples"] = new_triples
                summary_item["total_triples"] = total_triples
                logger.info(f"[AutoPipeline] 知识库 [{kid}] 自动图谱抽取完成: 新增/印证 {new_triples} 条，总计 {total_triples} 条三元组")

                # 3. 自动调度后台口语化问答提炼与刚性规则抽取 (HyPE)
                HyPEQuestionWorker.schedule_background_hype(kid, active_ver)
                summary_item["hype"] = "scheduled"
                logger.info(f"[AutoPipeline] 知识库 [{kid}] 已自动调度后台口语化与规则增强提炼")

                processed_summary[kid] = summary_item
            except Exception as item_err:
                logger.warning(f"[AutoPipeline] 处理知识库 [{kid}] 自动化增强流水线异常: {item_err}")
                processed_summary[kid] = {"error": str(item_err)}

        return processed_summary

    @classmethod
    async def get_kb(cls, kb_id: str) -> dict[str, Any] | None:
        """获取单个知识库详情（包含物理磁盘占用与切片统计）。"""
        async with db_manager.get_connection() as conn:
            async with conn.execute(
                """
                SELECT kb.*, ds.name as source_name, ds.source_type
                FROM knowledge_bases kb
                LEFT JOIN kb_data_sources ds ON kb.source_id = ds.source_id
                WHERE kb.kb_id = ?
                """,
                (kb_id,),
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                real_disk = KBStorageManager.get_kb_disk_size(kb_id)
                res = dict(row)
                res["disk_size_bytes"] = real_disk
                return res

    @classmethod
    async def list_knowledge_bases(cls, published_only: bool = False) -> list[dict[str, Any]]:
        """获取知识库资产列表（包含物理磁盘占用与切片统计）。

        Args:
            published_only: 为 True 时仅返回已发布正式版本的知识库（供专家挂载筛选使用）。
        """
        # 日常查询仅检查是否有尚未注册到主库的新挂载源，避免每次触发全量纳管扫描
        await cls.auto_adopt_mounted_kbs(only_missing=True)
        async with db_manager.get_connection() as conn:
            # 预读取已开启的知识库系统级定时同步任务状态
            sync_sched_map: dict[str, dict[str, str]] = {}
            try:
                sched_cur = await conn.execute(
                    "SELECT id, cron_expr, status, prompt_template FROM scheduled_tasks WHERE id LIKE 'sched_kbsync_%'"
                )
                for sr in await sched_cur.fetchall():
                    pt = str(sr["prompt_template"] or "")
                    if pt.startswith("__SYS_KB_SYNC__:"):
                        target_kb_id = pt.split(":", 1)[1].strip()
                        sync_sched_map[target_kb_id] = {
                            "task_id": str(sr["id"]),
                            "cron_expr": str(sr["cron_expr"] or "*/15 * * * *"),
                            "status": str(sr["status"] or "active"),
                        }
            except Exception:
                pass

            async with conn.execute("""
                SELECT kb.*, ds.name as source_name, ds.source_type
                FROM knowledge_bases kb
                LEFT JOIN kb_data_sources ds ON kb.source_id = ds.source_id
                ORDER BY kb.created_at ASC, kb.kb_id ASC
            """) as cursor:
                rows = await cursor.fetchall()
                kbs = []
                for r in rows:
                    is_pub = bool(r["is_published"]) if "is_published" in r.keys() and r["is_published"] else False
                    if published_only and not is_pub:
                        continue
                    kb_id = r["kb_id"]
                    active_ver = r["active_version"] or "v1.0.0"
                    real_disk = KBStorageManager.get_kb_disk_size(kb_id)
                    body_chunks = KBStorageManager.get_body_chunk_count(kb_id, active_ver)
                    display_chunks = body_chunks if body_chunks is not None else r["chunk_count"]
                    sched_info = sync_sched_map.get(kb_id)
                    kbs.append({
                        "kb_id": kb_id,
                        "name": r["name"],
                        "description": r["description"],
                        "ownership_type": r["ownership_type"],
                        "source_id": r["source_id"],
                        "source_name": r["source_name"] or "",
                        "source_type": r["source_type"] or "",
                        "active_version": r["active_version"],
                        "staging_version": r["staging_version"],
                        "embedding_model": r["embedding_model"],
                        "embedding_dim": r["embedding_dim"],
                        "chunk_count": display_chunks,
                        "triple_count": r["triple_count"],
                        "disk_size_bytes": real_disk,
                        "share_repo_uri": r["share_repo_uri"],
                        "is_published": is_pub,
                        "auto_sync_enabled": bool(sched_info),
                        "auto_sync_cron": sched_info["cron_expr"] if sched_info else "*/15 * * * *",
                        "auto_sync_task_id": sched_info["task_id"] if sched_info else None,
                        "created_at": r["created_at"],
                        "updated_at": r["updated_at"],
                    })
                return kbs

    @classmethod
    async def list_kb_versions(cls, kb_id: str) -> list[dict[str, Any]]:
        """获取指定知识库的所有版本历史与 Changelog。"""
        async with db_manager.get_connection() as conn:
            async with conn.execute(
                "SELECT * FROM kb_versions WHERE kb_id = ? ORDER BY created_at DESC", (kb_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                versions = []
                for r in rows:
                    versions.append({
                        "kb_id": r["kb_id"],
                        "version": r["version"],
                        "status": r["status"],
                        "changelog": r["changelog"],
                        "chunk_count": r["chunk_count"],
                        "triple_count": r["triple_count"],
                        "disk_size_bytes": r["disk_size_bytes"],
                        "package_hash": r["package_hash"],
                        "created_by": r["created_by"],
                        "is_published": bool(r["is_published"]) if "is_published" in r.keys() and r["is_published"] else (r["status"] == "published"),
                        "created_at": r["created_at"],
                    })
                return versions

    @classmethod
    async def set_version_publish_status(
        cls, kb_id: str, version: str, publish: bool
    ) -> dict[str, Any]:
        """切换指定知识库版本的发布状态。

        Args:
            kb_id: 知识库唯一标识
            version: 版本号（如 v1.0.0）
            publish: True=发布为正式版，False=取消发布/设为维护测试中
        """
        now = time.time()
        is_pub_int = 1 if publish else 0
        status_str = "published" if publish else "testing"

        async with db_manager.get_connection() as conn:
            await conn.execute(
                """
                UPDATE kb_versions
                SET is_published = ?, status = ?
                WHERE kb_id = ? AND version = ?
                """,
                (is_pub_int, status_str, kb_id, version),
            )
            if publish:
                await conn.execute(
                    """
                    UPDATE knowledge_bases
                    SET is_published = 1, active_version = ?, updated_at = ?
                    WHERE kb_id = ?
                    """,
                    (version, now, kb_id),
                )
            else:
                async with conn.execute(
                    "SELECT version FROM kb_versions WHERE kb_id = ? AND is_published = 1 AND version != ?",
                    (kb_id, version),
                ) as cur:
                    other_pub = await cur.fetchone()
                    if other_pub:
                        await conn.execute(
                            """
                            UPDATE knowledge_bases
                            SET is_published = 1, active_version = ?, updated_at = ?
                            WHERE kb_id = ?
                            """,
                            (other_pub["version"], now, kb_id),
                        )
                    else:
                        await conn.execute(
                            """
                            UPDATE knowledge_bases
                            SET is_published = 0, updated_at = ?
                            WHERE kb_id = ?
                            """,
                            (now, kb_id),
                        )
            await conn.commit()

        return {
            "success": True,
            "kb_id": kb_id,
            "version": version,
            "is_published": publish,
            "status": status_str,
            "message": f"知识库版本 [{version}] 已成功{'发布为正式版本' if publish else '转为测试维护状态（已取消对外部专家开放）'}",
        }

    @classmethod
    async def rollback_version(cls, kb_id: str, target_version: str) -> dict[str, Any]:
        """原子切换 active_version 指针至指定历史版本（耗时 < 50ms）。"""
        target_db = KBStorageManager.get_version_db_path(kb_id, target_version)
        if not target_db.exists():
            raise FileNotFoundError(f"目标版本数据库不存在: {target_version}")

        # 读取该版本的切片与三元组统计
        manifest = KBStorageManager.read_manifest(kb_id, target_version) or {}
        chunk_count = manifest.get("chunk_count", 0)
        triple_count = manifest.get("triple_count", 0)

        now = time.time()
        async with db_manager.get_connection() as conn:
            await conn.execute(
                """
                UPDATE knowledge_bases
                SET active_version = ?, chunk_count = ?, triple_count = ?, updated_at = ?
                WHERE kb_id = ?
                """,
                (target_version, chunk_count, triple_count, now, kb_id),
            )
            await conn.commit()

        logger.info(f"知识库 [{kb_id}] 已成功原子回滚至版本: {target_version}")
        return {
            "success": True,
            "kb_id": kb_id,
            "active_version": target_version,
            "message": f"已成功回滚至版本 {target_version}",
        }

    @classmethod
    async def promote_staging_version(cls, kb_id: str) -> dict[str, Any]:
        """将审核中的 staging 版本正式提升为 active 激活版本。"""
        async with db_manager.get_connection() as conn:
            async with conn.execute("SELECT staging_version FROM knowledge_bases WHERE kb_id = ?", (kb_id,)) as cur:
                row = await cur.fetchone()
                if not row or not row["staging_version"]:
                    raise ValueError("当前知识库没有待审核转正的 Staging 版本")
                staging_ver = row["staging_version"]

            # 读取该版本数据
            manifest = KBStorageManager.read_manifest(kb_id, staging_ver) or {}
            chunk_count = manifest.get("chunk_count", 0)
            triple_count = manifest.get("triple_count", 0)
            now = time.time()

            await conn.execute(
                """
                UPDATE knowledge_bases
                SET active_version = ?, staging_version = '', chunk_count = ?, triple_count = ?, updated_at = ?
                WHERE kb_id = ?
                """,
                (staging_ver, chunk_count, triple_count, now, kb_id),
            )
            await conn.execute(
                "UPDATE kb_versions SET status = 'ready' WHERE kb_id = ? AND version = ?",
                (kb_id, staging_ver),
            )
            await conn.commit()

        logger.info(f"知识库 [{kb_id}] Staging 版本 {staging_ver} 已转正为正式激活版本")
        return {
            "success": True,
            "kb_id": kb_id,
            "active_version": staging_ver,
            "message": f"Staging 草稿版本 {staging_ver} 已正式发布并生效",
        }

    @classmethod
    async def prune_versions(cls, kb_id: str, keep_count: int = 1) -> dict[str, Any]:
        """磁盘瘦身清理：仅保留最近 keep_count 个版本以及当前 active/staging 版本。"""
        async with db_manager.get_connection() as conn:
            async with conn.execute("SELECT active_version, staging_version FROM knowledge_bases WHERE kb_id = ?", (kb_id,)) as cur:
                kb_info = await cur.fetchone()
                if not kb_info:
                    raise ValueError(f"未找到知识库: {kb_id}")
                active_ver = kb_info["active_version"]
                staging_ver = kb_info["staging_version"]

            async with conn.execute(
                "SELECT version FROM kb_versions WHERE kb_id = ? ORDER BY created_at DESC", (kb_id,)
            ) as cur:
                all_vers = [r["version"] for r in await cur.fetchall()]

        # 保留集合：前 keep_count 个最新版本，加上当前的 active 与 staging
        keep_set = set(all_vers[:keep_count])
        if active_ver:
            keep_set.add(active_ver)
        if staging_ver:
            keep_set.add(staging_ver)

        pruned_count, freed_bytes = KBStorageManager.prune_old_versions(kb_id, list(keep_set))
        real_disk = KBStorageManager.get_kb_disk_size(kb_id)

        # 同步数据库清理被删除的版本记录并刷新磁盘占用
        async with db_manager.get_connection() as conn:
            if pruned_count > 0:
                placeholders = ",".join(["?"] * len(keep_set))
                await conn.execute(
                    f"DELETE FROM kb_versions WHERE kb_id = ? AND version NOT IN ({placeholders})",
                    [kb_id] + list(keep_set),
                )
            await conn.execute(
                "UPDATE knowledge_bases SET disk_size_bytes = ?, updated_at = ? WHERE kb_id = ?",
                (real_disk, time.time(), kb_id),
            )
            await conn.commit()

        return {
            "success": True,
            "kb_id": kb_id,
            "pruned_count": pruned_count,
            "freed_bytes": freed_bytes,
            "freed_mb": round(freed_bytes / (1024 * 1024), 2),
            "kept_versions": list(keep_set),
        }

    # ==========================================
    # 3. 专家角色与知识库绑定路由
    # ==========================================

    @classmethod
    async def assert_kbs_published(cls, kb_ids: list[str], conn: Any | None = None) -> None:
        """校验给定知识库列表是否均存在且已发布正式版本，未发布则抛出 ValueError。"""
        unique_ids = list(dict.fromkeys(kid for kid in kb_ids if kid))
        if not unique_ids:
            return

        async def _check(active_conn: Any) -> None:
            placeholders = ",".join(["?"] * len(unique_ids))
            async with active_conn.execute(
                f"SELECT kb_id, name, is_published FROM knowledge_bases WHERE kb_id IN ({placeholders})",
                unique_ids,
            ) as cursor:
                rows = await cursor.fetchall()
                row_map = {r["kb_id"]: r for r in rows}

            blocked_names: list[str] = []
            for kid in unique_ids:
                row = row_map.get(kid)
                if not row:
                    blocked_names.append(kid)
                elif not (bool(row["is_published"]) if "is_published" in row.keys() and row["is_published"] else False):
                    blocked_names.append(row["name"] or kid)

            if blocked_names:
                raise ValueError(
                    f"知识库【{'、'.join(blocked_names)}】尚未发布正式版本，请先在 LLMwiki 中发布后再绑定给专家"
                )

        if conn is not None:
            await _check(conn)
        else:
            async with db_manager.get_connection() as own_conn:
                await _check(own_conn)

    @classmethod
    async def bind_expert_kbs(
        cls,
        expert_id: str,
        bindings: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """保存专家绑定的知识库清单及优先级顺序 (Priority 1 > Priority 2 ...)。"""
        now = time.time()
        async with db_manager.get_connection() as conn:
            if bindings:
                await cls.assert_kbs_published([b["kb_id"] for b in bindings], conn=conn)

            await conn.execute("DELETE FROM expert_kb_bindings WHERE expert_id = ?", (expert_id,))
            for idx, b in enumerate(bindings):
                rank = b.get("priority_rank", idx + 1)
                pinned = b.get("pinned_version", "latest")
                await conn.execute(
                    """
                    INSERT INTO expert_kb_bindings (expert_id, kb_id, pinned_version, priority_rank, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (expert_id, b["kb_id"], pinned, rank, now),
                )
            await conn.commit()

        logger.info(f"专家 [{expert_id}] 成功绑定 {len(bindings)} 个知识库")
        return bindings

    @classmethod
    async def get_expert_kbs(cls, expert_id: str) -> list[dict[str, Any]]:
        """获取指定专家绑定的知识库列表（兼容 v1.5.9+ expert_knowledge_bases 与旧表）。"""
        async with db_manager.get_connection() as conn:
            # 1. 优先查询 v1.5.9+ 专家与知识库多对多绑定表 expert_knowledge_bases
            async with conn.execute("""
                SELECT ekb.kb_id, ekb.priority_weight, ekb.top_k_override,
                       kb.name, kb.active_version, kb.current_version, kb.chunk_count,
                       kb.domain_type, kb.rule_profile_json, kb.is_published
                FROM expert_knowledge_bases ekb
                JOIN knowledge_bases kb ON ekb.kb_id = kb.kb_id
                WHERE ekb.expert_id = ?
                ORDER BY ekb.priority_weight DESC, ekb.created_at ASC
            """, (expert_id,)) as cursor:
                rows = await cursor.fetchall()
                if rows:
                    return [
                        {
                            "kb_id": r["kb_id"],
                            "name": r["name"],
                            "pinned_version": "latest",
                            "effective_version": r["active_version"] or r["current_version"] or "v1.0.0",
                            "priority_rank": idx + 1,
                            "chunk_count": r["chunk_count"],
                            "domain_type": r["domain_type"] if "domain_type" in r.keys() else "general",
                            "rule_profile_json": r["rule_profile_json"] if "rule_profile_json" in r.keys() else "",
                            "is_published": bool(r["is_published"]) if "is_published" in r.keys() and r["is_published"] else False,
                        }
                        for idx, r in enumerate(rows)
                    ]

            # 2. 回退查询旧版 expert_kb_bindings 表
            async with conn.execute("""
                SELECT b.kb_id, b.pinned_version, b.priority_rank, kb.name, kb.active_version, kb.chunk_count,
                       kb.domain_type, kb.rule_profile_json, kb.is_published
                FROM expert_kb_bindings b
                JOIN knowledge_bases kb ON b.kb_id = kb.kb_id
                WHERE b.expert_id = ?
                ORDER BY b.priority_rank ASC
            """, (expert_id,)) as cursor:
                rows = await cursor.fetchall()
                return [
                    {
                        "kb_id": r["kb_id"],
                        "name": r["name"],
                        "pinned_version": r["pinned_version"],
                        "effective_version": r["active_version"] if r["pinned_version"] == "latest" else r["pinned_version"],
                        "priority_rank": r["priority_rank"],
                        "chunk_count": r["chunk_count"],
                        "domain_type": r["domain_type"] if "domain_type" in r.keys() else "general",
                        "rule_profile_json": r["rule_profile_json"] if "rule_profile_json" in r.keys() else "",
                        "is_published": bool(r["is_published"]) if "is_published" in r.keys() and r["is_published"] else False,
                    }
                    for r in rows
                ]

