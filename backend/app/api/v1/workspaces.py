"""
工作区 (Workspace) API 路由
支持用户自定义工作区、本地物理目录挂载、Windows 原生目录选择器与代码一键落盘
"""
import asyncio
import os
import subprocess
from pathlib import Path
from typing import Optional
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, status, Query
from ...repository.workspace_repository import WorkspaceRepository
from ...schema.workspace_schema import (
    WorkspaceCreate,
    WorkspaceUpdate,
    WorkspaceResponse,
    WorkspaceSaveFileRequest,
)
from ...core.logging_config import logger
from ...core.runtime_paths import get_silent_subprocess_kwargs
from ...tools.git_checkpoint import restore_shadow_snapshot

router = APIRouter(prefix="/workspaces", tags=["Workspaces"])


def _show_native_windows_folder_dialog(dialog_title: str = "请选择要挂载到 AgentHub 的本地物理工作区文件夹") -> str:
    """
    通过轻量子进程唤起 Windows 原生 FolderBrowserDialog 对话框
    特点：完全原生、无需繁重第三方 GUI 依赖、不阻塞主事件循环
    """
    ps_cmd = f"""
    Add-Type -AssemblyName System.Windows.Forms
    $fbd = New-Object System.Windows.Forms.FolderBrowserDialog
    $fbd.Description = '{dialog_title}'
    $fbd.ShowNewFolderButton = $true
    $result = $fbd.ShowDialog()
    if ($result -eq [System.Windows.Forms.DialogResult]::OK) {{
        Write-Output $fbd.SelectedPath
    }}
    """
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            timeout=120,
            **get_silent_subprocess_kwargs(allow_gui=True),
        )
        return proc.stdout.strip()
    except Exception as e:
        logger.warning(f"唤起 Windows 原生文件夹选择对话框异常: {e}")
        return ""


@router.get("", response_model=list[WorkspaceResponse])
async def list_workspaces():
    """获取所有工作区列表（支持用户自定义工作区与默认沙盒）"""
    return await WorkspaceRepository.get_all()


@router.post("/select-folder")
async def select_folder_native():
    """
    唤起 Windows 原生文件夹选择窗口，获取本地物理目录路径
    """
    loop = asyncio.get_running_loop()
    selected_path = await loop.run_in_executor(None, _show_native_windows_folder_dialog)
    return {
        "selected_path": selected_path,
        "canceled": not bool(selected_path),
    }


