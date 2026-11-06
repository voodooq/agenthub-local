"""
审计日志查询接口（T7）
允许前端查看工具调用历史、账号切换记录与权限拒绝事件。
"""
from fastapi import APIRouter, Query
from ...repository.db import db_manager

router = APIRouter(prefix="/audit", tags=["Audit"])


@router.get("/logs")
async def get_audit_logs(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    event_type: str | None = Query(default=None),
    account_name: str | None = Query(default=None),
):
    """查询审计日志，支持按事件类型和账号过滤"""
    # 显式转换为基础 Python 原生标量类型，彻底杜绝 FastAPI Query 默认对象泄露至 sqlite3 execute 参数
    effective_limit = int(getattr(limit, "default", limit) if not isinstance(limit, int) else limit)
    effective_offset = int(getattr(offset, "default", offset) if not isinstance(offset, int) else offset)
    effective_event_type = str(event_type) if event_type and not hasattr(event_type, "default") else None
    effective_account_name = str(account_name) if account_name and not hasattr(account_name, "default") else None

    conditions = []
    params: list = []

    if effective_event_type:
        conditions.append("event_type = ?")
        params.append(effective_event_type)
    if effective_account_name:
        conditions.append("account_name = ?")
        params.append(effective_account_name)

    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.extend([effective_limit, effective_offset])

    async with db_manager.get_connection() as conn:
        async with conn.execute(
            f"""
            SELECT id, event_type, actor, account_name, detail, approved, created_at
            FROM audit_log
            {where_clause}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            params
        ) as cursor:
            rows = await cursor.fetchall()

    return [dict(row) for row in rows]

