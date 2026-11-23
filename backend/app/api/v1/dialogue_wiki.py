"""
用户对话 Wiki（Dialogue Wiki）渠道对接与对话沉淀预留接口。
提供：
1. 数字专家 × IM 渠道 × 目标用户/群组的专用对接链接生成与管理（已落库持久化）；
2. 历史用户对话记录查询（预留表结构与接口，待 IM Webhook 接入后写入）；
3. 对话记录记忆提炼与向量化训练入口（预留触发接口）。
"""
from __future__ import annotations

import secrets
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ...repository.db import db_manager

router = APIRouter(prefix="/dialogue-wiki", tags=["dialogue-wiki"])

ALLOWED_CHANNELS: dict[str, str] = {
    "weixin_clawbot": "个人微信 (ClawBot)",
    "wecom": "企业微信 / 微信客服",
    "feishu": "飞书",
    "dingtalk": "钉钉",
    "web_widget": "网页嵌入 / H5",
    "webhook": "自定义 Webhook",
}


class DialogueLinkCreateRequest(BaseModel):
    """创建渠道专用对接链接的请求参数（支持绑定单个或多个数字专家）。"""

    expert_id: str = Field(default="", description="主数字专家 ID（向后兼容）")
    expert_ids: list[str] | None = Field(default=None, description="绑定的数字专家 ID 列表（支持多选跨专家服务）")
    channel_type: str = Field(..., min_length=1, description="IM 渠道类型")
    target_user_label: str = Field(..., min_length=1, max_length=120, description="目标用户或群组名称")
    target_user_id: str = Field(default="", max_length=120, description="外部渠道用户/群 ID（选填）")
    bound_role: str = Field(default="guest", description="服务端锁定数据角色: hq_admin | province_admin | club_admin | end_user | guest")
    bound_scope: str = Field(default="", max_length=300, description="服务端锁定数据作用域（如 region_code=310000 或 delegation_id=1052）")
    memory_extract_enabled: bool = Field(default=True, description="是否开启自动提炼记忆存储")
    vector_train_enabled: bool = Field(default=True, description="是否开启对话记录向量化训练")


class DialogueLinkUpdateRequest(BaseModel):
    """编辑已创建渠道对接链接的请求参数（保留原 token 与已绑定扫码关系，修改实时生效）。"""

    expert_ids: list[str] | None = Field(default=None, description="更新绑定的数字专家 ID 列表（支持多选）")
    target_user_label: str | None = Field(default=None, min_length=1, max_length=120, description="目标用户或群组名称")
    target_user_id: str | None = Field(default=None, max_length=120, description="外部渠道用户/群 ID")
    bound_role: str | None = Field(default=None, description="服务端锁定数据角色")
    bound_scope: str | None = Field(default=None, max_length=300, description="服务端锁定数据作用域 RLS")
    memory_extract_enabled: bool | None = Field(default=None, description="是否开启自动提炼记忆")
    vector_train_enabled: bool | None = Field(default=None, description="是否开启向量化训练")
    status: str | None = Field(default=None, description="状态: active | paused")


async def _ensure_dialogue_wiki_tables(conn: Any) -> None:
    """确保用户对话 Wiki 所需表结构存在（幂等初始化）。"""
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dialogue_channel_links (
            link_id TEXT PRIMARY KEY,
            expert_id TEXT NOT NULL,
            expert_name TEXT NOT NULL DEFAULT '',
            channel_type TEXT NOT NULL,
            target_user_label TEXT NOT NULL,
            target_user_id TEXT NOT NULL DEFAULT '',
            endpoint_token TEXT NOT NULL UNIQUE,
            bound_role TEXT NOT NULL DEFAULT 'guest',
            bound_scope_json TEXT NOT NULL DEFAULT '{}',
            memory_extract_enabled INTEGER NOT NULL DEFAULT 1,
            vector_train_enabled INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'active',
            message_count INTEGER NOT NULL DEFAULT 0,
            memory_count INTEGER NOT NULL DEFAULT 0,
            last_trained_at REAL,
            created_at REAL NOT NULL
        )
        """
    )
    # 幂等迁移旧表补充权限锁定列
    cur = await conn.execute("PRAGMA table_info(dialogue_channel_links)")
    existing_cols = {row["name"] for row in await cur.fetchall()}
    if "bound_role" not in existing_cols:
        await conn.execute("ALTER TABLE dialogue_channel_links ADD COLUMN bound_role TEXT NOT NULL DEFAULT 'guest'")
    if "bound_scope_json" not in existing_cols:
        await conn.execute("ALTER TABLE dialogue_channel_links ADD COLUMN bound_scope_json TEXT NOT NULL DEFAULT '{}'")
    if "channel_config_json" not in existing_cols:
        await conn.execute("ALTER TABLE dialogue_channel_links ADD COLUMN channel_config_json TEXT NOT NULL DEFAULT '{}'")
    if "expert_ids_json" not in existing_cols:
        await conn.execute("ALTER TABLE dialogue_channel_links ADD COLUMN expert_ids_json TEXT NOT NULL DEFAULT '[]'")
    if "expert_names_json" not in existing_cols:
        await conn.execute("ALTER TABLE dialogue_channel_links ADD COLUMN expert_names_json TEXT NOT NULL DEFAULT '[]'")
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dialogue_wiki_records (
            record_id TEXT PRIMARY KEY,
            link_id TEXT NOT NULL,
            expert_id TEXT NOT NULL,
            channel_type TEXT NOT NULL,
            sender_id TEXT NOT NULL DEFAULT '',
            sender_name TEXT NOT NULL DEFAULT '',
            user_message TEXT NOT NULL DEFAULT '',
            assistant_reply TEXT NOT NULL DEFAULT '',
            extracted_memory TEXT NOT NULL DEFAULT '',
            vector_status TEXT NOT NULL DEFAULT 'pending',
            created_at REAL NOT NULL
        )
        """
    )
    await conn.commit()


