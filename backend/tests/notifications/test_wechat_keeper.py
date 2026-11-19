"""微信 ClawBot 会话保活测试。"""

from datetime import datetime, timedelta, timezone

from app.models.notification import (
    Notification,
    NotificationChannel,
    NotificationDelivery,
)
from app.services.notifications.crypto import encrypt_secrets
import app.services.notifications.wechat_keeper as keeper_module


async def test_keeper_refreshes_context_token_and_wakes_delivery(
    notification_db, monkeypatch
):
    Session = notification_db
    monkeypatch.setattr(keeper_module, "async_session", Session)

    future = datetime.now(timezone.utc) + timedelta(hours=1)
    async with Session() as db:
        channel = NotificationChannel(
            name="微信 ClawBot",
            type="wechatclawbot",
            enabled=True,
            config={
                "account_id": "bot@im.bot",
                "to_user": "user@im.wechat",
                "context_token": "old-token",
            },
            secrets=encrypt_secrets({"bot_token": "t"}),
        )
        db.add(channel)
        await db.flush()
        notification = Notification(
            event_id="event-1",
            type="subscription.added",
            dedupe_key="dedupe-1",
            title="标题",
            markdown="正文",
        )
        db.add(notification)
        await db.flush()
        delivery = NotificationDelivery(
            notification_id=notification.id,
            channel_id=channel.id,
            status="pending",
            next_attempt_at=future,
        )
        db.add(delivery)
        failed_notification = Notification(
            event_id="event-2",
            type="subscription.added",
            dedupe_key="dedupe-2",
            title="标题2",
            markdown="正文2",
        )
        db.add(failed_notification)
        await db.flush()
        failed_delivery = NotificationDelivery(
            notification_id=failed_notification.id,
            channel_id=channel.id,
            status="failed",
            attempts=5,
            error="微信 ClawBot 拒收：会话已过期或下行配额已用尽。",
            next_attempt_at=future,
        )
        db.add(failed_delivery)
        await db.commit()
        channel_id = channel.id
        delivery_id = delivery.id
        failed_delivery_id = failed_delivery.id

    async def fake_updates(token, base_url, buffer, timeout):
        return {
            "messages": [
                {
                    "from_user_id": "user@im.wechat",
                    "message_type": 1,
                    "context_token": "fresh-token",
                }
            ]
        }

    monkeypatch.setattr(keeper_module, "fetch_updates", fake_updates)

    await keeper_module.wechat_context_keeper.tick()

    async with Session() as db:
        channel = await db.get(NotificationChannel, channel_id)
        assert channel.config["context_token"] == "fresh-token"
        delivery = await db.get(NotificationDelivery, delivery_id)
        next_at = delivery.next_attempt_at
        if next_at.tzinfo is None:
            next_at = next_at.replace(tzinfo=timezone.utc)
        assert next_at < future
        revived = await db.get(NotificationDelivery, failed_delivery_id)
        assert revived.status == "pending"
        assert revived.attempts == 0
