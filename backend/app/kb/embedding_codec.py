"""
知识库向量半精度（float16）编解码与 SQLite 空间瘦身工具。
核心设计：
1. 写入统一采用 IEEE-754 float16（2 字节/维），将向量存储空间直接减半；
2. 读取时根据 `len(blob)` 与查询向量维度 `expected_dim` 自动判别 float16 (2*dim) 与旧版 float32 (4*dim)，100% 向后兼容；
3. 提供安全的 `compact_kb_version_db` 瘦身例程：向量转 float16 + 清理 FTS5 幽灵冗余行 + page_size=16384 + VACUUM。
"""
from __future__ import annotations

import array
import sqlite3
import struct
from pathlib import Path
from typing import Any, Sequence

try:
    import numpy as np  # type: ignore
    HAS_NUMPY = True
except ImportError:
    np = None
    HAS_NUMPY = False

# 常见向量模型维度集合，用于无上下文时安全识别 float32 -> float16 转换
_COMMON_EMBEDDING_DIMS = frozenset({256, 384, 512, 768, 1024, 1536, 2048, 2560, 3072, 4096})


def encode_embedding_f16(vec: Sequence[float] | Any) -> bytes:
    """将浮点向量编码为紧凑的 float16 字节流（2 字节/维）。"""
    if HAS_NUMPY and np is not None:
        return np.asarray(vec, dtype=np.float16).tobytes()
    vals = vec.tolist() if hasattr(vec, "tolist") else list(vec)
    return struct.pack(f"<{len(vals)}e", *(float(x) for x in vals))


def ensure_f16_blob(blob: bytes | None, expected_dim: int | None = None) -> bytes | None:
    """若输入 blob 为旧版 float32 字节流，自动转换为 float16；已是 float16 或为空则原样返回。"""
    if not blob:
        return blob
    n_bytes = len(blob)
    if expected_dim is not None:
        if n_bytes == expected_dim * 2:
            return blob
        if n_bytes == expected_dim * 4:
            if HAS_NUMPY and np is not None:
                return np.frombuffer(blob, dtype=np.float32).astype(np.float16).tobytes()
            arr = array.array("f")
            arr.frombytes(blob)
            return struct.pack(f"<{len(arr)}e", *arr)
        return blob

    # 无显式维度时：仅对高置信度 float32 长度（>= 2560 字节即 >= 640 维 float32，且 /4 属于常见维度）转换
    # 避免误将 1024 维 float16 (2048 字节) 当作 512 维 float32 二次压缩
    if n_bytes >= 2560 and n_bytes % 4 == 0 and (n_bytes // 4) in _COMMON_EMBEDDING_DIMS:
        if HAS_NUMPY and np is not None:
            return np.frombuffer(blob, dtype=np.float32).astype(np.float16).tobytes()
        arr = array.array("f")
        arr.frombytes(blob)
        return struct.pack(f"<{len(arr)}e", *arr)
    return blob


def decode_embedding(blob: bytes | None, expected_dim: int) -> Any | None:
    """兼容解码 float16 (2 字节/维) 与旧版 float32 (4 字节/维) 向量。"""
    if not blob or expected_dim <= 0:
        return None
    n_bytes = len(blob)
    if n_bytes == expected_dim * 2:
        if HAS_NUMPY and np is not None:
            return np.frombuffer(blob, dtype=np.float16).astype(np.float32)
        vals = struct.unpack(f"<{expected_dim}e", blob)
        return array.array("f", vals)
    if n_bytes == expected_dim * 4:
        if HAS_NUMPY and np is not None:
            return np.frombuffer(blob, dtype=np.float32)
        arr = array.array("f")
        arr.frombytes(blob)
        return arr
    return None


def compact_kb_version_db(db_path: Path) -> dict[str, Any]:
    """对单个知识库版本 index.db 执行方案 B 安全瘦身：
    1. 将 float32 embedding_blob 批量转换为 float16；
    2. 清理 hybrid_chunks_fts 幽灵冗余行并修复触发器；
    3. 调整 SQLite page_size=16384 并执行 VACUUM 彻底释放磁盘空间。
    """
    if not db_path.exists():
        return {"skipped": True, "reason": "db_not_found"}

    size_before = db_path.stat().st_size
    converted_vecs = 0
    fts_before = 0
    fts_after = 0

    conn = sqlite3.connect(str(db_path))
    try:
        # 1. 向量转 float16
        has_chunks = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='hybrid_chunks'"
        ).fetchone()
        if has_chunks:
            rows = conn.execute(
                "SELECT id, embedding_blob FROM hybrid_chunks WHERE embedding_blob IS NOT NULL"
            ).fetchall()
            updates: list[tuple[bytes, str]] = []
            for cid, blob in rows:
                if not blob:
                    continue
                new_blob = ensure_f16_blob(blob)
                if new_blob is not None and len(new_blob) < len(blob):
                    updates.append((new_blob, cid))
            if updates:
                with conn:
                    conn.executemany(
                        "UPDATE hybrid_chunks SET embedding_blob = ? WHERE id = ?",
                        updates,
                    )
                converted_vecs = len(updates)

            # 2. 清理 FTS5 幽灵冗余行并更新触发器，防止后续 INSERT OR REPLACE 再次产生重复索引
            has_fts = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='hybrid_chunks_fts'"
            ).fetchone()
            if has_fts:
                fts_before = int(conn.execute("SELECT COUNT(*) FROM hybrid_chunks_fts").fetchone()[0] or 0)
                with conn:
                    conn.execute("DROP TRIGGER IF EXISTS trg_hc_ai")
                    conn.execute(
                        """
                        CREATE TRIGGER IF NOT EXISTS trg_hc_ai AFTER INSERT ON hybrid_chunks
                        BEGIN
                            DELETE FROM hybrid_chunks_fts WHERE chunk_id = new.id;
                            INSERT INTO hybrid_chunks_fts(rowid, content, title, chunk_id, remote_uri)
                            VALUES (new.rowid, new.content, new.title, new.id, new.remote_uri);
                        END;
                        """
                    )
                    conn.execute("DELETE FROM hybrid_chunks_fts")
                    conn.execute(
                        """
                        INSERT INTO hybrid_chunks_fts(rowid, content, title, chunk_id, remote_uri)
                        SELECT rowid, content, title, id, remote_uri FROM hybrid_chunks
                        """
                    )
                    # 关键：FTS5 的 DELETE 会留下大量墓碑段，必须执行 optimize 才能物理回收倒排索引空间
                    conn.execute("INSERT INTO hybrid_chunks_fts(hybrid_chunks_fts) VALUES('optimize');")
                fts_after = int(conn.execute("SELECT COUNT(*) FROM hybrid_chunks_fts").fetchone()[0] or 0)

        # 3. 调整页大小为 16KB 并执行 VACUUM 物理瘦身（WAL 模式下需先切 DELETE 才能修改 page_size）
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        conn.execute("PRAGMA journal_mode = DELETE;")
        conn.execute("PRAGMA page_size = 16384;")
        conn.execute("VACUUM;")
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
    finally:
        conn.close()

    size_after = db_path.stat().st_size
    return {
        "db_path": str(db_path),
        "size_before_bytes": size_before,
        "size_after_bytes": size_after,
        "saved_bytes": max(0, size_before - size_after),
        "converted_vecs": converted_vecs,
        "fts_before": fts_before,
        "fts_after": fts_after,
    }