@router.post("", response_model=WorkspaceResponse, status_code=status.HTTP_201_CREATED)
async def create_workspace(req: WorkspaceCreate):
    """
    创建用户自定义工作区
    - 支持虚拟沙盒托管工作区 (type='virtual')
    - 支持本地已有物理工程目录挂载 (type='physical')
    """
    try:
        created = await WorkspaceRepository.create(req.model_dump())
        return created
    except (ValueError, FileNotFoundError, NotADirectoryError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"创建工作区失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"创建工作区失败: {str(e)}")


@router.get("/{ws_id}")
async def get_workspace_detail(ws_id: str):
    """获取单个工作区详情（包含 WORKSPACE.md 契约内容）"""
    ws = await WorkspaceRepository.get_by_id(ws_id)
    if not ws:
        raise HTTPException(status_code=404, detail="工作区不存在")
    return ws


@router.put("/{ws_id}", response_model=WorkspaceResponse)
async def update_workspace(ws_id: str, req: WorkspaceUpdate):
    """更新工作区配置"""
    updated = await WorkspaceRepository.update(ws_id, req.model_dump(exclude_unset=True))
    if not updated:
        raise HTTPException(status_code=404, detail="工作区不存在")
    return updated


@router.delete("/{ws_id}")
async def delete_workspace(ws_id: str):
    """
    安全解除工作区挂载
    红线说明：绝不删除磁盘上的用户物理工程文件，仅解除在 AgentHub 中的挂载记录
    """
    try:
        success = await WorkspaceRepository.delete(ws_id)
        if not success:
            raise HTTPException(status_code=404, detail="工作区不存在")
        return {"success": True, "message": "已安全解除工作区挂载，本地物理文件不受影响"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{ws_id}/files")
async def save_file_to_workspace(ws_id: str, req: WorkspaceSaveFileRequest):
    """在工作区内落盘代码或文件（带防路径越界安全拦截）"""
    try:
        res = await WorkspaceRepository.save_file_to_workspace(
            ws_id=ws_id,
            rel_path=req.rel_path,
            content=req.content,
            overwrite=req.overwrite,
        )
        return res
    except (ValueError, FileExistsError, PermissionError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"工作区落盘文件失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{ws_id}/reindex")
async def reindex_workspace_fts(ws_id: str):
    """触发工作区本地文件的增量 FTS5 全文索引构建"""
    ws = await WorkspaceRepository.get_by_id(ws_id)
    if not ws:
        raise HTTPException(status_code=404, detail="工作区不存在")

    from ...memory.workspace_fts import WorkspaceFTSEngine
    try:
        engine = WorkspaceFTSEngine(ws["root_path"])
        stats = engine.reindex()
        # 更新数据库中的 fts_last_indexed_at 时间戳
        from datetime import datetime
        await WorkspaceRepository.update(ws_id, {"fts_last_indexed_at": datetime.now().isoformat()})
        return {"success": True, "stats": stats}
    except Exception as e:
        logger.error(f"构建工作区 FTS5 索引失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{ws_id}/search")
async def search_workspace_fts(ws_id: str, q: str = ""):
    """在当前工作区内全文检索代码与文档"""
    ws = await WorkspaceRepository.get_by_id(ws_id)
    if not ws:
        raise HTTPException(status_code=404, detail="工作区不存在")

    from ...memory.workspace_fts import WorkspaceFTSEngine
    try:
        engine = WorkspaceFTSEngine(ws["root_path"])
        hits = engine.search(q, limit=15)
        return {"query": q, "hits": hits}
    except Exception as e:
        logger.error(f"检索工作区 FTS5 失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{ws_id}/open")
async def open_workspace_in_explorer(ws_id: str, subpath: str | None = Query(None)):
    """在系统文件资源管理器中打开当前工作区物理根目录或指定子目录"""
    ws = await WorkspaceRepository.get_by_id(ws_id)
    if not ws or not ws.get("root_path"):
        raise HTTPException(status_code=404, detail="未找到工作区物理路径")
    target = Path(ws["root_path"]).resolve()
    if subpath:
        clean_sub = subpath.strip().replace("/", "\\").lstrip("\\")
        target = (target / clean_sub).resolve()
    target.mkdir(parents=True, exist_ok=True)
    try:
        if os.name == "nt":
            subprocess.Popen(["explorer", str(target)])
        return {"success": True, "path": str(target)}
    except Exception as e:
        logger.error(f"打开工作区文件夹失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class RollbackRequest(BaseModel):
    snapshot_tree_hash: str
    file_path: Optional[str] = None


@router.post("/{workspace_id}/rollback")
async def rollback_workspace_snapshot(workspace_id: str, req: RollbackRequest):
    """一键将工作区单文件或整体回滚到指定 Git Shadow 快照"""
    ws = await WorkspaceRepository.get_workspace_by_id(workspace_id)
    if not ws or not ws.get("root_path"):
        raise HTTPException(status_code=404, detail="未找到工作区物理路径")

    root_path = Path(ws["root_path"]).resolve()
    success = restore_shadow_snapshot(
        root_path,
        tree_hash=req.snapshot_tree_hash,
        target_file=req.file_path,
    )
    if not success:
        raise HTTPException(
            status_code=400,
            detail=f"撤销修改失败：无法将文件恢复到快照 {req.snapshot_tree_hash[:8]}",
        )

    logger.info(f"工作区 [{workspace_id}] 文件已无损回滚: {req.file_path or '全部文件'}")
    return {
        "success": True,
        "message": f"已成功将 {req.file_path or '工作区'} 恢复至快照状态",
        "snapshot_tree_hash": req.snapshot_tree_hash,
        "file_path": req.file_path,
    }



