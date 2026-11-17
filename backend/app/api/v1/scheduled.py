"""
已安排计划任务 (Scheduled Tasks) 路由
"""
import uuid
from datetime import datetime
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ...repository.db import db_manager
from ...scheduler.task_scheduler import (
    CronExpression,
    TaskScheduler,
    compute_next_run_iso,
)

router = APIRouter(prefix="/scheduled", tags=["Scheduled Tasks"])


class ScheduledTaskCreateRequest(BaseModel):
    workspace_id: str = Field(..., description="归属工作区 ID")
    name: str = Field(..., description="计划任务名称")
    cron_expr: str = Field(..., description="Cron 表达式或周期标识 (如 @daily, @hourly, 0 9 * * *)")
    prompt_template: str = Field(..., description="自动化执行提示词模板")
    agent_id: str | None = Field(None, description="可选：绑定执行的专家智能体 ID")


class ScheduleParseRequest(BaseModel):
    workspace_id: str = Field(..., description="归属工作区 ID")
    user_prompt: str = Field(..., description="用户口语化自然语言需求 (如: 工作日每天早晨9点半检查未读邮件并生成报告)")


class ScheduledTaskUpdateRequest(BaseModel):
    status: str | None = Field(None, description="active 或 paused")
    name: str | None = None
    cron_expr: str | None = None
    prompt_template: str | None = None


@router.post("/parse")
async def parse_schedule_intent(req: ScheduleParseRequest):
    """
    通过 AI / 本地语义规则深度分析用户的对话意图，
    自动提炼「执行时间 (When)」与「执行动作 (What)」，生成结构化计划任务配置。
    """
    from ...scheduler.intent_parser import ScheduleIntentParser

    parsed = ScheduleIntentParser.parse(req.user_prompt)
    return {
        "success": True,
        "data": parsed,
    }


@router.get("")
async def list_scheduled_tasks(workspace_id: str = Query(..., description="工作区 ID")):
    """
    获取指定工作区下的所有计划任务（含最近状态、耗时与下次计划运行时间）
    """
    TaskScheduler.get_instance().ensure_started()
    now_dt = datetime.now()
    now_iso = now_dt.isoformat()

    async with db_manager.get_connection() as conn:
        async with conn.execute(
            "SELECT * FROM scheduled_tasks WHERE workspace_id = ? ORDER BY created_at DESC;",
            (workspace_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            task_list = [dict(r) for r in rows]

        for task in task_list:
            if task.get("status") == "active":
                nxt = task.get("next_run_at")
                if not nxt or nxt < now_iso:
                    computed = compute_next_run_iso(task["cron_expr"], now_dt)
                    if computed:
                        task["next_run_at"] = computed
                        await conn.execute(
                            "UPDATE scheduled_tasks SET next_run_at = ? WHERE id = ?;",
                            (computed, task["id"]),
                        )
        await conn.commit()

    return {"tasks": task_list, "total": len(task_list)}


@router.post("")
async def create_scheduled_task(req: ScheduledTaskCreateRequest):
    """
    新建计划任务并校验、计算首次 next_run_at
    """
    TaskScheduler.get_instance().ensure_started()
    try:
        CronExpression.parse(req.cron_expr)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    task_id = f"sched_{uuid.uuid4().hex[:10]}"
    now_dt = datetime.now()
    now_str = now_dt.isoformat()
    next_run_at = compute_next_run_iso(req.cron_expr, now_dt)

    async with db_manager.get_connection() as conn:
        await conn.execute(
            """
            INSERT INTO scheduled_tasks (
                id, workspace_id, name, cron_expr, prompt_template, agent_id, status, next_run_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?);
            """,
            (task_id, req.workspace_id, req.name, req.cron_expr, req.prompt_template, req.agent_id, next_run_at, now_str),
        )
        await conn.commit()

    return {
        "id": task_id,
        "workspace_id": req.workspace_id,
        "name": req.name,
        "cron_expr": req.cron_expr,
        "prompt_template": req.prompt_template,
        "agent_id": req.agent_id,
        "status": "active",
        "next_run_at": next_run_at,
        "created_at": now_str,
    }


@router.patch("/{task_id}")
async def update_scheduled_task(task_id: str, req: ScheduledTaskUpdateRequest):
    """
    更新任务状态或参数，并在修改周期或恢复启用时同步刷新 next_run_at
    """
    TaskScheduler.get_instance().ensure_started()

    async with db_manager.get_connection() as conn:
        async with conn.execute("SELECT * FROM scheduled_tasks WHERE id = ?;", (task_id,)) as cursor:
            existing = await cursor.fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail="计划任务不存在")

        fields: list[str] = []
        params: list[object] = []

        target_status = req.status if req.status is not None else existing["status"]
        target_cron = req.cron_expr if req.cron_expr is not None else existing["cron_expr"]

        if req.cron_expr is not None:
            try:
                CronExpression.parse(req.cron_expr)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
            fields.append("cron_expr = ?")
            params.append(req.cron_expr)

        if req.status is not None:
            if req.status not in ("active", "paused"):
                raise HTTPException(status_code=400, detail="status 仅支持 active 或 paused")
            fields.append("status = ?")
            params.append(req.status)

        if req.name is not None:
            fields.append("name = ?")
            params.append(req.name)
        if req.prompt_template is not None:
            fields.append("prompt_template = ?")
            params.append(req.prompt_template)

        if req.status is not None or req.cron_expr is not None:
            next_run_at = compute_next_run_iso(target_cron) if target_status == "active" else None
            fields.append("next_run_at = ?")
            params.append(next_run_at)

        if not fields:
            return {"success": True}

        params.append(task_id)
        await conn.execute(
            f"UPDATE scheduled_tasks SET {', '.join(fields)} WHERE id = ?;",
            tuple(params),
        )
        await conn.commit()

    return {"success": True}


@router.post("/{task_id}/run")
async def run_task_immediately(task_id: str):
    """
    立即无头执行计划任务，记录运行历史并发送系统通知
    """
    scheduler = TaskScheduler.get_instance()
    result = await scheduler.run_task(task_id, trigger_type="manual")
    if not result.get("run_id") and not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "执行失败"))
    return result


@router.get("/{task_id}/runs")
async def list_task_runs(
    task_id: str,
    limit: int = Query(20, ge=1, le=100, description="返回最近运行历史条数"),
):
    """
    获取指定计划任务的运行历史详情列表（含完整输出正文、错误详情与耗时）
    """
    async with db_manager.get_connection() as conn:
        async with conn.execute("SELECT id FROM scheduled_tasks WHERE id = ?;", (task_id,)) as cursor:
            if not await cursor.fetchone():
                raise HTTPException(status_code=404, detail="计划任务不存在")

        async with conn.execute(
            """
            SELECT * FROM scheduled_task_runs
            WHERE task_id = ?
            ORDER BY started_at DESC
            LIMIT ?;
            """,
            (task_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
            runs = [dict(r) for r in rows]

    return {"task_id": task_id, "runs": runs, "total": len(runs)}


@router.delete("/{task_id}")
async def delete_scheduled_task(task_id: str):
    """
    删除计划任务
    """
    async with db_manager.get_connection() as conn:
        await conn.execute("DELETE FROM scheduled_tasks WHERE id = ?;", (task_id,))
        await conn.commit()
    return {"success": True}
