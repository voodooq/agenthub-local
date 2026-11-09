"""
本地物理目录零拷贝增量 FTS5 索引引擎 (Filtered & Incremental FTS5 Engine)
核心功能：
1. 强制遵循 .gitignore 与 .agenthubignore 规则，过滤无关大文件与中间产物
2. 增量水位线：基于 mtime + size + sha256 仅对增量改动文件建立 trigram BM25 全文索引
3. 数据库独立存放于 <workspace>/.agenthub/index.db，与用户代码完全解耦
"""
from contextlib import closing
import fnmatch
import hashlib
import os
import sqlite3
from pathlib import Path
from typing import Any
from ..core.logging_config import logger


# 内置全局忽略模式
DEFAULT_IGNORE_PATTERNS = [
    "*.git*",
    "*node_modules*",
    "*.agenthub*",
    "*dist*",
    "*build*",
    "*__pycache__*",
    "*.venv*",
    "*venv*",
    "*.next*",
    "*.nuxt*",
    "*.cache*",
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.webp",
    "*.ico",
    "*.pdf",
    "*.zip",
    "*.tar*",
    "*.gz",
    "*.exe",
    "*.dll",
    "*.so",
    "*.dylib",
    "*.mp4",
    "*.mp3",
    "*.wav",
    "*.pyc",
    "*.class",
]

# 允许索引的文件后缀白名单（代码与纯文本）
INDEXABLE_EXTENSIONS = {
    ".md", ".txt", ".py", ".ts", ".tsx", ".js", ".jsx", ".json",
    ".yaml", ".yml", ".html", ".css", ".scss", ".sql", ".sh",
    ".bat", ".ps1", ".rs", ".go", ".java", ".c", ".cpp", ".h", ".csv",
}


