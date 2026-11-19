"""候选卡片推送测试：归一化、文案与渠道投递。"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — 注册所有模型到 Base.metadata
from app.core.exceptions import APIException
from app.database import Base
from app.models.notification import NotificationChannel
from app.services.notifications import present
from app.services.notifications.presentation import (
    build_items,
    format_bitrate,
    format_duration,
    format_size,
)


def test_compact_units():
    assert format_duration(225) == "03:45"
    assert format_duration(3725) == "1:02:05"
    assert format_duration("03:39") == "03:39"
    assert format_duration(None) == ""
    assert format_size(55713936) == "53.1MB"
    assert format_size("8.2 MB") == "8.2MB"
    assert format_size(0) == ""
    assert format_bitrate(320000) == "320kbps"
    assert format_bitrate("2896 kbps") == "2896kbps"
    assert format_bitrate(320) == "320kbps"


def test_build_items_per_domain():
    music = build_items(
        "music",
        [
            {
                "title": "花开了就好",
                "artist": "姚佳顺子",
                "album": "花开了就好",
                "duration": 225,
                "bitrate": 320000,
                "size": 8200000,
                "cover": "https://img.example/m.jpg",
            }
        ],
    )[0]
    assert music.first_line(1) == "**1. 花开了就好** — 姚佳顺子"
    # 专辑与标题同名时省略
    assert music.meta == "03:45 · 320kbps · 7.8MB"
    assert music.image == "https://img.example/m.jpg"

    video = build_items(
        "video",
        [{"title": "举起手来！", "year": "2005", "type": "电影", "media_source": "themoviedb", "vote": 7.4}],
    )[0]
    assert video.first_line(1) == "**1. 举起手来！** — 2005"
    # 视频按「标签： 值」逐行展示，不用「 · 」拼接
    assert video.details == (
        "**类型：** 电影",
        "**评分：** 7.4 分",
        "**来源：** themoviedb",
    )
    assert video.plain_line(1) == (
        "1. 举起手来！ — 2005\n"
        "  **类型：** 电影\n"
        "  **评分：** 7.4 分\n"
        "  **来源：** themoviedb"
    )

    # 视频只展示类型、评分、来源，其余字段不进入卡片
    series = build_items(
        "video",
        [
            {
                "title": "最后生还者",
                "year": "2023",
                "type": "电视剧",
                "media_source": "themoviedb",
                "vote": 8.4,
                "vote_count": 7523,
                "original_title": "The Last of Us",
                "number_of_seasons": 2,
                "number_of_episodes": 16,
                "runtime": 123,
                "overview": "二十年  后，文明\n已被摧毁。" + "剧情" * 60,
            }
        ],
    )[0]
    assert series.details == (
        "**类型：** 电视剧",
        "**评分：** 8.4 分",
        "**来源：** themoviedb",
    )

    comic = build_items(
        "comic",
        [{"title": "开局表白美女师尊", "author": "博易动漫", "category": "comic", "status": "ONGOING", "in_library": True}],
    )[0]
    assert comic.meta == "漫画 · 连载中 · 已入库"

    novel = build_items(
        "novel",
        [{"title": "万古神帝", "author": "飞天鱼", "origin_name": "阅读书库（优）", "total_chapters": 4419}],
    )[0]
    assert novel.meta == "阅读书库（优） · 4419 章"


def _session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.mark.asyncio
async def test_push_candidates_delivers_to_enabled_channels(monkeypatch):
    engine, session_factory = _session_factory()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    sent = []

    class FakeChannel:
        def __init__(self, record):
            self.record = record

        async def send(self, message):
            sent.append((self.record.name, message))
            return f"om_{self.record.name}"

    monkeypatch.setattr(present, "build_channel", FakeChannel)

    async with session_factory() as db:
        db.add(
            NotificationChannel(
                name="家庭飞书", type="feishu", enabled=True, config={}, secrets=None
            )
        )
        db.add(
            NotificationChannel(
                name="停用渠道", type="feishu", enabled=False, config={}, secrets=None
            )
        )
        await db.commit()

        result = await present.push_candidates(
            db,
            domain="music",
            keyword="花开了",
            items=[
                {
                    "title": "花开了就好",
                    "artist": "姚佳顺子",
                    "duration": 225,
                    "bitrate": 320000,
                    "size": 8200000,
                }
            ],
        )

    assert result["items"] == 1
    assert [item["channel"] for item in result["pushed"]] == ["家庭飞书"]
    assert result["failed"] == []
    assert len(sent) == 1

    name, message = sent[0]
    assert name == "家庭飞书"
    assert message.title == "《花开了》"
    assert message.domain == "music"
    presentation = message.payload["presentation"]
    assert presentation["subtitle"] == "音乐 · 1 条候选"
    assert presentation["items"][0]["title"] == "花开了就好"
    assert presentation["note"] == "回复序号选择一首"
    assert "1. 花开了就好 — 姚佳顺子" in message.markdown
    assert "03:45 · 320kbps · 7.8MB" in message.markdown

    await engine.dispose()


@pytest.mark.asyncio
async def test_push_candidates_requires_channel(monkeypatch):
    engine, session_factory = _session_factory()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        with pytest.raises(APIException):
            await present.push_candidates(
                db,
                domain="music",
                items=[{"title": "花开了就好"}],
            )

    await engine.dispose()


@pytest.mark.asyncio
async def test_push_candidates_reports_unknown_channel_name(monkeypatch):
    engine, session_factory = _session_factory()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        db.add(
            NotificationChannel(
                name="家庭飞书", type="feishu", enabled=True, config={}, secrets=None
            )
        )
        await db.commit()
        with pytest.raises(APIException) as excinfo:
            await present.push_candidates(
                db,
                domain="music",
                items=[{"title": "花开了就好"}],
                channel_names=["不存在的渠道"],
            )
        assert "家庭飞书" in str(excinfo.value)

    await engine.dispose()


@pytest.mark.asyncio
async def test_push_candidates_keeps_going_when_one_channel_fails(monkeypatch):
    engine, session_factory = _session_factory()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    class FlakyChannel:
        def __init__(self, record):
            self.record = record

        async def send(self, message):
            if self.record.name == "坏的渠道":
                raise RuntimeError("boom")
            return "om_ok"

    monkeypatch.setattr(present, "build_channel", FlakyChannel)

    async with session_factory() as db:
        db.add_all(
            [
                NotificationChannel(
                    name="坏的渠道", type="feishu", enabled=True, config={}, secrets=None
                ),
                NotificationChannel(
                    name="好的渠道", type="feishu", enabled=True, config={}, secrets=None
                ),
            ]
        )
        await db.commit()
        result = await present.push_candidates(
            db, domain="music", items=[{"title": "花开了就好"}]
        )

    assert [item["channel"] for item in result["pushed"]] == ["好的渠道"]
    assert result["failed"][0]["channel"] == "坏的渠道"
    assert "boom" in result["failed"][0]["error"]

    await engine.dispose()
