"""通知事件、台账与分发器测试。"""

import sqlalchemy as sa

from app.models.notification import (
    Notification,
    NotificationChannel,
    NotificationDelivery,
    ResourceSubscription,
)
from app.services.notifications import events
from app.services.notifications.dispatcher import notification_dispatcher


async def test_emit_deduplicates_and_creates_delivery(notification_db):
    Session = notification_db
    async with Session() as db:
        db.add(
            NotificationChannel(
                name="飞书测试",
                type="feishu",
                enabled=True,
                config={"app_id": "cli", "receive_id": "chat"},
            )
        )
        await db.commit()

        first = await events.emit(
            db,
            event_type="subscription.added",
            title="已订阅",
            markdown="**已订阅**",
            domain="video",
            subject_key="video:tmdb:1:S1",
            dedupe_key="dedupe-1",
        )
        await db.commit()
        assert first is not None

        duplicate = await events.emit(
            db,
            event_type="subscription.added",
            title="已订阅",
            markdown="**已订阅**",
            domain="video",
            subject_key="video:tmdb:1:S1",
            dedupe_key="dedupe-1",
        )
        assert duplicate is None

        deliveries = (
            await db.execute(sa.select(NotificationDelivery))
        ).scalars().all()
        assert len(deliveries) == 1
        assert deliveries[0].status == "pending"


async def test_event_type_filter(notification_db):
    Session = notification_db
    async with Session() as db:
        db.add(
            NotificationChannel(
                name="只收整理",
                type="dingtalk",
                enabled=True,
                config={"webhook": "https://example.com/robot"},
            )
        )
        setting = await events.get_setting(db)
        setting.event_types = ["organize.completed"]
        await db.commit()
        await events.emit(
            db,
            event_type="subscription.added",
            title="订阅",
            markdown="订阅",
            dedupe_key="d-added",
        )
        await db.commit()
        assert (await db.execute(sa.select(NotificationDelivery))).scalars().all() == []

        await events.emit(
            db,
            event_type="organize.completed",
            title="整理",
            markdown="整理",
            dedupe_key="d-organized",
        )
        await db.commit()
        assert len((await db.execute(sa.select(NotificationDelivery))).scalars().all()) == 1


async def test_dispatcher_marks_sent(notification_db, monkeypatch):
    import app.services.notifications.dispatcher as dispatcher_module

    class FakeChannel:
        async def send(self, message):
            assert message.title == "测试"
            return "external-1"

    monkeypatch.setattr(dispatcher_module, "build_channel", lambda record: FakeChannel())
    Session = notification_db
    async with Session() as db:
        db.add(
            NotificationChannel(
                name="渠道",
                type="feishu",
                enabled=True,
                config={"app_id": "cli", "receive_id": "chat"},
            )
        )
        await db.commit()
        await events.emit(
            db,
            event_type="subscription.added",
            title="测试",
            markdown="测试",
            dedupe_key="dispatch-1",
        )
        await db.commit()

    sent = await notification_dispatcher.tick()
    assert sent == 1
    async with Session() as db:
        delivery = (await db.execute(sa.select(NotificationDelivery))).scalar_one()
        assert delivery.status == "sent"
        assert delivery.external_id == "external-1"
        assert delivery.attempts == 0


async def test_dispatcher_recovers_stale_sending_on_boot(notification_db, monkeypatch):
    import app.services.notifications.dispatcher as dispatcher_module

    class FakeChannel:
        async def send(self, message):
            return "external-recovered"

    monkeypatch.setattr(dispatcher_module, "build_channel", lambda record: FakeChannel())
    Session = notification_db
    async with Session() as db:
        db.add(NotificationChannel(name="渠道", type="feishu", enabled=True, config={}))
        await db.commit()
        await events.emit(
            db,
            event_type="subscription.added",
            title="重启前",
            markdown="重启前",
            dedupe_key="boot-recover",
        )
        await db.commit()
        delivery = (await db.execute(sa.select(NotificationDelivery))).scalar_one()
        # 模拟上个进程发到一半被重启打断
        delivery.status = "sending"
        delivery.updated_at = dispatcher_module._now()
        await db.commit()

    assert await notification_dispatcher.tick() == 0
    async with Session() as db:
        assert (
            await db.execute(sa.select(NotificationDelivery))
        ).scalar_one().status == "sending"

    assert await notification_dispatcher.tick(0) == 1
    async with Session() as db:
        delivery = (await db.execute(sa.select(NotificationDelivery))).scalar_one()
        assert delivery.status == "sent"
        assert delivery.external_id == "external-recovered"