class WorkspaceFTSEngine:
    """
    工作区专属 FTS5 检索引擎
    """
    def __init__(self, workspace_root: str):
        self.workspace_root = Path(workspace_root).resolve()
        self.db_dir = self.workspace_root / ".agenthub"
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.db_dir / "index.db"
        self._init_fts_db()

    def _init_fts_db(self) -> None:
        """
        初始化工作区专属 FTS5 表与文件元数据水印表
        """
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute("PRAGMA journal_mode = WAL;")
                conn.execute("""
                CREATE TABLE IF NOT EXISTS file_watermarks (
                    rel_path TEXT PRIMARY KEY,
                    mtime REAL NOT NULL,
                    size INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    last_indexed_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                """)
                conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS workspace_fts USING fts5(
                    rel_path UNINDEXED,
                    content,
                    tokenize='trigram'
                );
                """)

    def _load_ignore_patterns(self) -> list[str]:
        """
        加载 .gitignore 与 .agenthubignore 规则
        """
        patterns = list(DEFAULT_IGNORE_PATTERNS)
        for ign_filename in [".gitignore", ".agenthubignore"]:
            ign_file = self.workspace_root / ign_filename
            if ign_file.exists():
                try:
                    lines = ign_file.read_text(encoding="utf-8", errors="ignore").splitlines()
                    for line in lines:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            patterns.append(line.rstrip("/"))
                except Exception as e:
                    logger.warning(f"读取忽略文件 {ign_file} 失败: {e}")
        return patterns

    def _is_ignored(self, rel_path_str: str, patterns: list[str]) -> bool:
        """
        判定路径是否命中忽略规则
        """
        parts = rel_path_str.replace("\\", "/").split("/")
        for part in parts:
            if part.startswith(".") and part != "." and part != "..":
                if part != ".agenthub":  # agenthub 已在默认列表中
                    pass
            for pat in patterns:
                if fnmatch.fnmatch(part, pat) or fnmatch.fnmatch(rel_path_str.replace("\\", "/"), pat):
                    return True
        return False

    def reindex(self, max_file_size_bytes: int = 512 * 1024) -> dict[str, int]:
        """
        对工作区执行增量扫描与 FTS5 更新
        - 跳过超过 512KB 的巨型代码文件
        - 仅在 mtime/size 改变时计算哈希并更新索引
        """
        patterns = self._load_ignore_patterns()
        indexed_count = 0
        skipped_count = 0
        deleted_count = 0

        # 获取已有水印
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT rel_path, mtime, size, content_hash FROM file_watermarks;")
            existing_records = {row["rel_path"]: dict(row) for row in cursor.fetchall()}

        scanned_rel_paths: set[str] = set()

        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                for root, dirs, files in os.walk(self.workspace_root):
                    for filename in files:
                        full_file = Path(root) / filename
                        try:
                            rel_path = full_file.relative_to(self.workspace_root)
                            rel_path_str = str(rel_path).replace("\\", "/")
                        except ValueError:
                            continue

                        if self._is_ignored(rel_path_str, patterns):
                            continue

                        if full_file.suffix.lower() not in INDEXABLE_EXTENSIONS:
                            continue

                        scanned_rel_paths.add(rel_path_str)

                        try:
                            stat = full_file.stat()
                        except OSError:
                            continue

                        # 超过单文件大小上限则跳过
                        if stat.st_size > max_file_size_bytes or stat.st_size == 0:
                            skipped_count += 1
                            continue

                        # 水印检查：mtime 与 size 均未改变则视为命中缓存
                        old_meta = existing_records.get(rel_path_str)
                        if old_meta and abs(old_meta["mtime"] - stat.st_mtime) < 0.001 and old_meta["size"] == stat.st_size:
                            skipped_count += 1
                            continue

                        # 增量读取文件内容
                        try:
                            content = full_file.read_text(encoding="utf-8", errors="ignore")
                        except Exception:
                            continue

                        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
                        if old_meta and old_meta["content_hash"] == content_hash:
                            # 内容哈希相同，仅更新 mtime
                            conn.execute(
                                "UPDATE file_watermarks SET mtime = ?, size = ? WHERE rel_path = ?;",
                                (stat.st_mtime, stat.st_size, rel_path_str),
                            )
                            skipped_count += 1
                            continue

                        # 写入或刷新 FTS5
                        conn.execute("DELETE FROM workspace_fts WHERE rel_path = ?;", (rel_path_str,))
                        conn.execute(
                            "INSERT INTO workspace_fts(rel_path, content) VALUES (?, ?);",
                            (rel_path_str, content),
                        )
                        conn.execute(
                            """
                            INSERT INTO file_watermarks (rel_path, mtime, size, content_hash)
                            VALUES (?, ?, ?, ?)
                            ON CONFLICT(rel_path) DO UPDATE SET
                                mtime = excluded.mtime,
                                size = excluded.size,
                                content_hash = excluded.content_hash,
                                last_indexed_at = CURRENT_TIMESTAMP;
                            """,
                            (rel_path_str, stat.st_mtime, stat.st_size, content_hash),
                        )
                        indexed_count += 1

                # 清理物理已删除文件的索引
                for old_path in existing_records:
                    if old_path not in scanned_rel_paths:
                        conn.execute("DELETE FROM file_watermarks WHERE rel_path = ?;", (old_path,))
                        conn.execute("DELETE FROM workspace_fts WHERE rel_path = ?;", (old_path,))
                        deleted_count += 1

        logger.info(
            f"工作区 [{self.workspace_root.name}] 增量索引更新完成: 新增/更新 {indexed_count} 篇, 跳过 {skipped_count} 篇, 清理已删除 {deleted_count} 篇"
        )
        return {
            "indexed": indexed_count,
            "skipped": skipped_count,
            "deleted": deleted_count,
        }

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """
        基于 BM25 trigram 在工作区内检索匹配的代码/文档片段；
        当查询词长度 < 3 字符或 trigram MATCH 无命中时，平滑降级为 LIKE 子串检索防漏召回。
        """
        raw_query = (query or "").strip()
        if not raw_query:
            return []

        safe_limit = max(1, min(int(limit), 50))
        results: list[dict[str, Any]] = []

        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            if len(raw_query) >= 3:
                clean_query = raw_query.replace('"', '""')
                try:
                    cursor = conn.execute(
                        """
                        SELECT rel_path, snippet(workspace_fts, 1, '<b>', '</b>', '...', 20) AS snippet,
                               bm25(workspace_fts) AS rank
                        FROM workspace_fts
                        WHERE workspace_fts MATCH ?
                        ORDER BY rank
                        LIMIT ?;
                        """,
                        (f'"{clean_query}"', safe_limit),
                    )
                    for row in cursor.fetchall():
                        results.append({
                            "rel_path": row["rel_path"],
                            "snippet": row["snippet"],
                            "rank": round(float(row["rank"]), 4),
                        })
                except sqlite3.Error as e:
                    logger.debug(f"FTS5 trigram MATCH 查询失败，回退 LIKE 检索: {e}")

            if not results:
                escaped = raw_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                like_cursor = conn.execute(
                    """
                    SELECT rel_path, content
                    FROM workspace_fts
                    WHERE content LIKE ? ESCAPE '\\'
                    LIMIT ?;
                    """,
                    (f"%{escaped}%", safe_limit),
                )
                lower_q = raw_query.lower()
                for row in like_cursor.fetchall():
                    text = row["content"] or ""
                    idx = text.lower().find(lower_q)
                    if idx >= 0:
                        start = max(0, idx - 60)
                        end = min(len(text), idx + len(raw_query) + 120)
                        prefix = "..." if start > 0 else ""
                        suffix = "..." if end < len(text) else ""
                        snippet_text = f"{prefix}{text[start:end]}{suffix}"
                    else:
                        snippet_text = text[:180]
                    results.append({
                        "rel_path": row["rel_path"],
                        "snippet": snippet_text,
                        "rank": 0.0,
                    })

        return results