def _build_endpoint_url(request: Request, channel_type: str, token: str) -> str:
    """根据当前服务地址拼装渠道专用对接链接。"""
    base_url = str(request.base_url).rstrip("/")
    return f"{base_url}/api/v1/dialogue-wiki/ingress/{channel_type}/{token}"


@router.get("/links")
async def list_dialogue_links(request: Request, expert_id: str | None = None) -> dict[str, Any]:
    """列出已生成的渠道专用对接链接。"""
    async with db_manager.get_connection() as conn:
        await _ensure_dialogue_wiki_tables(conn)
        if expert_id:
            cursor = await conn.execute(
                """
                SELECT * FROM dialogue_channel_links
                WHERE expert_id = ?
                ORDER BY created_at DESC
                """,
                (expert_id,),
            )
        else:
            cursor = await conn.execute(
                "SELECT * FROM dialogue_channel_links ORDER BY created_at DESC"
            )
        rows = await cursor.fetchall()

    import json as _json
    from ...schema.access_control import ROLE_LABELS, parse_scope_input

    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["channel_label"] = ALLOWED_CHANNELS.get(item["channel_type"], item["channel_type"])
        item["endpoint_url"] = _build_endpoint_url(
            request, item["channel_type"], item["endpoint_token"]
        )
        try:
            e_ids = _json.loads(item.get("expert_ids_json") or "[]")
        except Exception:
            e_ids = []
        try:
            e_names = _json.loads(item.get("expert_names_json") or "[]")
        except Exception:
            e_names = []
        item["expert_ids"] = e_ids if isinstance(e_ids, list) and e_ids else ([item["expert_id"]] if item.get("expert_id") else [])
        item["expert_names"] = e_names if isinstance(e_names, list) and e_names else ([item["expert_name"]] if item.get("expert_name") else [])
        role_val = str(item.get("bound_role") or "guest")
        item["bound_role"] = role_val
        item["bound_role_label"] = ROLE_LABELS.get(role_val, role_val)
        item["bound_scope"] = parse_scope_input(item.get("bound_scope_json"))
        item["memory_extract_enabled"] = bool(item.get("memory_extract_enabled"))
        item["vector_train_enabled"] = bool(item.get("vector_train_enabled"))
        items.append(item)

    return {"success": True, "data": items}


