"""
工作区数据访问仓储层
职责：支持通用自定义工作区 CRUD、本地已有物理目录挂载校验、安全路径解析与落盘防护
"""
import uuid
from pathlib import Path
from typing import Any
from ..core.config import settings
from ..core.logging_config import logger
from .db import db_manager


class WorkspaceRepository:
    """工作区持久化操作仓储"""

    @staticmethod
    def _normalize_path(p: str) -> str:
        """规范化本地路径，消除多余斜杠并解析绝对路径"""
        return str(Path(p).resolve())

    @staticmethod
    async def get_all() -> list[dict[str, Any]]:
        """获取所有工作区列表，附带会话总数与路径可访问性"""
        query = """
        SELECT 
            w.id,
            w.name,
            w.type,
            w.root_path,
            w.context_prompt,
            w.model_preference,
            w.security_level,
            w.fts_last_indexed_at,
            w.created_at,
            COUNT(c.id) AS conversation_count
        FROM workspaces w
        LEFT JOIN conversations c ON c.workspace_id = w.id
        GROUP BY w.id
        ORDER BY CASE WHEN w.id = 'default' THEN 0 ELSE 1 END, w.created_at DESC;
        """
        async with db_manager.get_connection() as conn:
            cursor = await conn.execute(query)
            rows = await cursor.fetchall()
            results = []
            for row in rows:
                item = dict(row)
                p = Path(item["root_path"])
                item["is_path_accessible"] = p.exists() and p.is_dir()
                results.append(item)
            return results

    @staticmethod
    async def get_by_id(ws_id: str) -> dict[str, Any] | None:
        """根据 ID 获取工作区详情"""
        query = """
        SELECT 
            w.id,
            w.name,
            w.type,
            w.root_path,
            w.context_prompt,
            w.model_preference,
            w.security_level,
            w.fts_last_indexed_at,
            w.created_at,
            COUNT(c.id) AS conversation_count
        FROM workspaces w
        LEFT JOIN conversations c ON c.workspace_id = w.id
        WHERE w.id = ?
        GROUP BY w.id;
        """
        async with db_manager.get_connection() as conn:
            cursor = await conn.execute(query, (ws_id,))
            row = await cursor.fetchone()
            if not row:
                return None
            item = dict(row)
            p = Path(item["root_path"])
            item["is_path_accessible"] = p.exists() and p.is_dir()

            # 探测该工作区根目录下是否存在 WORKSPACE.md 契约文件
            contract_file = p / "WORKSPACE.md"
            if contract_file.exists() and contract_file.is_file():
                try:
                    item["workspace_contract_content"] = contract_file.read_text(encoding="utf-8")
                except Exception as e:
                    logger.warning(f"读取 WORKSPACE.md 失败: {e}")
                    item["workspace_contract_content"] = None
            else:
                item["workspace_contract_content"] = None

            return item

    @staticmethod
    async def create(data: dict[str, Any]) -> dict[str, Any]:
        """创建或挂载工作区"""
        ws_id = data.get("id") or str(uuid.uuid4())
        name = data["name"].strip()
        ws_type = data.get("type", "virtual")
        root_path = data.get("root_path")

        if ws_type == "physical":
            if not root_path:
                raise ValueError("挂载本地物理工作区时必须提供有效的绝对路径")
            norm_path = WorkspaceRepository._normalize_path(root_path)
            target_p = Path(norm_path)
            if not target_p.exists():
                raise FileNotFoundError(f"指定的本地物理目录不存在: {norm_path}")
            if not target_p.is_dir():
                raise NotADirectoryError(f"指定的路径不是一个有效的文件夹: {norm_path}")
        else:
            # 虚拟托管沙盒工作区
            if root_path:
                norm_path = WorkspaceRepository._normalize_path(root_path)
            else:
                norm_path = WorkspaceRepository._normalize_path(
                    str(Path(settings.db_path).parent / "workspaces" / ws_id)
                )
            target_p = Path(norm_path)
            target_p.mkdir(parents=True, exist_ok=True)

        # 确保自动创建 .agenthub 隐藏目录以备本地缓存、快照与私有技能使用
        try:
            agenthub_dir = target_p / ".agenthub"
            agenthub_dir.mkdir(parents=True, exist_ok=True)
            # 自动写入 .gitignore 避免将 agenthub 临时索引误提交
            gitignore_file = agenthub_dir / ".gitignore"
            if not gitignore_file.exists():
                gitignore_file.write_text("index.db\nsnapshots/\nlogs/\n", encoding="utf-8")
        except Exception as e:
            logger.debug(f"初始化 .agenthub 目录提示: {e}")

        context_prompt = data.get("context_prompt")
        model_preference = data.get("model_preference")
        security_level = data.get("security_level", "standard")

        insert_sql = """
        INSERT INTO workspaces (id, name, type, root_path, context_prompt, model_preference, security_level)
        VALUES (?, ?, ?, ?, ?, ?, ?);
        """
        async with db_manager.get_connection() as conn:
            await conn.execute(
                insert_sql,
                (ws_id, name, ws_type, norm_path, context_prompt, model_preference, security_level),
            )
            await conn.commit()

        created = await WorkspaceRepository.get_by_id(ws_id)
        return created or {}

    @staticmethod
    async def update(ws_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        """更新工作区信息"""
        fields = []
        params = []
        for key in ("name", "context_prompt", "model_preference", "security_level"):
            if key in data and data[key] is not None:
                fields.append(f"{key} = ?")
                params.append(data[key])

        if not fields:
            return await WorkspaceRepository.get_by_id(ws_id)

        params.append(ws_id)
        update_sql = f"UPDATE workspaces SET {', '.join(fields)} WHERE id = ?;"

        async with db_manager.get_connection() as conn:
            await conn.execute(update_sql, tuple(params))
            await conn.commit()

        return await WorkspaceRepository.get_by_id(ws_id)

    @staticmethod
    async def delete(ws_id: str) -> bool:
        """
        安全解除工作区挂载
        安全红线：绝不物理删除用户磁盘上的源码或文件，仅从元数据表中移除记录，将关联会话归入 default 工作区
        """
        if ws_id == "default":
            raise ValueError("系统默认工作区不允许删除或卸载")

        async with db_manager.get_connection() as conn:
            # 将该工作区下的所有会话平移迁移至 default
            await conn.execute("UPDATE conversations SET workspace_id = 'default' WHERE workspace_id = ?;", (ws_id,))
            cursor = await conn.execute("DELETE FROM workspaces WHERE id = ?;", (ws_id,))
            await conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    async def save_file_to_workspace(ws_id: str, rel_path: str, content: str, overwrite: bool = True) -> dict[str, Any]:
        """
        在当前工作区物理目录下安全落盘文件
        具备严格防越界校验 (Path Traversal Protection)
        """
        ws = await WorkspaceRepository.get_by_id(ws_id)
        if not ws:
            raise ValueError(f"工作区不存在: {ws_id}")

        root = Path(ws["root_path"]).resolve()
        if not root.exists():
            root.mkdir(parents=True, exist_ok=True)

        target_file = (root / rel_path).resolve()

        # 防越界安全红线校验：目标文件必须位于工作区物理根目录下
        try:
            target_file.relative_to(root)
        except ValueError:
            raise PermissionError(f"安全拦截：禁止向工作区外部路径越界写入文件: {rel_path}")

        if target_file.exists() and not overwrite:
            raise FileExistsError(f"文件已存在且未设置覆盖选项: {rel_path}")

        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text(content, encoding="utf-8")
        size_bytes = len(content.encode("utf-8"))

        # 自动向工作区资产库登记
        try:
            from .artifact_repository import ArtifactRepository
            ext = target_file.suffix.lower()
            file_type = "code" if ext in (".py", ".ts", ".tsx", ".js", ".html", ".css", ".sql", ".sh") else "document"
            await ArtifactRepository.register_artifact(
                workspace_id=ws_id,
                file_path=str(target_file),
                file_name=target_file.name,
                file_type=file_type,
                file_size=size_bytes,
                source="user_saved",
            )
        except Exception as e:
            logger.warning(f"自动登记资产库异常 (不影响落盘): {e}")

        return {
            "success": True,
            "workspace_id": ws_id,
            "rel_path": str(target_file.relative_to(root)),
            "abs_path": str(target_file),
            "size_bytes": size_bytes,
        }

