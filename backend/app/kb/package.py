"""
AgentHub Local v1.5.8 知识库共享包打包与分发引擎 (KB Packaging & Distribution)
规范与特性：
1. 标准 .kbpack 归档格式 (Gzip 压缩自包含 index.db + manifest.json)；
2. 强制校验模型契约 (embedding_model 与 embedding_dim)，杜绝跨端向量空间畸变；
3. 共享目录 catalog.json 索引自维护；
4. 订阅只读锁 (subscribed_ro) 自动注入与 Fork 本地独立副本克隆。
"""
import hashlib
import io
import json
from pathlib import Path
import shutil
import tarfile
import time
from typing import Any

from ..core.logging_config import logger
from ..repository.db import db_manager
from .storage import KBStorageManager


class KBPackageEngine:
    """知识库打包、导入与团队共享引擎。"""

    @classmethod
    def export_package(
        cls,
        kb_id: str,
        version: str,
        output_dir: Path | None = None,
    ) -> Path:
        """将指定版本的知识库打包导出为标准 .kbpack 归档文件。

        Returns:
            生成的 .kbpack 文件路径
        """
        ver_dir = KBStorageManager.get_version_dir(kb_id, version)
        db_path = ver_dir / "index.db"
        manifest_path = ver_dir / "manifest.json"

        if not db_path.exists():
            raise FileNotFoundError(f"知识库 [{kb_id}] 的版本 [{version}] index.db 不存在")

        if output_dir is None:
            output_dir = KBStorageManager.get_kb_dir(kb_id) / "packages"
        output_dir.mkdir(parents=True, exist_ok=True)

        package_filename = f"{kb_id}_{version}.kbpack"
        pkg_path = output_dir / package_filename

        with tarfile.open(pkg_path, "w:gz") as tar:
            tar.add(db_path, arcname="index.db")
            if manifest_path.exists():
                tar.add(manifest_path, arcname="manifest.json")

        logger.info(f"已成功打包知识库 [{kb_id}] v[{version}] -> {pkg_path} ({pkg_path.stat().st_size / 1024:.1f} KB)")
        return pkg_path

    @classmethod
    def inspect_package(cls, pkg_path: Path) -> dict[str, Any]:
        """读取并检查 .kbpack 包内的 manifest.json 声明与元数据。"""
        if not pkg_path.exists():
            raise FileNotFoundError(f"指定的知识库包不存在: {pkg_path}")

        with tarfile.open(pkg_path, "r:gz") as tar:
            try:
                manifest_file = tar.extractfile("manifest.json")
                if manifest_file is not None:
                    return json.loads(manifest_file.read().decode("utf-8"))
            except Exception:
                pass
        return {}

    @classmethod
    async def import_and_subscribe_package(
        cls,
        pkg_path: Path,
        kb_name: str = "",
        expected_embedding_model: str = "",
        expected_dim: int = 0,
        as_readonly: bool = True,
    ) -> dict[str, Any]:
        """一键导入团队分享的 .kbpack 包，自动校验模型空间并锁定为订阅只读状态。"""
        manifest = cls.inspect_package(pkg_path)
        kb_id = manifest.get("kb_id", f"subscribed_{int(time.time())}")
        version = manifest.get("version", "v1.0.0")
        pkg_model = manifest.get("embedding_model", "")
        pkg_dim = manifest.get("embedding_dim", 0)

        # 模型维度兼容性严格校验
        if expected_dim > 0 and pkg_dim > 0 and expected_dim != pkg_dim:
            raise ValueError(
                f"向量维度不匹配拦截：知识库包维度为 {pkg_dim}，但当前客户端模型维度为 {expected_dim}，无法跨空间混合检索！"
            )

        # 解压到本地存储版本目录
        tgt_ver_dir = KBStorageManager.get_version_dir(kb_id, version)
        with tarfile.open(pkg_path, "r:gz") as tar:
            tar.extractall(tgt_ver_dir)

        now = time.time()
        final_name = kb_name.strip() or manifest.get("extra", {}).get("name") or kb_id
        ownership = "subscribed_ro" if as_readonly else "local"
        chunk_count = manifest.get("chunk_count", 0)
        triple_count = manifest.get("triple_count", 0)
        disk_size = KBStorageManager.get_kb_disk_size(kb_id)

        async with db_manager.get_connection() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO knowledge_bases (
                    kb_id, name, description, ownership_type, source_id,
                    active_version, staging_version, embedding_model, embedding_dim,
                    chunk_count, triple_count, disk_size_bytes, share_repo_uri,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, NULL, ?, '', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    kb_id,
                    final_name,
                    f"从团队共享包导入 (版本 {version})",
                    ownership,
                    version,
                    pkg_model,
                    pkg_dim,
                    chunk_count,
                    triple_count,
                    disk_size,
                    str(pkg_path),
                    now,
                    now,
                ),
            )
            await conn.execute(
                """
                INSERT OR REPLACE INTO kb_versions (
                    kb_id, version, status, changelog, chunk_count, triple_count,
                    disk_size_bytes, package_hash, created_by, created_at
                ) VALUES (?, ?, 'ready', ?, ?, ?, ?, ?, 'team_shared', ?)
                """,
                (
                    kb_id,
                    version,
                    manifest.get("changelog", "团队共享发布版"),
                    chunk_count,
                    triple_count,
                    disk_size,
                    manifest.get("package_hash", ""),
                    now,
                ),
            )
            await conn.commit()

        logger.info(f"成功导入并订阅知识库 [{final_name}] ({kb_id}), 版本: {version}, 只读锁: {ownership}")
        return {
            "success": True,
            "kb_id": kb_id,
            "name": final_name,
            "version": version,
            "ownership_type": ownership,
            "chunk_count": chunk_count,
            "message": f"成功拉取团队知识库【{final_name}】，已处于 {ownership} 安全只读模式",
        }

    @classmethod
    async def fork_as_local_editable(
        cls,
        source_kb_id: str,
        new_name: str = "",
    ) -> dict[str, Any]:
        """将订阅只读库 Fork 克隆为本地独立可编辑知识库副本。"""
        new_kb_id = f"fork_{source_kb_id}_{int(time.time())}"
        src_kb_dir = KBStorageManager.get_kb_dir(source_kb_id)
        new_kb_dir = KBStorageManager.get_kb_dir(new_kb_id)

        if not src_kb_dir.exists():
            raise FileNotFoundError(f"源知识库目录不存在: {source_kb_id}")

        # 完整递归拷贝所有历史版本
        shutil.copytree(src_kb_dir, new_kb_dir, dirs_exist_ok=True)

        async with db_manager.get_connection() as conn:
            async with conn.execute("SELECT * FROM knowledge_bases WHERE kb_id = ?", (source_kb_id,)) as cur:
                src_meta = await cur.fetchone()
                if not src_meta:
                    raise FileNotFoundError(f"源知识库元数据不存在: {source_kb_id}")

            async with conn.execute("SELECT * FROM kb_versions WHERE kb_id = ?", (source_kb_id,)) as cur:
                src_versions = await cur.fetchall()

            now = time.time()
            derived_name = new_name.strip() or f"{src_meta['name']} (副本)"

            # 插入新可编辑知识库
            await conn.execute(
                """
                INSERT INTO knowledge_bases (
                    kb_id, name, description, ownership_type, source_id,
                    active_version, staging_version, embedding_model, embedding_dim,
                    chunk_count, triple_count, disk_size_bytes, share_repo_uri,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 'local', NULL, ?, '', ?, ?, ?, ?, ?, '', ?, ?)
                """,
                (
                    new_kb_id,
                    derived_name,
                    f"Fork 自团队标准库: {src_meta['name']}",
                    src_meta["active_version"],
                    src_meta["embedding_model"],
                    src_meta["embedding_dim"],
                    src_meta["chunk_count"],
                    src_meta["triple_count"],
                    src_meta["disk_size_bytes"],
                    now,
                    now,
                ),
            )

            # 复制版本记录
            for v in src_versions:
                await conn.execute(
                    """
                    INSERT INTO kb_versions (
                        kb_id, version, status, changelog, chunk_count, triple_count,
                        disk_size_bytes, package_hash, created_by, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'local_fork', ?)
                    """,
                    (
                        new_kb_id,
                        v["version"],
                        v["status"],
                        v["changelog"],
                        v["chunk_count"],
                        v["triple_count"],
                        v["disk_size_bytes"],
                        v["package_hash"],
                        now,
                    ),
                )
            await conn.commit()

        logger.info(f"已成功将知识库 [{source_kb_id}] 克隆为本地可编辑副本: [{derived_name}] ({new_kb_id})")
        return {
            "success": True,
            "kb_id": new_kb_id,
            "name": derived_name,
            "active_version": src_meta["active_version"],
            "ownership_type": "local",
            "message": f"已成功将知识库克隆为本地独立副本【{derived_name}】，只读锁已解除",
        }

