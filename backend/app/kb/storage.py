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
                id             TEXT PRIMARY KEY,
                remote_uri     TEXT NOT NULL,
                title          TEXT NOT NULL,
                content        TEXT NOT NULL,
                authority_tier INTEGER NOT NULL DEFAULT 2,
                mtime          REAL NOT NULL DEFAULT 0.0,
                etag           TEXT NOT NULL DEFAULT '',
                embedding_blob BLOB,
                created_at     REAL NOT NULL
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
                created_at  REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_kt_sub ON knowledge_triples(subject);
            CREATE INDEX IF NOT EXISTS idx_kt_obj ON knowledge_triples(object);
            CREATE INDEX IF NOT EXISTS idx_kt_chunk ON knowledge_triples(chunk_id);
            """)
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

        # 对保留活跃的各版本执行 VACUUM
        for ver in keep_set:
            db_p = cls.get_version_db_path(kb_id, ver)
            if db_p.exists():
                try:
                    conn = sqlite3.connect(str(db_p))
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

