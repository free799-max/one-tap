"""go-music-dl 音乐域适配器：把 OneTap 的音乐域语义映射到 music-dl 的网页/HTTP 接口。

music-dl 没有订阅概念，本模块把「订阅」翻译成「加入下载队列」：搜索后直接下载。
搜索接口返回服务端渲染的 HTML，这里只在开标签属性上做解析。
"""

from __future__ import annotations

import asyncio
import html
import json
import re
import uuid
from typing import Any
from urllib.parse import quote

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import APIException
from app.services.integrations.discovery import DeployedApp
from app.services.resources.base import base_url, resolve_app

APP_NAMES = ("music-dl",)
_TIMEOUT = 15.0
_NOTE = "音乐域用下载队列代替订阅"

_CARD_RE = re.compile(
    r'<li\b(?=[^>]*\bclass="[^"]*\bsong-card\b)[^>]*>', re.S
)
_ATTR_RE = re.compile(
    r"""([A-Za-z_:][-A-Za-z0-9_:.]*)\s*=\s*(?:"([^"]*)"|'([^']*)')"""
)


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return 0


def _pick(data: dict[str, Any], *keys: str) -> Any:
    """按候选键名取第一个有值的字段，兼容接口的大小写差异。"""
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return None


def _extra_param(extra: Any) -> str:
    if extra is None:
        return ""
    if isinstance(extra, str):
        return extra
    if isinstance(extra, (dict, list)):
        return json.dumps(extra, ensure_ascii=False, separators=(",", ":"))
    return str(extra)


def _prefix(state: DeployedApp) -> str:
    """music-dl 的接口默认挂在 /music 前缀下；实例地址已含该前缀时不再重复。"""
    return "" if base_url(state).endswith("/music") else "/music"


def _path(state: DeployedApp, suffix: str) -> str:
    return f"{_prefix(state)}{suffix}"


def _parse_cards(text: str) -> list[dict[str, Any]]:
    """解析搜索结果页里的 song-card，跳过缺少 id 或 source 的卡片。"""
    items: list[dict[str, Any]] = []
    for match in _CARD_RE.finditer(text):
        attrs: dict[str, str] = {}
        for attr in _ATTR_RE.finditer(match.group(0)):
            raw = attr.group(2) if attr.group(2) is not None else attr.group(3)
            attrs[attr.group(1).lower()] = html.unescape(raw or "")

        source = _text(attrs.get("data-source"))
        song_id = _text(attrs.get("data-id"))
        if not source or not song_id:
            continue
        items.append(
            {
                "item_id": f"{source}:{song_id}",
                "title": _text(attrs.get("data-name")),
                "artist": _text(attrs.get("data-artist")),
                "album": _text(attrs.get("data-album")),
                "duration": _int(attrs.get("data-duration")),
                "source": source,
                "cover": _text(attrs.get("data-cover")) or None,
                "extra": _text(attrs.get("data-extra")),
            }
        )
    return items


def _split_item_id(item_id: Any) -> tuple[str, str]:
    raw = _text(item_id)
    if ":" not in raw:
        return "", ""
    source, song_id = raw.split(":", 1)
    return source.strip(), song_id.strip()


def _song_payload(
    *,
    item_id: Any = None,
    name: str | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, str]:
    """把 item_id / name / options 归一成入队请求需要的歌曲字段。"""
    options = options or {}
    default_source, default_id = _split_item_id(options.get("item_id") or item_id)
    source = _text(options.get("source") or default_source)
    song_id = _text(options.get("id") or default_id)
    if not source or not song_id:
        raise APIException("加入下载队列需要提供歌曲的 source 与 id", 400)
    return {
        "id": song_id,
        "source": source,
        "name": _text(options.get("name") or options.get("title") or name),
        "artist": _text(options.get("artist")),
        "album": _text(options.get("album")),
        "cover": _text(options.get("cover")),
        "extra": _extra_param(options.get("extra")),
    }


async def _state(db: AsyncSession, instance_id: int | None) -> DeployedApp:
    return await resolve_app(db, APP_NAMES, instance_id)


def _client(state: DeployedApp) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=base_url(state),
        timeout=_TIMEOUT,
        headers={"X-Requested-With": "XMLHttpRequest"},
    )


def _error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        for key in ("error", "message", "detail", "reason"):
            value = payload.get(key)
            if value:
                return str(value)
    body = response.text.strip()
    return body[:200] if body else f"HTTP {response.status_code}"


async def _request(
    state: DeployedApp, method: str, path: str, **kwargs: Any
) -> httpx.Response:
    try:
        async with _client(state) as client:
            response = await client.request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        raise APIException(f"music-dl 调用失败：{exc}", 502) from exc
    if response.status_code >= 400:
        raise APIException(
            f"music-dl 调用失败：HTTP {response.status_code}：{_error_detail(response)}",
            502,
        )
    return response


