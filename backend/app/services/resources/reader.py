"""Reader 小说适配器：把 OneTap 的小说域语义映射到 Reader 网页端接口。

Reader 没有资源网关式的 MCP，订阅即书架条目、下载即章节缓存，
因此在本模块内把书架与缓存状态翻译成与影视域一致的函数面。
"""

from __future__ import annotations

import html
import re
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import APIException
from app.services.apps.reader.client import ReaderClientError, ReaderSession
from app.services.integrations.discovery import DeployedApp
from app.services.resources.base import base_url, resolve_app

APP_NAMES = ("reader",)
MAX_CHAPTERS = 20
MAX_SOURCE_CANDIDATES = 8
SOURCE_SEARCH_SIZE = 20
_TAG_PATTERN = re.compile(r"<[^>]+>")


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _plain(value: Any) -> str:
    """去掉书源简介里的 HTML 标签与实体。"""
    text = _TAG_PATTERN.sub("", _text(value))
    return html.unescape(text).strip()


def _number(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        return int(float(_text(value)))
    except ValueError:
        return 0


def _book_url(item: dict[str, Any]) -> str:
    return _text(item.get("bookUrl") or item.get("url"))


def _book_title(item: dict[str, Any]) -> str:
    return _plain(item.get("name") or item.get("title"))


def _cover(item: dict[str, Any]) -> str:
    return _text(item.get("coverUrl") or item.get("cover"))


def _optional(value: Any) -> str | None:
    text = _text(value)
    return text or None


def _progress(cached: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(max(0.0, min(cached / total, 1.0)), 2)


def _exact_match(
    candidate: dict[str, Any], title: str, author: str
) -> bool:
    return (
        _book_title(candidate) == title
        and _plain(candidate.get("author")) == author
    )


async def _select_best_source(
    session: ReaderSession,
    *,
    book_url: str,
    title: str,
    author: str,
    initial_book: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """搜索同书书源，按目录章节数保留最多的源。

    Reader 的换源接口会同时拉取目标源目录，因此可以直接用返回的
    ``totalChapterNum`` 比较；只有该值为空时才额外读取一次目录。
    """
    initial_origin = _text(initial_book.get("origin"))
    initial_origin_name = _optional(initial_book.get("originName"))
    initial_count = _number(initial_book.get("totalChapterNum"))
    if initial_count <= 0:
        try:
            initial_count = len(await session.chapter_list(book_url))
        except ReaderClientError:
            initial_count = 0

    selection: dict[str, Any] = {
        "checked": 0,
        "selected": {
            "origin": initial_origin or None,
            "origin_name": initial_origin_name,
            "total_chapters": initial_count,
            "book_url": book_url,
        },
        "candidates": [],
    }
    best_book = initial_book
    best_count = initial_count
    best_url = book_url
    best_origin = initial_origin

    try:
        found, _ = await session.search_book_sources(
            book_url, search_size=SOURCE_SEARCH_SIZE
        )
    except ReaderClientError as exc:
        selection["warning"] = f"搜索同书书源失败，保留当前源：{exc}"
        return best_book, selection

    candidates = []
    seen = set()
    current_origin_name = initial_origin_name
    for item in found:
        candidate_url = _book_url(item)
        origin = _text(item.get("origin"))
        if (
            not candidate_url
            or not origin
            or candidate_url == book_url
            or origin == initial_origin
            or not _exact_match(item, title, author)
        ):
            continue
        key = (origin, candidate_url)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(item)
        if len(candidates) >= MAX_SOURCE_CANDIDATES:
            break

    current_url = book_url
    for candidate in candidates:
        candidate_url = _book_url(candidate)
        origin = _text(candidate.get("origin"))
        origin_name = _optional(candidate.get("originName"))
        try:
            switched = await session.set_book_source(
                current_url, candidate_url, origin
            )
        except ReaderClientError as exc:
            selection["candidates"].append(
                {
                    "origin": origin,
                    "origin_name": origin_name,
                    "total_chapters": 0,
                    "status": "error",
                    "message": str(exc),
                }
            )
            continue

        switched_url = _book_url(switched) or candidate_url
        count = _number(switched.get("totalChapterNum"))
        if count <= 0:
            try:
                count = len(await session.chapter_list(switched_url))
            except ReaderClientError:
                count = 0
        current_url = switched_url
        current_origin_name = _optional(switched.get("originName")) or origin_name
        selection["candidates"].append(
            {
                "origin": origin,
                "origin_name": current_origin_name,
                "total_chapters": count,
                "status": "ok",
            }
        )
        if count > best_count:
            best_count = count
            best_url = switched_url
            best_origin = _text(switched.get("origin")) or origin
            best_book = switched

    selection["checked"] = len(candidates)
    if current_url != best_url and best_origin:
        try:
            switched = await session.set_book_source(
                current_url, best_url, best_origin
            )
            best_book = switched or best_book
        except ReaderClientError as exc:
            selection["warning"] = (
                f"已找到章节最多的源，但切回该源失败：{exc}"
            )

    selection["selected"] = {
        "origin": best_origin or None,
        "origin_name": (
            _optional(best_book.get("originName"))
            or (initial_origin_name if best_url == book_url else None)
        ),
        "total_chapters": best_count,
        "book_url": best_url,
    }
    if not candidates:
        selection["warning"] = "没有找到其他同书名、同作者的候选书源"
    elif best_count <= 0:
        selection["warning"] = "候选书源均未读取到目录章节"
    return best_book, selection


async def _call(
    db: AsyncSession,
    instance_id: int | None,
    action: Callable[[ReaderSession], Awaitable[Any]],
) -> tuple[DeployedApp, Any]:
    """取可用实例、登录并执行一次操作；Reader 每次登录都会轮换令牌。"""
    app = await resolve_app(db, APP_NAMES, instance_id)
    session = ReaderSession(
        base_url(app),
        username=app.username or "",
        password=app.password or "",
    )
    try:
        async with session:
            return app, await action(session)
    except ReaderClientError as exc:
        raise APIException(f"Reader 调用失败：{exc}", 502) from exc


def _book_payload(
    book_url: str, title: str, source: dict[str, Any]
) -> dict[str, Any]:
    """把搜索结果（可能是归一化字段）还原成 saveBook 需要的书籍对象。"""
    normalized_only = {
        "item_id",
        "cover",
        "origin_name",
        "latest_chapter",
        "total_chapters",
    }
    book = {
        key: value for key, value in source.items() if key not in normalized_only
    }
    book["bookUrl"] = book_url
    book["name"] = title
    book.setdefault("author", _text(source.get("author")))
    book.setdefault("origin", _text(source.get("origin")))
    book.setdefault(
        "originName", _text(source.get("originName") or source.get("origin_name"))
    )
    book.setdefault("coverUrl", _cover(source))
    book.setdefault("intro", _plain(source.get("intro")))
    book.setdefault(
        "latestChapterTitle",
        _text(source.get("latestChapterTitle") or source.get("latest_chapter")),
    )
    book.setdefault(
        "totalChapterNum",
        _number(
            source.get("totalChapterNum")
            if "totalChapterNum" in source
            else source.get("total_chapters")
        ),
    )
    book.setdefault("canUpdate", bool(source.get("canUpdate", True)))
    return book


async def search(
    db: AsyncSession,
    *,
    keyword: str,
    instance_id: int | None = None,
    page: int = 1,
    count: int = 20,
) -> dict[str, Any]:
    app, books = await _call(
        db, instance_id, lambda session: session.search_books(keyword, page)
    )
    limit = max(count, 0)
    items: list[dict[str, Any]] = []
    for book in books:
        item_id = _book_url(book)
        if not item_id:
            continue
        total = _number(book.get("totalChapterNum"))
        items.append(
            {
                "item_id": item_id,
                "title": _book_title(book),
                "author": _optional(book.get("author")),
                "origin": _optional(book.get("origin")),
                "origin_name": _optional(book.get("originName")),
                "cover": _optional(_cover(book)),
                "intro": _plain(book.get("intro"))[:300] or None,
                "latest_chapter": _optional(book.get("latestChapterTitle")),
                "total_chapters": total,
            }
        )
        if len(items) >= limit:
            break
    return {"instance_id": app.instance_id, "items": items}


async def detail(
    db: AsyncSession,
    *,
    item_id: str,
    instance_id: int | None = None,
) -> dict[str, Any]:
    async def action(session: ReaderSession) -> tuple[dict[str, Any], list[dict[str, Any]], str | None]:
        shelf = await session.shelf()
        book = next(
            (item for item in shelf if _book_url(item) == item_id), {}
        )
        warning: str | None = None
        try:
            chapters = await session.chapter_list(item_id)
        except ReaderClientError as exc:
            chapters = []
            warning = f"读取目录失败，仅返回书架信息：{exc}"
        return book, chapters, warning

    app, (book, chapters, warning) = await _call(db, instance_id, action)
    total = _number(book.get("totalChapterNum")) or len(chapters)
    result: dict[str, Any] = {
        "item_id": item_id,
        "title": _book_title(book),
        "author": _optional(book.get("author")),
        "origin": _optional(book.get("origin")),
        "cover": _optional(_cover(book)),
        "intro": _plain(book.get("intro"))[:300] or None,
        "total_chapters": total,
        "cached_chapters": _number(book.get("cachedChapterCount")),
        "chapters": [
            {
                "index": _number(chapter.get("index")),
                "title": _plain(chapter.get("title")),
                "url": _text(chapter.get("url")),
            }
            for chapter in chapters[:MAX_CHAPTERS]
        ],
    }
    if warning:
        result["warning"] = warning
    return {"instance_id": app.instance_id, "detail": result}


async def subscriptions(
    db: AsyncSession,
    *,
    instance_id: int | None = None,
    page: int = 1,
    count: int = 30,
) -> dict[str, Any]:
    app, shelf = await _call(db, instance_id, lambda session: session.shelf())
    offset = max(page - 1, 0) * max(count, 0)
    items: list[dict[str, Any]] = []
    for book in shelf[offset : offset + max(count, 0)]:
        book_url = _book_url(book)
        if not book_url:
            continue
        total = _number(book.get("totalChapterNum"))
        cached = _number(book.get("cachedChapterCount"))
        items.append(
            {
                "subscription_id": book_url,
                "title": _book_title(book),
                "author": _optional(book.get("author")),
                "total_chapters": total,
                "cached_chapters": cached,
                "progress": _progress(cached, total),
            }
        )
    return {"instance_id": app.instance_id, "items": items}


async def subscribe(
    db: AsyncSession,
    *,
    item_id: str,
    name: str | None = None,
    options: dict[str, Any] | None = None,
    instance_id: int | None = None,
) -> dict[str, Any]:
    source = dict(options or {})
    book_url = _book_url(source) or _text(item_id)
    title = _plain(name) if name else _book_title(source)
    author = _plain(source.get("author"))
    if not title or not book_url:
        raise APIException("小说订阅需要搜索结果里的书籍信息", 400)
    book = _book_payload(book_url, title, source)

    async def action(
        session: ReaderSession,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            shelf = await session.shelf()
        except ReaderClientError:
            shelf = []
        existing = next(
            (item for item in shelf if _book_url(item) == book_url), None
        )
        if existing is not None:
            return existing, {"skipped": "already_subscribed"}

        saved = await session.save_book(book)
        initial_book = {**book, **saved}
        if not author:
            data, selection = saved, {
                "checked": 0,
                "warning": "缺少作者信息，未比较其他书源",
            }
        else:
            data, selection = await _select_best_source(
                session,
                book_url=book_url,
                title=title,
                author=author,
                initial_book=initial_book,
            )
        if not _cover(data):
            # 搜索结果经常没有封面，书架条目会带 Reader 缓存后的封面地址，
            # 少了它「已加入书架」通知卡片就没有图片
            try:
                fresh = next(
                    (
                        item
                        for item in await session.shelf()
                        if _book_url(item) == book_url
                    ),
                    None,
                )
            except ReaderClientError:
                fresh = None
            if fresh is not None:
                data = {**data, **fresh}
        return data, selection

    app, (data, selection) = await _call(db, instance_id, action)
    cover = _cover(data)
    if cover.startswith("/"):
        # Reader 缓存封面返回站内相对路径，补成绝对地址通知渠道才能取图
        data = {**data, "coverUrl": f"{base_url(app).rstrip('/')}{cover}"}
    return {
        "instance_id": app.instance_id,
        "result": data,
        "source_selection": selection,
    }


async def unsubscribe(
    db: AsyncSession,
    *,
    subscription_id: str,
    instance_id: int | None = None,
) -> dict[str, Any]:
    async def action(session: ReaderSession) -> str:
        shelf = await session.shelf()
        book = next(
            (item for item in shelf if _book_url(item) == subscription_id), None
        )
        title = _book_title(book) if book else ""
        await session.remove_book(subscription_id, title)
        return title

    app, _ = await _call(db, instance_id, action)
    return {"instance_id": app.instance_id, "result": {"removed": subscription_id}}


async def downloads(
    db: AsyncSession,
    *,
    instance_id: int | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    app, shelf = await _call(db, instance_id, lambda session: session.shelf())
    root = base_url(app).rstrip("/")
    items: list[dict[str, Any]] = []
    for book in shelf[: max(limit, 0)]:
        book_url = _book_url(book)
        if not book_url:
            continue
        total = _number(book.get("totalChapterNum"))
        cached = _number(book.get("cachedChapterCount"))
        if total > 0 and cached >= total:
            status = "done"
        elif total > 0 and 0 < cached < total:
            status = "caching"
        else:
            status = "idle"
        cover = _cover(book)
        if cover.startswith("/"):
            cover = f"{root}{cover}"
        items.append(
            {
                "task_id": book_url,
                "title": _book_title(book),
                "author": _optional(book.get("author")),
                "origin_name": _optional(book.get("originName")),
                "cover": _optional(cover),
                "status": status,
                "progress": _progress(cached, total),
                "cached_chapters": cached,
                "total_chapters": total,
            }
        )
    return {"instance_id": app.instance_id, "items": items}


async def download(
    db: AsyncSession,
    *,
    task_id: str,
    action: str,
    confirm: bool = False,
    instance_id: int | None = None,
) -> dict[str, Any]:
    if action != "start":
        raise APIException("小说域暂不支持停止或删除缓存任务", 400)
    app, _ = await _call(
        db, instance_id, lambda session: session.cache_books([task_id])
    )
    return {
        "instance_id": app.instance_id,
        "result": {"task_id": task_id, "action": "start"},
    }


async def replace(
    db: AsyncSession,
    *,
    subscription_id: str,
    confirm: bool = False,
    options: dict[str, Any] | None = None,
    instance_id: int | None = None,
) -> dict[str, Any]:
    """重新缓存缺失章节：Reader 只补缺，不会删除已有文件。"""
    if not confirm:
        raise APIException("替换资源前需要用户确认，请传 confirm=true", 400)
    app, _ = await _call(
        db, instance_id, lambda session: session.cache_books([subscription_id])
    )
    return {"instance_id": app.instance_id, "result": {"requeued": subscription_id}}