async def test_dispatcher_sends_same_channel_notifications_individually(
    notification_db, monkeypatch
):
    import app.services.notifications.dispatcher as dispatcher_module

    class FakeChannel:
        def __init__(self):
            self.messages = []

        async def send(self, message):
            self.messages.append(message)
            return f"external-{len(self.messages)}"

    fake = FakeChannel()
    monkeypatch.setattr(dispatcher_module, "build_channel", lambda record: fake)
    Session = notification_db
    async with Session() as db:
        db.add(NotificationChannel(name="渠道", type="feishu", enabled=True, config={}))
        await db.commit()
        for index in (1, 2, 3):
            await events.emit(
                db,
                event_type="subscription.added",
                title=f"标题{index}",
                markdown=f"正文{index}",
                dedupe_key=f"batch-{index}",
            )
        await db.commit()

    sent = await notification_dispatcher.tick()
    assert sent == 3
    assert [message.title for message in fake.messages] == ["标题1", "标题2", "标题3"]
    assert [message.text for message in fake.messages] == ["正文1", "正文2", "正文3"]

    async with Session() as db:
        deliveries = (await db.execute(sa.select(NotificationDelivery))).scalars().all()
        assert [item.status for item in deliveries] == ["sent", "sent", "sent"]
        assert [item.external_id for item in deliveries] == [
            "external-1",
            "external-2",
            "external-3",
        ]


async def test_record_added_replaced_removed(notification_db):
    from app.services.notifications import subscriptions as subscription_events

    Session = notification_db
    options = {
        "name": "示例剧",
        "type": "电视剧",
        "media_source": "themoviedb",
        "media_id": "100",
        "season": 1,
        "year": "2026",
        "poster": "https://img.example/poster.jpg",
    }
    result = {"instance_id": 3, "result": {"id": 7}}

    async with Session() as db:
        record = await subscription_events.record_added(
            db,
            domain="video",
            item_id="100",
            options=options,
            result=result,
            instance_id=None,
        )
        await db.commit()
        assert record.subject_key == "video:themoviedb:100:S1"
        assert record.revision == 1
        assert record.upstream_ref["subscribe_id"] == 7

    async with Session() as db:
        rows = (await db.execute(sa.select(Notification))).scalars().all()
        assert len(rows) == 1
        assert rows[0].type == "subscription.added"

    async with Session() as db:
        record = await subscription_events.record_added(
            db,
            domain="video",
            item_id="100",
            options=options,
            result=result,
            instance_id=None,
        )
        await db.commit()
        assert record.revision == 2

    async with Session() as db:
        record = await subscription_events.record_removed(
            db, domain="video", subscription_id="7", instance_id=3
        )
        await db.commit()
        assert record is not None
        assert record.status == "removed"

    async with Session() as db:
        rows = (await db.execute(sa.select(Notification))).scalars().all()
        types = [row.type for row in rows]
        assert "subscription.added" in types
        assert "subscription.removed" in types


async def test_record_added_video_fetches_poster_when_missing(notification_db, monkeypatch):
    from app.services.notifications import subscriptions as subscription_events
    from app.services.resources import moviepilot

    async def fake_detail(db, **kwargs):
        return {
            "detail": {
                "poster_path": "https://img.example/movie.jpg",
                "vote_average": 7.1,
                "vote_count": 27,
                "runtime": 87,
                "genres": [{"name": "历史"}, {"name": "喜剧"}],
                "directors": [{"name": "冯小宁"}],
                "actors": [{"name": "郭达"}, {"name": "潘长江"}],
            }
        }

    monkeypatch.setattr(moviepilot, "detail", fake_detail)
    Session = notification_db

    async with Session() as db:
        record = await subscription_events.record_added(
            db,
            domain="video",
            item_id="413577",
            options={
                "name": "举起手来！",
                "type": "电影",
                "year": "2005",
                "media_source": "themoviedb",
            },
            result={"instance_id": 251, "result": {"id": 3}},
            instance_id=None,
        )
        await db.commit()
        assert record.poster == "https://img.example/movie.jpg"
        rows = (await db.execute(sa.select(Notification))).scalars().all()
        assert rows[0].image_url == "https://img.example/movie.jpg"
        # 详情元数据要带给通知卡片，否则「已加入订阅」只有类型和年份
        payload = rows[0].payload["options"]
        assert payload["rating"] == 7.1
        assert payload["runtime"] == 87
        assert payload["directors"] == ["冯小宁"]
        assert payload["actors"] == ["郭达", "潘长江"]
        assert payload["genres"] == ["历史", "喜剧"]


