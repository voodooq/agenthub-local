"""
通用 HTTP Webhook / 网页嵌入 / 在线模拟 IM 渠道适配器。
支持：
1. 标准 JSON 入站 payload（text / message / content, sender_id, sender_name, msg_id, callback_url）；
2. 自动剥离群聊中的 @机器人 前缀；
3. 若提供 callback_url 且处于 async_push 模式，则通过 HTTP POST 主动回推；否则供 sync_reply 直接返回。
"""
from __future__ import annotations

import re
import secrets
from typing import Any

import httpx

from ...core.logging_config import logger
from ..base import BaseChannelAdapter, InboundMessage, OutboundMessage

_AT_MENTION_RE = re.compile(r"^@\S+\s+")


class GenericWebhookAdapter(BaseChannelAdapter):
    """通用 Webhook / H5 / 外部系统集成适配器。"""

    channel_type = "webhook"
    display_name = "通用 Webhook / 自定义 IM"
    default_mode = "sync_reply"
    max_chunk_chars = 1500

    async def parse_inbound(
        self,
        *,
        endpoint_token: str,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
        link_config: dict[str, Any] | None = None,
    ) -> InboundMessage:
        raw_text = str(
            payload.get("text")
            or payload.get("message")
            or payload.get("content")
            or ""
        ).strip()
        clean_text = _AT_MENTION_RE.sub("", raw_text).strip()
        if not clean_text:
            raise ValueError("入站消息内容不能为空（需提供 text / message / content）")

        sender_id = str(
            payload.get("sender_id")
            or payload.get("user_id")
            or (link_config or {}).get("target_user_id")
            or "im_user"
        ).strip()
        sender_name = str(
            payload.get("sender_name")
            or payload.get("user_name")
            or (link_config or {}).get("target_user_label")
            or sender_id
        ).strip()
        chat_id = str(payload.get("chat_id") or sender_id).strip()
        external_msg_id = str(
            payload.get("external_msg_id")
            or payload.get("msg_id")
            or f"wh_{secrets.token_hex(8)}"
        ).strip()

        reply_ctx: dict[str, Any] = {}
        if payload.get("callback_url"):
            reply_ctx["callback_url"] = str(payload["callback_url"]).strip()

        return InboundMessage(
            channel_type=str(payload.get("channel_type") or (link_config or {}).get("channel_type") or self.channel_type),
            endpoint_token=endpoint_token,
            external_msg_id=external_msg_id,
            sender_id=sender_id,
            sender_name=sender_name,
            chat_id=chat_id,
            text=clean_text,
            attachments=payload.get("attachments") if isinstance(payload.get("attachments"), list) else [],
            reply_context=reply_ctx,
            raw_payload=payload,
        )

    async def send_reply(
        self,
        *,
        inbound: InboundMessage,
        outbound: OutboundMessage,
        link_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        callback_url = str(inbound.reply_context.get("callback_url") or "").strip()
        if not callback_url:
            return {"pushed": False, "mode": "inline_response"}

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    callback_url,
                    json={
                        "external_msg_id": inbound.external_msg_id,
                        "sender_id": inbound.sender_id,
                        "chat_id": inbound.chat_id,
                        "text": outbound.formatted_text,
                        "chunks": outbound.chunks,
                    },
                )
            return {"pushed": resp.status_code < 400, "status_code": resp.status_code}
        except Exception as err:
            logger.warning(f"[ChannelWebhook] 回调回推失败 ({callback_url}): {err}")
            return {"pushed": False, "error": str(err)}

