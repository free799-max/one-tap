"""资源订阅同步测试。"""

import sqlalchemy as sa

from app.models.notification import Notification, ResourceSubscription
from app.services.notifications import sync as sync_module
from app.services.notifications.sync import NotificationSyncService


class FakeApp:
    instance_id = 1
    url = "http://moviepilot:3001"
    api_key = "key"
    status = "running"
    has_persisted_url = True


class FakeState:
    app = FakeApp()
    available = True


def test_music_and_novel_sync_every_180_seconds():
    service = NotificationSyncService()
    names = {sync.__name__ for sync in service._syncs()}
    assert service._due_syncs(300, 1000) == names

    service._last_run = {name: 1000 for name in names}
    assert service._due_syncs(300, 1179) == set()
    fast = {"_sync_music", "_sync_novel"}
    assert service._due_syncs(300, 1180) == fast
    service._last_run.update({name: 1180 for name in fast})
    assert service._due_syncs(300, 1299) == set()
    slow = {"_sync_video", "_sync_comic"}
    assert service._due_syncs(300, 1300) == slow
    service._last_run.update({name: 1300 for name in slow})
    assert service._due_syncs(300, 1360) == fast


async def test_video_sync_baseline_then_update(notification_db, monkeypatch):
    Session = notification_db
    items = [
        {
            "id": 7,
            "name": "示例剧",
            "year": "2026",
            "media_source": "themoviedb",
            "media_id": "100",
            "season": 1,
            "poster": "https://img.example/poster.jpg",
            "total_episode": 10,
            "lack_episode": 2,
        }
    ]

    async def fake_list_upstreams(db, host, app_names=None):
        assert app_names == {"moviepilot"}
        return [FakeState()]

    async def fake_subscriptions(db, **kwargs):
        return {"items": items}

    monkeypatch.setattr(
        sync_module.mcp_config_service, "list_upstreams", fake_list_upstreams
    )
    monkeypatch.setattr(sync_module.moviepilot, "subscriptions", fake_subscriptions)

    async def fake_fetch_metadata(db, **kwargs):
        return {
            "genres": ["剧情", "喜剧"],
            "rating": 8.1,
            "vote_count": 120,
            "runtime": 45,
            "directors": ["导演甲"],
            "actors": ["演员甲", "演员乙"],
            "poster": "https://img.example/poster.jpg",
        }

    monkeypatch.setattr(
        sync_module.moviepilot, "fetch_metadata", fake_fetch_metadata
    )

    service = NotificationSyncService()

    async def no_history(db, state):
        return None

    monkeypatch.setattr(service, "_sync_video_history", no_history)
    await service.tick()

    async with Session() as db:
        sub = (
            await db.execute(sa.select(ResourceSubscription))
        ).scalar_one()
        assert sub.last_seen["total_episode"] == 10
        assert (await db.execute(sa.select(Notification))).scalars().all() == []

    items[0]["total_episode"] = 12
    items[0]["lack_episode"] = 0
    await service.tick()

    async with Session() as db:
        rows = (await db.execute(sa.select(Notification))).scalars().all()
        types = {row.type for row in rows}
        assert "subscription.updated" in types
        assert "subscription.completed" in types
        sub = (await db.execute(sa.select(ResourceSubscription))).scalar_one()
        assert sub.status == "completed"
        assert sub.last_seen["total_episode"] == 12
        # 同步时补抓的详情元数据要进入台账，并透传到通知卡片字段
        assert sub.last_seen["genres"] == ["剧情", "喜剧"]
        assert sub.last_seen["runtime"] == 45
        updated = next(row for row in rows if row.type == "subscription.updated")
        options = updated.payload["options"]
        assert options["genres"] == ["剧情", "喜剧"]
        assert options["rating"] == 8.1
        assert options["directors"] == ["导演甲"]
        assert options["detail"] == "新增 2 集"


