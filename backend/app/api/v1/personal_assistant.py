"""
私人助理与远程服务器运维管理 API（Phase 2-4）。
提供：
1. 托管服务器资产增删改查、一键健康体检（严格脱敏返回，绝不回显密码或私钥）；
2. L2 高危审批挂起历史查询与微信碎片收藏箱列表；
3. 超长输出 24 小时临时鉴权只读 HTML / Markdown 报告查看接口。
"""
from __future__ import annotations

import html
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field

from ...channels.gateway import channel_gateway
from ...repository.db import db_manager
from ...service.personal_assistant_service import personal_assistant_service
from ...service.personal_workflow_service import personal_workflow_service
from ...service.server_ops_service import server_ops_service

router = APIRouter(prefix="/personal-assistant", tags=["personal-assistant"])


class IMScheduledTaskCreateRequest(BaseModel):
    """创建微信/IM 计划任务与定时提醒请求。"""

    link_id: str = Field(..., min_length=1, max_length=64, description="关联的 IM 对接链接 ID")
    trigger_expr: str = Field(..., min_length=1, max_length=60, description="时间表达式（如 每天早上8:30、30分钟后、30 8 * * *）")
    content: str = Field(..., min_length=1, max_length=500, description="提醒内容或让 AI 到点执行的指令")
    run_as_agent: bool = Field(default=False, description="是否到点由 AI 执行联网/天气/汇总后再推送")
    target_user_id: str = Field(default="", max_length=120, description="可选目标微信/IM 用户 ID，留空自动匹配最近联系人")


class ServerAssetUpsertRequest(BaseModel):
    """新增或更新托管服务器资产请求（密码与私钥服务端加密落库）。"""

    alias: str = Field(..., min_length=1, max_length=60, description="服务器别名（如 生产机、测试机）")
    host: str = Field(..., min_length=1, max_length=120, description="IP 或域名")
    port: int = Field(default=22, ge=1, le=65535, description="SSH 端口")
    username: str = Field(default="root", min_length=1, max_length=60, description="SSH 登录用户名")
    auth_type: str = Field(default="key_path", description="认证方式: key_path | private_key_enc | password_enc")
    credential: str = Field(default="", description="SSH 密码或私钥全文（服务端 Fernet 加密存储）")
    key_path: str = Field(default="", description="容器/宿主机私钥文件路径（可选）")
    env_tier: str = Field(default="prod", description="环境分级: prod | staging | dev | host")
    default_workdir: str = Field(default="", description="默认工作目录")
    allow_write: bool = Field(default=False, description="是否允许写操作命令免只读白名单（生产机建议关闭）")
    notes: str = Field(default="", max_length=300, description="备注说明")


@router.get("/servers")
async def list_managed_servers() -> dict[str, Any]:
    """获取所有托管服务器资产（严格脱敏）。"""
    items = await server_ops_service.list_servers()
    return {"success": True, "data": items}


@router.post("/servers")
async def upsert_managed_server(payload: ServerAssetUpsertRequest) -> dict[str, Any]:
    """新增或更新托管服务器资产。"""
    try:
        item = await server_ops_service.upsert_server(
            alias=payload.alias,
            host=payload.host,
            port=payload.port,
            username=payload.username,
            auth_type=payload.auth_type,
            credential=payload.credential,
            key_path=payload.key_path,
            env_tier=payload.env_tier,
            default_workdir=payload.default_workdir,
            allow_write=payload.allow_write,
            notes=payload.notes,
        )
        return {"success": True, "data": item}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


@router.delete("/servers/{alias}")
async def delete_managed_server(alias: str) -> dict[str, Any]:
    """删除托管服务器资产。"""
    ok = await server_ops_service.delete_server(alias)
    if not ok:
        raise HTTPException(status_code=404, detail="未找到目标服务器")
    return {"success": True, "message": f"已删除托管服务器 {alias}"}


@router.post("/servers/{alias}/inspect")
async def inspect_managed_server(alias: str) -> dict[str, Any]:
    """手动触发一键服务器健康体检（输入 all 可巡检全部）。"""
    target = "" if alias.strip().lower() == "all" else alias
    res = await server_ops_service.inspect_server_health(alias=target)
    return {"success": bool(res.get("success")), "data": res}


@router.get("/approvals")
async def list_pending_approvals(limit: int = Query(default=20, ge=1, le=100)) -> dict[str, Any]:
    """获取 L2 高危审批挂起/执行审计记录。"""
    items = await server_ops_service.list_pending_approvals(limit=limit)
    return {"success": True, "data": items}


@router.get("/tasks")
async def list_im_scheduled_tasks(link_id: str = Query(default="")) -> dict[str, Any]:
    """列出所有活跃的微信/IM 计划任务与定时提醒。"""
    clauses = ["status = 'active'", "(notify_channel != '' OR id LIKE 'rem_%')"]
    params: list[Any] = []
    if link_id.strip():
        clauses.append("notify_link_id = ?")
        params.append(link_id.strip())
    sql = (
        "SELECT id, name, cron_expr, prompt_template, next_run_at, notify_channel, "
        "notify_link_id, notify_target_user_id, one_shot "
        f"FROM scheduled_tasks WHERE {' AND '.join(clauses)} ORDER BY next_run_at ASC LIMIT 50"
    )
    async with db_manager.get_connection() as conn:
        cur = await conn.execute(sql, tuple(params))
        rows = await cur.fetchall()
    items: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        pt = str(d.get("prompt_template") or "")
        d["run_as_agent"] = not pt.startswith("__IM_REMINDER__:")
        d["display_content"] = pt.split(":", 1)[1].strip() if pt.startswith("__IM_REMINDER__:") else pt
        items.append(d)
    return {"success": True, "data": items}


