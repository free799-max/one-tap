"""小说订阅通知：台账 subject_key 与缓存/导出事件。"""

import hashlib

import sqlalchemy as sa

from app.models.notification import Notification, ResourceSubscription
from app.services.notifications.subscriptions import (
    notify_reader_cached,
    notify_reader_export,
    record_added,
)

SEARCH_URL = "http://search.example/book/1.html"
FINAL_URL = "http://final.example/book/660.html"


def _subject_key(book_url: str) -> str:
    return "novel:" + hashlib.sha1(book_url.encode()).hexdigest()


def _subscription(book_url: str, *, origin: str) -> ResourceSubscription:
    return ResourceSubscription(
        domain="novel",
        subject_key=_subject_key(book_url),
        title="斗破苍穹",
        status="active",
        origin=origin,
        upstream_ref={"book_url": book_url},
        last_seen={"status": "active"},
    )


async def _notifications(Session) -> list[Notification]:
    async with Session() as db:
        return list((await db.execute(sa.select(Notification))).scalars())


async def test_record_added_uses_switched_book_url(notification_db):
    Session = notification_db
    async with Session() as db:
        record = await record_added(
            db,
            domain="novel",
            item_id=SEARCH_URL,
            options={"name": "斗破苍穹", "bookUrl": SEARCH_URL},
            result={
                "instance_id": 56,
                "result": {
                    "bookUrl": FINAL_URL,
                    "author": "天蚕土豆",
                    "originName": "七猫小说（优+）",
                    "coverUrl": "https://img.example/doupo.jpg",
                    "totalChapterNum": 1914,
                },
                "source_selection": {"selected": {"book_url": FINAL_URL}},
            },
            instance_id=56,
        )
        await db.commit()

    assert record.subject_key == _subject_key(FINAL_URL)
    assert record.upstream_ref["book_url"] == FINAL_URL
    assert record.poster == "https://img.example/doupo.jpg"
    assert record.last_seen["author"] == "天蚕土豆"
    assert record.last_seen["origin_name"] == "七猫小说（优+）"
    # 加入书架的台账与之后同步、导出使用的 subject_key 必须一致
    assert _subject_key(FINAL_URL) != _subject_key(SEARCH_URL)

    rows = await _notifications(Session)
    assert rows[0].image_url == "https://img.example/doupo.jpg"
    options = rows[0].payload["options"]
    assert options["author"] == "天蚕土豆"
    assert options["origin_name"] == "七猫小说（优+）"
    assert options["total_chapters"] == 1914


async def test_record_added_drops_non_image_cover(notification_db):
    """详情页地址不能当封面，留空等同步用书架封面补齐。"""
    Session = notification_db
    async with Session() as db:
        record = await record_added(
            db,
            domain="novel",
            item_id=SEARCH_URL,
            options={
                "name": "万古神帝",
                "bookUrl": SEARCH_URL,
                "cover": "http://www.92xs.info/book/862.html",
            },
            result={
                "instance_id": 56,
                "result": {
                    "bookUrl": FINAL_URL,
                    "coverUrl": "/assets/admin/covers/a.jpg",
                },
            },
            instance_id=56,
        )
        await db.commit()

    assert record.poster is None
    rows = await _notifications(Session)
    assert rows[0].image_url is None


async def test_record_added_falls_back_to_search_url(notification_db):
    Session = notification_db
    async with Session() as db:
        record = await record_added(
            db,
            domain="novel",
            item_id=SEARCH_URL,
            options={"name": "斗破苍穹", "bookUrl": SEARCH_URL},
            result={"instance_id": 56, "skipped": "already_subscribed"},
            instance_id=56,
        )
        await db.commit()

    assert record.subject_key == _subject_key(SEARCH_URL)


async def test_first_export_notifies_onetap_added_book(notification_db):
    Session = notification_db
    async with Session() as db:
        db.add(_subscription(FINAL_URL, origin="mcp"))
        await db.commit()

    await notify_reader_export(
        book_url=FINAL_URL,
        title="斗破苍穹",
        total=1914,
        previous_exported=-1,
        destination="/library/斗破苍穹.epub",
        author="天蚕土豆",
        origin_name="七猫小说（优+）",
        cover="https://img.example/doupo.jpg",
        size=2048000,
    )

    rows = await _notifications(Session)
    assert [row.type for row in rows] == ["organize.completed"]
    assert rows[0].title == "《斗破苍穹》已导出到书库"
    assert "已导出 1914 章" in rows[0].markdown
    assert rows[0].image_url == "https://img.example/doupo.jpg"
    options = rows[0].payload["options"]
    assert options["author"] == "天蚕土豆"
    assert options["origin_name"] == "七猫小说（优+）"
    assert options["total_chapters"] == 1914
    assert options["file_size"] == 2048000

    async with Session() as db:
        record = (await db.execute(sa.select(ResourceSubscription))).scalar_one()
        assert record.status == "completed"
        assert record.last_seen["exported_total"] == 1914
        assert record.poster == "https://img.example/doupo.jpg"


async def test_first_export_of_existing_shelf_book_only_baselines(notification_db):
    Session = notification_db
    async with Session() as db:
        db.add(_subscription(FINAL_URL, origin="sync"))
        await db.commit()

    await notify_reader_export(
        book_url=FINAL_URL,
        title="斗破苍穹",
        total=1914,
        previous_exported=-1,
        destination="/library/斗破苍穹.epub",
    )

    assert await _notifications(Session) == []
    async with Session() as db:
        record = (await db.execute(sa.select(ResourceSubscription))).scalar_one()
        assert record.last_seen["exported_total"] == 1914


async def test_later_export_notifies_existing_shelf_book(notification_db):
    Session = notification_db
    async with Session() as db:
        db.add(_subscription(FINAL_URL, origin="sync"))
        await db.commit()

    await notify_reader_export(
        book_url=FINAL_URL,
        title="斗破苍穹",
        total=1918,
        previous_exported=1914,
        destination="/library/斗破苍穹.epub",
    )

    rows = await _notifications(Session)
    assert [row.type for row in rows] == ["organize.completed"]
    assert "已导出 1918 章" in rows[0].markdown


async def test_reader_cached_notifies_once(notification_db):
    Session = notification_db
    async with Session() as db:
        db.add(_subscription(FINAL_URL, origin="mcp"))
        await db.commit()

    await notify_reader_cached(
        book_url=FINAL_URL, title="斗破苍穹", total=1914, cached=1914
    )
    await notify_reader_cached(
        book_url=FINAL_URL, title="斗破苍穹", total=1914, cached=1914
    )

    rows = await _notifications(Session)
    assert [row.type for row in rows] == ["download.completed"]
    assert rows[0].title == "《斗破苍穹》章节已缓存"
    assert "已缓存 1914/1914 章" in rows[0].markdown


async def test_reader_cached_skips_unknown_or_sync_book(notification_db):
    Session = notification_db
    async with Session() as db:
        db.add(_subscription(FINAL_URL, origin="sync"))
        await db.commit()

    await notify_reader_cached(
        book_url=FINAL_URL, title="斗破苍穹", total=1914, cached=1914
    )
    await notify_reader_cached(
        book_url="http://other.example/book/2.html",
        title="另一本",
        total=10,
        cached=10,
    )

    assert await _notifications(Session) == []
