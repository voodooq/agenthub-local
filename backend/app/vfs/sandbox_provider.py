"""
沙盒空间（本地硬盘工作区目录）VFS 提供商适配器。
支持将用户在 AgentHub 中创建的任意「沙盒与项目」（及其绑定的本地磁盘目录）直接作为知识库数据源挂载，
纳入统一的增量切片、向量化训练、文件增删改水位线追踪与定时自动同步体系。
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .base import CloudFileMeta, CloudVFSProvider

SUPPORTED_DOC_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".doc",
    ".txt",
    ".md",
    ".markdown",
    ".csv",
    ".xlsx",
    ".xls",
    ".json",
    ".html",
    ".htm",
}

IGNORED_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".agenthub",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".next",
    "dist",
    "out",
    "build",
    ".pytest_cache",
}


class SandboxVFSProvider(CloudVFSProvider):
    """沙盒工作区本地硬盘目录 VFS 适配器。"""

    def __init__(
        self,
        mount_id: str,
        mount_name: str,
        workspace_id: str = "",
        root_path: str = "",
        sub_path: str = "",
        authority_tier: int = 1,
    ) -> None:
        super().__init__(mount_id, mount_name, authority_tier=authority_tier)
        self.workspace_id = (workspace_id or "").strip()
        self.raw_root_path = (root_path or "").strip()
        self.sub_path = (sub_path or "").strip().strip("/\\")

    async def _resolve_target_dir(self) -> Path | None:
        """解析沙盒绑定的本地物理目录路径。"""
        base_str = self.raw_root_path
        if not base_str and self.workspace_id:
            try:
                from ..repository.workspace_repository import WorkspaceRepository

                ws = await WorkspaceRepository.get_by_id(self.workspace_id)
                if ws and ws.get("root_path"):
                    base_str = str(ws["root_path"]).strip()
            except Exception:
                pass
        if not base_str:
            return None
        base_dir = Path(base_str).expanduser().resolve()
        if self.sub_path:
            target_dir = (base_dir / self.sub_path).resolve()
            # 安全边界：子目录不得越出沙盒根目录
            if not str(target_dir).lower().startswith(str(base_dir).lower()):
                return None
            return target_dir
        return base_dir

    def _iter_valid_files(self, target_dir: Path, recursive: bool = True) -> list[Path]:
        if not target_dir.exists() or not target_dir.is_dir():
            return []
        results: list[Path] = []
        iterator = target_dir.rglob("*") if recursive else target_dir.glob("*")
        for p in iterator:
            try:
                if not p.is_file():
                    continue
                rel_parts = p.relative_to(target_dir).parts
                if any(part in IGNORED_DIR_NAMES or part.startswith(".") for part in rel_parts[:-1]):
                    continue
                if p.name.startswith(".") or p.name.startswith("~$"):
                    continue
                if p.suffix.lower() not in SUPPORTED_DOC_EXTENSIONS:
                    continue
                results.append(p)
            except Exception:
                continue
        results.sort(key=lambda x: x.as_posix().lower())
        return results

    @staticmethod
    def _is_internal_virtual_dir(target_dir: Path) -> bool:
        """判断目标路径是否属于系统内部虚拟工作区目录（data/workspaces/...）。"""
        norm = target_dir.as_posix().lower().rstrip("/")
        return "/data/workspaces/" in f"{norm}/" or norm.endswith("/data/workspaces")

    async def _validate_physical_workspace_only(self) -> str | None:
        """校验所选沙盒或路径是否为本地挂载的物理文件夹，拒绝系统内部虚拟沙盒。"""
        if self.workspace_id and not self.raw_root_path:
            try:
                from ..repository.workspace_repository import WorkspaceRepository

                ws = await WorkspaceRepository.get_by_id(self.workspace_id)
                if not ws:
                    return "所选工作区不存在，请重新选择本地挂载文件夹"
                if ws.get("type") != "physical":
                    return "知识库仅支持挂载「本地物理文件夹」，不支持系统内部虚拟沙盒"
            except Exception:
                pass
        return None

    async def test_connection(self) -> tuple[bool, str]:
        """校验本地挂载文件夹是否可读，并统计可训练文档数。"""
        ws_err = await self._validate_physical_workspace_only()
        if ws_err:
            return False, ws_err
        target_dir = await self._resolve_target_dir()
        if not target_dir:
            return False, "请选择已挂载的本地文件夹，或通过「浏览文件夹」选择本地目录路径"
        if self._is_internal_virtual_dir(target_dir):
            return False, "不支持挂载系统内部虚拟沙盒目录（data/workspaces），请选择真实的本地文档文件夹"
        if not target_dir.exists():
            return False, f"绑定的本地路径不存在: {target_dir}"
        if not target_dir.is_dir():
            return False, f"目标路径不是有效文件夹: {target_dir}"
        docs = self._iter_valid_files(target_dir, recursive=True)
        return True, f"本地文件夹连接成功！在 {target_dir} 下共发现 {len(docs)} 个可训练文档。"

    async def list_files(self, recursive: bool = True) -> list[CloudFileMeta]:
        """扫描沙盒空间下的所有支持切片的文档。"""
        target_dir = await self._resolve_target_dir()
        if not target_dir or not target_dir.exists() or not target_dir.is_dir():
            return []

        ws_tag = self.workspace_id or self.mount_id
        files: list[CloudFileMeta] = []
        for p in self._iter_valid_files(target_dir, recursive=recursive):
            try:
                st = p.stat()
                rel_posix = p.relative_to(target_dir).as_posix()
                remote_uri = f"sandbox://{ws_tag}/{rel_posix}"
                etag = hashlib.md5(f"{rel_posix}:{st.st_size}:{int(st.st_mtime)}".encode("utf-8")).hexdigest()
                files.append(
                    CloudFileMeta(
                        remote_uri=remote_uri,
                        title=p.name,
                        size=int(st.st_size),
                        etag=etag,
                        mtime=float(st.st_mtime),
                        mime_type=p.suffix.lstrip(".").lower(),
                        is_dir=False,
                        authority_tier=self.authority_tier,
                    )
                )
            except Exception:
                continue
        return files

    async def download_stream(self, remote_uri: str) -> bytes:
        """读取沙盒空间内指定文档的字节流（带目录防穿越校验）。"""
        target_dir = await self._resolve_target_dir()
        if not target_dir or not target_dir.exists():
            raise FileNotFoundError("沙盒本地目录不存在或不可达")

        prefix = f"sandbox://{self.workspace_id or self.mount_id}/"
        if remote_uri.startswith(prefix):
            rel_path = remote_uri[len(prefix) :]
        elif "://" in remote_uri:
            rel_path = remote_uri.split("://", 1)[1].split("/", 1)[-1]
        else:
            rel_path = remote_uri

        file_path = (target_dir / rel_path).resolve()
        if not str(file_path).lower().startswith(str(target_dir.resolve()).lower()):
            raise PermissionError(f"禁止越界访问沙盒目录外的文件: {remote_uri}")
        if not file_path.exists() or not file_path.is_file():
            raise FileNotFoundError(f"沙盒文档不存在: {file_path}")
        return file_path.read_bytes()

