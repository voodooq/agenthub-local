"""
CloudVFS 虚拟文件系统抽象基类与增量水位线追踪器 (Cloud VFS Base)
核心功能：
1. 统一云存储抽象接口（企微微盘 / WebDAV / S3 OSS）；
2. 远程文件元数据模型 CloudFileMeta；
3. 基于 cloud_vfs_sync_state 表的 ETag/mtime 增量水位线追踪。
"""
import abc
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import sqlite3
import time
from typing import Any

from ..core.logging_config import logger


@dataclass
class CloudFileMeta:
    """远程云端文件元数据对象。"""
    remote_uri: str
    title: str
    size: int
    etag: str
    mtime: float
    mime_type: str = ""
    is_dir: bool = False
    authority_tier: int = 2


class CloudVFSProvider(abc.ABC):
    """云端虚拟文件系统适配器抽象基类。"""

    def __init__(self, mount_id: str, mount_name: str, authority_tier: int = 2, config: dict[str, Any] | None = None):
        self.mount_id = mount_id
        self.mount_name = mount_name
        self.authority_tier = authority_tier
        self.config = config or {}

    @abc.abstractmethod
    async def test_connection(self) -> tuple[bool, str]:
        """连通性与鉴权测试。返回 (是否成功, 提示信息)。"""
        pass

    @abc.abstractmethod
    async def list_files(self, recursive: bool = True) -> list[CloudFileMeta]:
        """遍历远程文件列表。"""
        pass

    @abc.abstractmethod
    async def download_stream(self, remote_uri: str) -> bytes:
        """流式拉取远程单文件二进制字节，用完即焚。"""
        pass


class CloudVFSSyncManager:
    """基于 SQLite cloud_vfs_sync_state 表的增量同步水位线管理器。"""

    def __init__(self, workspace_root: str):
        self.workspace_root = Path(workspace_root).resolve()
        self.db_path = self.workspace_root / ".agenthub" / "index.db"

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=60.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 60000;")
        return conn

    def filter_modified_files(
        self, mount_id: str, files: list[CloudFileMeta]
    ) -> list[CloudFileMeta]:
        """比对本地水位线，筛选出新增或产生实质变更的远程文件。"""
        if not files:
            return []

        modified: list[CloudFileMeta] = []
        with closing(self._get_connection()) as conn:
            for f in files:
                if f.is_dir:
                    continue
                row = conn.execute("""
                    SELECT etag_or_sha, remote_mtime
                    FROM cloud_vfs_sync_state
                    WHERE mount_id = ? AND remote_uri = ?
                """, (mount_id, f.remote_uri)).fetchone()

                if not row:
                    modified.append(f)
                else:
                    last_etag = row["etag_or_sha"]
                    last_mtime = row["remote_mtime"]
                    # 若 ETag 变动，或者 mtime 增加
                    if f.etag and f.etag != last_etag:
                        modified.append(f)
                    elif f.mtime > (last_mtime + 1.0):
                        modified.append(f)

        return modified

    def update_sync_state(
        self, mount_id: str, file_meta: CloudFileMeta
    ) -> None:
        """更新单个文件的同步水位线。"""
        now = time.time()
        with closing(self._get_connection()) as conn:
            with conn:
                conn.execute("""
                INSERT INTO cloud_vfs_sync_state (
                    mount_id, remote_uri, etag_or_sha, remote_mtime, authority_tier, last_synced_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(mount_id, remote_uri) DO UPDATE SET
                    etag_or_sha = excluded.etag_or_sha,
                    remote_mtime = excluded.remote_mtime,
                    authority_tier = excluded.authority_tier,
                    last_synced_at = excluded.last_synced_at;
                """, (
                    mount_id, file_meta.remote_uri, file_meta.etag,
                    file_meta.mtime, file_meta.authority_tier, now
                ))

    def get_synced_count(self, mount_id: str) -> int:
        """获取指定挂载源已同步的文件数。"""
        with closing(self._get_connection()) as conn:
            row = conn.execute("""
                SELECT COUNT(*) as cnt FROM cloud_vfs_sync_state WHERE mount_id = ?
            """, (mount_id,)).fetchone()
            return row["cnt"] if row else 0
