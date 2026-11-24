"""
个人微信 ClawBot（官方 iLink 扫码长连接 Sidecar）适配器（Phase 3）。
架构：
- 本地按需运行轻量 Node 桥接进程 `sidecars/weixin-clawbot-bridge/`（默认监听 http://127.0.0.1:18791）；
- 收到个人微信消息后，Sidecar 转发至 AgentHub 统一入站接口 `/api/v1/dialogue-wiki/ingress/weixin_clawbot/{token}`；
- 本适配器负责解析入站消息、调用 Sidecar `/send` 异步回推，并通过 `/status` 向前端暴露扫码二维码与在线心跳。
"""
from __future__ import annotations

import secrets
from typing import Any

import httpx

from ...core.logging_config import logger
from ..base import BaseChannelAdapter, InboundMessage, OutboundMessage

DEFAULT_CLAWBOT_BRIDGE_URL = "http://127.0.0.1:18791"


class WeixinClawbotAdapter(BaseChannelAdapter):
    """个人微信 ClawBot 扫码长连接适配器。"""

    channel_type = "weixin_clawbot"
    display_name = "个人微信 (ClawBot)"
    default_mode = "async_push"
    max_chunk_chars = 1400

    async def parse_inbound(
        self,
        *,
        endpoint_token: str,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
        link_config: dict[str, Any] | None = None,
    ) -> InboundMessage:
        text = str(payload.get("text") or payload.get("message") or payload.get("content") or "").strip()
        attachments = payload.get("attachments") or []
        if not text and not attachments:
            raise ValueError("微信入站消息文本与媒体附件不能同时为空")
        if not text and attachments:
            kinds = {str(a.get("kind") or "") for a in attachments if isinstance(a, dict)}
            if "voice" in kinds:
                text = "[收到语音消息]"
            elif "image" in kinds:
                text = "[收到图片消息]"
            else:
                text = "[收到媒体附件]"

        sender_id = str(payload.get("sender_id") or payload.get("from_wxid") or "wx_peer").strip()
        sender_name = str(
            payload.get("sender_name") or (link_config or {}).get("target_user_label") or sender_id
        ).strip()
        chat_id = str(payload.get("chat_id") or payload.get("session_key") or sender_id).strip()
        external_msg_id = str(
            payload.get("external_msg_id") or payload.get("msg_id") or f"wx_{secrets.token_hex(8)}"
        ).strip()

        return InboundMessage(
            channel_type=self.channel_type,
            endpoint_token=endpoint_token,
            external_msg_id=external_msg_id,
            sender_id=sender_id,
            sender_name=sender_name,
            chat_id=chat_id,
            text=text,
            reply_context={
                "to_wxid": sender_id,
                "chat_id": chat_id,
                "session_key": payload.get("session_key") or chat_id,
            },
            raw_payload=payload,
        )

    async def send_reply(
        self,
        *,
        inbound: InboundMessage,
        outbound: OutboundMessage,
        link_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cfg = (link_config or {}).get("channel_config") or {}
        bridge_url = str(cfg.get("bridge_url") or DEFAULT_CLAWBOT_BRIDGE_URL).rstrip("/")
        bridge_secret = str(cfg.get("bridge_secret") or "").strip()
        req_headers = {"X-Bridge-Secret": bridge_secret} if bridge_secret else {}

        files_payload = [
            {"file_path": str(att["file_path"]), "file_name": str(att.get("file_name") or "")}
            for att in (outbound.attachments or [])
            if isinstance(att, dict) and att.get("file_path")
        ]

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{bridge_url}/send",
                    headers=req_headers,
                    json={
                        "endpoint_token": inbound.endpoint_token,
                        "to_wxid": inbound.reply_context.get("to_wxid") or inbound.sender_id,
                        "chat_id": inbound.reply_context.get("chat_id") or inbound.chat_id,
                        "chunks": outbound.chunks or [outbound.formatted_text],
                        "files": files_payload,
                    },
                )
                return {"pushed": resp.status_code < 400, "status_code": resp.status_code}
        except Exception as err:
            logger.debug(f"[WeixinClawbot] 本地 Sidecar 未运行或回推失败 ({bridge_url}): {err}")
            return {"pushed": False, "error": f"本地 ClawBot Sidecar 不可达 ({bridge_url})"}

    async def send_typing(
        self,
        *,
        inbound: InboundMessage,
        is_typing: bool = True,
        link_config: dict[str, Any] | None = None,
    ) -> None:
        cfg = (link_config or {}).get("channel_config") or {}
        bridge_url = str(cfg.get("bridge_url") or DEFAULT_CLAWBOT_BRIDGE_URL).rstrip("/")
        bridge_secret = str(cfg.get("bridge_secret") or "").strip()
        req_headers = {"X-Bridge-Secret": bridge_secret} if bridge_secret else {}
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                await client.post(
                    f"{bridge_url}/typing",
                    headers=req_headers,
                    json={
                        "to_wxid": inbound.reply_context.get("to_wxid") or inbound.sender_id,
                        "status": 1 if is_typing else 2,
                    },
                )
        except Exception:
            pass

    async def get_connection_status(
        self, link_config: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        cfg = (link_config or {}).get("channel_config") or {}
        bridge_url = str(cfg.get("bridge_url") or DEFAULT_CLAWBOT_BRIDGE_URL).rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                resp = await client.get(f"{bridge_url}/status")
                if resp.status_code == 200:
                    data = resp.json()
                    return {
                        "channel_type": self.channel_type,
                        "bridge_url": bridge_url,
                        "bridge_online": True,
                        "connected": bool(data.get("logged_in")),
                        "qr_status": data.get("qr_status") or "idle",
                        "qr_url": data.get("qr_url") or "",
                        "qr_ascii": data.get("qr_ascii") or "",
                        "account_label": data.get("account_label") or "",
                    }
        except Exception:
            pass
        return {
            "channel_type": self.channel_type,
            "bridge_url": bridge_url,
            "bridge_online": False,
            "connected": False,
            "hint": "请先启动本地 sidecars/weixin-clawbot-bridge（node index.mjs）以获取微信 ClawBot 扫码二维码。",
        }