async def test_video_download_waits_until_task_finishes(notification_db, monkeypatch):
    from app.models.notification import NotificationSyncState
    from app.services.notifications.sync import _save_sync_state

    Session = notification_db
    service = NotificationSyncService()
    history = [{"id": 5, "title": "好东西", "download_hash": "abc123"}]
    progress = {"value": 0.38}

    async def fake_history(state, kind):
        return history if kind == "download" else []

    async def fake_tasks(state):
        return {
            "abc123": {"hash": "abc123", "progress": progress["value"], "state": "downloading"}
        }

    monkeypatch.setattr(service, "_moviepilot_history", fake_history)
    monkeypatch.setattr(service, "_downloader_tasks", fake_tasks)

    async with Session() as db:
        db.add(
            ResourceSubscription(
                domain="video",
                subject_key="video:test:A",
                title="好东西",
                status="active",
                upstream_ref={},
                last_seen={},
            )
        )
        await _save_sync_state(
            db, "moviepilot:1:download", {"last_id": 4, "initialized": True}
        )
        await db.commit()

    # MoviePilot 在添加下载时就写历史；任务才 38% 时不能报「下载完成」
    async with Session() as db:
        await service._sync_video_history(db, FakeState())
        await db.commit()

    async with Session() as db:
        assert (await db.execute(sa.select(Notification))).scalars().all() == []
        state_row = (
            await db.execute(
                sa.select(NotificationSyncState).where(
                    NotificationSyncState.key == "moviepilot:1:download"
                )
            )
        ).scalar_one()
        assert state_row.value["last_id"] == 4

    # 任务真正下完后才发通知，水位线推进
    progress["value"] = 1.0
    async with Session() as db:
        await service._sync_video_history(db, FakeState())
        await db.commit()

    async with Session() as db:
        rows = (await db.execute(sa.select(Notification))).scalars().all()
        assert [row.type for row in rows] == ["download.completed"]
        state_row = (
            await db.execute(
                sa.select(NotificationSyncState).where(
                    NotificationSyncState.key == "moviepilot:1:download"
                )
            )
        ).scalar_one()
        assert state_row.value["last_id"] == 5


async def test_history_sync_watermark(notification_db, monkeypatch):
    Session = notification_db
    service = NotificationSyncService()
    history = [{"id": 2, "title": "电影 A", "poster": "https://img.example/a.jpg"}]

    async def fake_history(state, kind):
        return history if kind == "download" else []

    monkeypatch.setattr(service, "_moviepilot_history", fake_history)

    from app.services.notifications.sync import _save_sync_state

    async with Session() as db:
        db.add(
            ResourceSubscription(
                domain="video",
                subject_key="video:test:A",
                title="电影 A",
                status="active",
                upstream_ref={},
                last_seen={},
            )
        )
        db.add(
            ResourceSubscription(
                domain="video",
                subject_key="video:test:B",
                title="电影 B",
                status="active",
                upstream_ref={},
                last_seen={},
            )
        )
        await _save_sync_state(
            db, "moviepilot:1:download", {"last_id": 1, "initialized": True}
        )
        await db.commit()

    async with Session() as db:
        await service._sync_video_history(db, FakeState())
        await db.commit()

    async with Session() as db:
        rows = (await db.execute(sa.select(Notification))).scalars().all()
        assert len(rows) == 1
        assert rows[0].type == "download.completed"

    history.append({"id": 3, "title": "电影 B"})
    async with Session() as db:
        await service._sync_video_history(db, FakeState())
        await db.commit()

    async with Session() as db:
        rows = (await db.execute(sa.select(Notification))).scalars().all()
        assert len(rows) == 2

    async with Session() as db:
        await service._sync_video_history(db, FakeState())
        await db.commit()

    async with Session() as db:
        rows = (await db.execute(sa.select(Notification))).scalars().all()
        assert len(rows) == 2


async def test_music_download_completed_includes_metadata(notification_db, monkeypatch):
    Session = notification_db
    service = NotificationSyncService()

    async with Session() as db:
        db.add(
            ResourceSubscription(
                domain="music",
                subject_key="music:migu:1",
                title="月下西楼",
                status="active",
                poster="https://img.example/music.webp",
                upstream_ref={"source": "migu", "song_id": "1"},
                last_seen={
                    "status": "downloading",
                    "artist": "时生",
                    "album": "月下西楼",
                    "file_size": "53.0 MB",
                },
                revision=1,
            )
        )
        await db.commit()

    async def fake_candidate_apps(db, names):
        return [FakeApp()]

    async def fake_downloads(db, **kwargs):
        return {
            "items": [
                {
                    "task_id": "3",
                    "title": "月下西楼",
                    "source": "migu",
                    "status": "success",
                    "path": "/downloads/时生 - 月下西楼.flac",
                    "size": None,
                }
            ]
        }

    monkeypatch.setattr(sync_module, "candidate_apps", fake_candidate_apps)
    monkeypatch.setattr(sync_module.musicdl, "downloads", fake_downloads)

    async with Session() as db:
        await service._sync_music(db)
        await db.commit()
        notification = (await db.execute(sa.select(Notification))).scalar_one()
        assert notification.type == "download.completed"
        assert notification.payload == {
            "path": "/downloads/时生 - 月下西楼.flac",
            "options": {
                "artist": "时生",
                "album": "月下西楼",
                "file_size": "53.0 MB",
            },
        }


