"""
飞书机器人官方渠道适配器（Phase 4）。
支持：
1. 飞书事件订阅 URL 校验（type = url_verification -> 返回 challenge）；
2. im.message.receive_v1 事件解析与 JSON 联调兼容；
3. 自动缓存 tenant_access_token 并异步逐片回推消息。
"""
from __future__ import annotations

import json
import re
import secrets
import time
from typing import Any

import httpx

from ...core.logging_config import logger
from ..base import BaseChannelAdapter, InboundMessage, OutboundMessage

_FEISHU_TOKEN_CACHE: dict[tuple[str, str], tuple[str, float]] = {}
_FEISHU_AT_RE = re.compile(r"@_user_\d+\s*")


class FeishuChannelAdapter(BaseChannelAdapter):
    """飞书应用机器人适配器。"""

    channel_type = "feishu"
    display_name = "飞书机器人"
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
        # 兼容飞书事件 v2.0 结构
        event = payload.get("event") if isinstance(payload.get("event"), dict) else None
        if event and isinstance(event.get("message"), dict):
            msg = event["message"]
            raw_content = msg.get("content") or ""
            text = ""
            if isinstance(raw_content, str) and raw_content.strip().startswith("{"):
                try:
                    c_obj = json.loads(raw_content)
                    text = str(c_obj.get("text") or "")
                except Exception:
                    text = raw_content
            else:
                text = str(raw_content)
            text = _FEISHU_AT_RE.sub("", text).strip()
            if not text:
                raise ValueError("飞书入站消息内容为空")

            sender = event.get("sender") or {}
            sender_ids = sender.get("sender_id") or {}
            sender_id = str(
                sender_ids.get("open_id") or sender_ids.get("user_id") or "feishu_user"
            ).strip()
            chat_id = str(msg.get("chat_id") or sender_id).strip()
            msg_id = str(msg.get("message_id") or f"fs_{secrets.token_hex(8)}").strip()
            return InboundMessage(
                channel_type=self.channel_type,
                endpoint_token=endpoint_token,
                external_msg_id=msg_id,
                sender_id=sender_id,
                sender_name=sender_id,
                chat_id=chat_id,
                text=text,
                reply_context={"receive_id": chat_id, "receive_id_type": "chat_id"},
                raw_payload=payload,
            )

        # 兼容模拟测试 JSON 结构
        text = str(payload.get("text") or payload.get("message") or payload.get("content") or "").strip()
        if not text:
            raise ValueError("飞书入站消息内容不能为空")
        sender_id = str(payload.get("sender_id") or (link_config or {}).get("target_user_id") or "feishu_user").strip()
        sender_name = str(payload.get("sender_name") or (link_config or {}).get("target_user_label") or sender_id).strip()
        msg_id = str(payload.get("external_msg_id") or payload.get("msg_id") or f"fs_{secrets.token_hex(8)}").strip()
        chat_id = str(payload.get("chat_id") or sender_id).strip()
        return InboundMessage(
            channel_type=self.channel_type,
            endpoint_token=endpoint_token,
            external_msg_id=msg_id,
            sender_id=sender_id,
            sender_name=sender_name,
            chat_id=chat_id,
            text=text,
            reply_context={"receive_id": chat_id, "receive_id_type": "open_id"},
            raw_payload=payload,
        )

    async def _get_tenant_token(self, app_id: str, app_secret: str) -> str:
        cache_key = (app_id, app_secret)
        cached = _FEISHU_TOKEN_CACHE.get(cache_key)
        now = time.time()
        if cached and cached[1] > now + 60:
            return cached[0]

        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.post(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": app_id, "app_secret": app_secret},
            )
            data = resp.json()
            if data.get("code") != 0 or not data.get("tenant_access_token"):
                raise ValueError(f"获取飞书 tenant_access_token 失败: {data.get('msg')}")
            token = str(data["tenant_access_token"])
            expire = int(data.get("expire") or 7200)
            _FEISHU_TOKEN_CACHE[cache_key] = (token, now + expire)
            return token

    async def send_reply(
        self,
        *,
        inbound: InboundMessage,
        outbound: OutboundMessage,
        link_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cfg = (link_config or {}).get("channel_config") or {}
        app_id = str(cfg.get("app_id") or "").strip()
        app_secret = str(cfg.get("app_secret") or "").strip()
        receive_id = str(inbound.reply_context.get("receive_id") or inbound.chat_id).strip()
        receive_id_type = str(inbound.reply_context.get("receive_id_type") or "chat_id").strip()

        if not app_id or not app_secret or not receive_id:
            return {"pushed": False, "mode": "unconfigured_inline_only"}

        try:
            tenant_token = await self._get_tenant_token(app_id, app_secret)
            sent_chunks = 0
            async with httpx.AsyncClient(timeout=15.0) as client:
                for chunk in (outbound.chunks or [outbound.formatted_text]):
                    if not chunk:
                        continue
                    r = await client.post(
                        "https://open.feishu.cn/open-apis/im/v1/messages",
                        params={"receive_id_type": receive_id_type},
                        headers={"Authorization": f"Bearer {tenant_token}"},
                        json={
                            "receive_id": receive_id,
                            "msg_type": "text",
                            "content": json.dumps({"text": chunk}, ensure_ascii=False),
                        },
                    )
                    if r.json().get("code") == 0:
                        sent_chunks += 1
            return {"pushed": sent_chunks > 0, "sent_chunks": sent_chunks}
        except Exception as err:
            logger.warning(f"[FeishuAdapter] 主动回推异常: {err}")
            return {"pushed": False, "error": str(err)}

    async def get_connection_status(
        self, link_config: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        cfg = (link_config or {}).get("channel_config") or {}
        return {
            "channel_type": self.channel_type,
            "connected": bool(cfg.get("app_id") and cfg.get("app_secret")),
            "mode": self.default_mode,
        }

