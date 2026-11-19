"""巡影 Mediary Scout（网盘影视方案）资源适配器。

网盘方案由巡影负责 TMDB 匹配、资源转存、回读校验与持续追更，播放走
网盘或同组的 Jellyfin / Emby / 飞牛影视。这里把 OneTap 的统一 video
语义映射到巡影的 Agent HTTP API（``/api/agent/*``，Bearer Token 鉴权）。

与网盘方案语义相关的两点：

- 巡影只提供「查 TMDB → 入队获取」的 ``acquire``，没有公开的搜索端点；
  这里用巡影自己使用的 TMDB 代理（``tmdb-proxy.mediaryscout.app``）做只读
  搜索，让前端先展示候选再入队，避免"没看清就开转存"；
- 订阅即获取，转存、校验、追更都由巡影后台异步完成；进度看
  ``activity``，库存看 ``library``；v1 不支持退订、替换、取消。
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import APIException
from app.services.integrations.discovery import DeployedApp
from app.services.resources.base import base_url, resolve_app

_APP_NAMES = ("mediary-scout",)
_TIMEOUT = 30.0
_ACQUIRE_OK_STATUSES = {"requested", "already_tracked", "reserved"}
_SESSION_COOKIE = "mt_session"
_SESSION_TTL = 12 * 60 * 60.0
# instance_id -> (Cookie 头, 过期时间)，避免每次订阅都登录
_SESSION_CACHE: dict[int, tuple[str, float]] = {}

# 巡影自己没配 TMDB Key 时用的公共代理，同样支持无鉴权只读查询
_TMDB_PROXY_BASE = "https://tmdb-proxy.mediaryscout.app"
_TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w342"
_SEARCH_TIMEOUT = 20.0

_TYPE_MAP = {
    "电影": "movie",
    "movie": "movie",
    "电视剧": "tv",
    "剧集": "tv",
    "动漫": "tv",
    "tv": "tv",
}

_UNSUPPORTED = {
    "detail": "巡影不提供条目详情接口，搜索结果已带年份、类型与简介",
    "unsubscribe": "巡影 Agent API 暂不支持取消追踪，请在巡影「媒体库」页面操作",
    "download": "巡影没有单任务下载控制（获取由 agent 自动完成），进度请查 onetap_downloads",
    "replace": "巡影暂不支持替换资源，请在巡影「媒体库」里重新获取",
}


async def _resolve(db: AsyncSession, instance_id: int | None) -> DeployedApp:
    return await resolve_app(db, _APP_NAMES, instance_id)


def _token(app: DeployedApp) -> str:
    token = (app.api_key or "").strip()
    if not token:
        token = str((app.env_secrets or {}).get("api_key") or "").strip()
    if not token:
        raise APIException(
            "巡影未开启 Agent API：OneTap 重新装配时会自动生成 Token；"
            "也可以在巡影「设置 → 系统 → Agent API」手动生成后重新装配",
            400,
        )
    return token


async def _request(
    app: DeployedApp,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    cookie: str | None = None,
) -> httpx.Response:
    base = base_url(app)
    headers = {"Authorization": f"Bearer {_token(app)}"}
    if cookie:
        headers["Cookie"] = cookie
    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT, follow_redirects=True
        ) as client:
            return await client.request(
                method,
                f"{base}{path}",
                params=params,
                json=payload,
                headers=headers,
            )
    except httpx.RequestError as exc:
        raise APIException(f"无法连接巡影：{exc}", 502) from exc


async def _login_session(app: DeployedApp) -> str | None:
    """用 OneTap 装配时保存的巡影账号登录，拿 mt_session 会话 cookie。

    巡影多用户模式下 Agent API 的入队仍走会话校验（上游 bug：acquire 内部
    用 requireAuthenticatedAccountId，只认 cookie），所以写操作要带上会话。
    """
    username = (app.username or "").strip()
    password = app.password or ""
    if not username or not password:
        return None
    now = time.monotonic()
    cached = _SESSION_CACHE.get(app.instance_id)
    if cached and cached[1] > now:
        return cached[0]
    base = base_url(app)
    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT, follow_redirects=False
        ) as client:
            resp = await client.post(
                f"{base}/api/auth/login",
                json={"username": username, "password": password},
            )
    except httpx.RequestError:
        return None
    if resp.status_code != 200:
        return None
    value = resp.cookies.get(_SESSION_COOKIE)
    if not value:
        return None
    cookie = f"{_SESSION_COOKIE}={value}"
    _SESSION_CACHE[app.instance_id] = (cookie, now + _SESSION_TTL)
    return cookie


def _session_expired(resp: httpx.Response) -> bool:
    """巡影会话失效时：401，或 200 + unsupported + 「未登录」。"""
    if resp.status_code == 401:
        return True
    if resp.status_code != 200:
        return False
    try:
        data = resp.json()
    except ValueError:
        return False
    if not isinstance(data, dict) or data.get("status") != "unsupported":
        return False
    return "未登录" in str(data.get("message") or "")


def _credit_names(entries: Any, limit: int, job: str | None = None) -> list[str]:
    names: list[str] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        if job and entry.get("job") != job:
            continue
        name = str(entry.get("name") or "").strip()
        if name and name not in names:
            names.append(name)
        if len(names) >= limit:
            break
    return names


def _tmdb_metadata(data: dict[str, Any]) -> dict[str, Any]:
    """对齐 MoviePilot 的卡片元数据：评分/时长/类型/演职员。"""
    date = str(data.get("release_date") or data.get("first_air_date") or "")
    poster = str(data.get("poster_path") or "")
    credits = data.get("credits") if isinstance(data.get("credits"), dict) else {}
    runtime = data.get("runtime")
    if not runtime:
        episode_run_time = data.get("episode_run_time") or []
        if isinstance(episode_run_time, list) and episode_run_time:
            runtime = episode_run_time[0]
    genres = [
        str(item.get("name") or "").strip()
        for item in data.get("genres") or []
        if isinstance(item, dict)
    ][:4]
    return {
        "title": str(data.get("title") or data.get("name") or "").strip(),
        "year": date[:4] or None,
        "poster": f"{_TMDB_IMAGE_BASE}{poster}" if poster else "",
        "genres": genres,
        "rating": data.get("vote_average"),
        "vote_count": data.get("vote_count"),
        "runtime": runtime,
        "directors": _credit_names(credits.get("crew"), 2, job="Director"),
        "actors": _credit_names(credits.get("cast"), 3),
        "release_date": date,
    }


async def _tmdb_json(
    url: str, params: dict[str, str], *, timeout: float, attempts: int
) -> dict[str, Any]:
    for attempt in range(attempts):
        try:
            async with httpx.AsyncClient(
                timeout=timeout, follow_redirects=True
            ) as client:
                resp = await client.get(url, params=params)
        except httpx.HTTPError:
            resp = None
        if resp is not None and resp.status_code < 400:
            try:
                data = resp.json()
            except ValueError:
                data = None
            if isinstance(data, dict) and data:
                return data
        if attempt + 1 < attempts:
            await asyncio.sleep(0.6)
    return {}


async def _tmdb_detail(tmdb_id: int, media_type: str) -> dict[str, Any]:
    """按 tmdbId 补标题/年份/海报与卡片元数据（评分、时长、演职员）。"""
    base = (os.getenv("TMDB_PROXY_BASE_URL") or _TMDB_PROXY_BASE).rstrip("/")
    path = "movie" if media_type == "movie" else "tv"
    # TMDB 代理偶发 TLS 失败或超时，重试一次；海报缺失会直接体现在订阅卡片上
    data = await _tmdb_json(
        f"{base}/{path}/{tmdb_id}",
        {"language": "zh-CN"},
        timeout=_SEARCH_TIMEOUT,
        attempts=2,
    )
    if not data:
        return {}
    detail = _tmdb_metadata(data)
    if not detail.get("directors") or not detail.get("actors"):
        # 演职员挂在 credits 上，代理偶发超时；拿不到就不影响卡片主体
        credits = await _tmdb_json(
            f"{base}/{path}/{tmdb_id}/credits",
            {"language": "zh-CN"},
            timeout=8.0,
            attempts=1,
        )
        if credits:
            detail["directors"] = detail.get("directors") or _credit_names(
                credits.get("crew"), 2, job="Director"
            )
            detail["actors"] = detail.get("actors") or _credit_names(
                credits.get("cast"), 3
            )
    return detail


async def tmdb_metadata(tmdb_id: Any, type_name: str | None = None) -> dict[str, Any]:
    """按 tmdbId 取卡片元数据；type_name 可以是「电影」或 movie/tv。"""
    number = _tmdb_id(tmdb_id)
    if number is None:
        return {}
    raw = str(type_name or "").strip()
    media_type = "movie" if _TYPE_MAP.get(raw) == "movie" else "tv"
    return await _tmdb_detail(number, media_type)


def _unsupported(operation: str) -> APIException:
    return APIException(_UNSUPPORTED[operation], 400)


def _error_message(resp: httpx.Response, fallback: str) -> str:
    try:
        data = resp.json()
    except ValueError:
        return fallback
    if isinstance(data, dict):
        return str(data.get("error") or data.get("message") or fallback)
    return fallback


def _tmdb_item(row: dict[str, Any]) -> dict[str, Any] | None:
    media_type = str(row.get("media_type") or "")
    if media_type not in ("movie", "tv"):
        return None
    tmdb_id = row.get("id")
    title = str(row.get("title") or row.get("name") or "").strip()
    if not tmdb_id or not title:
        return None
    date = str(row.get("release_date") or row.get("first_air_date") or "")
    poster = str(row.get("poster_path") or "")
    vote = row.get("vote_average")
    return {
        "item_id": str(tmdb_id),
        "media_id": str(tmdb_id),
        "tmdb_id": tmdb_id,
        "title": title,
        "original_title": row.get("original_title") or row.get("original_name") or "",
        "year": date[:4],
        "type": "电影" if media_type == "movie" else "电视剧",
        "media_type": media_type,
        "media_source": "mediary-scout",
        "overview": row.get("overview") or "",
        "vote": round(float(vote), 1) if isinstance(vote, (int, float)) else None,
        "poster": f"{_TMDB_IMAGE_BASE}{poster}" if poster else "",
        "source": "TMDB",
    }


async def _tmdb_search(keyword: str, *, page: int = 1) -> list[dict[str, Any]]:
    base = (os.getenv("TMDB_PROXY_BASE_URL") or _TMDB_PROXY_BASE).rstrip("/")
    params = {
        "query": keyword,
        "language": "zh-CN",
        "page": str(max(page, 1)),
        "include_adult": "false",
    }
    # 公共代理偶发 TLS 握手超时，重试一次再报错
    resp: httpx.Response | None = None
    last_error: httpx.HTTPError | None = None
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(
                timeout=_SEARCH_TIMEOUT, follow_redirects=True
            ) as client:
                resp = await client.get(f"{base}/search/multi", params=params)
            last_error = None
            break
        except httpx.HTTPError as exc:
            last_error = exc
            if attempt == 0:
                await asyncio.sleep(0.6)
    if last_error is not None or resp is None:
        detail = f"{type(last_error).__name__} {last_error}".strip()
        raise APIException(
            f"TMDB 搜索失败：{detail}（检查服务器网络或系统代理设置）", 502
        ) from last_error
    if resp.status_code >= 400:
        raise APIException(f"TMDB 搜索失败：HTTP {resp.status_code}", 502)
    try:
        data = resp.json()
    except ValueError as exc:
        raise APIException("TMDB 搜索返回了非 JSON 响应", 502) from exc
    rows = data.get("results") if isinstance(data, dict) else None
    items: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        item = _tmdb_item(row)
        if item is not None:
            items.append(item)
    return items


async def search(
    db: AsyncSession,
    *,
    keyword: str,
    instance_id: int | None = None,
    page: int = 1,
    count: int = 20,
) -> dict[str, Any]:
    app = await _resolve(db, instance_id)
    items = await _tmdb_search(keyword, page=page)
    return {
        "instance_id": app.instance_id,
        "variant": "cloud",
        "page": page,
        "total": len(items),
        "items": items[: max(count, 0) or None],
    }


async def detail(
    db: AsyncSession,
    *,
    item_id: str,
    options: dict[str, Any] | None = None,
    instance_id: int | None = None,
) -> dict[str, Any]:
    raise _unsupported("detail")


async def library_raw(
    db: AsyncSession, *, instance_id: int | None = None
) -> list[dict[str, Any]]:
    """巡影媒体库原始条目（含 tmdbId/type/seasonNumber），供通知补元数据。"""
    app = await _resolve(db, instance_id)
    resp = await _request(app, "GET", "/api/agent/library")
    if resp.status_code >= 400:
        raise APIException(
            f"读取巡影媒体库失败：{_error_message(resp, f'HTTP {resp.status_code}')}",
            502,
        )
    data = resp.json() if resp.content else {}
    rows = data.get("items") if isinstance(data, dict) else []
    return [row for row in rows or [] if isinstance(row, dict)]


async def subscriptions(
    db: AsyncSession,
    *,
    instance_id: int | None = None,
    page: int = 1,
    count: int = 30,
) -> dict[str, Any]:
    app = await _resolve(db, instance_id)
    resp = await _request(app, "GET", "/api/agent/library")
    if resp.status_code >= 400:
        raise APIException(
            f"读取巡影媒体库失败：{_error_message(resp, f'HTTP {resp.status_code}')}",
            502,
        )
    data = resp.json() if resp.content else {}
    rows = data.get("items") if isinstance(data, dict) else []
    items = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        tmdb_id = row.get("tmdbId")
        season = row.get("seasonNumber") or 1
        media_type = str(row.get("type") or "")
        items.append(
            {
                "subscription_id": f"{tmdb_id}:s{season}",
                "title": row.get("title"),
                "year": row.get("year"),
                "type": "电视剧" if media_type == "tv" else "电影",
                "season": season,
                "status": row.get("status"),
                "total_episode": row.get("totalEpisodes"),
                "obtained_episode": row.get("obtainedCount"),
                "missing": row.get("missingAired") or [],
                "storage_id": row.get("connectedStorageId"),
            }
        )
    start = max(page - 1, 0) * max(count, 0)
    end = start + count if count else None
    return {
        "instance_id": app.instance_id,
        "variant": "cloud",
        "total": data.get("count", len(items)) if isinstance(data, dict) else len(items),
        "items": items[start:end],
    }


def _tmdb_id(value: Any) -> int | None:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


async def tmdb_poster(tmdb_id: Any, type_name: str | None = None) -> str:
    """按 tmdbId 取海报；订阅结果缺海报时兜底用。"""
    number = _tmdb_id(tmdb_id)
    if number is None:
        return ""
    media_type = (
        "movie"
        if _TYPE_MAP.get(str(type_name or "").strip()) == "movie"
        else "tv"
    )
    detail = await _tmdb_detail(number, media_type)
    return str(detail.get("poster") or "")


async def subscribe(
    db: AsyncSession,
    *,
    item_id: str = "",
    name: str | None = None,
    options: dict[str, Any] | None = None,
    instance_id: int | None = None,
) -> dict[str, Any]:
    app = await _resolve(db, instance_id)
    options = dict(options or {})
    title = str(name or options.get("name") or options.get("title") or "").strip()
    tmdb_id = _tmdb_id(
        options.get("tmdb_id") or options.get("tmdbId") or item_id
    )
    if not title and tmdb_id is None:
        raise APIException("网盘方案订阅需要片名（name）或 tmdb_id", 400)

    payload: dict[str, Any] = {}
    if title:
        payload["query"] = title
    media_type = _TYPE_MAP.get(str(options.get("type") or "").strip())
    if tmdb_id is not None:
        payload["tmdbId"] = tmdb_id
        # 巡影带 tmdbId 时会跳过 TMDB 查询，type 缺省按剧集处理
        if not media_type:
            raise APIException(
                "用 tmdb_id 订阅时必须带上 type（电影/电视剧），否则巡影会按剧集获取",
                400,
            )
    if media_type:
        payload["type"] = media_type
    season = options.get("season")
    if season not in (None, ""):
        try:
            payload["season"] = int(season)
        except (TypeError, ValueError):
            pass
    storage_id = str(options.get("storage_id") or "").strip()
    if storage_id:
        payload["storageId"] = storage_id

    cookie = await _login_session(app)
    resp = await _request(
        app, "POST", "/api/agent/acquire", payload=payload, cookie=cookie
    )
    if _session_expired(resp):
        _SESSION_CACHE.pop(app.instance_id, None)
        cookie = await _login_session(app)
        if cookie:
            resp = await _request(
                app, "POST", "/api/agent/acquire", payload=payload, cookie=cookie
            )
    if resp.status_code == 401:
        raise APIException("巡影 Agent Token 无效，请重新生成后重新装配", 400)
    if resp.status_code == 404:
        raise APIException(
            f"巡影在 TMDB 没搜到「{title or tmdb_id}」，换个关键词或补上类型再试",
            404,
        )
    if resp.status_code == 409:
        data = resp.json() if resp.content else {}
        candidates = []
        for row in (data.get("candidates") if isinstance(data, dict) else []) or []:
            if not isinstance(row, dict):
                continue
            candidates.append(
                {
                    "item_id": row.get("tmdbId"),
                    "tmdb_id": row.get("tmdbId"),
                    "title": row.get("title"),
                    "year": row.get("year"),
                    "type": "电视剧" if row.get("type") == "tv" else "电影",
                    "media_type": row.get("type"),
                }
            )
        return {
            "status": "ambiguous",
            "variant": "cloud",
            "instance_id": app.instance_id,
            "title": title,
            "candidates": candidates,
            "message": "巡影匹配到多个候选，请让用户选择后用 tmdb_id 重发订阅",
        }
    if resp.status_code >= 400:
        raise APIException(
            f"巡影获取失败：{_error_message(resp, f'HTTP {resp.status_code}')}",
            502,
        )
    data = resp.json() if resp.content else {}
    matched = data.get("matched") if isinstance(data, dict) else {}
    matched = matched if isinstance(matched, dict) else {}
    status = str(data.get("status") or "").strip() if isinstance(data, dict) else ""
    message = str(data.get("message") or "").strip() if isinstance(data, dict) else ""
    # 巡影对「没真正入队」的情况也回 200，status=unsupported 时不能当成功
    if status not in _ACQUIRE_OK_STATUSES:
        detail = message or status or "没有返回状态"
        if "未登录" in detail:
            detail += "（OneTap 用编排组保存的巡影账号登录失败，请检查账号密码）"
        raise APIException(f"巡影没有接受这次获取：{detail}", 502)
    poster = str(options.get("poster") or options.get("cover") or "").strip()
    result_title = str(matched.get("title") or title or "").strip()
    result_year = matched.get("year")
    resolved_tmdb = _tmdb_id(matched.get("tmdbId") or tmdb_id)
    detail: dict[str, Any] = {}
    if resolved_tmdb is not None:
        detail = await _tmdb_detail(
            resolved_tmdb, str(matched.get("type") or media_type or "tv")
        )
        result_title = result_title or str(detail.get("title") or "")
        result_year = result_year or detail.get("year")
        poster = poster or str(detail.get("poster") or "")
    return {
        "status": status,
        "variant": "cloud",
        "instance_id": app.instance_id,
        "id": data.get("workflowRunId") if isinstance(data, dict) else None,
        "subscribe_id": data.get("trackedSeasonId") if isinstance(data, dict) else None,
        "title": result_title or None,
        "year": result_year,
        "type": "电视剧" if (matched.get("type") or media_type) == "tv" else "电影",
        "tmdb_id": matched.get("tmdbId") or tmdb_id,
        "season": payload.get("season") or 1,
        "poster": poster or None,
        "genres": detail.get("genres"),
        "rating": detail.get("rating"),
        "vote_count": detail.get("vote_count"),
        "runtime": detail.get("runtime"),
        "directors": detail.get("directors"),
        "actors": detail.get("actors"),
        "release_date": detail.get("release_date"),
        "message": message or None,
    }


async def unsubscribe(
    db: AsyncSession,
    *,
    subscription_id: str,
    instance_id: int | None = None,
) -> dict[str, Any]:
    raise _unsupported("unsubscribe")


async def downloads(
    db: AsyncSession,
    *,
    instance_id: int | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    app = await _resolve(db, instance_id)
    resp = await _request(
        app, "GET", "/api/agent/activity", params={"limit": limit}
    )
    if resp.status_code >= 400:
        raise APIException(
            f"读取巡影任务失败：{_error_message(resp, f'HTTP {resp.status_code}')}",
            502,
        )
    data = resp.json() if resp.content else {}
    data = data if isinstance(data, dict) else {}
    items = []
    for row in data.get("active") or []:
        if not isinstance(row, dict):
            continue
        items.append(
            {
                "task_id": row.get("workflowRunId"),
                "title": row.get("title"),
                "status": row.get("status"),
                "kind": row.get("kind"),
                "season": row.get("seasonNumber"),
                "started_at": row.get("startedAt"),
            }
        )
    return {
        "instance_id": app.instance_id,
        "variant": "cloud",
        "items": items,
        "recent": data.get("recent") or [],
    }


async def download(
    db: AsyncSession,
    *,
    task_id: str,
    action: str,
    confirm: bool = False,
    instance_id: int | None = None,
) -> dict[str, Any]:
    raise _unsupported("download")


async def replace(
    db: AsyncSession,
    *,
    subscription_id: str,
    options: dict[str, Any] | None = None,
    confirm: bool = False,
    instance_id: int | None = None,
) -> dict[str, Any]:
    raise _unsupported("replace")