async def test_comic_sync_splits_online_and_local_lifecycle(
    notification_db, monkeypatch
):
    """在线漫画下载完成只算缓存，本地漫画才发「整理入库」。"""
    Session = notification_db

    class _ComicApp:
        def __init__(self, instance_id: int, app_name: str):
            self.instance_id = instance_id
            self.app_name = app_name

    async def fake_candidate_apps(db, names):
        return [_ComicApp(1, "suwayomi"), _ComicApp(2, "comic-auto")]

    async def fake_suwayomi_downloads(db, instance_id=None, limit=20):
        return {
            "items": [
                {
                    "task_id": "10",
                    "title": "在线漫画",
                    "total_chapters": 10,
                    "downloaded_chapters": 10,
                    "status": "done",
                }
            ]
        }

    async def fake_local_downloads(
        db, instance_id=None, limit=20, active_only=False
    ):
        return {
            "items": [
                {
                    "task_id": "20",
                    "title": "本地漫画",
                    "total_chapters": 10,
                    "downloaded_chapters": 10,
                    "status": "done",
                }
            ]
        }

    monkeypatch.setattr(sync_module, "candidate_apps", fake_candidate_apps)
    monkeypatch.setattr(sync_module.suwayomi, "downloads", fake_suwayomi_downloads)
    monkeypatch.setattr(sync_module.comic_auto, "downloads", fake_local_downloads)

    async with Session() as db:
        db.add(
            ResourceSubscription(
                domain="comic",
                subject_key="comic:1:10",
                title="在线漫画",
                status="active",
                upstream_ref={
                    "instance_id": 1,
                    "manga_id": "10",
                    "variant": "online",
                },
                last_seen={
                    "total_chapters": 10,
                    "downloaded_chapters": 9,
                    "status": "downloading",
                },
            )
        )
        db.add(
            ResourceSubscription(
                domain="comic",
                subject_key="comic:2:20",
                title="本地漫画",
                status="active",
                upstream_ref={
                    "instance_id": 2,
                    "manga_id": "20",
                    "variant": "local",
                },
                last_seen={
                    "total_chapters": 10,
                    "downloaded_chapters": 9,
                    "status": "downloading",
                },
            )
        )
        await db.commit()

    service = NotificationSyncService()
    async with Session() as db:
        await service._sync_comic(db)
        await db.commit()

    async with Session() as db:
        rows = (await db.execute(sa.select(Notification))).scalars().all()
        by_title = {row.title: row for row in rows}
        assert by_title["《在线漫画》章节已缓存"].type == "download.completed"
        assert by_title["《本地漫画》已整理入库"].type == "organize.completed"
        assert len(rows) == 2


async def test_cloud_video_sync_maps_lifecycle_notifications(
    notification_db, monkeypatch
):
    """巡影的获取/入库/失败通知要转成 OneTap 的影视生命周期通知。"""
    from app.services.integrations.discovery import DeployedApp

    Session = notification_db
    cloud_app = DeployedApp(
        instance_id=259,
        app_name="mediary-scout",
        display_name="巡影 Mediary Scout",
        instance_name="movie-cloud",
        status="running",
        url="http://mediary-scout:3000",
        api_key="agent-token",
    )
    activity = {
        "active": [],
        "recent": [
            {
                "id": "notification_0",
                "kind": "movie_init",
                "title": "旧记录",
                "body": "📺 旧记录\n\n✅ 已获取入库",
                "createdAt": "2026-11-18T09:00:00Z",
            }
        ],
    }

    async def fake_list_upstreams(db, host, app_names=None):
        return []

    async def fake_candidate_apps(db, app_names):
        return [cloud_app] if app_names == ("mediary-scout",) else []

    async def fake_downloads(db, **kwargs):
        return activity

    monkeypatch.setattr(
        sync_module.mcp_config_service, "list_upstreams", fake_list_upstreams
    )
    monkeypatch.setattr(sync_module, "candidate_apps", fake_candidate_apps)
    monkeypatch.setattr(sync_module.mediary_scout, "downloads", fake_downloads)

    async with Session() as db:
        db.add(
            ResourceSubscription(
                domain="video",
                subject_key="video:mediary-scout:413577:S1",
                title="举起手来！",
                status="active",
                upstream_ref={"media_source": "mediary-scout", "instance_id": 259},
                last_seen={},
            )
        )
        await db.commit()

    service = NotificationSyncService()
    # 首次同步只建立基线，不补发历史通知
    await service.tick()
    async with Session() as db:
        assert (await db.execute(sa.select(Notification))).scalars().all() == []

    activity["recent"] = [
        {
            "id": "notification_2",
            "kind": "no_coverage",
            "title": "饥饿游戏：收获日出",
            "body": "📺 饥饿游戏：收获日出\n\n🔍 暂未找到可用资源 · 将持续尝试",
            "createdAt": "2026-11-18T11:00:00Z",
        },
        {
            "id": "notification_1",
            "kind": "movie_init",
            "title": "举起手来！",
            "body": "📺 举起手来！\n\n✅ 已获取入库\n🎞 体积：12.3 GB",
            "createdAt": "2026-11-18T10:00:00Z",
        },
    ]
    await service.tick()

    async with Session() as db:
        rows = (await db.execute(sa.select(Notification))).scalars().all()
        by_title = {row.title: row for row in rows}
        assert by_title["《举起手来！》已整理入库"].type == "organize.completed"
        assert "体积：12.3 GB" in by_title["《举起手来！》已整理入库"].markdown
        failed = by_title["《饥饿游戏：收获日出》处理失败"]
        assert failed.type == "action.failed"
        assert "暂未找到可用资源" in failed.markdown
        sub = (await db.execute(sa.select(ResourceSubscription))).scalar_one()
        assert sub.status == "completed"

    # 同一批通知不会重复推送
    await service.tick()
    async with Session() as db:
        rows = (await db.execute(sa.select(Notification))).scalars().all()
        assert len(rows) == 2


