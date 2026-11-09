"""
工作区资产库 (Workspace Artifacts) 仓储层
实现代码落盘、AI报告与多媒体资产的统一注册、归档与双向会话溯源
"""
import os
import uuid
from datetime import datetime
from pathlib import Path
from ..core.logging_config import logger
from ..repository.db import db_manager


class ArtifactRepository:
    """
    资产库数据访问对象
    """

    IGNORE_DIRS = {
        ".git", ".svn", ".hg", "node_modules", ".venv", "venv", "__pycache__",
        ".idea", ".vscode", "dist", "build", ".next", ".agenthub",
        ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".nox", ".turbo", "coverage",
    }
    IGNORE_FILES = {".gitignore", ".DS_Store", "package-lock.json", "pnpm-lock.yaml", "Thumbs.db"}
    IGNORE_SUFFIXES = {".pyc", ".pyo"}

    @staticmethod
    def _is_ignored_rel_path(rel: Path) -> bool:
        """判断相对工作区根目录的路径是否应被自动扫描忽略（保留 .agenthub/attachments 上传区）"""
        parts = rel.parts
        if len(parts) >= 2 and parts[0].lower() == ".agenthub" and parts[1].lower() == "attachments":
            return rel.name in ArtifactRepository.IGNORE_FILES or rel.suffix.lower() in ArtifactRepository.IGNORE_SUFFIXES
        if any(p in ArtifactRepository.IGNORE_DIRS for p in parts[:-1]):
            return True
        if rel.name in ArtifactRepository.IGNORE_FILES or rel.suffix.lower() in ArtifactRepository.IGNORE_SUFFIXES:
            return True
        return False

    @staticmethod
    def _detect_file_type(file_path: Path) -> str:
        """根据文件扩展名智能推断资产类型"""
        ext = file_path.suffix.lower()
        if ext in (
            ".py", ".ts", ".tsx", ".js", ".jsx", ".html", ".htm", ".css", ".scss",
            ".sql", ".sh", ".bash", ".ps1", ".bat", ".cmd", ".json", ".yaml", ".yml",
            ".toml", ".ini", ".xml", ".c", ".cpp", ".h", ".rs", ".go", ".java"
        ):
            return "code"
        elif ext in (
            ".md", ".txt", ".doc", ".docx", ".pdf", ".rtf", ".csv", ".tsv",
            ".xlsx", ".xlsm", ".xls", ".pptx",
        ):
            return "document"
        elif ext in (
            ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp", ".ico",
            ".mp3", ".wav", ".mp4", ".mov", ".webm"
        ):
            return "media"
        return "document"

    @staticmethod
    async def register_artifact(
        workspace_id: str,
        file_path: str,
        file_name: str,
        file_type: str,
        file_size: int = 0,
        conversation_id: str | None = None,
        source: str = "ai_generated",
        summary: str | None = None,
    ) -> dict:
        """
        登记一件资产到工作区资产库（支持同名路径 UPSERT 更新）
        """
        now = datetime.now().isoformat()
        norm_path = str(Path(file_path).resolve())

        async with db_manager.get_connection() as conn:
            # 查重判断：若该路径已存在则更新，杜绝重复创建卡片
            async with conn.execute(
                "SELECT id FROM workspace_artifacts WHERE workspace_id = ? AND file_path = ?;",
                (workspace_id, norm_path),
            ) as cursor:
                existing = await cursor.fetchone()
                if existing:
                    art_id = existing["id"]
                    await conn.execute(
                        """
                        UPDATE workspace_artifacts
                        SET file_size = ?, file_type = ?, summary = ?, created_at = ?
                        WHERE id = ?;
                        """,
                        (file_size, file_type, summary or f"已更新: {file_name}", now, art_id),
                    )
                    await conn.commit()
                    return {
                        "id": art_id,
                        "workspace_id": workspace_id,
                        "conversation_id": conversation_id,
                        "file_path": norm_path,
                        "file_name": file_name,
                        "file_type": file_type,
                        "file_size": file_size,
                        "source": source,
                        "summary": summary,
                        "created_at": now,
                    }

            artifact_id = f"art_{uuid.uuid4().hex[:12]}"
            await conn.execute(
                """
                INSERT INTO workspace_artifacts (
                    id, workspace_id, conversation_id, file_path, file_name,
                    file_type, file_size, source, summary, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    artifact_id,
                    workspace_id,
                    conversation_id,
                    norm_path,
                    file_name,
                    file_type,
                    file_size,
                    source,
                    summary,
                    now,
                ),
            )
            await conn.commit()

        logger.info(f"成功登记资产: {file_name} ({artifact_id}) 至工作区 {workspace_id}")
        return {
            "id": artifact_id,
            "workspace_id": workspace_id,
            "conversation_id": conversation_id,
            "file_path": norm_path,
            "file_name": file_name,
            "file_type": file_type,
            "file_size": file_size,
            "source": source,
            "summary": summary,
            "created_at": now,
        }

    @staticmethod
    async def sync_physical_workspace_artifacts(workspace_id: str) -> int:
        """
        物理工作区双向精确同步：
        1. 失效清理 (Prune)：清理已在物理磁盘被删除的孤儿记录，以及历史误扫入的忽略缓存目录（如 .pytest_cache）记录；
        2. 增量补齐 (Discover)：扫描工作区物理根目录下真实存在的文件，自动补齐登记到 workspace_artifacts 表中。
        """
        from .workspace_repository import WorkspaceRepository

        ws = await WorkspaceRepository.get_by_id(workspace_id)
        if not ws or not ws.get("root_path"):
            return 0

        root = Path(ws["root_path"]).resolve()
        if not root.exists() or not root.is_dir():
            return 0

        # 查询当前已登记的资产列表，并比对磁盘真实存在性与忽略规则
        existing_paths: set[str] = set()
        stale_ids: list[str] = []
        async with db_manager.get_connection() as conn:
            async with conn.execute(
                "SELECT id, file_path, source FROM workspace_artifacts WHERE workspace_id = ?;",
                (workspace_id,),
            ) as cursor:
                rows = await cursor.fetchall()
                for r in rows:
                    raw_fp = str(r["file_path"] or "").strip()
                    if not raw_fp:
                        stale_ids.append(r["id"])
                        continue
                    fp_obj = Path(raw_fp)
                    try:
                        exists_on_disk = fp_obj.exists() and fp_obj.is_file()
                    except OSError:
                        exists_on_disk = False

                    if not exists_on_disk:
                        stale_ids.append(r["id"])
                        continue

                    resolved_fp = fp_obj.resolve()
                    # 若属于系统扫描入库 (workspace_sync) 且位于新版忽略目录/文件内，顺便清掉历史缓存脏记录
                    if r["source"] == "workspace_sync":
                        try:
                            rel_existing = resolved_fp.relative_to(root)
                            if ArtifactRepository._is_ignored_rel_path(rel_existing):
                                stale_ids.append(r["id"])
                                continue
                        except ValueError:
                            pass

                    existing_paths.add(os.path.normcase(str(resolved_fp)))

        if stale_ids:
            async with db_manager.get_connection() as conn:
                for start in range(0, len(stale_ids), 500):
                    chunk = stale_ids[start : start + 500]
                    placeholders = ",".join("?" for _ in chunk)
                    await conn.execute(
                        f"DELETE FROM workspace_artifacts WHERE id IN ({placeholders});",
                        tuple(chunk),
                    )
                await conn.commit()
            logger.info(f"工作区 [{workspace_id}] 双向同步已清理 {len(stale_ids)} 条失效/缓存资产记录")

        new_records = []
        now = datetime.now().isoformat()

        try:
            for item in root.rglob("*"):
                if not item.is_file():
                    continue

                rel = item.relative_to(root)
                if ArtifactRepository._is_ignored_rel_path(rel):
                    continue

                resolved_item = item.resolve()
                norm_key = os.path.normcase(str(resolved_item))
                if norm_key not in existing_paths:
                    artifact_id = f"art_{uuid.uuid4().hex[:12]}"
                    try:
                        file_size = item.stat().st_size
                    except OSError:
                        file_size = 0
                    file_type = ArtifactRepository._detect_file_type(item)
                    summary = f"工作区物理文件: {rel}"
                    new_records.append((
                        artifact_id,
                        workspace_id,
                        None,
                        str(resolved_item),
                        item.name,
                        file_type,
                        file_size,
                        "workspace_sync",
                        summary,
                        now,
                    ))
                    existing_paths.add(norm_key)
        except Exception as e:
            logger.warning(f"扫描工作区物理目录异常: {e}")

        if new_records:
            async with db_manager.get_connection() as conn:
                await conn.executemany(
                    """
                    INSERT INTO workspace_artifacts (
                        id, workspace_id, conversation_id, file_path, file_name,
                        file_type, file_size, source, summary, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    new_records,
                )
                await conn.commit()
            logger.info(f"工作区 [{workspace_id}] 物理同步完成，新增登记 {len(new_records)} 件资产")

        return len(new_records) + len(stale_ids)

    @staticmethod
    async def list_artifacts(
        workspace_id: str,
        file_type: str | None = None,
        limit: int = 100,
        auto_sync: bool = True,
    ) -> list[dict]:
        """
        查询指定工作区下的资产列表（默认自动前置物理增量扫描同步）
        """
        if auto_sync:
            try:
                await ArtifactRepository.sync_physical_workspace_artifacts(workspace_id)
            except Exception as e:
                logger.warning(f"列举资产前自动物理同步异常: {e}")

        query = "SELECT * FROM workspace_artifacts WHERE workspace_id = ?"
        params: list = [workspace_id]

        if file_type:
            query += " AND file_type = ?"
            params.append(file_type)

        query += " ORDER BY created_at DESC LIMIT ?;"
        params.append(limit)

        async with db_manager.get_connection() as conn:
            async with conn.execute(query, tuple(params)) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

