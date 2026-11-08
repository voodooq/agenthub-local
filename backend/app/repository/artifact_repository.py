"""
工作区资产库 (Workspace Artifacts) 仓储层
实现代码落盘、AI报告与多媒体资产的统一注册、归档与双向会话溯源
"""
import uuid
from datetime import datetime
from pathlib import Path
from ..core.logging_config import logger
from ..repository.db import db_manager


class ArtifactRepository:
    """
    资产库数据访问对象
    """

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
        elif ext in (".md", ".txt", ".doc", ".docx", ".pdf", ".rtf", ".csv", ".tsv"):
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
        物理工作区智能反向同步：
        扫描工作区物理根目录下真实存在的代码、文档与产出物，自动补齐登记到 workspace_artifacts 表中。
        """
        from .workspace_repository import WorkspaceRepository

        ws = await WorkspaceRepository.get_by_id(workspace_id)
        if not ws or not ws.get("root_path"):
            return 0

        root = Path(ws["root_path"]).resolve()
        if not root.exists() or not root.is_dir():
            return 0

        # 查询当前已登记的资产列表
        existing_paths: set[str] = set()
        async with db_manager.get_connection() as conn:
            async with conn.execute(
                "SELECT file_path FROM workspace_artifacts WHERE workspace_id = ?;",
                (workspace_id,),
            ) as cursor:
                rows = await cursor.fetchall()
                for r in rows:
                    existing_paths.add(str(Path(r["file_path"]).resolve()))

        # 系统级忽略目录与文件
        IGNORE_DIRS = {
            ".git", ".svn", ".hg", "node_modules", ".venv", "venv", "__pycache__",
            ".idea", ".vscode", "dist", "build", ".next", ".agenthub"
        }
        IGNORE_FILES = {".gitignore", ".DS_Store", "package-lock.json", "pnpm-lock.yaml"}

        new_records = []
        now = datetime.now().isoformat()

        try:
            for item in root.rglob("*"):
                if not item.is_file():
                    continue

                # 排除内部系统目录
                rel = item.relative_to(root)
                if any(p in IGNORE_DIRS for p in rel.parts[:-1]):
                    continue
                if item.name in IGNORE_FILES:
                    continue

                abs_str = str(item.resolve())
                if abs_str not in existing_paths:
                    artifact_id = f"art_{uuid.uuid4().hex[:12]}"
                    try:
                        file_size = item.stat().st_size
                    except Exception:
                        file_size = 0
                    file_type = ArtifactRepository._detect_file_type(item)
                    summary = f"工作区物理文件: {rel}"
                    new_records.append((
                        artifact_id,
                        workspace_id,
                        None,
                        abs_str,
                        item.name,
                        file_type,
                        file_size,
                        "workspace_sync",
                        summary,
                        now,
                    ))
                    existing_paths.add(abs_str)
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

        return len(new_records)

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

