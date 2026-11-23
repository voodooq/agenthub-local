"""
钉钉机器人官方渠道适配器（Phase 4）。
支持：
1. 钉钉回调 HMAC-SHA256 签名校验（timestamp + "\\n" + app_secret）；
2. 标准钉钉群/单聊机器人入站解析（text.content / sessionWebhook）与 JSON 联调兼容；
3. 通过 sessionWebhook 异步逐片回推文本回复。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from typing import Any

import httpx

from ...core.logging_config import logger
from ..base import BaseChannelAdapter, InboundMessage, OutboundMessage


def compute_dingtalk_signature(timestamp: str, app_secret: str) -> str:
    """计算钉钉官方回调 HMAC-SHA256 Base64 签名。"""
    string_to_sign = f"{timestamp}\n{app_secret}".encode("utf-8")
    digest = hmac.new(app_secret.encode("utf-8"), string_to_sign, digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


class DingTalkChannelAdapter(BaseChannelAdapter):
    """钉钉机器人适配器。"""

    channel_type = "dingtalk"
    display_name = "钉钉机器人"
    default_mode = "async_push"
    max_chunk_chars = 1500

    async def parse_inbound(
        self,
        *,
        endpoint_token: str,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
        link_config: dict[str, Any] | None = None,
    ) -> InboundMessage:
        cfg = (link_config or {}).get("channel_config") or {}
        app_secret = str(cfg.get("app_secret") or "").strip()
        hdrs = {k.lower(): v for k, v in (headers or {}).items()}
        if app_secret and hdrs.get("timestamp") and hdrs.get("sign"):
            expected = compute_dingtalk_signature(hdrs["timestamp"], app_secret)
            if not hmac.compare_digest(expected, hdrs["sign"]):
                raise ValueError("钉钉回调 HMAC-SHA256 签名校验失败")

        text_field = payload.get("text")
        if isinstance(text_field, dict):
            text = str(text_field.get("content") or "").strip()
        else:
            text = str(text_field or payload.get("message") or payload.get("content") or "").strip()
        if not text:
            raise ValueError("钉钉入站消息内容不能为空")

        sender_id = str(
            payload.get("senderStaffId")
            or payload.get("senderId")
            or payload.get("sender_id")
            or (link_config or {}).get("target_user_id")
            or "dingtalk_user"
        ).strip()
        sender_name = str(
            payload.get("senderNick")
            or payload.get("sender_name")
            or (link_config or {}).get("target_user_label")
            or sender_id
        ).strip()
        chat_id = str(payload.get("conversationId") or payload.get("chat_id") or sender_id).strip()
        msg_id = str(
            payload.get("msgId") or payload.get("external_msg_id") or f"dt_{secrets.token_hex(8)}"
        ).strip()

        return InboundMessage(
            channel_type=self.channel_type,
            endpoint_token=endpoint_token,
            external_msg_id=msg_id,
            sender_id=sender_id,
            sender_name=sender_name,
            chat_id=chat_id,
            text=text,
            reply_context={"session_webhook": str(payload.get("sessionWebhook") or "").strip()},
            raw_payload=payload,
        )

    async def send_reply(
        self,
        *,
        inbound: InboundMessage,
        outbound: OutboundMessage,
        link_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        session_webhook = str(inbound.reply_context.get("session_webhook") or "").strip()
        if not session_webhook:
            return {"pushed": False, "mode": "inline_response"}

        try:
            sent_chunks = 0
            async with httpx.AsyncClient(timeout=15.0) as client:
                for chunk in (outbound.chunks or [outbound.formatted_text]):
                    if not chunk:
                        continue
                    r = await client.post(
                        session_webhook,
                        json={"msgtype": "text", "text": {"content": chunk}},
                    )
                    if r.status_code < 400:
                        sent_chunks += 1
            return {"pushed": sent_chunks > 0, "sent_chunks": sent_chunks}
        except Exception as err:
            logger.warning(f"[DingTalkAdapter] sessionWebhook 回推异常: {err}")
            return {"pushed": False, "error": str(err)}

    async def get_connection_status(
        self, link_config: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        cfg = (link_config or {}).get("channel_config") or {}
        return {
            "channel_type": self.channel_type,
            "connected": bool(cfg.get("app_secret")),
            "mode": self.default_mode,
        }