@router.post("/tasks")
async def create_im_scheduled_task(payload: IMScheduledTaskCreateRequest) -> dict[str, Any]:
    """在控制台为指定 IM 对接链接创建计划任务或定时提醒。"""
    async with db_manager.get_connection() as conn:
        cur = await conn.execute(
            "SELECT link_id, expert_id, channel_type, target_user_label, target_user_id FROM dialogue_channel_links WHERE link_id = ? LIMIT 1",
            (payload.link_id.strip(),),
        )
        link_row = await cur.fetchone()
        if not link_row:
            raise HTTPException(status_code=404, detail="未找到指定的 IM 对接链接")
        link_info = dict(link_row)
        target_uid = payload.target_user_id.strip() or str(link_info.get("target_user_id") or "").strip()
        if not target_uid:
            cur_sess = await conn.execute(
                "SELECT sender_id FROM dialogue_channel_sessions WHERE link_id = ? ORDER BY updated_at DESC LIMIT 1",
                (payload.link_id.strip(),),
            )
            sess_row = await cur_sess.fetchone()
            if sess_row:
                target_uid = str(sess_row["sender_id"] or "").strip()
    if not target_uid:
        raise HTTPException(
            status_code=400,
            detail="该链接尚未记录到收件人 ID，请先用微信给机器人发一条消息后再添加计划任务。",
        )
    ws_id = await channel_gateway.ensure_im_link_workspace(
        link_id=str(link_info["link_id"]),
        target_user_label=str(link_info.get("target_user_label") or ""),
        expert_id=str(link_info.get("expert_id") or ""),
    )
    try:
        created = await personal_assistant_service.create_im_reminder(
            workspace_id=ws_id,
            link_id=str(link_info["link_id"]),
            channel_type=str(link_info["channel_type"]),
            target_user_id=target_uid,
            content=payload.content,
            trigger_expr=payload.trigger_expr,
            run_as_agent=payload.run_as_agent,
        )
        return {"success": True, "data": created}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


@router.delete("/tasks/{task_id}")
async def cancel_im_scheduled_task(task_id: str) -> dict[str, Any]:
    """取消指定的 IM 计划任务。"""
    async with db_manager.get_connection() as conn:
        cur = await conn.execute("SELECT id FROM scheduled_tasks WHERE id = ? LIMIT 1", (task_id.strip(),))
        if not await cur.fetchone():
            raise HTTPException(status_code=404, detail="未找到目标计划任务")
        await conn.execute(
            "UPDATE scheduled_tasks SET status = 'paused', next_run_at = NULL WHERE id = ?",
            (task_id.strip(),),
        )
        await conn.commit()
    return {"success": True, "message": "已取消该计划任务"}


@router.get("/inbox")
async def list_inbox_items(
    sender_id: str = Query(default="default"),
    keyword: str = Query(default=""),
    limit: int = Query(default=20, ge=1, le=50),
) -> dict[str, Any]:
    """获取微信碎片收藏箱列表。"""
    items = await personal_workflow_service.search_inbox_items(
        sender_id=sender_id, keyword=keyword, limit=limit
    )
    return {"success": True, "data": items}


@router.get("/reports/{token}")
async def view_shared_report(token: str, raw: int = Query(default=0)):
    """查看 24 小时临时鉴权只读报告（移动端免登录 token 访问，raw=1 下载 Markdown 原文）。"""
    rep = await personal_workflow_service.get_shared_report(token)
    if not rep:
        raise HTTPException(status_code=404, detail="报告不存在或已超过 24 小时有效期")
    if raw == 1:
        return PlainTextResponse(
            content=str(rep["content_md"]),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": 'inline; filename="report.md"'},
        )
    esc_title = html.escape(str(rep["title"]))
    esc_body = html.escape(str(rep["content_md"]))
    page_html = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>{esc_title} - AgentHub</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; max-width: 820px; margin: 24px auto; padding: 0 16px; background: #0f172a; color: #e2e8f0; line-height: 1.6; }}
  .hdr {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #334155; padding-bottom: 12px; margin-bottom: 16px; }}
  h1 {{ font-size: 18px; margin: 0; color: #f8fafc; }}
  a.btn {{ font-size: 12px; color: #38bdf8; text-decoration: none; border: 1px solid #38bdf8; padding: 4px 10px; border-radius: 6px; }}
  pre {{ white-space: pre-wrap; word-break: break-word; background: #1e293b; padding: 16px; border-radius: 10px; font-size: 13px; border: 1px solid #334155; }}
</style>
</head>
<body>
  <div class="hdr">
    <h1>{esc_title}</h1>
    <a class="btn" href="?raw=1">下载 .md 原文</a>
  </div>
  <pre>{esc_body}</pre>
</body>
</html>"""
    return HTMLResponse(content=page_html)