@router.post("/links")
async def create_dialogue_link(
    payload: DialogueLinkCreateRequest, request: Request
) -> dict[str, Any]:
    """为指定一个或多个数字专家 + IM 渠道 + 目标用户生成专用对接链接（服务端锁定角色与数据作用域）。"""
    import json as _json
    from ...schema.access_control import ROLE_LABELS, VALID_ROLES, parse_scope_input

    if payload.channel_type not in ALLOWED_CHANNELS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的渠道类型: {payload.channel_type}",
        )

    raw_expert_ids: list[str] = []
    if isinstance(payload.expert_ids, list):
        for eid in payload.expert_ids:
            s = str(eid).strip()
            if s and s not in raw_expert_ids:
                raw_expert_ids.append(s)
    if not raw_expert_ids and payload.expert_id.strip():
        raw_expert_ids.append(payload.expert_id.strip())
    if not raw_expert_ids:
        raise HTTPException(status_code=400, detail="请至少选择一位数字专家")

    bound_role = payload.bound_role.strip() if payload.bound_role in VALID_ROLES else "guest"
    bound_scope_dict = parse_scope_input(payload.bound_scope)
    bound_scope_json = _json.dumps(bound_scope_dict, ensure_ascii=False)

    async with db_manager.get_connection() as conn:
        await _ensure_dialogue_wiki_tables(conn)
        resolved_ids: list[str] = []
        resolved_names: list[str] = []
        for eid in raw_expert_ids:
            cur = await conn.execute(
                "SELECT id, name FROM agent_profiles WHERE id = ? LIMIT 1",
                (eid,),
            )
            expert_row = await cur.fetchone()
            if expert_row:
                rd = dict(expert_row)
                resolved_ids.append(str(rd["id"]))
                resolved_names.append(str(rd.get("name") or rd["id"]))
        if not resolved_ids:
            raise HTTPException(status_code=404, detail="所选数字专家均不存在")

        primary_expert_id = resolved_ids[0]
        primary_expert_name = resolved_names[0]
        link_id = f"dlink_{secrets.token_hex(6)}"
        endpoint_token = f"dw_{secrets.token_hex(12)}"
        now_ts = time.time()

        await conn.execute(
            """
            INSERT INTO dialogue_channel_links (
                link_id, expert_id, expert_name, expert_ids_json, expert_names_json,
                channel_type, target_user_label, target_user_id, endpoint_token,
                bound_role, bound_scope_json,
                memory_extract_enabled, vector_train_enabled,
                status, message_count, memory_count, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 0, 0, ?)
            """,
            (
                link_id,
                primary_expert_id,
                primary_expert_name,
                _json.dumps(resolved_ids, ensure_ascii=False),
                _json.dumps(resolved_names, ensure_ascii=False),
                payload.channel_type,
                payload.target_user_label.strip(),
                payload.target_user_id.strip(),
                endpoint_token,
                bound_role,
                bound_scope_json,
                1 if payload.memory_extract_enabled else 0,
                1 if payload.vector_train_enabled else 0,
                now_ts,
            ),
        )
        await conn.commit()

    try:
        from ...channels.gateway import channel_gateway

        await channel_gateway.ensure_im_link_workspace(
            link_id=link_id,
            target_user_label=payload.target_user_label.strip(),
            expert_id=primary_expert_id,
        )
    except Exception:
        pass

    return {
        "success": True,
        "data": {
            "link_id": link_id,
            "expert_id": primary_expert_id,
            "expert_name": primary_expert_name,
            "expert_ids": resolved_ids,
            "expert_names": resolved_names,
            "channel_type": payload.channel_type,
            "bound_role": bound_role,
            "bound_role_label": ROLE_LABELS.get(bound_role, bound_role),
            "bound_scope": bound_scope_dict,
            "channel_label": ALLOWED_CHANNELS[payload.channel_type],
            "target_user_label": payload.target_user_label.strip(),
            "target_user_id": payload.target_user_id.strip(),
            "endpoint_token": endpoint_token,
            "endpoint_url": _build_endpoint_url(request, payload.channel_type, endpoint_token),
            "memory_extract_enabled": payload.memory_extract_enabled,
            "vector_train_enabled": payload.vector_train_enabled,
            "status": "active",
            "message_count": 0,
            "memory_count": 0,
            "created_at": now_ts,
        },
    }


