"""
AgentHub Local v1.5.8 知识库独立隔离存储引擎 (KB Storage Engine)
核心职责：
1. 管理 ~/.agenthub/kbs/{kb_id}/versions/{semver}/index.db 独立隔离库物理路径与架构初始化；
2. 毫秒级版本快照拷贝、原子指针切换与 index.db 架构自同步；
3. 知识库版本清单 manifest.json (含向量维度与模型契约) 生成与校验；
4. 物理磁盘占用统计、旧版本瘦身清理 (Prune) 与 VACUUM 收缩。
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time
from typing import Any

from ..core.logging_config import logger

KB_STORE_ROOT = Path.home() / ".agenthub" / "kbs"


class KBStorageManager:
    """独立知识库物理存储与版本库管理器。"""

    @classmethod
    def get_kb_dir(cls, kb_id: str) -> Path:
        """获取知识库资产根目录。"""
        p = KB_STORE_ROOT / kb_id
        p.mkdir(parents=True, exist_ok=True)
        return p

    @classmethod
    def get_version_dir(cls, kb_id: str, version: str) -> Path:
        """获取指定版本的存储目录。"""
        p = cls.get_kb_dir(kb_id) / "versions" / version
        p.mkdir(parents=True, exist_ok=True)
        return p

    @classmethod
    def upsert_chunks_to_version_db(
        cls,
        kb_id: str,
        version: str,
        chunks: list[dict[str, Any]],
        embedding_model: str = "null_offline_lexical",
        embedding_dim: int = 768,
    ) -> tuple[int, int]:
        """将切片批量写入/更新至指定版本的独立 index.db 中，并同步更新 manifest.json。

        Returns:
            tuple[int, int]: (total_chunk_count, disk_size_bytes)
        """
        db_path = cls.get_version_db_path(kb_id, version)
        cls.init_version_db_schema(db_path)

        now = time.time()
        conn = sqlite3.connect(str(db_path))
        try:
            from ..search.light_graph_rag import LightGraphRAGEngine
            from ..vfs.document_extractor import DocumentStreamExtractor
            actual_written = 0
            with conn:
                # 预读已有切片 ID 与内容哈希，严格比对已有内容，避免盲目重写与 FTS5 索引无脑膨胀
                cur = conn.cursor()
                cur.execute("SELECT id, content FROM hybrid_chunks")
                existing_map = {r[0]: hashlib.md5(r[1].encode("utf-8")).hexdigest() for r in cur.fetchall()}

                # 本批次内部去重 set，防止同批次内重复扫描导致重复插入
                seen_chunk_ids_in_batch: set[str] = set()

                for c in chunks:
                    content = c.get("content_text") or c.get("content") or ""
                    title = c.get("title") or Path(c["remote_uri"]).name
                    content_hash = hashlib.md5(content.encode("utf-8")).hexdigest()
                    section_p = str(c.get("section_path") or "")

                    # 严格确定性唯一哈希 ID：杜绝重复切片
                    chunk_id = hashlib.md5(
                        f"{c['remote_uri']}:{section_p}:{content_hash}".encode("utf-8")
                    ).hexdigest()

                    if chunk_id in seen_chunk_ids_in_batch:
                        continue
                    seen_chunk_ids_in_batch.add(chunk_id)

                    # 核心对比防无脑叠加：内容未变且 ID 存在时跳过
                    if chunk_id in existing_map and existing_map[chunk_id] == content_hash:
                        continue

                    tier = c.get("authority_tier", 2)
                    mtime = c.get("mtime", now)
                    etag = c.get("etag", "")
                    blob = c.get("embedding_blob")

                    doc_year = c.get("doc_year")
                    if doc_year is None or doc_year == 0:
                        doc_year = DocumentStreamExtractor.extract_doc_year(title, content[:400])

                    topic_cluster_id = c.get("topic_cluster_id")
                    if not topic_cluster_id:
                        topic_cluster_id = DocumentStreamExtractor.extract_topic_cluster_id(title)

                    is_latest = c.get("is_latest_in_cluster", 1)

                    conn.execute(
                        """
                        INSERT OR REPLACE INTO hybrid_chunks (
                            id, remote_uri, title, content, authority_tier, mtime, etag, embedding_blob,
                            doc_year, topic_cluster_id, is_latest_in_cluster, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            chunk_id,
                            c["remote_uri"],
                            title,
                            content,
                            tier,
                            mtime,
                            etag,
                            blob,
                            doc_year,
                            topic_cluster_id,
                            is_latest,
                            now,
                        ),
                    )
                    actual_written += 1

                    # 提取并写入启发式知识三元组
                    triples = LightGraphRAGEngine.extract_triples_from_text(
                        content, c["remote_uri"], authority_tier=tier, effective_time=mtime
                    )
                    for t in triples:
                        conn.execute(
                            """
                            INSERT INTO knowledge_triples (
                                subject, predicate, object, chunk_id, confidence, created_at, updated_at
                            ) VALUES (?, ?, ?, ?, 1.0, ?, ?)
                            ON CONFLICT(subject, predicate, object)
                            DO UPDATE SET
                                confidence = MIN(confidence + 0.1, 2.0),
                                updated_at = excluded.created_at
                            """,
                            (t.subject.strip(), t.predicate.strip(), t.object.strip(), chunk_id, now, now),
                        )

                # 族内最新版本定标（Cluster Head Refresh）：自动标记每个主题族最新年份为 is_latest_in_cluster=1
                conn.execute("""
                    UPDATE hybrid_chunks
                    SET is_latest_in_cluster = CASE
                        WHEN doc_year = (
                            SELECT MAX(c2.doc_year)
                            FROM hybrid_chunks c2
                            WHERE c2.topic_cluster_id = hybrid_chunks.topic_cluster_id
                        ) THEN 1
                        ELSE 0
                    END
                    WHERE topic_cluster_id != '';
                """)

                cursor = conn.cursor()
                cursor.execute("SELECT count(*) FROM hybrid_chunks")
                total_chunks = cursor.fetchone()[0]
                cursor.execute("SELECT count(*) FROM knowledge_triples")
                total_triples = cursor.fetchone()[0]

            if actual_written > 0:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        finally:
            conn.close()

        manifest = cls.write_manifest(
            kb_id=kb_id,
            version=version,
            embedding_model=embedding_model,
            embedding_dim=embedding_dim,
            chunk_count=total_chunks,
            triple_count=total_triples,
            changelog=f"写入/更新 {len(chunks)} 个切片",
        )

        disk_size = manifest.get("disk_size_bytes", 0)
        return total_chunks, disk_size

    @classmethod
    def extract_and_save_triples_for_kb(
        cls, kb_id: str, version: str, force_rebuild: bool = False
    ) -> tuple[int, int]:
        """批量抽取并写入知识图谱三元组（支持增量切片水位线与复合唯一幂等写入）。

        Args:
            kb_id: 知识库 ID
            version: 版本号
            force_rebuild: 是否强制重构（缺省 False 为增量水位线模式）

        Returns:
            tuple[int, int]: (new_extracted_triples, total_triples)
        """
        db_path = cls.get_version_db_path(kb_id, version)
        if not db_path.exists():
            return 0, 0

        cls.init_version_db_schema(db_path)
        from ..search.light_graph_rag import LightGraphRAGEngine

        now = time.time()
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            if force_rebuild:
                cur.execute("DELETE FROM knowledge_triples")
                cur.execute("UPDATE hybrid_chunks SET graph_extracted = 0")
                conn.commit()

            # 仅提取未标记抽取完成的切片（graph_extracted = 0）
            cur.execute("SELECT id, remote_uri, title, content, authority_tier, mtime FROM hybrid_chunks WHERE graph_extracted = 0")
            unprocessed_chunks = cur.fetchall()

            cur.execute("SELECT count(*) FROM knowledge_triples")
            existing_triples_count = cur.fetchone()[0]

            # 极速短路优化：所有切片均已完成图谱抽取，绝不浪费算力
            if not unprocessed_chunks and not force_rebuild:
                return 0, existing_triples_count

            new_extracted_count = 0
            processed_ids = []

            with conn:
                for r in unprocessed_chunks:
                    chunk_id = str(r["id"])
                    content = r["content"] or ""
                    uri = r["remote_uri"]
                    tier = r["authority_tier"]
                    mtime = r["mtime"] or now
                    processed_ids.append(chunk_id)

                    triples = LightGraphRAGEngine.extract_triples_from_text(
                        content, uri, authority_tier=tier, effective_time=mtime
                    )
                    for t in triples:
                        # 幂等 Upsert：同一条关系在不同切片中再次印证，自动累加置信度与更新时间
                        cur.execute(
                            """
                            INSERT INTO knowledge_triples (
                                subject, predicate, object, chunk_id, confidence, created_at, updated_at
                            ) VALUES (?, ?, ?, ?, 1.0, ?, ?)
                            ON CONFLICT(subject, predicate, object) DO UPDATE SET
                                confidence = confidence + 1.0,
                                updated_at = excluded.updated_at
                            """,
                            (t.subject, t.predicate, t.object, chunk_id, now, now),
                        )
                        if cur.rowcount > 0:
                            new_extracted_count += 1

                # 批量标记切片为已抽取
                if processed_ids:
                    batch_size = 500
                    for i in range(0, len(processed_ids), batch_size):
                        sub_ids = processed_ids[i:i + batch_size]
                        placeholders = ",".join("?" for _ in sub_ids)
                        cur.execute(f"UPDATE hybrid_chunks SET graph_extracted = 1 WHERE id IN ({placeholders})", sub_ids)

                cur.execute("SELECT count(*) FROM hybrid_chunks")
                total_chunks = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM knowledge_triples")
                total_triples = cur.fetchone()[0]

            # 触发 FTS5 彻底重构与 SQLite VACUUM 释放物理空间
            try:
                conn.execute("INSERT INTO hybrid_chunks_fts(hybrid_chunks_fts) VALUES('rebuild');")
                conn.commit()
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                conn.execute("VACUUM;")
            except Exception:
                pass

            cls.write_manifest(
                kb_id=kb_id,
                version=version,
                embedding_model="null_offline_lexical",
                embedding_dim=768,
                chunk_count=total_chunks,
                triple_count=total_triples,
                changelog=f"图谱抽取构建完成：新增/印证 {new_extracted_count} 条关系，总计 {total_triples} 条",
            )
            try:
                from ..repository.db import db_manager
                with sqlite3.connect(str(db_manager.db_path)) as mconn:
                    mconn.execute(
                        "UPDATE knowledge_bases SET triple_count = ?, chunk_count = ?, updated_at = ? WHERE kb_id = ?",
                        (total_triples, total_chunks, now, kb_id),
                    )
                    mconn.execute(
                        "UPDATE kb_versions SET triple_count = ?, chunk_count = ? WHERE kb_id = ? AND version = ?",
                        (total_triples, total_chunks, kb_id, version),
                    )
                    mconn.commit()
            except Exception as e:
                logger.debug(f"同步三元组统计至元数据表: {e}")

            return new_extracted_count, total_triples
        finally:
            conn.close()

    @classmethod
    def clean_invalid_chunks_for_kb(
        cls,
        kb_id: str,
        version: str,
        valid_prefix: str,
        valid_subpath: str | None = None,
    ) -> tuple[int, int, int]:
        """清理独立知识库中不符合当前有效前缀或目录的切片，并重新校准切片数与磁盘大小。

        Returns:
            (deleted_count, remaining_chunks, disk_size)
        """
        db_path = cls.get_version_db_path(kb_id, version)
        if not db_path.exists():
            return 0, 0, 0

        conn = sqlite3.connect(str(db_path))
        deleted_count = 0
        try:
            cur = conn.cursor()
            cur.execute("SELECT id, remote_uri FROM hybrid_chunks")
            rows = cur.fetchall()
            bad_ids = []
            for cid, uri in rows:
                is_ok = uri.startswith(valid_prefix)
                if is_ok and valid_subpath and valid_subpath != "/":
                    sub = uri[len(valid_prefix):]
                    from urllib.parse import unquote
                    clean_norm = "/" + unquote(sub).strip("/")
                    if not clean_norm.startswith(valid_subpath):
                        is_ok = False
                if not is_ok:
                    bad_ids.append(cid)

            if bad_ids:
                deleted_count = len(bad_ids)
                placeholders = ",".join("?" for _ in bad_ids)
                cur.execute(f"DELETE FROM hybrid_chunks WHERE id IN ({placeholders})", bad_ids)
                try:
                    cur.execute(f"DELETE FROM knowledge_triples WHERE chunk_id IN ({placeholders})", bad_ids)
                except Exception:
                    pass
                conn.commit()
                # 触发 FTS5 彻底重构与 SQLite 物理瘦身，彻底阻断空间虚高膨胀
                try:
                    conn.execute("INSERT INTO hybrid_chunks_fts(hybrid_chunks_fts) VALUES('rebuild');")
                    conn.commit()
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                    conn.execute("VACUUM;")
                except Exception:
                    pass

            cur.execute("SELECT count(*) FROM hybrid_chunks")
            remaining_chunks = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM knowledge_triples")
            remaining_triples = cur.fetchone()[0]
        finally:
            conn.close()

        manifest = cls.write_manifest(
            kb_id=kb_id,
            version=version,
            embedding_model="null_offline_lexical",
            embedding_dim=768,
            chunk_count=remaining_chunks,
            triple_count=remaining_triples,
            changelog=f"隔离清理完成：移除非当前挂载目录/前缀的 {deleted_count} 个切片" if deleted_count > 0 else "切片完整性校验完成",
        )
        disk_size = manifest.get("disk_size_bytes", 0)
        return deleted_count, remaining_chunks, disk_size

    @classmethod
    def get_version_db_path(cls, kb_id: str, version: str) -> Path:
        """获取指定版本独立 index.db 路径。"""
        return cls.get_version_dir(kb_id, version) / "index.db"

    @classmethod
    def get_manifest_path(cls, kb_id: str, version: str) -> Path:
        """获取指定版本清单 manifest.json 路径。"""
        return cls.get_version_dir(kb_id, version) / "manifest.json"

    @classmethod
    def init_version_db_schema(cls, db_path: Path) -> None:
        """初始化独立知识库 index.db 的 SQLite 结构（切片、FTS5 与三元组）。"""
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS hybrid_chunks (
                id              TEXT PRIMARY KEY,
                remote_uri      TEXT NOT NULL,
                title           TEXT NOT NULL,
                content         TEXT NOT NULL,
                authority_tier  INTEGER NOT NULL DEFAULT 2,
                mtime           REAL NOT NULL DEFAULT 0.0,
                etag            TEXT NOT NULL DEFAULT '',
                embedding_blob  BLOB,
                graph_extracted INTEGER NOT NULL DEFAULT 0,
                created_at      REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_hc_uri ON hybrid_chunks(remote_uri);
            CREATE INDEX IF NOT EXISTS idx_hc_tier ON hybrid_chunks(authority_tier);

            CREATE VIRTUAL TABLE IF NOT EXISTS hybrid_chunks_fts USING fts5(
                content,
                title,
                chunk_id UNINDEXED,
                remote_uri UNINDEXED,
                tokenize='trigram'
            );

            CREATE TRIGGER IF NOT EXISTS trg_hc_ai AFTER INSERT ON hybrid_chunks
            BEGIN
                INSERT INTO hybrid_chunks_fts(rowid, content, title, chunk_id, remote_uri)
                VALUES (new.rowid, new.content, new.title, new.id, new.remote_uri);
            END;

            CREATE TRIGGER IF NOT EXISTS trg_hc_ad AFTER DELETE ON hybrid_chunks
            BEGIN
                DELETE FROM hybrid_chunks_fts WHERE rowid = old.rowid;
            END;

            CREATE TRIGGER IF NOT EXISTS trg_hc_au AFTER UPDATE ON hybrid_chunks
            BEGIN
                UPDATE hybrid_chunks_fts SET content = new.content, title = new.title WHERE rowid = old.rowid;
            END;

            CREATE TABLE IF NOT EXISTS knowledge_triples (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                subject     TEXT NOT NULL,
                predicate   TEXT NOT NULL,
                object      TEXT NOT NULL,
                chunk_id    TEXT NOT NULL,
                confidence  REAL NOT NULL DEFAULT 1.0,
                created_at  REAL NOT NULL,
                updated_at  REAL NOT NULL DEFAULT 0.0
            );

            CREATE INDEX IF NOT EXISTS idx_kt_sub ON knowledge_triples(subject);
            CREATE INDEX IF NOT EXISTS idx_kt_obj ON knowledge_triples(object);
            CREATE INDEX IF NOT EXISTS idx_kt_chunk ON knowledge_triples(chunk_id);
            """)

            # 兼容性平滑升级：补齐 graph_extracted 列
            try:
                conn.execute("ALTER TABLE hybrid_chunks ADD COLUMN graph_extracted INTEGER NOT NULL DEFAULT 0;")
            except Exception:
                pass
            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_hc_gext ON hybrid_chunks(graph_extracted);")
            except Exception:
                pass

            # 兼容性平滑升级：补齐 knowledge_triples 的 confidence / updated_at 列
            for _col, _col_def in [
                ("confidence", "REAL NOT NULL DEFAULT 1.0"),
                ("updated_at", "REAL NOT NULL DEFAULT 0.0"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE knowledge_triples ADD COLUMN {_col} {_col_def};")
                except Exception:
                    pass
            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_kt_conf ON knowledge_triples(confidence DESC);")
            except Exception:
                pass

            # 兼容性平滑升级：补齐 hybrid_chunks 的父子分层与时空元数据字段（v1.5.9+）
            for _col, _col_def in [
                ("chunk_role",           "TEXT NOT NULL DEFAULT 'child'"),
                ("parent_chunk_id",      "TEXT DEFAULT NULL"),
                ("section_path",         "TEXT NOT NULL DEFAULT ''"),
                ("doc_year",             "INTEGER NOT NULL DEFAULT 0"),
                ("effective_date",       "TEXT NOT NULL DEFAULT ''"),
                ("effective_ts",         "INTEGER NOT NULL DEFAULT 0"),
                ("doc_priority_type",    "TEXT NOT NULL DEFAULT 'normal'"),
                ("topic_cluster_id",     "TEXT NOT NULL DEFAULT ''"),
                ("is_latest_in_cluster", "INTEGER NOT NULL DEFAULT 1"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE hybrid_chunks ADD COLUMN {_col} {_col_def};")
                except Exception:
                    pass
            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_hc_parent ON hybrid_chunks(parent_chunk_id);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_hc_temporal_cluster ON hybrid_chunks(topic_cluster_id, effective_ts DESC);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_hc_role ON hybrid_chunks(chunk_role);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_hc_cluster ON hybrid_chunks(topic_cluster_id, doc_year);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_hc_latest  ON hybrid_chunks(is_latest_in_cluster);")
            except Exception:
                pass

            # 清理历史已存在的完全重复三元组，保留置信度最高或最小 id 记录
            try:
                conn.execute("""
                DELETE FROM knowledge_triples
                WHERE id NOT IN (
                    SELECT MIN(id)
                    FROM knowledge_triples
                    GROUP BY LOWER(TRIM(subject)), LOWER(TRIM(predicate)), LOWER(TRIM(object))
                );
                """)
            except Exception:
                pass

            # 建立三元组复合唯一约束，杜绝重复边，提升多切片印证时的置信度权重
            try:
                conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_kt_unique
                ON knowledge_triples (subject, predicate, object);
                """)
            except Exception:
                pass

            conn.commit()
        finally:
            conn.close()

    @classmethod
    def write_manifest(
        cls,
        kb_id: str,
        version: str,
        embedding_model: str,
        embedding_dim: int,
        chunk_count: int,
        triple_count: int,
        changelog: str = "",
        extra_meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """生成并写入自包含 manifest.json 清单。"""
        db_path = cls.get_version_db_path(kb_id, version)
        package_hash = ""
        disk_size = 0
        if db_path.exists():
            disk_size = db_path.stat().st_size
            h = hashlib.sha256()
            with open(db_path, "rb") as f:
                while chunk := f.read(65536):
                    h.update(chunk)
            package_hash = h.hexdigest()

        manifest = {
            "kb_id": kb_id,
            "version": version,
            "embedding_model": embedding_model,
            "embedding_dim": embedding_dim,
            "chunk_count": chunk_count,
            "triple_count": triple_count,
            "disk_size_bytes": disk_size,
            "package_hash": package_hash,
            "changelog": changelog,
            "created_at": time.time(),
            "extra": extra_meta or {},
        }
        manifest_file = cls.get_manifest_path(kb_id, version)
        manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return manifest

    @classmethod
    def read_manifest(cls, kb_id: str, version: str) -> dict[str, Any] | None:
        """读取指定版本的 manifest.json。"""
        p = cls.get_manifest_path(kb_id, version)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    @classmethod
    def copy_version_snapshot(
        cls,
        kb_id: str,
        source_version: str,
        target_version: str,
    ) -> bool:
        """基于已有版本复制生成新版本初始快照（极速快照创建）。"""
        src_dir = cls.get_version_dir(kb_id, source_version)
        tgt_dir = cls.get_version_dir(kb_id, target_version)
        src_db = src_dir / "index.db"
        tgt_db = tgt_dir / "index.db"

        if not src_db.exists():
            cls.init_version_db_schema(tgt_db)
            return True

        # 使用 SQLite 在线备份确保快照一致性
        src_conn = sqlite3.connect(str(src_db))
        tgt_conn = sqlite3.connect(str(tgt_db))
        try:
            src_conn.backup(tgt_conn)
            tgt_conn.commit()
        finally:
            tgt_conn.close()
            src_conn.close()

        # 复制或初始化 manifest
        src_manifest = src_dir / "manifest.json"
        if src_manifest.exists():
            shutil.copy2(str(src_manifest), str(tgt_dir / "manifest.json"))
        return True

    @classmethod
    def get_kb_disk_size(cls, kb_id: str) -> int:
        """统计知识库目录下所有版本所占用的真实物理磁盘字节数。"""
        kb_dir = cls.get_kb_dir(kb_id)
        if not kb_dir.exists():
            return 0
        total = 0
        for root, _, files in os.walk(kb_dir):
            for f in files:
                fp = Path(root) / f
                try:
                    total += fp.stat().st_size
                except Exception:
                    pass
        return total

    @classmethod
    def prune_old_versions(
        cls,
        kb_id: str,
        keep_versions: list[str],
    ) -> tuple[int, int]:
        """清理不在 keep_versions 中的历史旧版本目录，并对保留的 index.db 执行 VACUUM。

        Returns:
            (pruned_count, freed_bytes)
        """
        versions_root = cls.get_kb_dir(kb_id) / "versions"
        if not versions_root.exists():
            return 0, 0

        keep_set = set(keep_versions)
        pruned_count = 0
        freed_bytes = 0

        for child in list(versions_root.iterdir()):
            if child.is_dir() and child.name not in keep_set:
                try:
                    dir_size = sum(f.stat().st_size for f in child.glob("**/*") if f.is_file())
                    shutil.rmtree(child, ignore_errors=True)
                    pruned_count += 1
                    freed_bytes += dir_size
                    logger.info(f"已清理知识库 [{kb_id}] 历史旧版本: {child.name} (释放 {dir_size / 1024:.1f} KB)")
                except Exception as e:
                    logger.warning(f"清理旧版本目录 [{child}] 失败: {e}")

        # 对保留活跃的各版本执行 FTS5 重建与 VACUUM 深度瘦身
        for ver in keep_set:
            db_p = cls.get_version_db_path(kb_id, ver)
            if db_p.exists():
                try:
                    conn = sqlite3.connect(str(db_p))
                    try:
                        conn.execute("INSERT INTO hybrid_chunks_fts(hybrid_chunks_fts) VALUES('rebuild');")
                        conn.commit()
                    except Exception:
                        pass
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                    conn.execute("VACUUM;")
                    conn.close()
                except Exception:
                    pass

        return pruned_count, freed_bytes

    @classmethod
    def physically_destroy_kb(cls, kb_id: str) -> bool:
        """物理销毁整个知识库存储目录。"""
        kb_dir = cls.get_kb_dir(kb_id)
        if kb_dir.exists():
            shutil.rmtree(kb_dir, ignore_errors=True)
            logger.info(f"已彻底物理销毁知识库存储目录: {kb_dir}")
            return True
        return False

    @classmethod
    def physically_destroy_all_kbs(cls) -> int:
        """彻底物理销毁所有独立知识库的物理存储目录（清除所有版本的 index.db 与 manifest）。"""
        destroyed_count = 0
        if KB_STORE_ROOT.exists():
            for child in list(KB_STORE_ROOT.iterdir()):
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                    destroyed_count += 1
            logger.info(f"已彻底物理销毁所有独立知识库存储目录（共 {destroyed_count} 个）: {KB_STORE_ROOT}")
        return destroyed_count

