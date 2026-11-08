"""
已安排计划任务 (Scheduled Tasks) 路由
"""
import uuid
from datetime import datetime
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from ...core.logging_config import logger
from ...repository.db import db_manager
from ...scheduler.task_scheduler import TaskScheduler

router = APIRouter(prefix="/scheduled", tags=["Scheduled Tasks"])


class ScheduledTaskCreateRequest(BaseModel):
    workspace_id: str = Field(..., description="归属工作区 ID")
    name: str = Field(..., description="计划任务名称")
    cron_expr: str = Field(..., description="Cron 表达式或周期标识 (如 daily, hourly, 0 9 * * *)")
    prompt_template: str = Field(..., description="自动化执行提示词模板")


class ScheduledTaskUpdateRequest(BaseModel):
    status: str | None = Field(None, description="active 或 paused")
    name: str | None = None
    cron_expr: str | None = None
    prompt_template: str | None = None


@router.get("")
async def list_scheduled_tasks(workspace_id: str = Query(..., description="工作区 ID")):
    """
    获取指定工作区下的所有计划任务
    """
    async with db_manager.get_connection() as conn:
        async with conn.execute(
            "SELECT * FROM scheduled_tasks WHERE workspace_id = ? ORDER BY created_at DESC;",
            (workspace_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            return {"tasks": [dict(r) for r in rows], "total": len(rows)}


@router.post("")
async def create_scheduled_task(req: ScheduledTaskCreateRequest):
    """
    新建计划任务
    """
    task_id = f"sched_{uuid.uuid4().hex[:10]}"
    now_str = datetime.now().isoformat()

    async with db_manager.get_connection() as conn:
        await conn.execute(
            """
            INSERT INTO scheduled_tasks (
                id, workspace_id, name, cron_expr, prompt_template, status, created_at
            ) VALUES (?, ?, ?, ?, ?, 'active', ?);
            """,
            (task_id, req.workspace_id, req.name, req.cron_expr, req.prompt_template, now_str),
        )
        await conn.commit()

    return {
        "id": task_id,
        "workspace_id": req.workspace_id,
        "name": req.name,
        "cron_expr": req.cron_expr,
        "prompt_template": req.prompt_template,
        "status": "active",
        "created_at": now_str,
    }


@router.patch("/{task_id}")
async def update_scheduled_task(task_id: str, req: ScheduledTaskUpdateRequest):
    """
    更新任务状态或参数
    """
    fields = []
    params = []
    if req.status is not None:
        fields.append("status = ?")
        params.append(req.status)
    if req.name is not None:
        fields.append("name = ?")
        params.append(req.name)
    if req.cron_expr is not None:
        fields.append("cron_expr = ?")
        params.append(req.cron_expr)
    if req.prompt_template is not None:
        fields.append("prompt_template = ?")
        params.append(req.prompt_template)

    if not fields:
        return {"success": True}

    params.append(task_id)
    async with db_manager.get_connection() as conn:
        await conn.execute(
            f"UPDATE scheduled_tasks SET {', '.join(fields)} WHERE id = ?;",
            tuple(params),
        )
        await conn.commit()

    return {"success": True}


@router.post("/{task_id}/run")
async def run_task_immediately(task_id: str):
    """
    立即执行计划任务并发送系统通知
    """
    scheduler = TaskScheduler.get_instance()
    result = await scheduler.run_task(task_id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "执行失败"))
    return result


@router.delete("/{task_id}")
async def delete_scheduled_task(task_id: str):
    """
    删除计划任务
    """
    async with db_manager.get_connection() as conn:
        await conn.execute("DELETE FROM scheduled_tasks WHERE id = ?;", (task_id,))
        await conn.commit()
    return {"success": True}