@router.patch("/links/{link_id}")
async def update_dialogue_link(
    link_id: str, payload: DialogueLinkUpdateRequest, request: Request
) -> dict[str, Any]:
    """编辑已创建的渠道对接链接（修改授权专家、数据角色/RLS 权限、名称与记忆开关，不改变原 token 与微信扫码绑定）。"""
    import json as _json
    from ...schema.access_control import ROLE_LABELS, VALID_ROLES, parse_scope_input

    async with db_manager.get_connection() as conn:
        await _ensure_dialogue_wiki_tables(conn)
        cur = await conn.execute(
            "SELECT * FROM dialogue_channel_links WHERE link_id = ? LIMIT 1",
            (link_id,),
        )
        existing = await cur.fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="渠道对接链接不存在")
        row = dict(existing)

        # 1. 更新授权数字专家列表
        if payload.expert_ids is not None:
            dedup_ids: list[str] = []
            for eid in payload.expert_ids:
                s = str(eid).strip()
                if s and s not in dedup_ids:
                    dedup_ids.append(s)
            if not dedup_ids:
                raise HTTPException(status_code=400, detail="请至少保留一位授权数字专家")
            resolved_ids: list[str] = []
            resolved_names: list[str] = []
            for eid in dedup_ids:
                ecur = await conn.execute(
                    "SELECT id, name FROM agent_profiles WHERE id = ? LIMIT 1",
                    (eid,),
                )
                erow = await ecur.fetchone()
                if erow:
                    ed = dict(erow)
                    resolved_ids.append(str(ed["id"]))
                    resolved_names.append(str(ed.get("name") or ed["id"]))
            if not resolved_ids:
                raise HTTPException(status_code=404, detail="所选数字专家均不存在")
            primary_expert_id = resolved_ids[0]
            primary_expert_name = resolved_names[0]
            expert_ids_json = _json.dumps(resolved_ids, ensure_ascii=False)
            expert_names_json = _json.dumps(resolved_names, ensure_ascii=False)
        else:
            primary_expert_id = str(row.get("expert_id") or "")
            primary_expert_name = str(row.get("expert_name") or "")
            expert_ids_json = str(row.get("expert_ids_json") or "[]")
            expert_names_json = str(row.get("expert_names_json") or "[]")
            try:
                resolved_ids = _json.loads(expert_ids_json) or ([primary_expert_id] if primary_expert_id else [])
                resolved_names = _json.loads(expert_names_json) or ([primary_expert_name] if primary_expert_name else [])
            except Exception:
                resolved_ids = [primary_expert_id] if primary_expert_id else []
                resolved_names = [primary_expert_name] if primary_expert_name else []

        # 2. 更新角色与 RLS 作用域
        if payload.bound_role is not None:
            new_role = payload.bound_role.strip()
            if new_role not in VALID_ROLES:
                raise HTTPException(status_code=400, detail=f"不支持的数据权限角色: {new_role}")
        else:
            new_role = str(row.get("bound_role") or "guest")

        if payload.bound_scope is not None:
            new_scope_dict = parse_scope_input(payload.bound_scope)
            new_scope_json = _json.dumps(new_scope_dict, ensure_ascii=False)
        else:
            new_scope_json = str(row.get("bound_scope_json") or "{}")
            new_scope_dict = parse_scope_input(new_scope_json)

        new_label = (
            payload.target_user_label.strip()
            if payload.target_user_label is not None
            else str(row.get("target_user_label") or "")
        )
        new_uid = (
            payload.target_user_id.strip()
            if payload.target_user_id is not None
            else str(row.get("target_user_id") or "")
        )
        new_mem = (
            (1 if payload.memory_extract_enabled else 0)
            if payload.memory_extract_enabled is not None
            else int(row.get("memory_extract_enabled") or 0)
        )
        new_vec = (
            (1 if payload.vector_train_enabled else 0)
            if payload.vector_train_enabled is not None
            else int(row.get("vector_train_enabled") or 0)
        )
        new_status = (
            payload.status.strip()
            if payload.status in ("active", "paused")
            else str(row.get("status") or "active")
        )

        await conn.execute(
            """
            UPDATE dialogue_channel_links
            SET expert_id = ?,
                expert_name = ?,
                expert_ids_json = ?,
                expert_names_json = ?,
                target_user_label = ?,
                target_user_id = ?,
                bound_role = ?,
                bound_scope_json = ?,
                memory_extract_enabled = ?,
                vector_train_enabled = ?,
                status = ?
            WHERE link_id = ?
            """,
            (
                primary_expert_id,
                primary_expert_name,
                expert_ids_json,
                expert_names_json,
                new_label,
                new_uid,
                new_role,
                new_scope_json,
                new_mem,
                new_vec,
                new_status,
                link_id,
            ),
        )
        await conn.commit()

    try:
        from ...channels.gateway import channel_gateway

        await channel_gateway.ensure_im_link_workspace(
            link_id=link_id,
            target_user_label=new_label,
            expert_id=primary_expert_id,
        )
    except Exception:
        pass

    channel_type = str(row.get("channel_type") or "webhook")
    endpoint_token = str(row.get("endpoint_token") or "")
    return {
        "success": True,
        "message": "已更新机器人授权专家与访问权限配置（实时生效，无需重新扫码）",
        "data": {
            "link_id": link_id,
            "expert_id": primary_expert_id,
            "expert_name": primary_expert_name,
            "expert_ids": resolved_ids,
            "expert_names": resolved_names,
            "channel_type": channel_type,
            "channel_label": ALLOWED_CHANNELS.get(channel_type, channel_type),
            "target_user_label": new_label,
            "target_user_id": new_uid,
            "bound_role": new_role,
            "bound_role_label": ROLE_LABELS.get(new_role, new_role),
            "bound_scope": new_scope_dict,
            "endpoint_token": endpoint_token,
            "endpoint_url": _build_endpoint_url(request, channel_type, endpoint_token),
            "memory_extract_enabled": bool(new_mem),
            "vector_train_enabled": bool(new_vec),
            "status": new_status,
            "message_count": int(row.get("message_count") or 0),
            "memory_count": int(row.get("memory_count") or 0),
            "last_trained_at": row.get("last_trained_at"),
            "created_at": row.get("created_at"),
        },
    }


@router.delete("/links/{link_id}")
async def delete_dialogue_link(link_id: str) -> dict[str, Any]:
    """删除指定的渠道专用对接链接。"""
    async with db_manager.get_connection() as conn:
        await _ensure_dialogue_wiki_tables(conn)
        await conn.execute(
            "DELETE FROM dialogue_channel_links WHERE link_id = ?",
            (link_id,),
        )
        await conn.commit()
    return {"success": True, "message": "已移除该渠道对接链接"}


