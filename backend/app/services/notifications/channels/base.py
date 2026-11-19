"""通知渠道基类与消息结构。"""

from __future__ import annotations

from dataclasses import dataclass, field


class ChannelError(Exception):
    """渠道发送失败，可重试。"""


@dataclass(frozen=True)
class ChannelMessage:
    title: str
    markdown: str
    text: str
    image_url: str | None = None
    link: str | None = None
    payload: dict = field(default_factory=dict)
    event_type: str | None = None
    domain: str | None = None


class BaseChannel:
    """所有外部渠道的最小接口。"""

    type = ""

    def __init__(self, name: str, config: dict, secrets: dict):
        self.name = name
        self.config = config
        self.secrets = secrets

    async def send(self, message: ChannelMessage) -> str | None:
        """发送消息，返回上游消息 ID；失败抛 ChannelError。"""
        raise NotImplementedError

    async def test(self) -> None:
        """发送一条测试消息。"""
        await self.send(
            ChannelMessage(
                title="OneTap 通知测试",
                markdown="如果你收到这条消息，说明通知渠道配置正确。",
                text="OneTap 通知测试\n如果你收到这条消息，说明通知渠道配置正确。",
            )
        )
