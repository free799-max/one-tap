"""Comic Auto（本地漫画自动化）资源适配器。

本地漫画方案由 Comic Auto 负责订阅、下载与刮削，并把结果写入同组的
Komga / Kavita 媒体库；阅读在 Komga / Kavita 里完成。这里把 OneTap 的
统一 comic 语义映射到 Comic Auto 的 REST API（``/api/v1/app/...``）。

与在线方案（Suwayomi）的差异：

- 订阅会触发异步任务：搜索 → 匹配 → 下载 → 刮削入库，因此订阅后有
  ``download.completed`` / ``organize.completed`` 生命周期；
- ``downloads`` 用每个订阅的章节统计表示进度；
- 不提供暂停或删除单个下载任务的接口，``download`` 只支持 ``start``。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import APIException
from app.models.orchestration import AppOrchestrationInstance
from app.services.integrations.discovery import DeployedApp
from app.services.resources.base import (
    base_url,
    download_phase,
    resolve_app,
)

_APP_NAMES = ("comic-auto",)
_API = "/api/v1/app"
_TIMEOUT = 30.0
_SUMMARY_CONCURRENCY = 5
# Comic Auto 的搜索接口没有 page_size 参数：授权库结果每页固定 10 条，
# 记录表/实时站点结果不分页（一次返回全部）。count 超过一页时向上游翻页聚合。
_PAGE_SIZE = 10

# 订阅请求允许透传的字段（来自 onetap_search 的条目）
_SUBSCRIBE_KEYS = (
    "name",
    "category",
    "author",
    "status",
    "summary",
    "age_rating",
    "tags",
    "cover_url",
    "source",
    "source_comic_href",
    "source_author",
    "source_status",
    "source_summary",
    "source_age_rating",
    "source_tags",
    "source_cover_url",
    "source_latest_chapter",
    "crawling_type",
    "station_url",
    "comic_href",
    "license_subscription_id",
    "license_comic_id",
)


async def _login(
    client: httpx.AsyncClient, base: str, username: str, password: str
) -> tuple[str | None, str | None]:
    if not username or not password:
        return None, "缺少账号或密码"
    try:
        resp = await client.post(
            f"{base}/api/v1/auth/login/",
            json={"username": username, "password": password},
        )
    except httpx.RequestError as exc:
        raise APIException(f"无法连接 Comic Auto：{exc}", 502) from exc
    if resp.status_code in (401, 403):
        return None, "用户名或密码错误"
    if resp.status_code >= 400:
        return None, f"登录接口异常：HTTP {resp.status_code}"
    try:
        payload = resp.json()
    except ValueError:
        return None, "登录接口返回异常"
    token = payload.get("access") if isinstance(payload, dict) else None
    if not token:
        return None, "登录接口未返回 Token"
    return str(token), None


@asynccontextmanager
async def _api(app: DeployedApp) -> AsyncIterator[tuple[httpx.AsyncClient, str]]:
    """建立已认证的 Comic Auto 会话：优先 API Key，其次账号登录。"""
    base = base_url(app)
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        api_key = (app.api_key or "").strip()
        if api_key:
            client.headers["X-API-Key"] = api_key
        else:
            token, error = await _login(
                client, base, app.username or "", app.password or ""
            )
            if token is None:
                raise APIException(
                    f"Comic Auto 登录失败：{error or '缺少账号密码'}", 400
                )
            client.headers["Authorization"] = f"Bearer {token}"
        yield client, base


async def _request(
    client: httpx.AsyncClient, method: str, url: str, **kwargs: Any
) -> Any:
    try:
        resp = await client.request(method, url, **kwargs)
    except httpx.RequestError as exc:
        raise APIException(f"Comic Auto 请求失败：{exc}", 502) from exc
    payload: Any = None
    try:
        payload = resp.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict) and "code" in payload:
        if payload.get("code") not in (0, None):
            raise APIException(
                str(payload.get("msg") or "Comic Auto 接口返回错误"), 400
            )
        return payload.get("data")
    if resp.status_code >= 400:
        raise APIException(
            f"Comic Auto 接口异常：HTTP {resp.status_code}", 502
        )
    return payload


async def _resolve(db: AsyncSession, instance_id: int | None) -> DeployedApp:
    return await resolve_app(db, _APP_NAMES, instance_id)


def _meta(record: dict[str, Any]) -> dict[str, Any]:
    """把 Comic Auto 订阅详情压成通知卡片需要的字段。"""
    chapters = record.get("chapter_list")
    values: dict[str, Any] = {
        "author": record.get("author") or "",
        "tags": record.get("tags") or "",
        "category": record.get("category") or "",
        "comic_status": record.get("status") or "",
        "cover": record.get("cover_url") or "",
        "latest_chapter": record.get("latest_chapter") or "",
        "total_chapters": len(chapters) if isinstance(chapters, list) else 0,
    }
    return {key: value for key, value in values.items() if value not in ("", 0)}


async def _search_meta(
    client: httpx.AsyncClient, base: str, name: str
) -> dict[str, Any]:
    """按名称补全订阅元数据。

    订阅详情要等首次刮削/下载计划建立后才有状态与章节数，而通知在创建时就
    发出；搜索接口（授权库链路）能立即给出连载状态、章节数与更新进度。
    """
    keyword = name.strip()
    if not keyword:
        return {}
    try:
        data = await _request(
            client,
            "POST",
            f"{base}{_API}/explore/search/",
            json={"keyword": keyword},
        ) or {}
    except APIException:
        return {}
    items = [item for item in (data.get("items") or []) if isinstance(item, dict)]
    # 只认同名条目，避免把续作/同系列的状态和章节数写到订阅上
    match = next(
        (item for item in items if str(item.get("name") or "").strip() == keyword),
        None,
    )
    if match is None:
        return {}
    values: dict[str, Any] = {
        "author": match.get("author") or "",
        "comic_status": match.get("status") or "",
        "latest_chapter": match.get("update_info") or "",
        "total_chapters": int(match.get("chapter_count") or 0),
    }
    return {key: value for key, value in values.items() if value not in ("", 0)}


async def _library(db: AsyncSession, app: DeployedApp) -> dict[str, Any] | None:
    """读取实例组里已装配的 Komga / Kavita 媒体库。"""
    if not app.group_id:
        return None
    group = await db.get(AppOrchestrationInstance, app.group_id)
    if group is None:
        return None
    entry = (group.app_configs or {}).get("comic-auto") or {}
    integrations = entry.get("integrations") or {}
    for record in integrations.get("mediaservers") or []:
        if isinstance(record, dict) and record.get("status") == "ok":
            return {"app": record.get("name"), "url": record.get("url")}
    return None


def _normalize_item(raw: dict[str, Any]) -> dict[str, Any]:
    name = str(raw.get("name") or "")
    comic_href = str(raw.get("comic_href") or "")
    category = str(raw.get("category") or "")
    crawling_type = str(raw.get("crawling_type") or "")
    station_url = str(raw.get("station_url") or "")
    item: dict[str, Any] = {
        "variant": "local",
        "item_id": comic_href or name,
        "title": name,
        "name": name,
        "author": raw.get("author") or "",
        "category": category,
        "status": raw.get("status") or "",
        "cover": raw.get("cover_url") or "",
        "in_library": bool(raw.get("local_subscription_id")),
        "source": crawling_type or "comic-auto",
        "crawling_type": crawling_type,
        "station_url": station_url,
        "comic_href": comic_href,
    }
    if raw.get("is_license"):
        item.update(
            {
                "is_license": True,
                "chapter_count": raw.get("chapter_count") or 0,
                "update_info": raw.get("update_info") or "",
                "license_subscription_id": raw.get("subscription_id"),
                "license_comic_id": raw.get("comic_id"),
                "local_subscription_id": raw.get("local_subscription_id"),
                "local_sub_status": raw.get("local_sub_status") or "",
            }
        )
    return item


async def search(
    db: AsyncSession,
    *,
    keyword: str,
    instance_id: int | None = None,
    page: int = 1,
    count: int = 20,
    source_id: str | None = None,
) -> dict[str, Any]:
    app = await _resolve(db, instance_id)
    limit = max(count, 0)
    # page/count 是 MCP 语义：count 超过上游一页（10 条）时按 count 计算起点并翻页聚合
    start = max(page - 1, 0) * limit
    upstream_page = start // _PAGE_SIZE + 1
    offset = start % _PAGE_SIZE
    raw_items: list[dict[str, Any]] = []
    source = None
    total = 0
    has_more = False
    async with _api(app) as (client, base):
        while len(raw_items) < offset + limit:
            data = await _request(
                client,
                "POST",
                f"{base}{_API}/explore/search/",
                json={"keyword": keyword, "page": upstream_page},
            ) or {}
            chunk = data.get("items") or []
            raw_items.extend(chunk)
            source = data.get("source") or source
            total = data.get("total") or total
            has_more = bool(data.get("has_more"))
            if not chunk or not has_more:
                break
            upstream_page += 1
    items = [_normalize_item(item) for item in raw_items[offset : offset + limit]]
    return {
        "instance_id": app.instance_id,
        "variant": "local",
        "source": source,
        "page": page,
        "total": total or len(items),
        # 只有授权库分页；记录表/实时站点一次返回全部，再翻页只会重复
        "has_more": source == "license" and offset + len(items) < total,
        "items": items,
    }


async def detail(
    db: AsyncSession,
    *,
    item_id: str,
    options: dict[str, Any] | None = None,
    instance_id: int | None = None,
) -> dict[str, Any]:
    app = await _resolve(db, instance_id)
    options = dict(options or {})
    base_detail: dict[str, Any] = {
        "item_id": item_id,
        "title": options.get("name") or options.get("title") or item_id,
        "author": options.get("author") or "",
        "status": options.get("status") or "",
        "cover": options.get("cover_url") or options.get("cover") or "",
        "summary": options.get("summary") or "",
        "source": options.get("crawling_type") or "",
        "crawling_type": options.get("crawling_type") or "",
        "station_url": options.get("station_url") or "",
        "comic_href": options.get("comic_href") or item_id,
    }
    async with _api(app) as (client, base):
        local_id = options.get("local_subscription_id")
        if options.get("is_license") and local_id:
            data = await _request(
                client,
                "GET",
                f"{base}{_API}/subscriptions/{local_id}/",
            )
            record = data or {}
            base_detail.update(
                {
                    "title": record.get("name") or base_detail["title"],
                    "author": record.get("author") or base_detail["author"],
                    "status": record.get("sub_status") or base_detail["status"],
                    "cover": record.get("cover_url") or base_detail["cover"],
                    "summary": record.get("summary") or base_detail["summary"],
                    "in_library": True,
                    "latest_chapter": record.get("latest_chapter") or "",
                }
            )
            return {"instance_id": app.instance_id, "variant": "local", "detail": base_detail}

        if options.get("is_license"):
            # 授权库条目没有站点爬虫信息，不能调用 comic-detail；按搜索结果返回概况
            base_detail["total_chapters"] = options.get("chapter_count") or 0
            return {"instance_id": app.instance_id, "variant": "local", "detail": base_detail}

        body = {
            "crawling_type": base_detail["crawling_type"],
            "station_url": base_detail["station_url"],
            "comic_href": base_detail["comic_href"],
            "name": str(options.get("name") or ""),
            "category": str(options.get("category") or ""),
        }
        missing = [key for key, value in body.items() if not value]
        if missing:
            raise APIException(
                "本地漫画详情需要搜索结果里的 "
                + "、".join(missing)
                + "，请放在 options 中",
                400,
            )
        data = await _request(
            client,
            "POST",
            f"{base}{_API}/explore/comic-detail/",
            json=body,
        )
    record = data or {}
    chapters = record.get("chapter_list") or []
    base_detail.update(
        {
            "author": record.get("author") or base_detail["author"],
            "status": record.get("status") or base_detail["status"],
            "cover": record.get("cover_url") or base_detail["cover"],
            "summary": record.get("summary") or base_detail["summary"],
            "total_chapters": len(chapters),
        }
    )
    return {"instance_id": app.instance_id, "variant": "local", "detail": base_detail}


async def subscriptions(
    db: AsyncSession,
    *,
    instance_id: int | None = None,
    page: int = 1,
    count: int = 30,
) -> dict[str, Any]:
    app = await _resolve(db, instance_id)
    async with _api(app) as (client, base):
        data = await _request(
            client,
            "GET",
            f"{base}{_API}/subscriptions/",
            params={"page": page, "page_size": count},
        )
    raw_items = (data or {}).get("items") or []
    items = [
        {
            "subscription_id": str(item.get("id")),
            "title": item.get("name"),
            "author": item.get("author"),
            "cover": item.get("cover_url"),
            "status": item.get("sub_status"),
            "latest_chapter": item.get("latest_chapter"),
            "source": item.get("crawling_type") or item.get("source"),
            "category": item.get("category"),
            "in_library": True,
        }
        for item in raw_items
    ]
    return {
        "instance_id": app.instance_id,
        "variant": "local",
        "total": (data or {}).get("total", len(items)),
        "items": items,
    }


def _looks_like_href(value: str) -> bool:
    return value.startswith("/")


async def subscribe(
    db: AsyncSession,
    *,
    item_id: str,
    name: str | None = None,
    options: dict[str, Any] | None = None,
    instance_id: int | None = None,
) -> dict[str, Any]:
    app = await _resolve(db, instance_id)
    options = dict(options or {})
    payload = {
        key: options[key]
        for key in _SUBSCRIBE_KEYS
        if options.get(key) not in (None, "")
    }
    if name:
        payload.setdefault("name", name)
    if options.get("cover") and not payload.get("cover_url"):
        payload["cover_url"] = options["cover"]
    # 没有搜索结果时允许只给名称：Comic Auto 会自己搜索全部站点并匹配最优源
    if not payload.get("name"):
        candidate = str(options.get("title") or item_id or "").strip()
        if candidate and not _looks_like_href(candidate):
            payload["name"] = candidate
    if _looks_like_href(item_id):
        payload.setdefault("comic_href", item_id)
    if not payload.get("name") and not payload.get("license_subscription_id"):
        raise APIException(
            "本地漫画订阅需要 name（或授权库条目的 license_subscription_id）", 400
        )

    meta: dict[str, Any] = {}
    try:
        async with _api(app) as (client, base):
            data = await _request(
                client,
                "POST",
                f"{base}{_API}/subscriptions/",
                json=payload,
            )
            subscription_id = (data or {}).get("subscription_id")
            if subscription_id:
                # 订阅详情用于通知卡片（作者/标签/分类/封面/章节数），失败不影响订阅
                try:
                    record = await _request(
                        client,
                        "GET",
                        f"{base}{_API}/subscriptions/{subscription_id}/",
                    )
                    meta = _meta(record or {})
                except APIException:
                    meta = {}
                if payload.get("name") and not (
                    meta.get("comic_status") and meta.get("total_chapters")
                ):
                    extra = await _search_meta(client, base, payload["name"])
                    for key, value in extra.items():
                        meta.setdefault(key, value)
    except APIException as exc:
        if "已订阅" in exc.message:
            return {
                "instance_id": app.instance_id,
                "variant": "local",
                "title": payload["name"],
                "already_subscribed": True,
                "message": exc.message,
            }
        raise

    library = await _library(db, app)
    return {
        "instance_id": app.instance_id,
        "variant": "local",
        "subscription_id": str((data or {}).get("subscription_id") or ""),
        "task_id": (data or {}).get("task_id"),
        "title": payload["name"],
        "in_library": True,
        "queued": True,
        "library": library,
        "message": (data or {}).get("message"),
        "meta": meta,
    }


async def unsubscribe(
    db: AsyncSession,
    *,
    subscription_id: str,
    instance_id: int | None = None,
) -> dict[str, Any]:
    app = await _resolve(db, instance_id)
    try:
        numeric_id = int(subscription_id)
    except (TypeError, ValueError) as exc:
        raise APIException("本地漫画订阅 ID 必须是整数", 400) from exc
    async with _api(app) as (client, base):
        data = await _request(
            client, "DELETE", f"{base}{_API}/subscriptions/{numeric_id}/"
        )
    return {
        "instance_id": app.instance_id,
        "variant": "local",
        "subscription_id": str(numeric_id),
        "removed": True,
        "message": (data or {}).get("message") if isinstance(data, dict) else None,
    }


async def _chapter_summary(
    client: httpx.AsyncClient, base: str, subscription_id: Any
) -> dict[str, int]:
    data = await _request(
        client,
        "GET",
        f"{base}{_API}/subscriptions/{subscription_id}/chapters/",
        params={"page": 1, "page_size": 1},
    )
    summary = (data or {}).get("summary") or {}
    return {
        "total": int(summary.get("total") or 0),
        "downloaded": int(summary.get("downloaded") or 0),
    }


async def downloads(
    db: AsyncSession,
    *,
    instance_id: int | None = None,
    limit: int = 20,
    active_only: bool = False,
) -> dict[str, Any]:
    app = await _resolve(db, instance_id)
    params: dict[str, Any] = {"page": 1, "page_size": limit}
    if active_only:
        # 通知同步只关心订阅中的漫画，避免为已完结条目反复拉章节统计
        params["sub_status"] = "subscribing"
    async with _api(app) as (client, base):
        data = await _request(
            client,
            "GET",
            f"{base}{_API}/subscriptions/",
            params=params,
        )
        subs = (data or {}).get("items") or []
        semaphore = asyncio.Semaphore(_SUMMARY_CONCURRENCY)

        async def summary_for(sub: dict[str, Any]) -> dict[str, int]:
            async with semaphore:
                return await _chapter_summary(client, base, sub.get("id"))

        summaries = await asyncio.gather(
            *(summary_for(sub) for sub in subs), return_exceptions=True
        )

    items: list[dict[str, Any]] = []
    for sub, summary in zip(subs, summaries, strict=True):
        if isinstance(summary, Exception):
            total = downloaded = 0
        else:
            total = summary["total"]
            downloaded = summary["downloaded"]
        items.append(
            {
                "task_id": str(sub.get("id")),
                "title": sub.get("name"),
                "author": sub.get("author") or "",
                "tags": sub.get("tags") or "",
                "category": sub.get("category") or "",
                "comic_status": sub.get("status") or "",
                "cover": sub.get("cover_url") or "",
                "latest_chapter": sub.get("latest_chapter") or "",
                "total_chapters": total,
                "downloaded_chapters": downloaded,
                "status": download_phase(total, downloaded),
            }
        )
    return {
        "instance_id": app.instance_id,
        "variant": "local",
        "total": (data or {}).get("total", len(items)),
        "items": items,
    }


async def download(
    db: AsyncSession,
    *,
    task_id: str,
    action: str,
    confirm: bool = False,
    options: dict[str, Any] | None = None,
    instance_id: int | None = None,
) -> dict[str, Any]:
    if action not in ("start", "stop", "delete"):
        raise APIException("action 只能是 start / stop / delete", 400)
    if action == "stop":
        raise APIException(
            "Comic Auto 不支持暂停单个订阅的下载任务，任务会在后台继续", 400
        )
    if action == "delete":
        raise APIException(
            "Comic Auto 未提供删除下载文件的接口；如需移除请用 "
            "onetap_unsubscribe 删除订阅",
            400,
        )

    app = await _resolve(db, instance_id)
    try:
        numeric_id = int(task_id)
    except (TypeError, ValueError) as exc:
        raise APIException("本地漫画任务 ID 必须是订阅 ID（整数）", 400) from exc
    async with _api(app) as (client, base):
        data = await _request(
            client, "POST", f"{base}{_API}/subscriptions/{numeric_id}/process/"
        )
    return {
        "instance_id": app.instance_id,
        "variant": "local",
        "task_id": str(numeric_id),
        "subscription_id": str(numeric_id),
        "action": "start",
        "queued": True,
        "async_task_id": (data or {}).get("task_id"),
        "download_status": "started",
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
            "重新处理本地漫画订阅前需要用户确认，请传 confirm=true", 400
        )
    app = await _resolve(db, instance_id)
    try:
        numeric_id = int(subscription_id)
    except (TypeError, ValueError) as exc:
        raise APIException("本地漫画订阅 ID 必须是整数", 400) from exc
    async with _api(app) as (client, base):
        data = await _request(
            client, "POST", f"{base}{_API}/subscriptions/{numeric_id}/process/"
        )
    return {
        "instance_id": app.instance_id,
        "variant": "local",
        "subscription_id": str(numeric_id),
        "queued": True,
        "async_task_id": (data or {}).get("task_id"),
        "message": "已重新提交处理，缺失章节会重新下载，已有文件不会删除",
    }
