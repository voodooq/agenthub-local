"""
AgentHub 统一多渠道 IM 接入插件底座（Channel Gateway Architecture）。
"""
from .base import BaseChannelAdapter, InboundMessage, OutboundMessage
from .registry import channel_registry
from .gateway import channel_gateway

__all__ = [
    "BaseChannelAdapter",
    "InboundMessage",
    "OutboundMessage",
    "channel_registry",
    "channel_gateway",
]

