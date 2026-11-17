"""
后台长驻托管进程 RESTful API 路由 (Processes Router)
为前端 RightActionDrawer 监控抽屉、LoopHUD 及 Agent 交互提供：
1. GET  /api/v1/processes                  - 查询工作区活跃托管进程列表
2. GET  /api/v1/processes/{proc_id}/logs   - 获取指定进程最新日志切片（支持错误过滤）
3. POST /api/v1/processes/{proc_id}/stop   - 手动强行终止指定托管进程树
"""
from typing import Any
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ...tools.background_process import (
    BackgroundProcessManager,
    list_background_processes,
    read_process_logs,
    stop_background_process,
)

router = APIRouter(prefix="/processes", tags=["Background Processes"])


class StopProcessResponse(BaseModel):
    success: bool
    proc_id: str
    status: str
    message: str


@router.get("", summary="获取当前工作区后台托管进程列表")
async def get_processes(
    workspace_id: str | None = Query(default=None, description="工作区 ID，不传则返回全部"),
) -> dict[str, Any]:
    """返回当前工作区下所有由 AgentHub 托管的后台进程状态与嗅探端口"""
    return list_background_processes(workspace_id=workspace_id)


@router.get("/{proc_id}/logs", summary="获取指定进程日志切片")
async def get_process_logs(
    proc_id: str,
    tail_lines: int = Query(default=80, ge=1, le=500, description="获取末尾日志行数"),
    only_errors: bool = Query(default=False, description="是否仅返回错误堆栈"),
) -> dict[str, Any]:
    """读取后台进程的固定容量环形日志"""
    res = read_process_logs(proc_id=proc_id, tail_lines=tail_lines, only_errors=only_errors)
    if not res.get("success"):
        raise HTTPException(status_code=404, detail=res.get("error", "未找到指定进程"))
    return res


@router.post("/{proc_id}/stop", response_model=StopProcessResponse, summary="强杀指定后台进程树")
async def kill_process(proc_id: str) -> StopProcessResponse:
    """递归强杀指定后台进程及其所有衍生子进程树（包括 Node / Python 等）"""
    res = stop_background_process(proc_id=proc_id)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "终止进程失败"))
    return StopProcessResponse(
        success=True,
        proc_id=proc_id,
        status=res.get("status", "killed"),
        message=res.get("message", "进程已成功终止"),
    )
