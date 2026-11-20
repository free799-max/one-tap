"""通知渠道工厂。"""

from __future__ import annotations

from app.models.notification import NotificationChannel
from app.services.notifications.channels.base import (
    BaseChannel,
    ChannelError,
    ChannelMessage,
)
from app.services.notifications.channels.dingtalk_app import DingTalkAppChannel
from app.services.notifications.channels.feishu import FeishuChannel
from app.services.notifications.channels.qqbot import QQBotChannel
from app.services.notifications.channels.wechatclawbot import WeChatClawBotChannel
from app.services.notifications.crypto import decrypt_secrets

CHANNEL_TYPES = {
    "feishu": FeishuChannel,
    "dingtalk_app": DingTalkAppChannel,
    "wechatclawbot": WeChatClawBotChannel,
    "qqbot": QQBotChannel,
}

CHANNEL_LABELS = {
    "feishu": "飞书",
    "dingtalk_app": "钉钉应用机器人",
    "wechatclawbot": "微信 ClawBot",
    "qqbot": "QQ Bot",
}


def build_channel(record: NotificationChannel) -> BaseChannel:
    channel_class = CHANNEL_TYPES.get(record.type)
    if channel_class is None:
        raise ChannelError(f"不支持的通知渠道类型：{record.type}")
    return channel_class(
        record.name,
        dict(record.config or {}),
        decrypt_secrets(record.secrets),
    )


__all__ = [
    "BaseChannel",
    "CHANNEL_LABELS",
    "CHANNEL_TYPES",
    "ChannelError",
    "ChannelMessage",
    "build_channel",
]