async def test_record_added_comic_merges_subscription_meta(notification_db):
    from app.services.notifications import subscriptions as subscription_events

    Session = notification_db
    result = {
        "instance_id": 257,
        "variant": "local",
        "subscription_id": "1",
        "title": "开局表白美女师尊",
        "meta": {
            "author": "博易动漫",
            "tags": "格斗,推理",
            "category": "comic",
            "comic_status": "ONGOING",
            "cover": "https://img.example/cover.webp",
            "total_chapters": 65,
        },
    }

    async with Session() as db:
        record = await subscription_events.record_added(
            db,
            domain="comic",
            item_id="开局表白美女师尊",
            options={"name": "开局表白美女师尊", "category": "comic"},
            result=result,
            instance_id=257,
        )
        await db.commit()
        assert record.poster == "https://img.example/cover.webp"
        assert record.last_seen["author"] == "博易动漫"
        assert record.last_seen["total_chapters"] == 65

    async with Session() as db:
        rows = (await db.execute(sa.select(Notification))).scalars().all()
        assert len(rows) == 1
        assert rows[0].title == "新增漫画订阅：开局表白美女师尊"
        assert rows[0].image_url == "https://img.example/cover.webp"
        assert rows[0].payload["options"]["author"] == "博易动漫"
        assert rows[0].payload["options"]["total_chapters"] == 65


async def test_dispatcher_retries_failure(notification_db, monkeypatch):
    import app.services.notifications.dispatcher as dispatcher_module
    from app.services.notifications.channels.base import ChannelError

    class FailingChannel:
        async def send(self, message):
            raise ChannelError("上游 500")

    monkeypatch.setattr(dispatcher_module, "build_channel", lambda record: FailingChannel())
    Session = notification_db
    async with Session() as db:
        db.add(
            NotificationChannel(
                name="失败渠道",
                type="feishu",
                enabled=True,
                config={"app_id": "cli", "receive_id": "chat"},
            )
        )
        await db.commit()
        await events.emit(
            db,
            event_type="subscription.added",
            title="失败",
            markdown="失败",
            dedupe_key="dispatch-fail-1",
        )
        await db.commit()

    await notification_dispatcher.tick()
    async with Session() as db:
        delivery = (await db.execute(sa.select(NotificationDelivery))).scalar_one()
        assert delivery.status == "pending"
        assert delivery.attempts == 1
        assert delivery.error == "上游 500"
        assert delivery.next_attempt_at > delivery.created_at


async def test_music_and_reader_helpers(notification_db):
    from app.services.notifications import subscriptions as subscription_events

    Session = notification_db
    async with Session() as db:
        record = await events.upsert_subscription(
            db,
            domain="music",
            subject_key="music:kuwo:1",
            title="晴天",
            kind="request",
            source_of_truth="local",
            upstream_ref={"source": "kuwo", "song_id": "1", "task_id": "t1"},
            last_seen={
                "status": "queued",
                "artist": "周杰伦",
                "album": "叶惠美",
            },
        )
        from app.models.orchestration import MusicAutoOrganizeFile

        db.add(
            MusicAutoOrganizeFile(
                group_id=1,
                path="/music/downloads/晴天.mp3",
                size=123456,
                status="done",
            )
        )
        await db.commit()
        assert record.id

    await subscription_events.notify_music_file(
        path="/music/downloads/晴天.mp3",
        status="done",
        previous_status="pending",
    )
    async with Session() as db:
        notification = (await db.execute(sa.select(Notification))).scalar_one()
        assert notification.type == "organize.completed"
        assert notification.subject_key == "music:kuwo:1"
        assert notification.payload == {
            "path": "/music/downloads/晴天.mp3",
            "options": {
                "artist": "周杰伦",
                "album": "叶惠美",
                "audio_format": "MP3",
                "file_size": 123456,
            },
        }

    await subscription_events.notify_reader_export(
        book_url="https://reader/book/1",
        title="测试书",
        total=10,
        previous_exported=-1,
        destination="/books/1.epub",
    )
    await subscription_events.notify_reader_export(
        book_url="https://reader/book/1",
        title="测试书",
        total=12,
        previous_exported=10,
        destination="/books/1.epub",
    )
    async with Session() as db:
        rows = (
            await db.execute(
                sa.select(Notification).where(Notification.domain == "novel")
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].type == "organize.completed"

        sub = (
            await db.execute(
                sa.select(ResourceSubscription).where(
                    ResourceSubscription.domain == "novel"
                )
            )
        ).scalar_one()
        assert sub.last_seen["exported_total"] == 12
