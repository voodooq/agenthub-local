"""
多渠道 IM 统一标准消息契约与适配器抽象基类。
新增任意 IM（个人微信 ClawBot、企业微信、飞书、钉钉、Web Widget 等）只需继承 BaseChannelAdapter 并注册即可。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class InboundMessage:
    """所有 IM 渠道入站标准化消息对象。"""

    channel_type: str
    endpoint_token: str
    external_msg_id: str
    sender_id: str
    sender_name: str
    chat_id: str
    text: str
    attachments: list[dict[str, Any]] = field(default_factory=list)
    reply_context: dict[str, Any] = field(default_factory=dict)
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class OutboundMessage:
    """所有 IM 渠道出站标准化回复对象。"""

    raw_markdown: str
    formatted_text: str
    chunks: list[str] = field(default_factory=list)
    attachments: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseChannelAdapter(ABC):
    """渠道插件抽象基类：屏蔽不同 IM 的验签、加解密、排版与回推差异。"""

    channel_type: str = ""
    display_name: str = ""
    default_mode: str = "async_push"  # async_push | sync_reply
    max_chunk_chars: int = 1500

    @abstractmethod
    async def parse_inbound(
        self,
        *,
        endpoint_token: str,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
        link_config: dict[str, Any] | None = None,
    ) -> InboundMessage:
        """校验并解析外部渠道入站请求为标准 InboundMessage。"""

    @abstractmethod
    async def send_reply(
        self,
        *,
        inbound: InboundMessage,
        outbound: OutboundMessage,
        link_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """将格式化后的回复通过该渠道的主动发送/回调接口回推给用户。"""

    async def send_typing(
        self,
        *,
        inbound: InboundMessage,
        is_typing: bool = True,
        link_config: dict[str, Any] | None = None,
    ) -> None:
        """可选：向支持“对方正在输入中...”的 IM 客户端推送或取消输入状态。"""
        return None

    async def get_connection_status(
        self, link_config: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """可选：返回长连/扫码型渠道（如个人微信 ClawBot）的在线状态或二维码信息。"""
        return {"channel_type": self.channel_type, "connected": True, "mode": self.default_mode}