def _build_records_filter_sql(
    *,
    link_id: str | None = None,
    expert_id: str | None = None,
    channel_type: str | None = None,
    vector_status: str | None = None,
    keyword: str | None = None,
    record_ids: str | None = None,
) -> tuple[str, list[Any]]:
    conditions: list[str] = []
    params: list[Any] = []
    if record_ids:
        ids = [x.strip() for x in record_ids.split(",") if x.strip()]
        if ids:
            placeholders = ",".join("?" for _ in ids)
            conditions.append(f"record_id IN ({placeholders})")
            params.extend(ids)
    if link_id:
        conditions.append("link_id = ?")
        params.append(link_id)
    if expert_id:
        conditions.append("expert_id = ?")
        params.append(expert_id)
    if channel_type:
        conditions.append("channel_type = ?")
        params.append(channel_type)
    if vector_status:
        conditions.append("COALESCE(vector_status, 'pending') = ?")
        params.append(vector_status)
    if keyword and keyword.strip():
        like = f"%{keyword.strip()}%"
        conditions.append("(user_message LIKE ? OR assistant_reply LIKE ? OR sender_name LIKE ?)")
        params.extend([like, like, like])
    where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    return where_sql, params


@router.get("/records")
async def list_dialogue_records(
    link_id: str | None = None,
    expert_id: str | None = None,
    channel_type: str | None = None,
    vector_status: str | None = None,
    keyword: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """按多维条件查询历史 IM 用户对话记录与提炼记忆。"""
    safe_limit = max(1, min(limit, 500))
    where_sql, params = _build_records_filter_sql(
        link_id=link_id,
        expert_id=expert_id,
        channel_type=channel_type,
        vector_status=vector_status,
        keyword=keyword,
    )
    async with db_manager.get_connection() as conn:
        await _ensure_dialogue_wiki_tables(conn)
        cur = await conn.execute(
            f"""
            SELECT * FROM dialogue_wiki_records
            {where_sql}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (*params, safe_limit),
        )
        rows = await cur.fetchall()

    return {
        "success": True,
        "data": [dict(r) for r in rows],
    }


@router.get("/records/export")
async def export_dialogue_records(
    fmt: str = "markdown",
    link_id: str | None = None,
    expert_id: str | None = None,
    channel_type: str | None = None,
    vector_status: str | None = None,
    keyword: str | None = None,
    record_ids: str | None = None,
) -> Any:
    """有针对性地导出筛选或勾选后的用户对话记录，支持 markdown / jsonl / csv 三种训练语料格式。"""
    import csv
    import io
    import json as _json
    from datetime import datetime
    from fastapi.responses import Response

    where_sql, params = _build_records_filter_sql(
        link_id=link_id,
        expert_id=expert_id,
        channel_type=channel_type,
        vector_status=vector_status,
        keyword=keyword,
        record_ids=record_ids,
    )
    async with db_manager.get_connection() as conn:
        await _ensure_dialogue_wiki_tables(conn)
        cur = await conn.execute(
            f"""
            SELECT * FROM dialogue_wiki_records
            {where_sql}
            ORDER BY created_at ASC
            LIMIT 2000
            """,
            params,
        )
        rows = [dict(r) for r in await cur.fetchall()]

    ts_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
    fmt_low = (fmt or "markdown").strip().lower()

    if fmt_low == "jsonl":
        lines: list[str] = []
        for r in rows:
            lines.append(
                _json.dumps(
                    {
                        "messages": [
                            {"role": "user", "content": r.get("user_message") or ""},
                            {"role": "assistant", "content": r.get("assistant_reply") or ""},
                        ],
                        "metadata": {
                            "record_id": r.get("record_id"),
                            "channel_type": r.get("channel_type"),
                            "expert_id": r.get("expert_id"),
                            "sender_name": r.get("sender_name"),
                            "vector_status": r.get("vector_status"),
                            "extracted_memory": r.get("extracted_memory") or "",
                        },
                    },
                    ensure_ascii=False,
                )
            )
        content_bytes = "\n".join(lines).encode("utf-8")
        return Response(
            content=content_bytes,
            media_type="application/x-ndjson; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="dialogue_train_{ts_tag}.jsonl"'},
        )

    if fmt_low == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["记录ID", "时间", "渠道", "发送者", "用户提问", "专家回复", "提炼记忆", "训练状态"])
        for r in rows:
            dt_str = datetime.fromtimestamp(float(r.get("created_at") or 0)).strftime("%Y-%m-%d %H:%M:%S")
            writer.writerow(
                [
                    r.get("record_id", ""),
                    dt_str,
                    ALLOWED_CHANNELS.get(str(r.get("channel_type") or ""), r.get("channel_type", "")),
                    r.get("sender_name", ""),
                    r.get("user_message", ""),
                    r.get("assistant_reply", ""),
                    r.get("extracted_memory", ""),
                    r.get("vector_status", "pending"),
                ]
            )
        # UTF-8 BOM 确保 Excel 双击打开中文不乱码
        content_bytes = ("\ufeff" + buf.getvalue()).encode("utf-8")
        return Response(
            content=content_bytes,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="dialogue_records_{ts_tag}.csv"'},
        )

    # 默认 markdown 格式（可直接导入百科工作台或知识库再次训练）
    md_parts = [f"# 用户对话语料导出（共 {len(rows)} 条 · {ts_tag}）\n"]
    for idx, r in enumerate(rows, 1):
        dt_str = datetime.fromtimestamp(float(r.get("created_at") or 0)).strftime("%Y-%m-%d %H:%M:%S")
        ch_lbl = ALLOWED_CHANNELS.get(str(r.get("channel_type") or ""), str(r.get("channel_type") or ""))
        q_title = " ".join(str(r.get("user_message") or "").split())[:50] or f"对话 {idx}"
        md_parts.append(
            f"## {idx}. {q_title}\n\n"
            f"- **时间**：{dt_str} | **渠道**：{ch_lbl} | **发送者**：{r.get('sender_name', '')}\n\n"
            f"**问**：{r.get('user_message', '')}\n\n"
            f"**答**：{r.get('assistant_reply', '')}\n"
        )
    content_bytes = "\n---\n\n".join(md_parts).encode("utf-8")
    return Response(
        content=content_bytes,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="dialogue_wiki_{ts_tag}.md"'},
    )


@router.delete("/records/{record_id}")
async def delete_dialogue_record(record_id: str) -> dict[str, Any]:
    """删除单条闲聊或无效对话记录，提升后续训练语料纯度。"""
    async with db_manager.get_connection() as conn:
        await _ensure_dialogue_wiki_tables(conn)
        await conn.execute("DELETE FROM dialogue_wiki_records WHERE record_id = ?", (record_id,))
        await conn.commit()
    return {"success": True, "message": "已删除该条对话记录"}


@router.post("/links/{link_id}/train")
async def trigger_dialogue_vector_train(link_id: str) -> dict[str, Any]:
    """触发指定对接链接的历史对话提炼记忆与增量向量化训练（写入专家专属对话知识库）。"""
    from ...channels.dialogue_trainer import train_dialogue_records_for_link

    async with db_manager.get_connection() as conn:
        await _ensure_dialogue_wiki_tables(conn)
    try:
        res = await train_dialogue_records_for_link(link_id)
        return {"success": True, **res}
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"对话向量化训练失败: {exc}")


@router.get("/adapters")
async def list_channel_adapters() -> dict[str, Any]:
    """列出当前底座已注册的所有 IM 渠道适配器插件。"""
    from ...channels import adapters as _adapters  # noqa: F401
    from ...channels.registry import channel_registry

    return {"success": True, "data": channel_registry.list_adapters()}


class UpdateChannelConfigRequest(BaseModel):
    """更新渠道对接链接的专属凭据/连接配置（如企微 Token/AESKey 或微信 ClawBot 桥接地址）。"""

    channel_config: dict[str, Any] = Field(default_factory=dict)


@router.put("/links/{link_id}/config")
async def update_dialogue_link_config(
    link_id: str, payload: UpdateChannelConfigRequest
) -> dict[str, Any]:
    """更新渠道链接专属配置（对留空的敏感字段保留历史值，防止覆盖清空）。"""
    import json as _json

    async with db_manager.get_connection() as conn:
        await _ensure_dialogue_wiki_tables(conn)
        cur = await conn.execute(
            "SELECT channel_config_json FROM dialogue_channel_links WHERE link_id = ? LIMIT 1",
            (link_id,),
        )
        row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="未找到对应的渠道对接链接")

        try:
            existing_cfg = _json.loads(dict(row).get("channel_config_json") or "{}")
        except Exception:
            existing_cfg = {}

        merged = dict(existing_cfg)
        for k, v in payload.channel_config.items():
            if v is None:
                continue
            s_val = str(v).strip()
            if k in ("corpsecret", "encoding_aes_key", "bridge_secret", "app_secret") and not s_val:
                continue
            merged[k] = s_val

        await conn.execute(
            "UPDATE dialogue_channel_links SET channel_config_json = ? WHERE link_id = ?",
            (_json.dumps(merged, ensure_ascii=False), link_id),
        )
        await conn.commit()

    return {"success": True, "message": "渠道配置已安全保存"}


@router.get("/links/{link_id}/status")
async def get_dialogue_link_status(link_id: str) -> dict[str, Any]:
    """查询指定渠道链接对应适配器的连接状态（或个人微信 ClawBot 扫码二维码）。"""
    import json as _json
    from ...channels import adapters as _adapters  # noqa: F401
    from ...channels.registry import channel_registry

    async with db_manager.get_connection() as conn:
        await _ensure_dialogue_wiki_tables(conn)
        cur = await conn.execute(
            "SELECT * FROM dialogue_channel_links WHERE link_id = ? LIMIT 1",
            (link_id,),
        )
        row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="未找到对应的渠道对接链接")
        link = dict(row)
        try:
            link["channel_config"] = _json.loads(link.get("channel_config_json") or "{}")
        except Exception:
            link["channel_config"] = {}

    adapter = channel_registry.get(link["channel_type"])
    if not adapter:
        return {"success": True, "data": {"connected": False}}
    status_info = await adapter.get_connection_status(link)
    return {"success": True, "data": status_info}


@router.get("/ingress/wecom/{endpoint_token}")
async def wecom_callback_url_verify(
    endpoint_token: str,
    msg_signature: str = "",
    timestamp: str = "",
    nonce: str = "",
    echostr: str = "",
) -> Any:
    """企业微信后台保存回调 URL 时的官方 GET 验签解密接口（必须返回纯文本 echostr）。"""
    from fastapi.responses import PlainTextResponse
    from ...channels.adapters.wecom_adapter import WeComChannelAdapter
    from ...channels.gateway import channel_gateway

    link = await channel_gateway._get_active_link(endpoint_token)
    if not link:
        raise HTTPException(status_code=404, detail="无效的企微对接令牌")
    adapter = WeComChannelAdapter()
    try:
        plain_echo = adapter.verify_url_challenge(
            msg_signature=msg_signature,
            timestamp=timestamp,
            nonce=nonce,
            echostr=echostr,
            channel_config=link.get("channel_config") or {},
        )
        return PlainTextResponse(content=plain_echo)
    except ValueError as ve:
        raise HTTPException(status_code=403, detail=str(ve))


@router.post("/ingress/{channel_type}/{endpoint_token}")
async def channel_ingress_endpoint(
    channel_type: str,
    endpoint_token: str,
    request: Request,
    mode: str | None = None,
) -> dict[str, Any]:
    """统一多渠道 IM 入站网关入口：兼容 JSON 与企业微信 XML，自动验签解析、幂等去重、锁定身份并沉淀记录。"""
    from ...channels.gateway import channel_gateway

    raw_bytes = await request.body()
    raw_text = raw_bytes.decode("utf-8", errors="ignore").strip()
    payload: dict[str, Any] = {}
    if raw_text.startswith("<"):
        payload = {
            "_raw_xml": raw_text,
            "_query_params": dict(request.query_params),
        }
    else:
        try:
            import json as _json
            parsed = _json.loads(raw_text) if raw_text else {}
            payload = parsed if isinstance(parsed, dict) else {}
        except Exception:
            payload = {}

    # 飞书事件订阅 URL 校验握手：直接原样返回 challenge
    if channel_type == "feishu" and payload.get("type") == "url_verification" and payload.get("challenge"):
        return {"challenge": payload["challenge"]}

    try:
        res = await channel_gateway.handle_inbound(
            channel_type=channel_type,
            endpoint_token=endpoint_token,
            payload=payload,
            headers=dict(request.headers),
            mode=mode,
        )
        return {"success": True, "data": res}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"渠道消息处理失败: {exc}")


class CreateBindTicketRequest(BaseModel):
    """生成 IM 扫码自动绑定二维码请求参数。"""

    max_uses: int = Field(default=1, ge=0, le=1000, description="最大扫码绑定人数（1=一次性单人防转发，0=不限人数）")
    ttl_minutes: int = Field(default=60, ge=5, le=43200, description="二维码有效分钟数")


class ClaimBindTicketRequest(BaseModel):
    """扫码确认认领请求参数（供 H5 确认页、OAuth 回调、ClawBot Sidecar 统一调用）。"""

    external_user_id: str = Field(..., min_length=1, max_length=120, description="IM 客户端外部用户 ID")
    external_user_name: str = Field(default="", max_length=120, description="IM 客户端用户昵称")


@router.post("/links/{link_id}/qr-ticket")
async def create_link_qr_ticket(
    link_id: str, payload: CreateBindTicketRequest, request: Request
) -> dict[str, Any]:
    """为指定渠道链接生成高清离线扫码绑定二维码（模式 A 带参码 / 模式 B 个人微信 ClawBot 扫码）。"""
    from ...channels.qr_binding import create_bind_ticket

    async with db_manager.get_connection() as conn:
        await _ensure_dialogue_wiki_tables(conn)
    try:
        res = await create_bind_ticket(
            link_id=link_id,
            base_url=str(request.base_url),
            max_uses=payload.max_uses,
            ttl_minutes=payload.ttl_minutes,
        )
        return {"success": True, "data": res}
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))


@router.get("/links/{link_id}/bindings")
async def get_link_bindings(link_id: str) -> dict[str, Any]:
    """查看指定渠道链接下所有已扫码自动绑定的外部 IM 用户。"""
    from ...channels.qr_binding import list_link_bindings

    items = await list_link_bindings(link_id)
    return {"success": True, "data": items}


@router.delete("/links/{link_id}/bindings/{binding_id}")
async def delete_link_binding(link_id: str, binding_id: str) -> dict[str, Any]:
    """解绑单个已扫码绑定的外部 IM 用户。"""
    from ...channels.qr_binding import remove_link_binding

    await remove_link_binding(link_id, binding_id)
    return {"success": True, "message": "已解除该外部用户绑定"}


@router.post("/bind/{ticket}/claim")
async def claim_link_bind_ticket(ticket: str, payload: ClaimBindTicketRequest) -> dict[str, Any]:
    """扫码自动认领票据：将外部 IM 用户写入绑定表并自动带入专家、角色与 RLS 作用域。"""
    from ...channels.qr_binding import claim_bind_ticket

    try:
        res = await claim_bind_ticket(
            ticket=ticket,
            external_user_id=payload.external_user_id,
            external_user_name=payload.external_user_name,
        )
        return {"success": True, "data": res}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


@router.get("/bind/{ticket}")
async def render_qr_bind_landing_page(
    ticket: str,
    external_user_id: str = "",
    external_user_name: str = "",
) -> Any:
    """IM 客户端扫码落地页：若 URL 已携带外部用户 ID（OAuth/回调）则静默自动绑定，否则展示极简一键确认绑定页。"""
    from html import escape
    from fastapi.responses import HTMLResponse
    from ...channels.qr_binding import claim_bind_ticket

    if external_user_id.strip():
        try:
            res = await claim_bind_ticket(
                ticket=ticket,
                external_user_id=external_user_id.strip(),
                external_user_name=external_user_name.strip(),
            )
            msg_html = escape(res["welcome_message"])
            return HTMLResponse(
                f"<!doctype html><html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
                f"<body style='font-family:sans-serif;background:#0f172a;color:#e2e8f0;padding:32px;text-align:center'>"
                f"<div style='max-width:420px;margin:40px auto;background:#1e293b;padding:24px;border-radius:16px'>"
                f"<h2 style='color:#34d399;margin-top:0'>绑定成功</h2><p style='line-height:1.6'>{msg_html}</p></div></body></html>"
            )
        except ValueError as ve:
            return HTMLResponse(
                f"<!doctype html><html><meta charset='utf-8'><body style='font-family:sans-serif;background:#0f172a;color:#f87171;padding:32px;text-align:center'>"
                f"<h3>绑定失败</h3><p>{escape(str(ve))}</p></body></html>",
                status_code=400,
            )

    safe_ticket = escape(ticket)
    html_page = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AgentHub IM 扫码自动绑定</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #0f172a; color: #e2e8f0; margin: 0; padding: 24px; }}
    .card {{ max-width: 420px; margin: 36px auto; background: #1e293b; border: 1px solid #334155; border-radius: 16px; padding: 24px; }}
    h2 {{ margin: 0 0 8px; font-size: 18px; color: #f8fafc; }}
    p {{ font-size: 13px; color: #94a3b8; line-height: 1.5; }}
    input {{ width: 100%; box-sizing: border-box; padding: 10px 12px; margin: 8px 0 12px; border-radius: 10px; border: 1px solid #475569; background: #0f172a; color: #f8fafc; font-size: 14px; }}
    button {{ width: 100%; padding: 11px; border-radius: 10px; border: 0; background: #10b981; color: #fff; font-weight: 600; font-size: 14px; cursor: pointer; }}
    #msg {{ margin-top: 14px; font-size: 13px; line-height: 1.5; }}
  </style>
</head>
<body>
  <div class="card">
    <h2>扫码确认绑定数字专家</h2>
    <p>确认后将自动为您绑定对应的数字专家、数据权限角色与作用域，后续在 IM 中发消息即可直连回答。</p>
    <input id="uid" placeholder="请输入您的 IM 用户标识 / 手机号 / 工号" />
    <input id="uname" placeholder="您的称呼或单位名称（选填）" />
    <button onclick="doClaim()">确认自动绑定</button>
    <div id="msg"></div>
  </div>
  <script>
    async function doClaim() {{
      const uid = document.getElementById('uid').value.trim();
      const uname = document.getElementById('uname').value.trim();
      const msg = document.getElementById('msg');
      if (!uid) {{ msg.style.color = '#f87171'; msg.textContent = '请填写用户标识'; return; }}
      const res = await fetch('/api/v1/dialogue-wiki/bind/{safe_ticket}/claim', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ external_user_id: uid, external_user_name: uname }})
      }});
      const json = await res.json();
      if (res.ok && json.success) {{
        msg.style.color = '#34d399';
        msg.textContent = json.data.welcome_message;
      }} else {{
        msg.style.color = '#f87171';
        msg.textContent = json.detail || '绑定失败';
      }}
    }}
  </script>
</body>
</html>"""
    return HTMLResponse(content=html_page)