async def _json(
    state: DeployedApp, method: str, path: str, **kwargs: Any
) -> dict[str, Any]:
    response = await _request(state, method, path, **kwargs)
    try:
        payload = response.json()
    except ValueError as exc:
        raise APIException("music-dl 调用失败：响应不是合法 JSON", 502) from exc
    if not isinstance(payload, dict):
        raise APIException("music-dl 调用失败：响应格式不符合预期", 502)
    return payload


def _task_item(task: dict[str, Any]) -> dict[str, Any]:
    raw_id = _pick(task, "ID", "id")
    return {
        "task_id": str(raw_id).strip() if raw_id is not None else "",
        "title": _text(_pick(task, "Name", "name")),
        "artist": _text(_pick(task, "Artist", "artist")),
        "source": _text(_pick(task, "Source", "source")),
        "status": _text(_pick(task, "Status", "status")),
        "progress": _pick(task, "Progress", "progress"),
        "path": _pick(task, "Path", "path"),
        "size": _pick(task, "Size", "size", "FileSize", "file_size"),
        "error": _pick(task, "Error", "error"),
    }


def _queue_result(payload: dict[str, Any]) -> dict[str, int]:
    return {
        "added": _int(_pick(payload, "added")),
        "existing": _int(_pick(payload, "existing")),
    }


def _bitrate_value(value: Any) -> float:
    match = re.search(r"\d+(?:\.\d+)?", str(value or ""))
    return float(match.group(0)) if match else 0