async def test_cloud_video_sync_creates_subscription_from_library(
    notification_db, monkeypatch
):
    """巡影里直接订阅的条目没有 OneTap 台账，同步时按媒体库补一条并带上海报。"""
    from app.services.integrations.discovery import DeployedApp

    Session = notification_db
    cloud_app = DeployedApp(
        instance_id=259,
        app_name="mediary-scout",
        display_name="巡影 Mediary Scout",
        instance_name="movie-cloud",
        status="running",
        url="http://mediary-scout:3000",
        api_key="agent-token",
    )
    activity = {"active": [], "recent": []}

    async def fake_list_upstreams(db, host, app_names=None):
        return []

    async def fake_candidate_apps(db, app_names):
        return [cloud_app] if app_names == ("mediary-scout",) else []

    async def fake_downloads(db, **kwargs):
        return activity

    async def fake_library(db, **kwargs):
        return [
            {
                "title": "饥饿游戏：收获日出",
                "tmdbId": 1300968,
                "type": "movie",
                "year": 2026,
                "seasonNumber": 1,
                "totalEpisodes": 1,
                "obtainedCount": 0,
            }
        ]

    async def fake_metadata(tmdb_id, type_name=None):
        return {
            "poster": "https://image.tmdb.org/t/p/w342/hunger.jpg",
            "year": "2026",
            "genres": ["科幻", "动作"],
            "rating": 9.4,
            "runtime": 159,
            "directors": ["弗朗西斯·劳伦斯"],
            "actors": ["Joseph Zada"],
        }

    monkeypatch.setattr(
        sync_module.mcp_config_service, "list_upstreams", fake_list_upstreams
    )
    monkeypatch.setattr(sync_module, "candidate_apps", fake_candidate_apps)
    monkeypatch.setattr(sync_module.mediary_scout, "downloads", fake_downloads)
    monkeypatch.setattr(sync_module.mediary_scout, "library_raw", fake_library)
    monkeypatch.setattr(
        sync_module.mediary_scout, "tmdb_metadata", fake_metadata
    )

    service = NotificationSyncService()
    # 首次同步只建立基线
    await service.tick()
    activity["recent"] = [
        {
            "id": "notification_9",
            "kind": "no_coverage",
            "title": "饥饿游戏：收获日出",
            "body": "📺 饥饿游戏：收获日出\n\n🔍 暂未找到可用资源 · 将持续尝试",
            "createdAt": "2026-11-18T11:00:00Z",
        }
    ]
    await service.tick()

    async with Session() as db:
        record = (await db.execute(sa.select(ResourceSubscription))).scalar_one()
        assert record.title == "饥饿游戏：收获日出"
        assert record.subject_key == "video:mediary-scout:1300968:S1"
        assert record.poster == "https://image.tmdb.org/t/p/w342/hunger.jpg"
        assert record.last_seen["rating"] == 9.4
        assert record.last_seen["directors"] == ["弗朗西斯·劳伦斯"]

        notification = (await db.execute(sa.select(Notification))).scalar_one()
        assert notification.type == "action.failed"
        assert notification.image_url == record.poster
        assert notification.resource_subscription_id == record.id
        options = notification.payload["options"]
        assert options["type"] == "电影"
        assert options["year"] == "2026"
        assert options["rating"] == 9.4
