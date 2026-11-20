"""
AgentHub Local v1.5.9 老旧知识库一键重塑为父子分层与 HyPE 结构服务 (KB Hierarchy Upgrade Service)
规范参考: SPEC-1.5.9-HARDENING-ADDENDUM
核心功能:
1. 检测知识库是否缺失 parent/child 分层索引；
2. 升级前自动执行快照备份 (auto_backup) 确保可无损回滚；
3. 将存量扁平切片或文档重新通过 ParentChildASTChunker 切分；
4. 调度向量计算与后台 HyPE 口语问答提炼。
"""
from __future__ import annotations

import asyncio
from datetime import datetime
import hashlib
from pathlib import Path
import re
import sqlite3
import time
from typing import Any

from ..core.logging_config import logger
from ..repository.db import db_manager
from ..kb.manager import KBLifecycleManager
from ..kb.storage import KBStorageManager
from ..kb.exchange import KBExchangeEngine
from ..kb.version_helper import get_next_version
from ..search.ast_chunker import ParentChildASTChunker
from ..search.embedding_provider import embedding_router
from ..search.hype_worker import HyPEQuestionWorker


class KBUpgradeService:
    """老旧知识库索引结构重塑升级器。"""

    @classmethod
    async def check_hierarchy_status(cls, kb_id: str, version: str = "v1.0.0") -> dict[str, Any]:
        """检查指定知识库是否具备父子分层与 HyPE 索引结构。"""
        db_path = KBStorageManager.get_version_db_path(kb_id, version)
        if not db_path.exists():
            return {"needs_upgrade": False, "total_chunks": 0, "parent_chunks": 0}

        def _query():
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()
            try:
                cur.execute("SELECT count(*) FROM hybrid_chunks")
                total = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM hybrid_chunks WHERE chunk_role = 'parent'")
                parents = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM hybrid_chunks WHERE chunk_role = 'hype_question'")
                hypes = cur.fetchone()[0]
                return total, parents, hypes
            except Exception:
                return 0, 0, 0
            finally:
                conn.close()

        total, parents, hypes = await asyncio.to_thread(_query)
        # 只要有切片且无父切片（老扁平索引），即标记需要升级
        needs_upgrade = total > 0 and parents == 0

        return {
            "kb_id": kb_id,
            "version": version,
            "total_chunks": total,
            "parent_chunks": parents,
            "hype_questions": hypes,
            "needs_upgrade": needs_upgrade,
        }

    @classmethod
    async def upgrade_kb_to_parent_child(
        cls, kb_id: str, version: str = "v1.0.0"
    ) -> dict[str, Any]:
        """将老旧知识库一键重塑为父子分层结构并生成 HyPE 口语问答。"""
        db_path = KBStorageManager.get_version_db_path(kb_id, version)
        if not db_path.exists():
            raise FileNotFoundError(f"知识库数据库文件不存在: {db_path}")

        # 计算下一个不重复的语义递增版本号 (如 v1.0.0 -> v1.0.1)
        existing_versions = await KBLifecycleManager.list_kb_versions(kb_id)
        existing_snaps = await KBExchangeEngine.list_snapshots(kb_id)
        existing_tags = list(set(
            [v.get("version") for v in existing_versions if v.get("version")] +
            [s.get("version_tag") for s in existing_snaps if s.get("version_tag")] +
            [version]
        ))
        new_version = get_next_version(existing_tags, bump="patch")

        # Step 1: 升级前自动创建 auto_backup 快照备份（快照标记原版本标签）
        backup_id = await KBExchangeEngine.create_snapshot(
            kb_id=kb_id,
            version_tag=version,
            snapshot_type="auto_backup",
            change_summary=f"升级为父子分层前自动备份原版本（新版本提升至 {new_version}）",
        )
        logger.info(f"[KBUpgrade] 已完成升级前自动快照备份: {backup_id}, 原版本={version}, 提升新版本={new_version}")

        # Step 2: 读取存量切片，按 remote_uri 聚合出完整文档文本
        def _read_docs_from_db() -> list[dict[str, Any]]:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            try:
                rows = cur.execute(
                    "SELECT remote_uri, title, content, doc_year, effective_date, effective_ts, doc_priority_type, topic_cluster_id, authority_tier "
                    "FROM hybrid_chunks WHERE chunk_role IN ('parent', 'child') ORDER BY rowid ASC"
                ).fetchall()
                docs_dict: dict[str, dict[str, Any]] = {}
                seen_bodies: dict[str, set[str]] = {}
                for r in rows:
                    uri = r["remote_uri"]
                    raw_part = str(r["content"] or "")
                    clean_part = re.sub(r"(?m)^(?:【文档来源】|【来源】)[^\n]*\n?", "", raw_part)
                    clean_part = re.sub(r"(?m)^\[指针：上一片=[^\n]*\]\s*$", "", clean_part).strip()
                    if not clean_part:
                        continue
                    body_sig = hashlib.md5(clean_part.encode("utf-8")).hexdigest()
                    if uri not in docs_dict:
                        docs_dict[uri] = {
                            "remote_uri": uri,
                            "title": r["title"],
                            "content_parts": [],
                            "doc_year": int(r["doc_year"] or 0),
                            "effective_date": r["effective_date"] or "",
                            "effective_ts": int(r["effective_ts"] or 0),
                            "doc_priority_type": r["doc_priority_type"] or "normal",
                            "topic_cluster_id": r["topic_cluster_id"] or "",
                            "authority_tier": r["authority_tier"] or 2,
                        }
                        seen_bodies[uri] = set()
                    if body_sig in seen_bodies[uri]:
                        continue
                    seen_bodies[uri].add(body_sig)
                    docs_dict[uri]["content_parts"].append(clean_part)

                from ..search.temporal_reranker import TemporalMetadataExtractor
                results = []
                for doc in docs_dict.values():
                    full_text = "\n\n".join(doc["content_parts"])
                    tmeta = TemporalMetadataExtractor.extract_metadata(doc["title"], full_text[:800])
                    results.append({
                        "remote_uri": doc["remote_uri"],
                        "title": doc["title"],
                        "full_text": full_text,
                        "doc_year": doc["doc_year"] or tmeta.doc_year,
                        "effective_date": doc["effective_date"] or tmeta.effective_date,
                        "effective_ts": doc["effective_ts"] or tmeta.effective_ts,
                        "doc_priority_type": doc["doc_priority_type"] or tmeta.doc_priority_type,
                        "topic_cluster_id": doc["topic_cluster_id"] or tmeta.topic_cluster_id,
                        "authority_tier": doc["authority_tier"],
                    })
                return results
            finally:
                conn.close()

        docs_to_rechunk = await asyncio.to_thread(_read_docs_from_db)
        if not docs_to_rechunk:
            return {"status": "empty", "message": "知识库中暂无可升级的切片内容"}

        # Step 3: 通过 ParentChildASTChunker 重新切分
        new_chunks: list[dict[str, Any]] = []
        for doc in docs_to_rechunk:
            h_chunks = ParentChildASTChunker.chunk_document(
                text=doc["full_text"],
                filename=doc["title"],
                remote_uri=doc["remote_uri"],
                authority_tier=doc["authority_tier"],
                doc_year=doc["doc_year"],
                effective_date=doc["effective_date"],
                effective_ts=doc["effective_ts"],
                doc_priority_type=doc["doc_priority_type"],
                topic_cluster_id=doc["topic_cluster_id"],
            )
            for hc in h_chunks:
                new_chunks.append(hc.to_dict())

        # Step 4: 将重构后的切片写入新版本数据库中
        total_count, disk_sz = KBStorageManager.upsert_chunks_to_version_db(
            kb_id=kb_id,
            version=new_version,
            chunks=new_chunks,
        )

        # 原子同步主表与版本表
        async with db_manager.get_connection() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO kb_versions (
                    kb_id, version, status, changelog, chunk_count, triple_count,
                    disk_size_bytes, is_published, created_by, created_at
                ) VALUES (?, ?, 'ready', '升级为父子分层与HyPE口语索引结构', ?, 0, ?, 1, 'system', ?)
                """,
                (kb_id, new_version, total_count, disk_sz, int(time.time())),
            )
            await conn.execute(
                "UPDATE knowledge_bases SET active_version = ?, current_version = ?, chunk_count = ?, disk_size_bytes = ?, updated_at = CURRENT_TIMESTAMP WHERE kb_id = ?",
                (new_version, new_version, total_count, disk_sz, kb_id),
            )
            await conn.commit()

        # Step 5: 调度后台 HyPE 任务针对新版本执行增强
        HyPEQuestionWorker.schedule_background_hype(kb_id=kb_id, version=new_version)

        logger.info(f"[KBUpgrade] 知识库 [{kb_id}] 已成功升级为分层结构新版本 [{new_version}]，生成 {total_count} 条父子切片并调度 HyPE 增强。")
        return {
            "status": "upgraded",
            "kb_id": kb_id,
            "version": new_version,
            "old_version": version,
            "backup_snapshot_id": backup_id,
            "new_total_chunks": total_count,
            "message": f"已成功重塑为父子分层结构新版本 [{new_version}]，口语化问答正于后台异步提炼中。",
        }