async def _sort_by_quality(
    state: DeployedApp, items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(5)

    async def inspect(item: dict[str, Any]) -> dict[str, Any]:
        source, song_id = _split_item_id(item.get("item_id"))
        async with semaphore:
            try:
                payload = await _json(
                    state,
                    "GET",
                    _path(state, "/inspect"),
                    params={
                        "id": song_id,
                        "source": source,
                        "extra": _extra_param(item.get("extra")),
                        "duration": item.get("duration") or 0,
                    },
                )
            except APIException:
                payload = {}
        return {
            **item,
            "size": payload.get("size"),
            "bitrate": payload.get("bitrate"),
            "resolvable": bool(payload.get("valid")),
        }

    inspected = await asyncio.gather(*(inspect(item) for item in items))
    return [
        item
        for _, item in sorted(
            enumerate(inspected),
            key=lambda pair: (-_bitrate_value(pair[1].get("bitrate")), pair[0]),
        )
    ]


async def search(
    db: AsyncSession,
    *,
    keyword: str,
    instance_id: int | None = None,
    page: int = 1,
    count: int = 20,
    sources: list[str] | None = None,
    sort: str = "default",
) -> dict[str, Any]:
    if sort not in {"default", "quality"}:
        raise APIException("音乐排序仅支持 default / quality", 400)
    state = await _state(db, instance_id)
    params: list[tuple[str, str]] = [("q", keyword), ("type", "song")]
    if isinstance(sources, str):
        sources = [sources]
    for source in sources or []:
        name = _text(source)
        if name:
            params.append(("sources", name))

    response = await _request(state, "GET", _path(state, "/search"), params=params)
    items = _parse_cards(response.text)
    if sort == "quality" and items:
        items = await _sort_by_quality(state, items)
    size = max(count, 0)
    start = max(page - 1, 0) * size
    return {
        "instance_id": state.instance_id,
        "items": items[start : start + size],
    }


async def detail(
    db: AsyncSession,
    *,
    item_id: str,
    instance_id: int | None = None,
    extra: Any = None,
) -> dict[str, Any]:
    state = await _state(db, instance_id)
    source, song_id = _split_item_id(item_id)
    if not source or not song_id:
        raise APIException("item_id 必须是 source:id 格式", 400)

    params: dict[str, str] = {"id": song_id, "source": source}
    encoded_extra = _extra_param(extra)
    if encoded_extra:
        params["extra"] = encoded_extra

    payload = await _json(
        state, "GET", _path(state, "/inspect"), params=params
    )
    resolvable = bool(payload.get("valid"))
    detail_payload: dict[str, Any] = {
        "item_id": item_id,
        "source": source,
        "id": song_id,
        "resolvable": resolvable,
        "size": payload.get("size"),
        "bitrate": payload.get("bitrate"),
        "raw": payload,
    }
    if not resolvable:
        detail_payload["reason"] = payload.get("reason") or "inspect 判定该歌曲不可下载"
    return {"instance_id": state.instance_id, "detail": detail_payload}


async def subscriptions(
    db: AsyncSession,
    *,
    instance_id: int | None = None,
    page: int = 1,
    count: int = 30,
) -> dict[str, Any]:
    state = await _state(db, instance_id)
    payload = await _json(
        state,
        "GET",
        _path(state, "/api/downloads/queue"),
        params={"page": max(page, 1)},
    )
    tasks = [task for task in payload.get("tasks") or [] if isinstance(task, dict)]
    size = max(count, 0)
    items: list[dict[str, Any]] = []
    for task in tasks:
        if len(items) >= size:
            break
        mapped = _task_item(task)
        if not mapped["task_id"]:
            continue
        items.append(
            {
                "subscription_id": mapped["task_id"],
                "title": mapped["title"],
                "artist": mapped["artist"],
                "source": mapped["source"],
                "status": mapped["status"],
                "progress": mapped["progress"],
            }
        )
    return {
        "instance_id": state.instance_id,
        "items": items,
        "note": _NOTE,
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
    song = _song_payload(item_id=item_id, name=name, options=options)
    request_id = uuid.uuid4().hex
    payload = await _json(
        state,
        "POST",
        _path(state, "/api/downloads/queue"),
        json={"request_id": request_id, "songs": [song]},
    )
    size = None
    try:
        inspection = await _json(
            state,
            "GET",
            _path(state, "/inspect"),
            params={
                "id": song["id"],
                "source": song["source"],
                "extra": song["extra"],
            },
        )
        size = inspection.get("size")
    except APIException:
        pass
    return {
        "instance_id": state.instance_id,
        "request_id": request_id,
        "size": size,
        "result": _queue_result(payload),
    }


async def unsubscribe(
    db: AsyncSession,
    *,
    subscription_id: str,
    instance_id: int | None = None,
) -> dict[str, Any]:
    state = await _state(db, instance_id)
    task_id = _text(subscription_id)
    if not task_id:
        raise APIException("subscription_id 不能为空", 400)
    payload = await _json(
        state,
        "DELETE",
        _path(state, f"/api/downloads/queue/{quote(task_id, safe='')}"),
    )
    return {
        "instance_id": state.instance_id,
        "subscription_id": task_id,
        "result": payload,
    }


async def downloads(
    db: AsyncSession,
    *,
    instance_id: int | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    state = await _state(db, instance_id)
    tasks: list[dict[str, Any]] = []
    counts: dict[str, Any] = {}
    page = 1
    while True:
        payload = await _json(
            state,
            "GET",
            _path(state, "/api/downloads/queue"),
            params={"page": page},
        )
        batch = [task for task in payload.get("tasks") or [] if isinstance(task, dict)]
        counts = payload.get("counts") or counts
        tasks.extend(batch)
        total_pages = _int(payload.get("total_pages")) or 1
        if len(tasks) >= max(limit, 0) or page >= total_pages or not batch:
            break
        page += 1

    size = max(limit, 0)
    return {
        "instance_id": state.instance_id,
        "items": [_task_item(task) for task in tasks[:size]],
        "counts": counts,
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
        raise APIException("删除下载任务前需要用户确认，请传 confirm=true", 400)

    state = await _state(db, instance_id)
    task = _text(task_id)
    if not task:
        raise APIException("task_id 不能为空", 400)
    encoded = quote(task, safe="")

    if action == "delete":
        payload = await _json(
            state, "DELETE", _path(state, f"/api/downloads/queue/{encoded}")
        )
    else:
        remote = "retry" if action == "start" else "cancel"
        payload = await _json(
            state,
            "POST",
            _path(state, f"/api/downloads/queue/{encoded}/{remote}"),
        )
    return {"instance_id": state.instance_id, "result": payload}


async def _remove_task(state: DeployedApp, task_id: str) -> bool:
    """取消并删除旧队列任务；任务已结束或已不存在时忽略失败。"""
    encoded = quote(task_id, safe="")
    try:
        await _request(
            state, "POST", _path(state, f"/api/downloads/queue/{encoded}/cancel")
        )
    except APIException:
        pass
    try:
        await _request(
            state, "DELETE", _path(state, f"/api/downloads/queue/{encoded}")
        )
    except APIException:
        return False
    return True


async def replace(
    db: AsyncSession,
    *,
    subscription_id: str,
    confirm: bool = False,
    options: dict[str, Any] | None = None,
    instance_id: int | None = None,
) -> dict[str, Any]:
    if not confirm:
        raise APIException("替换音乐前需要用户确认，请传 confirm=true", 400)

    options = options or {}
    default_source, default_id = _split_item_id(options.get("item_id"))
    if not (
        _text(options.get("id") or default_id)
        and _text(options.get("source") or default_source)
    ):
        raise APIException("替换音乐需要提供歌曲信息（options）", 400)

    state = await _state(db, instance_id)
    task_id = _text(subscription_id)
    if not task_id:
        raise APIException("subscription_id 不能为空", 400)

    song = _song_payload(options=options)
    removed = await _remove_task(state, task_id)
    request_id = uuid.uuid4().hex
    payload = await _json(
        state,
        "POST",
        _path(state, "/api/downloads/queue"),
        json={"request_id": request_id, "songs": [song]},
    )
    return {
        "instance_id": state.instance_id,
        "subscription_id": task_id,
        "request_id": request_id,
        "removed": removed,
        "result": _queue_result(payload),
    }
