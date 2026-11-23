"""
内置渠道适配器集合：导入即自动注册到 channel_registry。
"""
from .webhook_adapter import GenericWebhookAdapter
from .wecom_adapter import WeComChannelAdapter
from .weixin_clawbot import WeixinClawbotAdapter
from .feishu_adapter import FeishuChannelAdapter
from .dingtalk_adapter import DingTalkChannelAdapter
from ..registry import channel_registry

channel_registry.register(GenericWebhookAdapter())
channel_registry.register(WeComChannelAdapter())
channel_registry.register(WeixinClawbotAdapter())
channel_registry.register(FeishuChannelAdapter())
channel_registry.register(DingTalkChannelAdapter())

__all__ = [
    "GenericWebhookAdapter",
    "WeComChannelAdapter",
    "WeixinClawbotAdapter",
    "FeishuChannelAdapter",
    "DingTalkChannelAdapter",
]
