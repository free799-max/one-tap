"""AI 通知方式偏好测试：读取、保存、技能更新后重新询问、外发开关。"""

import sqlalchemy as sa
import pytest

from app.core.exceptions import APIException
from app.models.notification import (
    Notification,
    NotificationChannel,
    NotificationDelivery,
)
from app.services.notifications import events, preferences


async def _channel(db):
    db.add(
        NotificationChannel(
            name="家庭飞书",
            type="feishu",
            enabled=True,
            config={"app_id": "cli", "receive_id": "chat"},
        )
    )
    await db.commit()


async def test_preference_defaults_to_push_but_needs_onboarding(notification_db):
    Session = notification_db
    async with Session() as db:
        await _channel(db)
        state = await preferences.read_preference(db)

    assert state["mode"] == "push"
    assert state["configured"] is False
    assert state["needs_onboarding"] is True
    assert state["skill_version"] == preferences.current_skill_version()
    assert state["channels"] == [{"name": "家庭飞书", "type": "feishu"}]


async def test_set_preference_marks_onboarding_done(notification_db):
    Session = notification_db
    async with Session() as db:
        await _channel(db)
        state = await preferences.set_preference(db, "chat")
        assert state["mode"] == "chat"
        assert state["mode_label"] == "对话内回复"
        assert state["configured"] is True
        assert state["needs_onboarding"] is False

        # 重新读取走的是同一条设置记录
        again = await preferences.read_preference(db)
        assert again["mode"] == "chat"
        assert again["needs_onboarding"] is False


async def test_skill_version_bump_triggers_onboarding_again(notification_db):
    Session = notification_db
    async with Session() as db:
        await preferences.set_preference(db, "push")
        setting = await events.get_setting(db)
        setting.notify_skill_version = "0"
        await db.commit()

        state = await preferences.read_preference(db)
        # 模式还留着，但要重新问一次
        assert state["mode"] == "push"
        assert state["needs_onboarding"] is True


async def test_invalid_mode_is_rejected(notification_db):
    Session = notification_db
    async with Session() as db:
        with pytest.raises(APIException) as excinfo:
            await preferences.set_preference(db, "email")
        assert "chat / push" in str(excinfo.value)


async def test_chat_mode_skips_channel_delivery(notification_db):
    Session = notification_db
    async with Session() as db:
        await _channel(db)
        setting = await events.get_setting(db)
        setting.notify_mode = "chat"
        await db.commit()

        await events.emit(
            db,
            event_type="download.completed",
            title="下载完成",
            markdown="**下载完成**",
            domain="video",
            subject_key="video:1",
            dedupe_key="chat-mode",
        )
        await db.commit()

        notification = (
            await db.execute(
                sa.select(Notification).where(Notification.dedupe_key == "chat-mode")
            )
        ).scalar_one()
        deliveries = (
            await db.execute(
                sa.select(NotificationDelivery).where(
                    NotificationDelivery.notification_id == notification.id
                )
            )
        ).scalars().all()
    # 站内收件箱保留，渠道投递不再生成
    assert notification.title == "下载完成"
    assert deliveries == []


async def test_push_mode_keeps_channel_delivery(notification_db):
    Session = notification_db
    async with Session() as db:
        await _channel(db)
        await preferences.set_preference(db, "push")

        await events.emit(
            db,
            event_type="download.completed",
            title="下载完成",
            markdown="**下载完成**",
            domain="video",
            subject_key="video:2",
            dedupe_key="push-mode",
        )
        await db.commit()

        deliveries = (
            await db.execute(sa.select(NotificationDelivery))
        ).scalars().all()
    assert len(deliveries) == 1
    assert deliveries[0].status == "pending"
