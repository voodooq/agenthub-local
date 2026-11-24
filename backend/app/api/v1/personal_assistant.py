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

from ...service.personal_workflow_service import personal_workflow_service
from ...service.server_ops_service import server_ops_service

router = APIRouter(prefix="/personal-assistant", tags=["personal-assistant"])


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

