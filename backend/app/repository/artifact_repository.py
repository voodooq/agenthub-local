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
        登记一件资产到工作区资产库
        """
        artifact_id = f"art_{uuid.uuid4().hex[:12]}"
        now = datetime.now().isoformat()

        async with db_manager.get_connection() as conn:
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
                    file_path,
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
            "file_path": file_path,
            "file_name": file_name,
            "file_type": file_type,
            "file_size": file_size,
            "source": source,
            "summary": summary,
            "created_at": now,
        }

    @staticmethod
    async def list_artifacts(
        workspace_id: str,
        file_type: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """
        查询指定工作区下的资产列表
        """
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

