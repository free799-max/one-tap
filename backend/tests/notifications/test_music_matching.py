"""音乐同名歌曲匹配测试：文件与任务必须按歌手区分。"""

import sqlalchemy as sa

from app.models.notification import Notification, ResourceSubscription
from app.models.orchestration import MusicAutoOrganizeFile
from app.services.notifications import subscriptions as subscription_events
from app.services.notifications.sync import NotificationSyncService


async def _add_song(db, *, subject_key, artist, album, size, path):
    db.add(
        ResourceSubscription(
            domain="music",
            subject_key=subject_key,
            title="加减乘除",
            status="downloading",
            kind="request",
            source_of_truth="local",
            upstream_ref={"source": "netease", "song_id": subject_key},
            last_seen={"status": "downloading", "artist": artist, "album": album},
        )
    )
    db.add(
        MusicAutoOrganizeFile(group_id=1, path=path, size=size, status="done")
    )
    await db.commit()


async def test_music_file_notification_matches_artist(notification_db):
    Session = notification_db
    async with Session() as db:
        await _add_song(
            db,
            subject_key="music:netease:263609",
            artist="刘小慧",
            album="改改",
            size=89444566,
            path="/app/media/downloads/刘小慧 - 加减乘除.flac",
        )
        await _add_song(
            db,
            subject_key="music:netease:1325711950",
            artist="梦涵",
            album="加减乘除",
            size=31490700,
            path="/app/media/downloads/梦涵 - 加减乘除.flac",
        )

    await subscription_events.notify_music_file(
        path="/app/media/downloads/梦涵 - 加减乘除.flac",
        status="done",
        previous_status="pending",
    )
    await subscription_events.notify_music_file(
        path="/app/media/downloads/刘小慧 - 加减乘除.flac",
        status="done",
        previous_status="pending",
    )

    async with Session() as db:
        rows = (
            await db.execute(
                sa.select(Notification).where(Notification.domain == "music")
            )
        ).scalars().all()
        assert len(rows) == 2
        by_key = {row.subject_key: row for row in rows}
        meng = by_key["music:netease:1325711950"]
        liu = by_key["music:netease:263609"]
        assert meng.payload["options"]["file_size"] == 31490700
        assert meng.payload["options"]["artist"] == "梦涵"
        assert meng.payload["path"] == "/app/media/downloads/梦涵 - 加减乘除.flac"
        assert liu.payload["options"]["file_size"] == 89444566
        assert liu.payload["options"]["artist"] == "刘小慧"

        records = (
            await db.execute(
                sa.select(ResourceSubscription).where(
                    ResourceSubscription.domain == "music"
                )
            )
        ).scalars().all()
        paths = {row.subject_key: row.last_seen.get("path") for row in records}
        assert paths["music:netease:263609"] == "/app/media/downloads/刘小慧 - 加减乘除.flac"
        assert paths["music:netease:1325711950"] == "/app/media/downloads/梦涵 - 加减乘除.flac"


async def test_music_file_notification_skips_ambiguous_title(notification_db):
    Session = notification_db
    async with Session() as db:
        await _add_song(
            db,
            subject_key="music:netease:263609",
            artist="刘小慧",
            album="改改",
            size=89444566,
            path="/app/media/downloads/刘小慧 - 加减乘除.flac",
        )
        await _add_song(
            db,
            subject_key="music:netease:1325711950",
            artist="梦涵",
            album="加减乘除",
            size=31490700,
            path="/app/media/downloads/梦涵 - 加减乘除.flac",
        )

    # 文件名没有歌手，同名候选无法区分时不发通知
    await subscription_events.notify_music_file(
        path="/app/media/downloads/加减乘除.flac",
        status="done",
        previous_status="pending",
    )

    async with Session() as db:
        rows = (
            await db.execute(
                sa.select(Notification).where(Notification.domain == "music")
            )
        ).scalars().all()
        assert rows == []


def _record(artist):
    return ResourceSubscription(
        domain="music",
        subject_key=f"music:netease:{artist}",
        title="加减乘除",
        upstream_ref={"source": "netease"},
        last_seen={"artist": artist},
    )


def _task(task_id, artist):
    return {
        "task_id": task_id,
        "title": "加减乘除",
        "artist": artist,
        "source": "netease",
        "status": "success",
    }


def test_match_music_task_uses_artist():
    service = NotificationSyncService()
    tasks = [_task("10", "梦涵"), _task("9", "刘小慧")]

    matched = service._match_music_task(_record("刘小慧"), tasks)
    assert matched["task_id"] == "9"

    matched = service._match_music_task(_record("梦涵"), tasks)
    assert matched["task_id"] == "10"


def test_match_music_task_rejects_ambiguous_same_title():
    service = NotificationSyncService()
    tasks = [_task("10", "梦涵"), _task("9", "刘小慧")]

    # 台账没有歌手信息时，同名任务区分不了，不猜
    assert service._match_music_task(_record(""), tasks) is None
    # 同名但歌手对不上，也不该认领
    assert service._match_music_task(_record("张学友"), tasks) is None
