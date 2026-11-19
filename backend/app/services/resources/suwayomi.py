"""Suwayomi（Tachiyomi 服务端）漫画资源适配器。

把 OneTap 的统一资源语义映射到 Suwayomi 的 GraphQL 接口。
所有查询都通过 variables 传参，避免拼接用户输入。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import APIException
from app.services.apps.suwayomi.client import execute_graphql
from app.services.integrations.discovery import DeployedApp
from app.services.resources.base import base_url, download_phase, resolve_app

_APP_NAMES = ("suwayomi",)
_CHAPTER_PAGE_SIZE = 200

SOURCES_QUERY = """
query Sources($first: Int!) {
  sources(first: $first) {
    nodes { id name lang }
  }
}
"""

SEARCH_MUTATION = """
mutation SearchManga(
  $source: LongString!
  $query: String
  $page: Int!
  $type: FetchSourceMangaType!
) {
  fetchSourceManga(input: {source: $source, query: $query, page: $page, type: $type}) {
    hasNextPage
    mangas {
      id
      title
      author
      artist
      genre
      status
      thumbnailUrl
      sourceId
      user { inLibrary }
    }
  }
}
"""

DETAIL_QUERY = """
query MangaDetail($id: Int!) {
  manga(id: $id) {
    id
    title
    author
    artist
    description
    genre
    status
    thumbnailUrl
    realUrl
    sourceId
    user { inLibrary }
    chapters { totalCount }
  }
}
"""

LIBRARY_QUERY = """
query Library($first: Int!, $offset: Int!) {
  mangas(filter: {inLibrary: {equalTo: true}}, first: $first, offset: $offset) {
    totalCount
    nodes {
      id
      title
      author
      thumbnailUrl
      initialized
    }
  }
}
"""

DOWNLOAD_OVERVIEW_QUERY = """
query DownloadOverview($first: Int!, $offset: Int!) {
  mangas(filter: {inLibrary: {equalTo: true}}, first: $first, offset: $offset) {
    totalCount
    nodes {
      id
      title
      user { downloadCount }
      chapters { totalCount }
    }
  }
}
"""

CHAPTERS_QUERY = """
query Chapters($mangaId: Int!, $first: Int!, $offset: Int!) {
  chapters(filter: {mangaId: {equalTo: $mangaId}}, first: $first, offset: $offset) {
    totalCount
    nodes {
      id
      name
      user { isDownloaded }
    }
  }
}
"""

UPDATE_LIBRARY_MUTATION = """
mutation UpdateLibrary($id: Int!, $inLibrary: Boolean!) {
  updateManga(input: {id: $id, patch: {inLibrary: $inLibrary}}) {
    manga { id title user { inLibrary } }
  }
}
"""

FETCH_MANGA_AND_CHAPTERS_MUTATION = """
mutation FetchMangaAndChapters($id: Int!) {
  fetchMangaAndChapters(input: {id: $id, fetchManga: true, fetchChapters: true}) {
    manga { id }
  }
}
"""

ENQUEUE_DOWNLOADS_MUTATION = """
mutation EnqueueDownloads($ids: [Int!]!) {
  enqueueChapterDownloads(input: {ids: $ids}) {
    downloadStatus { state queueSize }
  }
}
"""

START_DOWNLOADER_MUTATION = """
mutation StartDownloader {
  startDownloader(input: {}) {
    downloadStatus { state queueSize }
  }
}
"""

STOP_DOWNLOADER_MUTATION = """
mutation StopDownloader {
  stopDownloader(input: {}) {
    downloadStatus { state queueSize }
  }
}
"""

CLEAR_DOWNLOADER_MUTATION = """
mutation ClearDownloader {
  clearDownloader(input: {}) {
    downloadStatus { state queueSize }
  }
}
"""


def _as_int(value: Any, field: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise APIException(f"{field} 必须是数字", 400) from exc


async def _state(db: AsyncSession, instance_id: int | None) -> DeployedApp:
    return await resolve_app(db, _APP_NAMES, instance_id)


async def _gql(
    state: DeployedApp,
    query: str,
    variables: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """执行一次 GraphQL 请求，并把网络/协议错误统一转成 APIException。"""
    return await execute_graphql(
        base_url(state),
        query,
        variables,
        username=state.username or "",
        password=state.password or "",
    )


async def _pick_source(state: DeployedApp) -> str:
    """按装源结果选择在线图源，未配置时优先中文源。"""
    data = await _gql(state, SOURCES_QUERY, {"first": 500})
    sources = [
        item
        for item in (data.get("sources") or {}).get("nodes") or []
        if str(item.get("id") or "") not in {"", "0"}
    ]
    by_id = {str(item["id"]): item for item in sources}
    for source_id in state.source_priority:
        if source_id in by_id:
            return source_id
    for source in sources:
        if str(source.get("lang") or "").lower().startswith("zh"):
            return str(source["id"])
    if sources:
        return str(sources[0]["id"])
    raise APIException(
        "Suwayomi 还没有安装在线图源，请先在扩展里安装并启用图源", 400
    )


async def _list_chapters(state: DeployedApp, manga_id: int) -> list[dict[str, Any]]:
    """读取一部漫画的全部章节（含当前用户的 isDownloaded 状态）。"""
    chapters: list[dict[str, Any]] = []
    offset = 0
    while True:
        data = await _gql(
            state,
            CHAPTERS_QUERY,
            {"mangaId": manga_id, "first": _CHAPTER_PAGE_SIZE, "offset": offset},
        )
        connection = data.get("chapters") or {}
        nodes = connection.get("nodes") or []
        chapters.extend(nodes)
        total = int(connection.get("totalCount") or 0)
        offset += len(nodes)
        if not nodes or offset >= total:
            return chapters


async def _fetch_and_list_chapters(
    state: DeployedApp, manga_id: int
) -> list[dict[str, Any]]:
    """先让 Suwayomi 拉取最新章节，再读回完整章节列表。"""
    await _gql(state, FETCH_MANGA_AND_CHAPTERS_MUTATION, {"id": manga_id})
    return await _list_chapters(state, manga_id)


def _pending_chapter_ids(chapters: list[dict[str, Any]]) -> list[int]:
    pending: list[int] = []
    for chapter in chapters:
        user = chapter.get("user") or {}
        chapter_id = chapter.get("id")
        if chapter_id is not None and not user.get("isDownloaded"):
            pending.append(int(chapter_id))
    return pending


def _download_status(payload: dict[str, Any] | None) -> dict[str, Any]:
    status = (payload or {}).get("downloadStatus") or {}
    return {"state": status.get("state"), "queue_size": status.get("queueSize")}


def _normalize_item(node: dict[str, Any]) -> dict[str, Any]:
    user = node.get("user") or {}
    return {
        "variant": "online",
        "item_id": str(node.get("id")),
        "title": node.get("title"),
        "author": node.get("author"),
        "artist": node.get("artist"),
        "genre": node.get("genre"),
        "status": node.get("status"),
        "cover": node.get("thumbnailUrl"),
        "in_library": bool(user.get("inLibrary")),
        "source_id": str(node.get("sourceId") or ""),
    }


async def _enqueue_missing_and_start(
    state: DeployedApp, manga_id: int
) -> tuple[int, int, dict[str, Any]]:
    """拉取章节、入队未下载章节并启动下载器，返回（入队数、章节总数、下载器状态）。"""
    chapters = await _fetch_and_list_chapters(state, manga_id)
    pending = _pending_chapter_ids(chapters)

    if pending:
        await _gql(state, ENQUEUE_DOWNLOADS_MUTATION, {"ids": pending})

    started = await _gql(state, START_DOWNLOADER_MUTATION, {})
    status = _download_status(started.get("startDownloader"))
    return len(pending), len(chapters), status


async def search(
    db: AsyncSession,
    *,
    keyword: str,
    instance_id: int | None = None,
    page: int = 1,
    count: int = 20,
    source_id: str | None = None,
) -> dict[str, Any]:
    state = await _state(db, instance_id)
    resolved_source = source_id or await _pick_source(state)
    data = await _gql(
        state,
        SEARCH_MUTATION,
        {
            "source": resolved_source,
            "query": keyword,
            "page": page,
            "type": "SEARCH",
        },
    )
    payload = data.get("fetchSourceManga") or {}
    mangas = payload.get("mangas") or []
    return {
        "instance_id": state.instance_id,
        "variant": "online",
        "source_id": resolved_source,
        "items": [_normalize_item(item) for item in mangas[:count]],
    }


async def detail(
    db: AsyncSession,
    *,
    item_id: str,
    instance_id: int | None = None,
) -> dict[str, Any]:
    state = await _state(db, instance_id)
    manga_id = _as_int(item_id, "item_id")
    data = await _gql(state, DETAIL_QUERY, {"id": manga_id})
    manga = data.get("manga") or {}
    user = manga.get("user") or {}
    chapters = manga.get("chapters") or {}
    return {
        "instance_id": state.instance_id,
        "variant": "online",
        "detail": {
            "item_id": str(manga.get("id")),
            "title": manga.get("title"),
            "author": manga.get("author"),
            "artist": manga.get("artist"),
            "description": manga.get("description"),
            "genre": manga.get("genre"),
            "status": manga.get("status"),
            "cover": manga.get("thumbnailUrl"),
            "real_url": manga.get("realUrl"),
            "source_id": str(manga.get("sourceId") or ""),
            "in_library": bool(user.get("inLibrary")),
            "total_chapters": int(chapters.get("totalCount") or 0),
        },
    }


async def subscriptions(
    db: AsyncSession,
    *,
    instance_id: int | None = None,
    page: int = 1,
    count: int = 30,
) -> dict[str, Any]:
    state = await _state(db, instance_id)
    offset = max(page - 1, 0) * count
    data = await _gql(state, LIBRARY_QUERY, {"first": count, "offset": offset})
    connection = data.get("mangas") or {}
    nodes = connection.get("nodes") or []
    return {
        "instance_id": state.instance_id,
        "variant": "online",
        "total": int(connection.get("totalCount") or 0),
        "items": [
            {
                "subscription_id": str(node.get("id")),
                "title": node.get("title"),
                "author": node.get("author"),
                "cover": node.get("thumbnailUrl"),
                "initialized": bool(node.get("initialized")),
            }
            for node in nodes
        ],
    }


async def subscribe(
    db: AsyncSession,
    *,
    item_id: str,
    name: str | None = None,
    options: dict[str, Any] | None = None,
    instance_id: int | None = None,
) -> dict[str, Any]:
    state = await _state(db, instance_id)
    manga_id = _as_int(item_id, "item_id")
    data = await _gql(
        state, UPDATE_LIBRARY_MUTATION, {"id": manga_id, "inLibrary": True}
    )
    manga = (data.get("updateManga") or {}).get("manga") or {}

    chapters_fetched = True
    warning: str | None = None
    try:
        await _gql(state, FETCH_MANGA_AND_CHAPTERS_MUTATION, {"id": manga_id})
    except APIException as exc:
        chapters_fetched = False
        warning = f"已加入书库，但拉取章节失败：{exc.message}"

    meta: dict[str, Any] = {}
    try:
        detail = await _gql(state, DETAIL_QUERY, {"id": manga_id})
        record = detail.get("manga") or {}
        values = {
            "author": record.get("author") or "",
            "tags": record.get("genre") or [],
            "comic_status": record.get("status") or "",
            "cover": record.get("thumbnailUrl") or "",
            "total_chapters": int((record.get("chapters") or {}).get("totalCount") or 0),
        }
        meta = {key: value for key, value in values.items() if value not in ("", [], 0)}
    except APIException:
        meta = {}

    return {
        "instance_id": state.instance_id,
        "variant": "online",
        "subscription_id": str(manga_id),
        "title": manga.get("title") or name,
        "in_library": True,
        "chapters_fetched": chapters_fetched,
        "warning": warning,
        "meta": meta,
    }


async def unsubscribe(
    db: AsyncSession,
    *,
    subscription_id: str,
    instance_id: int | None = None,
) -> dict[str, Any]:
    state = await _state(db, instance_id)
    manga_id = _as_int(subscription_id, "subscription_id")
    data = await _gql(
        state, UPDATE_LIBRARY_MUTATION, {"id": manga_id, "inLibrary": False}
    )
    manga = (data.get("updateManga") or {}).get("manga") or {}
    return {
        "instance_id": state.instance_id,
        "variant": "online",
        "subscription_id": str(manga_id),
        "title": manga.get("title"),
        "in_library": False,
    }


async def downloads(
    db: AsyncSession,
    *,
    instance_id: int | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    state = await _state(db, instance_id)
    data = await _gql(
        state, DOWNLOAD_OVERVIEW_QUERY, {"first": limit, "offset": 0}
    )
    connection = data.get("mangas") or {}
    nodes = connection.get("nodes") or []

    items: list[dict[str, Any]] = []
    for node in nodes:
        user = node.get("user") or {}
        chapters = node.get("chapters") or {}
        total_chapters = int(chapters.get("totalCount") or 0)
        downloaded_raw = user.get("downloadCount")
        if downloaded_raw is None:
            downloaded_chapters: int | None = None
            phase = "unknown"
        else:
            downloaded_chapters = int(downloaded_raw)
            phase = download_phase(total_chapters, downloaded_chapters)
        items.append(
            {
                "task_id": str(node.get("id")),
                "title": node.get("title"),
                "total_chapters": total_chapters,
                "downloaded_chapters": downloaded_chapters,
                "status": phase,
            }
        )

    return {
        "instance_id": state.instance_id,
        "variant": "online",
        "total": int(connection.get("totalCount") or 0),
        "items": items,
    }


async def download(
    db: AsyncSession,
    *,
    task_id: str,
    action: str,
    confirm: bool = False,
    instance_id: int | None = None,
) -> dict[str, Any]:
    if action not in ("start", "stop", "delete"):
        raise APIException("action 只能是 start / stop / delete", 400)
    if action == "delete" and not confirm:
        raise APIException(
            "删除漫画下载缓存前需要用户确认，请传 confirm=true", 400
        )

    state = await _state(db, instance_id)
    manga_id = _as_int(task_id, "task_id")

    if action == "start":
        queued, total_chapters, status = await _enqueue_missing_and_start(
            state, manga_id
        )
        return {
            "instance_id": state.instance_id,
        "variant": "online",
            "task_id": str(manga_id),
            "action": "start",
            "queued": queued,
            "total_chapters": total_chapters,
            "download_status": status,
        }

    if action == "stop":
        data = await _gql(state, STOP_DOWNLOADER_MUTATION, {})
        return {
            "instance_id": state.instance_id,
        "variant": "online",
            "task_id": str(manga_id),
            "action": "stop",
            "download_status": _download_status(data.get("stopDownloader")),
        }

    data = await _gql(state, CLEAR_DOWNLOADER_MUTATION, {})
    return {
        "instance_id": state.instance_id,
        "variant": "online",
        "task_id": str(manga_id),
        "action": "delete",
        "note": "clearDownloader 只清空下载队列，不会删除已下载的章节文件",
        "download_status": _download_status(data.get("clearDownloader")),
    }


async def replace(
    db: AsyncSession,
    *,
    subscription_id: str,
    confirm: bool = False,
    options: dict[str, Any] | None = None,
    instance_id: int | None = None,
) -> dict[str, Any]:
    if not confirm:
        raise APIException(
            "重新拉起漫画下载前需要用户确认，请传 confirm=true", 400
        )

    state = await _state(db, instance_id)
    manga_id = _as_int(subscription_id, "subscription_id")
    queued, total_chapters, status = await _enqueue_missing_and_start(
        state, manga_id
    )
    return {
        "instance_id": state.instance_id,
        "variant": "online",
        "subscription_id": str(manga_id),
        "queued": queued,
        "total_chapters": total_chapters,
        "download_status": status,
    }
