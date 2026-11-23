"""
多渠道 IM 适配器注册中心（ChannelRegistry）。
支持按 channel_type 动态查找适配器，未专门注册的外部 Webhook 渠道自动回退至通用 Webhook 适配器。
"""
from __future__ import annotations

from typing import Any

from .base import BaseChannelAdapter


class ChannelRegistry:
    """插件化渠道适配器注册表。"""

    def __init__(self) -> None:
        self._adapters: dict[str, BaseChannelAdapter] = {}

    def register(self, adapter: BaseChannelAdapter) -> None:
        """注册一个渠道适配器实例。"""
        key = (adapter.channel_type or "").strip().lower()
        if not key:
            raise ValueError("适配器 channel_type 不能为空")
        self._adapters[key] = adapter

    def get(self, channel_type: str) -> BaseChannelAdapter | None:
        """按渠道名获取已注册适配器；若未单独注册则回退至通用 webhook 适配器。"""
        key = (channel_type or "").strip().lower()
        return self._adapters.get(key) or self._adapters.get("webhook")

    def list_adapters(self) -> list[dict[str, Any]]:
        """列出所有已注册渠道适配器元信息。"""
        return [
            {
                "channel_type": ad.channel_type,
                "display_name": ad.display_name,
                "default_mode": ad.default_mode,
                "max_chunk_chars": ad.max_chunk_chars,
            }
            for ad in self._adapters.values()
        ]


channel_registry = ChannelRegistry()

