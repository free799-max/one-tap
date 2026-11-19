"""通知投递分发器。

采用 Postgres 行锁 + 状态机实现轻量 outbox：
pending -> sending -> sent / failed，失败按指数退避重试。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa

from app.database import async_session
from app.models.notification import (
    Notification,
    NotificationChannel,
    NotificationDelivery,
)
from app.services.notifications.channels import ChannelError, ChannelMessage, build_channel
from app.services.notifications.events import markdown_to_text

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
RETRY_DELAYS = (60, 300, 900, 3600)
STALE_SENDING_SECONDS = 300


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime:
    if value is None:
        return _now()
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _retry_delay(attempts: int) -> timedelta:
    index = min(max(attempts - 1, 0), len(RETRY_DELAYS) - 1)
    return timedelta(seconds=RETRY_DELAYS[index])


class NotificationDispatcher:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._tick_lock = asyncio.Lock()

    def start_scheduler(self, interval_seconds: float = 5.0) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(interval_seconds))

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

    async def _loop(self, interval_seconds: float) -> None:
        # 启动后的第一轮把上个进程遗留的 sending 全部回收，
        # 避免重启时正在发送的通知要等满 5 分钟才重试。
        stale_seconds: float = 0
        while True:
            try:
                await self.tick(stale_seconds)
            except Exception as exc:  # noqa: BLE001 - 后台循环不能因单次异常退出
                logger.warning("通知分发失败：%s", exc)
            stale_seconds = STALE_SENDING_SECONDS
            await asyncio.sleep(interval_seconds)

    async def tick(self, stale_seconds: float = STALE_SENDING_SECONDS) -> int:
        """处理一批待投递记录，返回成功发送数量。"""
        async with self._tick_lock:
            async with async_session() as db:
                await self._reset_stale(db, stale_seconds)
                rows = await self._claim(db)
                if not rows:
                    return 0

            groups: dict[int | None, list[tuple[int, int]]] = {}
            for delivery_id, notification_id, channel_id in rows:
                groups.setdefault(channel_id, []).append((delivery_id, notification_id))

            sent = 0
            for channel_id, items in groups.items():
                sent += await self._send_batch(channel_id, items)
            return sent

    async def _reset_stale(
        self, db, max_age_seconds: float = STALE_SENDING_SECONDS
    ) -> None:
        cutoff = _now() - timedelta(seconds=max_age_seconds)
        await db.execute(
            sa.update(NotificationDelivery)
            .where(
                NotificationDelivery.status == "sending",
                NotificationDelivery.updated_at < cutoff,
            )
            .values(status="pending", next_attempt_at=_now())
        )
        await db.commit()

    async def _claim(self, db) -> list[tuple[int, int, int | None]]:
        statement = (
            sa.select(
                NotificationDelivery.id,
                NotificationDelivery.notification_id,
                NotificationDelivery.channel_id,
            )
            .join(Notification, Notification.id == NotificationDelivery.notification_id)
            .where(
                NotificationDelivery.status == "pending",
                NotificationDelivery.next_attempt_at <= _now(),
                Notification.available_at <= _now(),
            )
            .order_by(NotificationDelivery.next_attempt_at, NotificationDelivery.id)
            .limit(20)
        )
        bind = db.get_bind()
        if bind is not None and bind.dialect.name != "sqlite":
            statement = statement.with_for_update(skip_locked=True)
        rows = list((await db.execute(statement)).all())
        if not rows:
            return []
        ids = [row[0] for row in rows]
        await db.execute(
            sa.update(NotificationDelivery)
            .where(NotificationDelivery.id.in_(ids))
            .values(status="sending", updated_at=_now())
        )
        await db.commit()
        return [(int(row[0]), int(row[1]), row[2]) for row in rows]

    async def _send_batch(
        self, channel_id: int | None, items: list[tuple[int, int]]
    ) -> int:
        """逐条发送同一渠道本轮到期的通知，保留每个资源的生命周期。"""
        async with async_session() as db:
            channel = await db.get(NotificationChannel, channel_id) if channel_id else None
            pending: list[tuple[NotificationDelivery, Notification]] = []
            for delivery_id, notification_id in items:
                delivery = await db.get(NotificationDelivery, delivery_id)
                notification = await db.get(Notification, notification_id)
                if delivery is not None and notification is not None:
                    pending.append((delivery, notification))
            if not pending:
                return 0

            if channel is None or not channel.enabled:
                for delivery, _ in pending:
                    delivery.status = "skipped"
                    delivery.error = "渠道已删除或停用"
                    delivery.updated_at = _now()
                await db.commit()
                return 0

            channel_impl = build_channel(channel)
            channel_config = getattr(channel_impl, "config", None)
            sent = 0
            for delivery, notification in pending:
                try:
                    external_id = await channel_impl.send(self._message(notification))
                except ChannelError as exc:
                    await self._persist_channel_config(db, channel, channel_config)
                    await self._mark_failed(delivery, str(exc))
                    await db.commit()
                    continue
                except Exception as exc:  # noqa: BLE001 - 单条异常不中断后续通知
                    logger.warning("通知渠道 %s 发送异常：%s", channel.name, exc)
                    await self._persist_channel_config(db, channel, channel_config)
                    await self._mark_failed(delivery, str(exc))
                    await db.commit()
                    continue

                await self._persist_channel_config(db, channel, channel_config)
                now = _now()
                delivery.status = "sent"
                delivery.sent_at = now
                delivery.external_id = external_id
                delivery.error = None
                delivery.updated_at = now
                await db.commit()
                sent += 1
            return sent

    @staticmethod
    def _message(notification: Notification) -> ChannelMessage:
        return ChannelMessage(
            title=notification.title,
            markdown=notification.markdown,
            text=markdown_to_text(notification.markdown),
            image_url=notification.image_url,
            link=notification.link,
            payload=dict(notification.payload or {}),
            event_type=notification.type,
            domain=notification.domain,
        )

    async def _persist_channel_config(
        self, db, channel: NotificationChannel, config: dict | None
    ) -> None:
        if not config:
            return
        current = dict(channel.config or {})
        updated = dict(config)
        if updated == current:
            return
        channel.config = updated
        await db.commit()

    async def _mark_failed(self, delivery: NotificationDelivery, error: str) -> None:
        delivery.attempts = (delivery.attempts or 0) + 1
        delivery.error = error[:1000]
        delivery.updated_at = _now()
        if delivery.attempts >= MAX_ATTEMPTS:
            delivery.status = "failed"
        else:
            delivery.status = "pending"
            delivery.next_attempt_at = _now() + _retry_delay(delivery.attempts)


notification_dispatcher = NotificationDispatcher()
