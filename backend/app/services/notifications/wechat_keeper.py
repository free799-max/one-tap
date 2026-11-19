"""微信 ClawBot 会话保活。

iLink 的主动推送依赖用户最近互动产生的 context_token：用户发来的每条消息
都会带一个新的 token；token 过期或下行配额用尽时，sendmessage 返回
ret=-2「prepare failed」。这里持续长轮询上行消息，把最新 token 写回渠道
配置，并唤醒积压的投递记录。

只对启用的微信 ClawBot 渠道生效，单个渠道一次长轮询约 35 秒。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx
import sqlalchemy as sa

from app.database import async_session
from app.models.notification import NotificationChannel, NotificationDelivery
from app.services.notifications.channels import build_channel
from app.services.notifications.channels.wechatclawbot import (
    DEFAULT_BASE_URL,
    fetch_updates,
)

logger = logging.getLogger(__name__)

POLL_TIMEOUT_SECONDS = 35.0
IDLE_SLEEP_SECONDS = 1.0


class WeChatContextKeeper:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None

    def start_scheduler(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop())

    async def stop_scheduler(self) -> None:
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception as exc:  # noqa: BLE001 - 后台循环不能因单次异常退出
                logger.warning("微信会话保活失败：%s", exc)
            await asyncio.sleep(IDLE_SLEEP_SECONDS)

    async def tick(self) -> None:
        async with async_session() as db:
            channels = list(
                (
                    await db.execute(
                        sa.select(NotificationChannel).where(
                            NotificationChannel.type == "wechatclawbot",
                            NotificationChannel.enabled.is_(True),
                        )
                    )
                )
                .scalars()
                .all()
            )
        for channel in channels:
            await self._poll_channel(channel)

    async def _poll_channel(self, channel: NotificationChannel) -> None:
        impl = build_channel(channel)
        bot_token = str(impl.secrets.get("bot_token") or "").strip()
        if not bot_token:
            return
        config = dict(channel.config or {})
        base_url = str(config.get("base_url") or DEFAULT_BASE_URL)
        try:
            result = await fetch_updates(
                bot_token, base_url, "", timeout=POLL_TIMEOUT_SECONDS
            )
        except httpx.HTTPError:
            return
        messages = result.get("messages") or []
        if not messages:
            return
        latest = messages[-1]
        context_token = str(latest.get("context_token") or "").strip()
        if not context_token or config.get("context_token") == context_token:
            return
        from_user = str(latest.get("from_user_id") or "").strip()

        async with async_session() as db:
            record = await db.get(NotificationChannel, channel.id)
            if record is None:
                return
            updated = dict(record.config or {})
            updated["context_token"] = context_token
            if from_user:
                updated["to_user"] = from_user
            record.config = updated
            await db.execute(
                sa.update(NotificationDelivery)
                .where(
                    NotificationDelivery.channel_id == record.id,
                    NotificationDelivery.status == "pending",
                    NotificationDelivery.next_attempt_at > sa.func.now(),
                )
                .values(next_attempt_at=sa.func.now())
            )
            cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
            await db.execute(
                sa.update(NotificationDelivery)
                .where(
                    NotificationDelivery.channel_id == record.id,
                    NotificationDelivery.status == "failed",
                    NotificationDelivery.error.like("%会话已过期%"),
                    NotificationDelivery.updated_at >= cutoff,
                )
                .values(status="pending", attempts=0, next_attempt_at=sa.func.now())
            )
            await db.commit()
        logger.info("微信 ClawBot 收到新消息，已刷新会话并唤醒积压通知")


wechat_context_keeper = WeChatContextKeeper()
