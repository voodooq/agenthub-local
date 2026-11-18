"""
AgentHub Local v1.5.8 知识库生命周期管理核心 (KB Lifecycle Manager)
核心功能：
1. 数据源注册与双模式安全清理（断连保库 frozen_snapshot vs 物理连带销毁）；
2. 知识库资产元数据维护、版本快照流转（staging -> ready / rollback）；
3. 专家角色与知识库绑定路由管理；
4. 检索命中率测试台 (Retrieval Playground) 执行。
"""
import json
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
                    chunk_count, triple_count, disk_size_bytes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, '', ?, ?, 0, 0, ?, ?, ?)
                """,
                (kb_id, name, description, ownership_type, source_id, initial_version, embedding_model, embedding_dim, disk_size, now, now),
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

        logger.info(f"成功创建知识库资产: {name} (ID: {kb_id}, 版本: {initial_version})")
        return {
            "kb_id": kb_id,
            "name": name,
            "active_version": initial_version,
            "ownership_type": ownership_type,
            "disk_size_bytes": disk_size,
        }

    @classmethod
    async def list_knowledge_bases(cls) -> list[dict[str, Any]]:
        """获取所有知识库资产列表（包含物理磁盘占用与切片统计）。"""
        async with db_manager.get_connection() as conn:
            async with conn.execute("""
                SELECT kb.*, ds.name as source_name, ds.source_type
                FROM knowledge_bases kb
                LEFT JOIN kb_data_sources ds ON kb.source_id = ds.source_id
                ORDER BY kb.updated_at DESC
            """) as cursor:
                rows = await cursor.fetchall()
                kbs = []
                for r in rows:
                    kb_id = r["kb_id"]
                    real_disk = KBStorageManager.get_kb_disk_size(kb_id)
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
                        "chunk_count": r["chunk_count"],
                        "triple_count": r["triple_count"],
                        "disk_size_bytes": real_disk,
                        "share_repo_uri": r["share_repo_uri"],
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
                        "created_at": r["created_at"],
                    })
                return versions

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
    async def prune_versions(cls, kb_id: str, keep_count: int = 3) -> dict[str, Any]:
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

        # 同步数据库清理被删除的版本记录
        if pruned_count > 0:
            async with db_manager.get_connection() as conn:
                placeholders = ",".join(["?"] * len(keep_set))
                await conn.execute(
                    f"DELETE FROM kb_versions WHERE kb_id = ? AND version NOT IN ({placeholders})",
                    [kb_id] + list(keep_set),
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
    async def bind_expert_kbs(
        cls,
        expert_id: str,
        bindings: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """保存专家绑定的知识库清单及优先级顺序 (Priority 1 > Priority 2 ...)。"""
        now = time.time()
        async with db_manager.get_connection() as conn:
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
        """获取指定专家绑定的知识库列表（按优先级升序）。"""
        async with db_manager.get_connection() as conn:
            async with conn.execute("""
                SELECT b.kb_id, b.pinned_version, b.priority_rank, kb.name, kb.active_version, kb.chunk_count
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
                    }
                    for r in rows
                ]

