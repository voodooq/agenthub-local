"""
IM 客户端扫码自动绑定与带入引擎（同时覆盖模式 A 外部带参码认领 + 模式 B 个人微信 ClawBot 扫码直连）。
核心能力：
1. 生成本地离线高清 SVG 二维码（segno，零外部图床依赖）；
2. 管理 dialogue_bind_tickets（支持一次性单人防转发 max_uses=1 或长期多人通用 max_uses=0 + TTL 过期控制）；
3. 扫码认领后自动写入 dialogue_link_bindings 并回填 dialogue_channel_links.target_user_id，自动带入专家、角色与 RLS 作用域。
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import segno

from ..core.logging_config import logger
from ..repository.db import db_manager
from ..schema.access_control import ROLE_LABELS, parse_scope_input

_CLAWBOT_SIDECAR_PROC: subprocess.Popen[Any] | None = None


async def _is_bridge_alive(bridge_url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            r = await client.get(f"{bridge_url.rstrip('/')}/status")
            return r.status_code == 200
    except Exception:
        return False


async def ensure_clawbot_sidecar_running(
    bridge_url: str = "http://127.0.0.1:18791",
) -> tuple[bool, str]:
    """检测本地微信 ClawBot Sidecar 是否在线；未启动时自动在后台静默拉起，免去用户手动敲命令。"""
    global _CLAWBOT_SIDECAR_PROC

    clean_url = bridge_url.rstrip("/")
    if await _is_bridge_alive(clean_url):
        return True, "本地微信 ClawBot 桥接服务已在运行"

    # 仅对本机 127.0.0.1 / localhost 自动拉起
    if "127.0.0.1" not in clean_url and "localhost" not in clean_url:
        return False, f"远端桥接地址 {clean_url} 未就绪"

    node_bin = shutil.which("node")
    if not node_bin:
        return False, "本机未检测到 Node.js 环境，无法自动启动微信桥接服务"

    # 兼容本地开发目录 (parents[3]/sidecars) 与 Docker 容器挂载目录 (/app/sidecars)
    resolved_here = Path(__file__).resolve()
    candidate_roots = [
        resolved_here.parents[3],
        resolved_here.parents[2],
        Path("/app"),
    ]
    script_path = next(
        (
            root / "sidecars" / "weixin-clawbot-bridge" / "index.mjs"
            for root in candidate_roots
            if (root / "sidecars" / "weixin-clawbot-bridge" / "index.mjs").exists()
        ),
        resolved_here.parents[3] / "sidecars" / "weixin-clawbot-bridge" / "index.mjs",
    )
    if not script_path.exists():
        return False, f"未找到桥接脚本: {script_path}"

    try:
        if _CLAWBOT_SIDECAR_PROC is None or _CLAWBOT_SIDECAR_PROC.poll() is not None:
            env = os.environ.copy()
            creationflags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
            _CLAWBOT_SIDECAR_PROC = subprocess.Popen(
                [node_bin, str(script_path)],
                cwd=str(script_path.parent),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
            logger.info(f"[WeixinClawbot] 已自动在后台静默拉起本地 Sidecar (PID={_CLAWBOT_SIDECAR_PROC.pid})")
    except Exception as spawn_err:
        return False, f"自动启动本地微信桥接失败: {spawn_err}"

    # 轮询等待端口就绪（最多 2.5 秒）
    for _ in range(10):
        await asyncio.sleep(0.25)
        if await _is_bridge_alive(clean_url):
            return True, "已自动拉起本地微信 ClawBot 桥接服务"

    return False, "已尝试启动本地微信桥接，但端口尚未就绪，请稍后点击刷新二维码"


async def ensure_qr_binding_tables(conn: Any) -> None:
    """幂等初始化扫码绑定票据表与已绑定外部用户关系表。"""
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dialogue_bind_tickets (
            ticket TEXT PRIMARY KEY,
            link_id TEXT NOT NULL,
            channel_type TEXT NOT NULL,
            bind_mode TEXT NOT NULL DEFAULT 'external_claim',
            max_uses INTEGER NOT NULL DEFAULT 1,
            used_count INTEGER NOT NULL DEFAULT 0,
            expires_at REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at REAL NOT NULL
        )
        """
    )
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dialogue_link_bindings (
            binding_id TEXT PRIMARY KEY,
            link_id TEXT NOT NULL,
            channel_type TEXT NOT NULL,
            external_user_id TEXT NOT NULL,
            external_user_name TEXT NOT NULL DEFAULT '',
            ticket TEXT NOT NULL DEFAULT '',
            bound_at REAL NOT NULL,
            UNIQUE(link_id, channel_type, external_user_id)
        )
        """
    )
    await conn.commit()


def make_qr_svg_data_uri(payload_text: str) -> str:
    """离线本地生成高清 SVG 二维码 Data URI（可直接作为 <img src> 与下载文件）。"""
    qr = segno.make(payload_text, error="m")
    return qr.svg_data_uri(scale=6, border=2, dark="#111827", light="#ffffff")


async def create_bind_ticket(
    *,
    link_id: str,
    base_url: str,
    max_uses: int = 1,
    ttl_minutes: int = 60,
) -> dict[str, Any]:
    """为指定渠道链接生成带参扫码绑定票据与离线高清二维码（模式 A / 模式 B 统一入口）。"""
    async with db_manager.get_connection() as conn:
        await ensure_qr_binding_tables(conn)
        cur = await conn.execute(
            "SELECT * FROM dialogue_channel_links WHERE link_id = ? AND status = 'active' LIMIT 1",
            (link_id,),
        )
        row = await cur.fetchone()
        if not row:
            raise ValueError("未找到对应的有效渠道对接链接")
        link = dict(row)

        channel_type = str(link["channel_type"])
        bind_mode = "clawbot_login" if channel_type == "weixin_clawbot" else "external_claim"
        ticket = f"btk_{secrets.token_hex(10)}"
        now_ts = time.time()
        safe_ttl = max(5, min(int(ttl_minutes), 60 * 24 * 30))
        expires_at = now_ts + safe_ttl * 60

        await conn.execute(
            """
            INSERT INTO dialogue_bind_tickets (
                ticket, link_id, channel_type, bind_mode,
                max_uses, used_count, expires_at, status, created_at
            ) VALUES (?, ?, ?, ?, ?, 0, ?, 'active', ?)
            """,
            (ticket, link_id, channel_type, bind_mode, max(0, int(max_uses)), expires_at, now_ts),
        )
        await conn.commit()

    clean_base = base_url.rstrip("/")
    bind_url = f"{clean_base}/api/v1/dialogue-wiki/bind/{ticket}"
    qr_target_url = bind_url
    clawbot_bridge_info: dict[str, Any] = {}

    # 模式 B：个人微信 ClawBot 生成二维码时自动拉起本地 Sidecar（免去用户手动开终端）
    if bind_mode == "clawbot_login":
        try:
            cfg = json.loads(link.get("channel_config_json") or "{}")
        except Exception:
            cfg = {}
        bridge_url = str(cfg.get("bridge_url") or "http://127.0.0.1:18791").rstrip("/")
        is_ready, ready_msg = await ensure_clawbot_sidecar_running(bridge_url)
        if is_ready:
            try:
                async with httpx.AsyncClient(timeout=4.0) as client:
                    from ..core.config import settings as _app_settings
                    resp = await client.post(
                        f"{bridge_url}/qr/start",
                        json={
                            "link_id": link_id,
                            "ticket": ticket,
                            "endpoint_token": link["endpoint_token"],
                            "access_token": _app_settings.access_token or "",
                            "claim_url": f"{clean_base}/api/v1/dialogue-wiki/bind/{ticket}/claim",
                            "ingress_url": f"{clean_base}/api/v1/dialogue-wiki/ingress/weixin_clawbot/{link['endpoint_token']}",
                        },
                    )
                    if resp.status_code == 200:
                        clawbot_bridge_info = resp.json()
                        clawbot_bridge_info["auto_started_notice"] = ready_msg
                        if clawbot_bridge_info.get("qr_url"):
                            qr_target_url = str(clawbot_bridge_info["qr_url"])
            except Exception as err:
                clawbot_bridge_info = {
                    "bridge_online": False,
                    "hint": f"微信桥接服务请求异常: {err}",
                }
        else:
            clawbot_bridge_info = {
                "bridge_online": False,
                "hint": ready_msg,
            }

    return {
        "ticket": ticket,
        "link_id": link_id,
        "channel_type": channel_type,
        "bind_mode": bind_mode,
        "bind_url": bind_url,
        "qr_target_url": qr_target_url,
        "qr_svg_data_uri": make_qr_svg_data_uri(qr_target_url),
        "state_param": ticket,
        "max_uses": max(0, int(max_uses)),
        "expires_at": expires_at,
        "expert_name": link.get("expert_name") or link.get("expert_id"),
        "bound_role": link.get("bound_role") or "guest",
        "bound_role_label": ROLE_LABELS.get(str(link.get("bound_role") or "guest"), "访客"),
        "bound_scope": parse_scope_input(link.get("bound_scope_json")),
        "clawbot_bridge": clawbot_bridge_info,
    }


async def claim_bind_ticket(
    *,
    ticket: str,
    external_user_id: str,
    external_user_name: str = "",
) -> dict[str, Any]:
    """扫码认领绑定票据：校验时效与防转发次数上限，写入绑定表并自动带入专家、角色与 RLS。"""
    ext_uid = (external_user_id or "").strip()
    if not ext_uid:
        raise ValueError("缺少外部 IM 用户标识 (external_user_id)")
    ext_name = (external_user_name or ext_uid).strip()

    now_ts = time.time()
    async with db_manager.get_connection() as conn:
        await ensure_qr_binding_tables(conn)
        cur = await conn.execute(
            "SELECT * FROM dialogue_bind_tickets WHERE ticket = ? LIMIT 1",
            (ticket.strip(),),
        )
        t_row = await cur.fetchone()
        if not t_row:
            raise ValueError("无效的扫码绑定票据")
        t_data = dict(t_row)

        if t_data["status"] != "active":
            raise ValueError("该绑定二维码已被使用或已作废，请重新生成")
        if float(t_data["expires_at"]) < now_ts:
            await conn.execute(
                "UPDATE dialogue_bind_tickets SET status = 'expired' WHERE ticket = ?",
                (ticket,),
            )
            await conn.commit()
            raise ValueError("该绑定二维码已过期，请重新生成")

        max_uses = int(t_data.get("max_uses") or 0)
        used_count = int(t_data.get("used_count") or 0)
        if max_uses > 0 and used_count >= max_uses:
            raise ValueError("该一次性绑定二维码已被他人使用（已触发防转发保护）")

        l_cur = await conn.execute(
            "SELECT * FROM dialogue_channel_links WHERE link_id = ? LIMIT 1",
            (t_data["link_id"],),
        )
        l_row = await l_cur.fetchone()
        if not l_row:
            raise ValueError("关联的渠道对接链接不存在")
        link = dict(l_row)

        binding_id = f"dbind_{secrets.token_hex(6)}"
        await conn.execute(
            """
            INSERT INTO dialogue_link_bindings (
                binding_id, link_id, channel_type, external_user_id, external_user_name, ticket, bound_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(link_id, channel_type, external_user_id) DO UPDATE SET
                external_user_name = excluded.external_user_name,
                ticket = excluded.ticket,
                bound_at = excluded.bound_at
            """,
            (binding_id, link["link_id"], link["channel_type"], ext_uid, ext_name, ticket, now_ts),
        )

        # 若主链接尚未填写 target_user_id，自动带入首个扫码认领的用户 ID
        if not str(link.get("target_user_id") or "").strip():
            await conn.execute(
                "UPDATE dialogue_channel_links SET target_user_id = ? WHERE link_id = ?",
                (ext_uid, link["link_id"]),
            )

        new_used = used_count + 1
        new_status = "consumed" if (max_uses > 0 and new_used >= max_uses) else "active"
        await conn.execute(
            "UPDATE dialogue_bind_tickets SET used_count = ?, status = ? WHERE ticket = ?",
            (new_used, new_status, ticket),
        )
        await conn.commit()

    role = str(link.get("bound_role") or "guest")
    role_label = ROLE_LABELS.get(role, role)
    scope = parse_scope_input(link.get("bound_scope_json"))
    scope_desc = ", ".join(f"{k}={v}" for k, v in scope.items()) or "无额外限定"
    welcome_message = (
        f"✅ 绑定成功！您已接入【{link.get('expert_name') or link['expert_id']}】"
        f"（锁定权限：{role_label} | 作用域：{scope_desc}），现在可以直接发消息提问。"
    )
    return {
        "binding_id": binding_id,
        "link_id": link["link_id"],
        "expert_id": link["expert_id"],
        "expert_name": link.get("expert_name") or link["expert_id"],
        "channel_type": link["channel_type"],
        "external_user_id": ext_uid,
        "external_user_name": ext_name,
        "bound_role": role,
        "bound_role_label": role_label,
        "bound_scope": scope,
        "ticket_status": new_status,
        "welcome_message": welcome_message,
    }


async def list_link_bindings(link_id: str) -> list[dict[str, Any]]:
    """列出指定渠道链接下所有已通过扫码自动绑定的外部 IM 用户。"""
    async with db_manager.get_connection() as conn:
        await ensure_qr_binding_tables(conn)
        cur = await conn.execute(
            """
            SELECT * FROM dialogue_link_bindings
            WHERE link_id = ?
            ORDER BY bound_at DESC
            """,
            (link_id,),
        )
        return [dict(r) for r in await cur.fetchall()]


async def remove_link_binding(link_id: str, binding_id: str) -> None:
    """解除单个已扫码绑定的外部 IM 用户。"""
    async with db_manager.get_connection() as conn:
        await ensure_qr_binding_tables(conn)
        await conn.execute(
            "DELETE FROM dialogue_link_bindings WHERE link_id = ? AND binding_id = ?",
            (link_id, binding_id),
        )
        await conn.commit()

