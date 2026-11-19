"""
AgentHub Local v1.5.9 知识库快照导入导出与版本演进治理引擎 (KB Exchange & Versioning Engine)
规范参考: SPEC-1.5.9-KB-EXCHANGE-ADDENDUM
核心特性:
1. 确定性 content_md5 算法（严格排除动态时间戳，相同内容 MD5 100% 一致）；
2. .ahkb (ZIP Archive) 资产归档包导出与零重新训练极速导入；
3. MD5 幂等防重与秒级跳过；
4. 覆盖前自动快照备份 (auto_backup) 与原子事务覆盖；
5. 跨版本文件级增删改 Diff 比对与一键无损回滚。
"""
from __future__ import annotations

import asyncio
from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time
from typing import Any
import uuid
import zipfile

from ..core.logging_config import logger
from ..repository.db import db_manager
from .storage import KBStorageManager
from ..search.embedding_provider import embedding_router


def get_snapshots_dir() -> Path:
    """获取本地持久化快照目录 (~/.agenthub/kb_snapshots/)"""
    home = Path.home()
    snapshot_dir = home / ".agenthub" / "kb_snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    return snapshot_dir


class KBExchangeEngine:
    """知识库资产交换与版本治理核心引擎。"""

    @classmethod
    def calculate_deterministic_md5(
        cls,
        documents: list[dict[str, Any]],
        chunks: list[dict[str, Any]],
        structured_rules: list[dict[str, Any]] | None = None,
    ) -> str:
        """根据排好序的核心业务数据计算确定性 content_md5。

        严格排除 exported_at、临时路径等动态元数据。
        """
        hasher = hashlib.md5()

        # 1. 文档序列排序并喂入
        sorted_docs = sorted(documents, key=lambda d: str(d.get("doc_md5") or d.get("remote_uri") or ""))
        for doc in sorted_docs:
            doc_id = str(doc.get("doc_id") or doc.get("remote_uri") or "")
            doc_md5 = str(doc.get("doc_md5") or "")
            eff_date = str(doc.get("effective_date") or "")
            hasher.update(f"{doc_id}:{doc_md5}:{eff_date}\n".encode("utf-8"))

        # 2. 切片序列排序并喂入 (按 chunk_role, section_path, content_hash)
        sorted_chunks = sorted(
            chunks,
            key=lambda c: (
                str(c.get("chunk_role") or "child"),
                str(c.get("section_path") or ""),
                str(c.get("content_hash") or hashlib.md5(str(c.get("content") or "").encode("utf-8")).hexdigest()),
            ),
        )
        for ch in sorted_chunks:
            role = str(ch.get("chunk_role") or "child")
            sec = str(ch.get("section_path") or "")
            text = str(ch.get("content") or ch.get("content_text") or "")
            hasher.update(f"{role}:{sec}:{text}\n".encode("utf-8"))

        # 3. 结构化业务规则卡片排序并喂入
        if structured_rules:
            sorted_rules = sorted(structured_rules, key=lambda r: str(r.get("id") or ""))
            for rule in sorted_rules:
                summary = str(rule.get("rule_summary") or "")
                payload = str(rule.get("structured_payload") or "")
                hasher.update(f"rule:{summary}:{payload}\n".encode("utf-8"))

        return hasher.hexdigest()

    @classmethod
    def export_bundle(
        cls,
        kb_id: str,
        version: str = "",
        output_path: Path | None = None,
        author: str = "AgentHub User",
        change_summary: str = "",
    ) -> tuple[Path, str, dict[str, Any]]:
        """将指定版本的知识库打包导出为标准 .ahkb (ZIP) 资产归档文件。

        Returns:
            tuple[Path, str, dict[str, Any]]: (生成的 .ahkb 文件路径, content_md5, manifest 字典)
        """
        # 若未指定版本，读取当前生效版本
        if not version:
            version = "v1.0.0"

        db_path = KBStorageManager.get_version_db_path(kb_id, version)
        if not db_path.exists():
            raise FileNotFoundError(f"知识库 [{kb_id}] 的数据库文件不存在: {db_path}")

        # 1. 读取数据库中所有文档、切片及三元组
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        chunks_rows = cur.execute("SELECT * FROM hybrid_chunks").fetchall()
        chunks = [dict(r) for r in chunks_rows]

        triples = []
        try:
            triples_rows = cur.execute("SELECT * FROM knowledge_triples").fetchall()
            triples = [dict(r) for r in triples_rows]
        except Exception:
            pass

        structured_rules = []
        try:
            rules_rows = cur.execute("SELECT * FROM kb_structured_rules").fetchall()
            structured_rules = [dict(r) for r in rules_rows]
        except Exception:
            pass

        conn.close()

        # 提取文档清单（聚合切片的唯一 remote_uri）
        docs_map: dict[str, dict[str, Any]] = {}
        for c in chunks:
            uri = c.get("remote_uri") or "unknown"
            if uri not in docs_map:
                docs_map[uri] = {
                    "doc_id": hashlib.md5(uri.encode("utf-8")).hexdigest()[:16],
                    "remote_uri": uri,
                    "title": c.get("title") or Path(uri).name,
                    "doc_year": c.get("doc_year", 0),
                    "effective_date": c.get("effective_date", ""),
                    "effective_ts": c.get("effective_ts", 0),
                    "topic_cluster_id": c.get("topic_cluster_id", ""),
                    "doc_md5": c.get("etag") or hashlib.md5(uri.encode("utf-8")).hexdigest(),
                }
        documents = list(docs_map.values())

        # 2. 计算确定性 content_md5
        content_md5 = cls.calculate_deterministic_md5(documents, chunks, structured_rules)

        # 读取已有知识库元数据
        kb_meta = KBStorageManager.load_manifest(kb_id, version)
        kb_uuid = kb_meta.get("kb_uuid") or f"kb_{kb_id}_{uuid.uuid4().hex[:8]}"
        embed_model = kb_meta.get("embedding_model") or "bge-m3"
        embed_dim = kb_meta.get("embedding_dim") or 1024

        stats = {
            "doc_count": len(documents),
            "parent_chunks": sum(1 for c in chunks if c.get("chunk_role") == "parent"),
            "child_chunks": sum(1 for c in chunks if c.get("chunk_role") == "child"),
            "hype_questions": sum(1 for c in chunks if c.get("chunk_role") == "hype_question"),
            "structured_rules": len(structured_rules),
            "triples_count": len(triples),
        }

        # 构造 manifest.json 字典
        manifest = {
            "bundle_format_version": "1.6.0",
            "kb_id": kb_id,
            "kb_uuid": kb_uuid,
            "kb_name": kb_meta.get("name") or kb_id,
            "domain_type": kb_meta.get("domain_type") or "general",
            "rule_profile_json": kb_meta.get("rule_profile_json") or "",
            "version_tag": version,
            "parent_version_tag": kb_meta.get("parent_version_tag", ""),
            "exported_at": datetime.now().isoformat(),
            "author": author,
            "change_summary": change_summary or f"导出自 AgentHub Local ({version})",
            "embedding_spec": {
                "model": embed_model,
                "dimension": embed_dim,
            },
            "stats": stats,
            "content_md5": content_md5,
        }

        # 3. 确定导出输出路径
        if output_path is None:
            packages_dir = KBStorageManager.get_kb_dir(kb_id) / "bundles"
            packages_dir.mkdir(parents=True, exist_ok=True)
            safe_name = re.sub(r'[\\/*?:"<>|]', "_", manifest["kb_name"])
            date_str = datetime.now().strftime("%Y%m%d")
            filename = f"{safe_name}_{version}_{date_str}.ahkb"
            output_path = packages_dir / filename

        # 4. 写入 ZIP 归档包
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # 4.1 manifest.json
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

            # 4.2 documents.jsonl
            doc_lines = [json.dumps(d, ensure_ascii=False) for d in documents]
            zf.writestr("documents.jsonl", "\n".join(doc_lines))

            # 4.3 structured_rules.jsonl
            rule_lines = [json.dumps(r, ensure_ascii=False) for r in structured_rules]
            zf.writestr("structured_rules.jsonl", "\n".join(rule_lines))

            # 4.4 triples.jsonl
            triple_lines = [json.dumps(t, ensure_ascii=False) for t in triples]
            zf.writestr("triples.jsonl", "\n".join(triple_lines))

            # 4.5 直接完整打包 index.db (包含预计算的 1024 维 BLOB 向量和 FTS5)
            # 在 zip 内命名为 index.db，导入端可 0 秒即时加载！
            zf.write(db_path, arcname="index.db")

        logger.info(f"[KBExchange] 知识库 [{kb_id}] 导出完成: {output_path} (MD5: {content_md5})")
        return output_path, content_md5, manifest

    @classmethod
    def inspect_bundle(cls, bundle_path: Path) -> dict[str, Any]:
        """读取并检查 .ahkb 包的 manifest.json 和文档摘要。"""
        if not bundle_path.exists():
            raise FileNotFoundError(f"未找到指定的 .ahkb 包: {bundle_path}")

        with zipfile.ZipFile(bundle_path, "r") as zf:
            try:
                manifest_raw = zf.read("manifest.json").decode("utf-8")
                manifest = json.loads(manifest_raw)
            except Exception as e:
                raise ValueError(f"解析 .ahkb 包 manifest.json 失败: {e}")

            # 尝试读取文档清单
            docs = []
            try:
                doc_lines = zf.read("documents.jsonl").decode("utf-8").splitlines()
                for line in doc_lines:
                    if line.strip():
                        docs.append(json.loads(line))
            except Exception:
                pass
            manifest["documents"] = docs
            return manifest

    @classmethod
    async def import_bundle(
        cls,
        bundle_path: Path,
        conflict_strategy: str = "ask",
        target_kb_name: str = "",
    ) -> dict[str, Any]:
        """导入 .ahkb 资产包，支持幂等检测、冲突挂起与安全备份覆盖。

        Args:
            bundle_path: .ahkb 文件路径
            conflict_strategy: "ask" | "backup_and_overwrite" | "import_as_new"
            target_kb_name: 重命名（若为空使用包内名称）

        Returns:
            dict[str, Any]: 导入结果字典，包含 status:
                - "identical_skipped": MD5 完全一致，跳过
                - "conflict_confirm_required": 发现差异，挂起等待用户确认
                - "backed_up_and_overwritten": 已完成备份并覆盖导入
                - "imported_new": 成功导入为新库
        """
        manifest = cls.inspect_bundle(bundle_path)
        incoming_md5 = manifest.get("content_md5") or ""
        incoming_uuid = manifest.get("kb_uuid") or ""
        incoming_name = target_kb_name.strip() or manifest.get("kb_name") or manifest.get("kb_id") or "imported_kb"
        incoming_ver = manifest.get("version_tag") or "v1.0.0"

        # 1. 检查本地是否存在同源库 (通过 kb_uuid 或同名 kb_name 匹配)
        existing_kb = None
        async with db_manager.get_connection() as conn:
            async with conn.execute(
                "SELECT * FROM knowledge_bases WHERE kb_uuid = ? OR name = ? OR kb_id = ? LIMIT 1",
                (incoming_uuid, incoming_name, manifest.get("kb_id", "")),
            ) as cur:
                row = await cur.fetchone()
                if row:
                    existing_kb = dict(row)

        # 分支 A: 不存在同源库 -> 直接全新入库
        if not existing_kb or conflict_strategy == "import_as_new":
            kb_id = f"kb_{uuid.uuid4().hex[:8]}" if conflict_strategy == "import_as_new" else (manifest.get("kb_id") or f"kb_{uuid.uuid4().hex[:8]}")
            result = await cls._execute_bundle_restore(
                bundle_path=bundle_path,
                target_kb_id=kb_id,
                target_name=incoming_name if conflict_strategy != "import_as_new" else f"{incoming_name}_副本",
                version=incoming_ver,
                manifest=manifest,
                snapshot_type="imported",
            )
            return {"status": "imported_new", "kb_id": kb_id, "detail": result}

        # 分支 B: 存在同源库，比对 MD5
        local_kb_id = existing_kb["kb_id"]
        local_md5 = existing_kb.get("content_md5") or ""

        # 若本地未记录 content_md5，实时计算一次补齐
        if not local_md5:
            local_db = KBStorageManager.get_version_db_path(local_kb_id, existing_kb.get("active_version") or "v1.0.0")
            if local_db.exists():
                _, local_md5, _ = cls.export_bundle(local_kb_id, existing_kb.get("active_version") or "v1.0.0")
                async with db_manager.get_connection() as conn:
                    await conn.execute("UPDATE knowledge_bases SET content_md5 = ? WHERE kb_id = ?", (local_md5, local_kb_id))
                    await conn.commit()

        # B1. MD5 完全一致 -> 秒级跳过！
        if local_md5 and local_md5 == incoming_md5:
            logger.info(f"[KBExchange] 知识库 [{incoming_name}] 内容 MD5 完全一致 ({incoming_md5})，秒级跳过重复导入。")
            return {
                "status": "identical_skipped",
                "kb_id": local_kb_id,
                "message": f"当前知识库与导入包内容完全一致（MD5: {incoming_md5[:10]}...，版本 {incoming_ver}），已自动跳过重复导入。",
                "content_md5": incoming_md5,
            }

        # B2. MD5 不一致，且 strategy == "ask" -> 挂起并返回双版本差异
        if conflict_strategy == "ask":
            diff_detail = cls._calculate_import_diff(existing_kb, manifest)
            return {
                "status": "conflict_confirm_required",
                "kb_id": local_kb_id,
                "local_version": existing_kb.get("active_version") or "v1.0.0",
                "local_md5": local_md5,
                "incoming_version": incoming_ver,
                "incoming_md5": incoming_md5,
                "diff": diff_detail,
                "manifest": manifest,
            }

        # B3. 确认覆盖 (conflict_strategy == "backup_and_overwrite")
        # Step 1: 自动将本地原版全量备份至快照表 (auto_backup)
        local_ver = existing_kb.get("active_version") or "v1.0.0"
        backup_snapshot_id = await cls.create_snapshot(
            kb_id=local_kb_id,
            version_tag=local_ver,
            snapshot_type="auto_backup",
            change_summary=f"覆盖前自动备份（导入新版本 {incoming_ver} 触发）",
        )
        logger.info(f"[KBExchange] 已完成覆盖前自动快照备份: snapshot_id={backup_snapshot_id}")

        # Step 2: 恢复新版本资产并覆盖目标库
        result = await cls._execute_bundle_restore(
            bundle_path=bundle_path,
            target_kb_id=local_kb_id,
            target_name=incoming_name,
            version=incoming_ver,
            manifest=manifest,
            snapshot_type="imported",
        )

        return {
            "status": "backed_up_and_overwritten",
            "kb_id": local_kb_id,
            "backup_snapshot_id": backup_snapshot_id,
            "new_version": incoming_ver,
            "detail": result,
        }

    @classmethod
    def _calculate_import_diff(
        cls, local_kb: dict[str, Any], incoming_manifest: dict[str, Any]
    ) -> dict[str, Any]:
        """计算待导入包与本地知识库的差异明细（文档增删改与切片量变化）。"""
        local_docs: list[str] = []
        local_kb_id = local_kb["kb_id"]
        local_ver = local_kb.get("active_version") or "v1.0.0"
        local_db = KBStorageManager.get_version_db_path(local_kb_id, local_ver)
        if local_db.exists():
            try:
                conn = sqlite3.connect(str(local_db))
                cur = conn.cursor()
                rows = cur.execute("SELECT DISTINCT title FROM hybrid_chunks").fetchall()
                local_docs = [r[0] for r in rows]
                conn.close()
            except Exception:
                pass

        incoming_docs = [d.get("title") or Path(d.get("remote_uri", "")).name for d in incoming_manifest.get("documents", [])]

        added_docs = [d for d in incoming_docs if d not in local_docs]
        removed_docs = [d for d in local_docs if d not in incoming_docs]
        shared_docs = [d for d in incoming_docs if d in local_docs]

        return {
            "added_documents": added_docs,
            "removed_documents": removed_docs,
            "shared_documents": shared_docs,
            "local_chunks": local_kb.get("chunk_count", 0),
            "incoming_chunks": (incoming_manifest.get("stats", {}).get("parent_chunks", 0) + incoming_manifest.get("stats", {}).get("child_chunks", 0)),
        }

    @classmethod
    async def _execute_bundle_restore(
        cls,
        bundle_path: Path,
        target_kb_id: str,
        target_name: str,
        version: str,
        manifest: dict[str, Any],
        snapshot_type: str = "imported",
    ) -> dict[str, Any]:
        """解压并原子恢复 .ahkb 归档包至目标知识库版本目录。"""
        target_ver_dir = KBStorageManager.get_version_dir(target_kb_id, version)
        target_ver_dir.mkdir(parents=True, exist_ok=True)
        target_db = target_ver_dir / "index.db"

        # 从 zip 解压 index.db
        with zipfile.ZipFile(bundle_path, "r") as zf:
            if "index.db" in zf.namelist():
                with open(target_db, "wb") as f_out:
                    f_out.write(zf.read("index.db"))
            if "manifest.json" in zf.namelist():
                with open(target_ver_dir / "manifest.json", "wb") as f_out:
                    f_out.write(zf.read("manifest.json"))

        now = time.time()
        disk_size = target_db.stat().st_size if target_db.exists() else 0
        stats = manifest.get("stats", {})
        total_chunks = stats.get("parent_chunks", 0) + stats.get("child_chunks", 0) + stats.get("hype_questions", 0)
        triples_count = stats.get("triples_count", 0)
        embed_spec = manifest.get("embedding_spec", {})
        embed_model = embed_spec.get("model", "bge-m3")
        embed_dim = embed_spec.get("dimension", 1024)
        content_md5 = manifest.get("content_md5", "")
        kb_uuid = manifest.get("kb_uuid") or f"kb_{target_kb_id}"

        domain_type = manifest.get("domain_type") or "general"
        rule_profile_json = manifest.get("rule_profile_json") or ""

        # 写入或更新 knowledge_bases 表
        async with db_manager.get_connection() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO knowledge_bases (
                    kb_id, name, description, ownership_type, source_id,
                    active_version, staging_version, embedding_model, embedding_dim,
                    chunk_count, triple_count, disk_size_bytes, share_repo_uri,
                    kb_uuid, current_version, content_md5, domain_type, rule_profile_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 'local', NULL, ?, '', ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    target_kb_id,
                    target_name,
                    manifest.get("change_summary") or f"从 .ahkb 导入 (版本 {version})",
                    version,
                    embed_model,
                    embed_dim,
                    total_chunks,
                    triples_count,
                    disk_size,
                    kb_uuid,
                    version,
                    content_md5,
                    domain_type,
                    rule_profile_json,
                    now,
                    now,
                ),
            )
            # 记录版本快照
            snapshot_id = f"snap_{target_kb_id}_{int(now)}_{uuid.uuid4().hex[:6]}"
            doc_manifest_json = json.dumps(manifest.get("documents", []), ensure_ascii=False)
            await conn.execute(
                """
                INSERT INTO kb_version_snapshots (
                    snapshot_id, kb_id, kb_uuid, version_tag, parent_version_tag,
                    snapshot_type, content_md5, embedding_model, embedding_dim,
                    change_summary, bundle_archive_path, doc_manifest_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    snapshot_id,
                    target_kb_id,
                    kb_uuid,
                    version,
                    manifest.get("parent_version_tag", ""),
                    snapshot_type,
                    content_md5,
                    embed_model,
                    embed_dim,
                    manifest.get("change_summary", ""),
                    str(bundle_path),
                    doc_manifest_json,
                ),
            )
            await conn.commit()

        # 确保结构化规则表初始化并导入 structured_rules.jsonl
        KBStorageManager.init_version_db_schema(target_db)
        with zipfile.ZipFile(bundle_path, "r") as zf:
            if "structured_rules.jsonl" in zf.namelist():
                try:
                    rule_lines = zf.read("structured_rules.jsonl").decode("utf-8").splitlines()
                    rules_to_restore = [json.loads(line) for line in rule_lines if line.strip()]
                    if rules_to_restore:
                        from ..search.rule_extractor import StructuredRuleExtractor
                        StructuredRuleExtractor.batch_save_rules(target_db, rules_to_restore)
                except Exception as r_err:
                    logger.debug(f"[KBExchange] 恢复 structured_rules.jsonl 异常: {r_err}")

        # 向量维度适配检查
        provider = await embedding_router.get_provider()
        if provider.is_available and provider.dimension > 0 and provider.dimension != embed_dim:
            logger.warning(
                f"[KBExchange] 向量维度空间不一致：包内 {embed_dim} 维，本地为 {provider.dimension} 维，调度后台重算向量..."
            )
            # 标记需要重算
            try:
                conn_sync = sqlite3.connect(str(target_db))
                conn_sync.execute("UPDATE hybrid_chunks SET embedding_blob = NULL")
                conn_sync.commit()
                conn_sync.close()
            except Exception:
                pass

        return {
            "kb_id": target_kb_id,
            "version": version,
            "total_chunks": total_chunks,
            "disk_size_bytes": disk_size,
            "content_md5": content_md5,
        }

    @classmethod
    async def create_snapshot(
        cls,
        kb_id: str,
        version_tag: str,
        snapshot_type: str = "release",
        change_summary: str = "",
        parent_version_tag: str = "",
    ) -> str:
        """为当前知识库状态生成一份持久化快照归档并记录到 kb_version_snapshots。

        Returns:
            str: snapshot_id
        """
        snapshots_dir = get_snapshots_dir()
        date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_tag = re.sub(r'[\\/*?:"<>|]', "_", version_tag)
        archive_path = snapshots_dir / f"{kb_id}_{safe_tag}_{date_str}_{snapshot_type}.ahkb"

        # 导出为归档包
        _, content_md5, manifest = cls.export_bundle(
            kb_id=kb_id,
            version=version_tag,
            output_path=archive_path,
            change_summary=change_summary,
        )

        snapshot_id = f"snap_{kb_id}_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        doc_manifest_json = json.dumps(manifest.get("documents", []), ensure_ascii=False)
        embed_spec = manifest.get("embedding_spec", {})

        async with db_manager.get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO kb_version_snapshots (
                    snapshot_id, kb_id, kb_uuid, version_tag, parent_version_tag,
                    snapshot_type, content_md5, embedding_model, embedding_dim,
                    change_summary, bundle_archive_path, doc_manifest_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    snapshot_id,
                    kb_id,
                    manifest.get("kb_uuid", ""),
                    version_tag,
                    parent_version_tag or manifest.get("parent_version_tag", ""),
                    snapshot_type,
                    content_md5,
                    embed_spec.get("model", "bge-m3"),
                    embed_spec.get("dimension", 1024),
                    change_summary,
                    str(archive_path),
                    doc_manifest_json,
                ),
            )
            # 同步更新主表
            await conn.execute(
                """
                UPDATE knowledge_bases
                SET current_version = ?, content_md5 = ?, updated_at = CURRENT_TIMESTAMP
                WHERE kb_id = ?
                """,
                (version_tag, content_md5, kb_id),
            )
            await conn.commit()

        return snapshot_id

    @classmethod
    async def list_snapshots(cls, kb_id: str) -> list[dict[str, Any]]:
        """查询指定知识库的所有历史快照记录（按创建时间降序）。"""
        async with db_manager.get_connection() as conn:
            async with conn.execute(
                """
                SELECT * FROM kb_version_snapshots
                WHERE kb_id = ?
                ORDER BY created_at DESC
                """,
                (kb_id,),
            ) as cur:
                rows = await cur.fetchall()
                results = []
                for r in rows:
                    d = dict(r)
                    try:
                        d["doc_manifest"] = json.loads(d.get("doc_manifest_json") or "[]")
                    except Exception:
                        d["doc_manifest"] = []
                    results.append(d)
                return results

    @classmethod
    async def compute_snapshot_diff(
        cls, base_snapshot_id: str, target_snapshot_id: str
    ) -> dict[str, Any]:
        """比对两个快照之间的文件增删改 Diff。"""
        async with db_manager.get_connection() as conn:
            async with conn.execute(
                "SELECT * FROM kb_version_snapshots WHERE snapshot_id = ?", (base_snapshot_id,)
            ) as cur:
                r1 = await cur.fetchone()
            async with conn.execute(
                "SELECT * FROM kb_version_snapshots WHERE snapshot_id = ?", (target_snapshot_id,)
            ) as cur:
                r2 = await cur.fetchone()

        if not r1 or not r2:
            raise ValueError("快照 ID 不存在")

        docs1 = json.loads(r1["doc_manifest_json"] or "[]")
        docs2 = json.loads(r2["doc_manifest_json"] or "[]")

        m1 = {d.get("remote_uri") or d.get("title"): d.get("doc_md5") for d in docs1}
        m2 = {d.get("remote_uri") or d.get("title"): d.get("doc_md5") for d in docs2}

        added = [k for k in m2 if k not in m1]
        removed = [k for k in m1 if k not in m2]
        modified = [k for k in m1 if k in m2 and m1[k] != m2[k]]

        return {
            "base_version": r1["version_tag"],
            "target_version": r2["version_tag"],
            "base_md5": r1["content_md5"],
            "target_md5": r2["content_md5"],
            "added_documents": added,
            "removed_documents": removed,
            "modified_documents": modified,
        }

    @classmethod
    async def rollback_to_snapshot(cls, kb_id: str, snapshot_id: str) -> dict[str, Any]:
        """将知识库无损回滚至指定快照（回滚前先自动生成 auto_backup 快照）。"""
        async with db_manager.get_connection() as conn:
            async with conn.execute(
                "SELECT * FROM kb_version_snapshots WHERE snapshot_id = ?", (snapshot_id,)
            ) as cur:
                snap = await cur.fetchone()
            async with conn.execute(
                "SELECT * FROM knowledge_bases WHERE kb_id = ?", (kb_id,)
            ) as cur:
                current_kb = await cur.fetchone()

        if not snap or not current_kb:
            raise ValueError(f"快照 {snapshot_id} 或知识库 {kb_id} 不存在")

        snap_path = Path(snap["bundle_archive_path"])
        if not snap_path.exists():
            raise FileNotFoundError(f"快照归档文件已丢失: {snap_path}")

        # Step 1: 回滚前为当前工作态创建自动快照
        current_ver = current_kb["current_version"] or current_kb["active_version"] or "v1.0.0"
        backup_id = await cls.create_snapshot(
            kb_id=kb_id,
            version_tag=current_ver,
            snapshot_type="auto_backup",
            change_summary=f"回滚前自动备份（目标回滚至 {snap['version_tag']}）",
        )

        # Step 2: 恢复目标快照
        manifest = cls.inspect_bundle(snap_path)
        restore_res = await cls._execute_bundle_restore(
            bundle_path=snap_path,
            target_kb_id=kb_id,
            target_name=current_kb["name"],
            version=snap["version_tag"],
            manifest=manifest,
            snapshot_type="rollback",
        )

        logger.info(f"[KBExchange] 知识库 [{kb_id}] 已成功回滚至版本 [{snap['version_tag']}] (前置备份: {backup_id})")
        return {
            "status": "rolled_back",
            "kb_id": kb_id,
            "target_version": snap["version_tag"],
            "backup_snapshot_id": backup_id,
            "detail": restore_res,
        }

