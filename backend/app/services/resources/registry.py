"""资源网关：把统一的域语义分发给各资源适配器。"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import APIException
from app.services.notifications import subscriptions as subscription_events
from app.services.resources import (
    comic_auto,
    mediary_scout,
    moviepilot,
    musicdl,
    reader,
    suwayomi,
)
from app.services.resources.base import (
    candidate_apps,
    instance_summary,
    resolve_variant_app,
    variant_summary,
)

logger = logging.getLogger(__name__)

DOMAINS = ("video", "comic", "music", "novel")
DOMAIN_APPS = {
    # 影视有两套方案：本地（MoviePilot + qBittorrent）与网盘（巡影）
    "video": ("moviepilot", "mediary-scout"),
    # 漫画有两套方案：在线（Suwayomi）与本地（Comic Auto + Komga/Kavita）
    "comic": ("suwayomi", "comic-auto"),
    "music": ("music-dl",),
    "novel": ("reader",),
}
# 各变体的资源生命周期，供 Agent 判断要不要等下载/入库
_VIDEO_LIFECYCLE = {
    "local": ["subscribe", "download", "organize"],
    "cloud": ["acquire", "transfer", "verify", "track"],
}
_COMIC_LIFECYCLE = {
    "online": ["library", "chapters"],
    "local": ["subscribe", "download", "organize"],
}
OPERATIONS = [
    "search",
    "detail",
    "subscriptions",
    "subscribe",
    "unsubscribe",
    "downloads",
    "download",
    "replace",
]
# 网盘方案用 TMDB 只读搜索，但没有退订、替换与单任务控制
_CLOUD_VIDEO_OPERATIONS = ["search", "subscriptions", "subscribe", "downloads"]
_VIDEO_OPERATIONS = {
    "local": OPERATIONS,
    "cloud": _CLOUD_VIDEO_OPERATIONS,
}


def _check(domain: str) -> str:
    if domain not in DOMAINS:
        raise APIException("domain 只能是 video / comic / music / novel", 400)
    return domain


async def _comic_app(db: AsyncSession, instance_id: int | None):
    """解析漫画实例：在线/本地同时可用时必须显式选择。"""
    return await resolve_variant_app(db, DOMAIN_APPS["comic"], instance_id)


async def _video_app(db: AsyncSession, instance_id: int | None):
    """解析影视实例：本地（MoviePilot）与网盘（巡影）同时可用时必须显式选择。"""
    return await resolve_variant_app(db, DOMAIN_APPS["video"], instance_id)


def _video_entry(apps: list) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "available": bool(apps),
        "instances": instance_summary(apps),
        "operations": OPERATIONS,
    }
    if not apps:
        entry["reason"] = (
            "没有可用的 " + " / ".join(DOMAIN_APPS["video"]) + " 实例"
        )
        return entry
    variants = variant_summary(apps)
    for variant in variants:
        name = variant["variant"]
        variant["lifecycle"] = _VIDEO_LIFECYCLE.get(name, [])
        variant["operations"] = _VIDEO_OPERATIONS.get(name, OPERATIONS)
    entry["variants"] = variants
    # 两个方案同时可用时必须由用户/Agent 显式选择
    entry["requires_instance_id"] = len(apps) > 1
    return entry


async def capabilities(db: AsyncSession) -> dict[str, Any]:
    apps = await candidate_apps(db, DOMAIN_APPS["video"])
    domains: dict[str, Any] = {"video": _video_entry(apps)}
    for domain in DOMAINS[1:]:
        apps = await candidate_apps(db, DOMAIN_APPS[domain])
        entry: dict[str, Any] = {
            "available": bool(apps),
            "instances": instance_summary(apps),
            "operations": OPERATIONS,
        }
        if domain == "comic":
            variants = variant_summary(apps)
            for variant in variants:
                variant["lifecycle"] = _COMIC_LIFECYCLE.get(
                    variant["variant"], []
                )
            entry["variants"] = variants
            # 两个方案同时可用时必须由用户/Agent 显式选择
            entry["requires_instance_id"] = len(apps) > 1
        if not apps:
            entry["reason"] = (
                "没有可用的 " + " / ".join(DOMAIN_APPS[domain]) + " 实例"
            )
        domains[domain] = entry
    return {"domains": domains}


async def search(
    db: AsyncSession,
    *,
    domain: str,
    keyword: str,
    instance_id: int | None = None,
    page: int = 1,
    count: int = 20,
    sources: list[str] | None = None,
    source_id: str | None = None,
    sort: str = "default",
) -> dict[str, Any]:
    _check(domain)
    if domain == "video":
        app = await _video_app(db, instance_id)
        if app.app_name == "mediary-scout":
            return await mediary_scout.search(
                db,
                keyword=keyword,
                instance_id=app.instance_id,
                page=page,
                count=count,
            )
        return await moviepilot.search(
            db,
            title=keyword,
            instance_id=app.instance_id,
            media_source=sources,
            page=page,
            count=count,
        )
    if domain == "comic":
        app = await _comic_app(db, instance_id)
        if app.app_name == "comic-auto":
            return await comic_auto.search(
                db,
                keyword=keyword,
                instance_id=app.instance_id,
                page=page,
                count=count,
                source_id=source_id,
            )
        return await suwayomi.search(
            db,
            keyword=keyword,
            instance_id=app.instance_id,
            page=page,
            count=count,
            source_id=source_id,
        )
    if domain == "music":
        return await musicdl.search(
            db,
            keyword=keyword,
            instance_id=instance_id,
            page=page,
            count=count,
            sources=sources,
            sort=sort,
        )
    return await reader.search(
        db, keyword=keyword, instance_id=instance_id, page=page, count=count
    )


async def detail(
    db: AsyncSession,
    *,
    domain: str,
    item_id: str,
    options: dict[str, Any] | None = None,
    instance_id: int | None = None,
) -> dict[str, Any]:
    _check(domain)
    options = options or {}
    if domain == "video":
        app = await _video_app(db, instance_id)
        if app.app_name == "mediary-scout":
            return await mediary_scout.detail(
                db,
                item_id=item_id,
                options=options,
                instance_id=app.instance_id,
            )
        return await moviepilot.detail(
            db,
            media_id=item_id,
            media_source=str(options.get("media_source") or ""),
            type_name=str(options.get("type_name") or ""),
            instance_id=app.instance_id,
        )
    if domain == "comic":
        app = await _comic_app(db, instance_id)
        if app.app_name == "comic-auto":
            return await comic_auto.detail(
                db,
                item_id=item_id,
                options=options,
                instance_id=app.instance_id,
            )
        return await suwayomi.detail(
            db, item_id=item_id, instance_id=app.instance_id
        )
    if domain == "music":
        return await musicdl.detail(
            db, item_id=item_id, instance_id=instance_id, extra=options.get("extra")
        )
    return await reader.detail(db, item_id=item_id, instance_id=instance_id)


async def subscriptions(
    db: AsyncSession,
    *,
    domain: str,
    instance_id: int | None = None,
    page: int = 1,
    count: int = 30,
) -> dict[str, Any]:
    _check(domain)
    if domain == "video":
        app = await _video_app(db, instance_id)
        if app.app_name == "mediary-scout":
            return await mediary_scout.subscriptions(
                db, instance_id=app.instance_id, page=page, count=count
            )
        return await moviepilot.subscriptions(
            db, instance_id=app.instance_id, page=page, count=count
        )
    if domain == "comic":
        app = await _comic_app(db, instance_id)
        if app.app_name == "comic-auto":
            return await comic_auto.subscriptions(
                db, instance_id=app.instance_id, page=page, count=count
            )
        return await suwayomi.subscriptions(
            db, instance_id=app.instance_id, page=page, count=count
        )
    if domain == "music":
        return await musicdl.subscriptions(
            db, instance_id=instance_id, page=page, count=count
        )
    return await reader.subscriptions(
        db, instance_id=instance_id, page=page, count=count
    )


async def subscribe(
    db: AsyncSession,
    *,
    domain: str,
    item_id: str = "",
    options: dict[str, Any] | None = None,
    instance_id: int | None = None,
) -> dict[str, Any]:
    _check(domain)
    options = dict(options or {})
    if domain == "video":
        app = await _video_app(db, instance_id)
        if app.app_name == "mediary-scout":
            result = await mediary_scout.subscribe(
                db,
                item_id=item_id,
                name=options.get("name"),
                options=options,
                instance_id=app.instance_id,
            )
            # 歧义时还没入队，不能记台账或发订阅成功通知
            if result.get("status") == "ambiguous":
                return result
            options["media_source"] = "mediary-scout"
            if result.get("tmdb_id") not in (None, ""):
                options.setdefault("media_id", str(result["tmdb_id"]))
            options.setdefault("season", result.get("season") or 1)
            # 搜索/MCP 没带海报时用适配器从 TMDB 补的，台账与通知卡片才有封面
            if result.get("poster"):
                options.setdefault("poster", result["poster"])
            if not options.get("poster"):
                # 适配器那次 TMDB 请求偶发失败时再兜一次，避免订阅卡片没封面
                poster = await mediary_scout.tmdb_poster(
                    result.get("tmdb_id"), options.get("type")
                )
                if poster:
                    options["poster"] = poster
            if result.get("year") and not options.get("year"):
                options["year"] = result["year"]
            # TMDB 详情里的评分/时长/演职员，通知卡片与 MoviePilot 方案保持一致
            for key in (
                "genres",
                "rating",
                "vote_count",
                "runtime",
                "directors",
                "actors",
                "release_date",
            ):
                if result.get(key) not in (None, "", []):
                    options.setdefault(key, result[key])
        else:
            media_id = str(options.get("media_id") or item_id or "") or None
            result = await moviepilot.subscribe(
                db,
                name=str(options.get("name") or ""),
                type=str(options.get("type") or ""),
                year=options.get("year"),
                media_source=options.get("media_source"),
                media_id=media_id,
                season=options.get("season"),
                resolution=options.get("resolution"),
                quality=options.get("quality"),
                best_version=options.get("best_version"),
                instance_id=app.instance_id,
            )
    elif domain == "comic":
        app = await _comic_app(db, instance_id)
        if app.app_name == "comic-auto":
            result = await comic_auto.subscribe(
                db,
                item_id=item_id,
                name=options.get("name"),
                options=options,
                instance_id=app.instance_id,
            )
        else:
            result = await suwayomi.subscribe(
                db,
                item_id=item_id,
                name=options.get("name"),
                options=options,
                instance_id=app.instance_id,
            )
    elif domain == "music":
        result = await musicdl.subscribe(
            db,
            item_id=item_id,
            name=options.get("name"),
            options=options,
            instance_id=instance_id,
        )
    else:
        result = await reader.subscribe(
            db,
            item_id=item_id,
            name=options.get("name"),
            options=options,
            instance_id=instance_id,
        )

    try:
        await subscription_events.record_added(
            db,
            domain=domain,
            item_id=item_id,
            options=options,
            result=result,
            instance_id=instance_id,
        )
        await db.commit()
    except Exception as exc:  # noqa: BLE001 - 上游已成功，通知失败不反悔
        await db.rollback()
        logger.warning("记录订阅台账失败（%s/%s）：%s", domain, item_id, exc)
    return result


async def unsubscribe(
    db: AsyncSession,
    *,
    domain: str,
    subscription_id: str,
    instance_id: int | None = None,
) -> dict[str, Any]:
    _check(domain)
    if domain == "video":
        app = await _video_app(db, instance_id)
        if app.app_name == "mediary-scout":
            result = await mediary_scout.unsubscribe(
                db,
                subscription_id=subscription_id,
                instance_id=app.instance_id,
            )
        else:
            try:
                numeric_id = int(subscription_id)
            except (TypeError, ValueError) as exc:
                raise APIException("影视订阅 ID 必须是整数", 400) from exc
            result = await moviepilot.unsubscribe(
                db, subscribe_id=numeric_id, instance_id=app.instance_id
            )
    elif domain == "comic":
        app = await _comic_app(db, instance_id)
        if app.app_name == "comic-auto":
            result = await comic_auto.unsubscribe(
                db,
                subscription_id=subscription_id,
                instance_id=app.instance_id,
            )
        else:
            result = await suwayomi.unsubscribe(
                db,
                subscription_id=subscription_id,
                instance_id=app.instance_id,
            )
    elif domain == "music":
        result = await musicdl.unsubscribe(
            db, subscription_id=subscription_id, instance_id=instance_id
        )
    else:
        result = await reader.unsubscribe(
            db, subscription_id=subscription_id, instance_id=instance_id
        )

    try:
        await subscription_events.record_removed(
            db,
            domain=domain,
            subscription_id=subscription_id,
            instance_id=instance_id,
        )
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        logger.warning("记录退订事件失败（%s/%s）：%s", domain, subscription_id, exc)
    return result


async def downloads(
    db: AsyncSession,
    *,
    domain: str,
    instance_id: int | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    _check(domain)
    if domain == "video":
        app = await _video_app(db, instance_id)
        if app.app_name == "mediary-scout":
            return await mediary_scout.downloads(
                db, instance_id=app.instance_id, limit=limit
            )
        return await moviepilot.downloads(
            db, limit=limit, instance_id=app.instance_id
        )
    if domain == "comic":
        app = await _comic_app(db, instance_id)
        if app.app_name == "comic-auto":
            return await comic_auto.downloads(
                db, instance_id=app.instance_id, limit=limit
            )
        return await suwayomi.downloads(
            db, instance_id=app.instance_id, limit=limit
        )
    if domain == "music":
        return await musicdl.downloads(
            db, instance_id=instance_id, limit=limit
        )
    return await reader.downloads(db, instance_id=instance_id, limit=limit)


async def download(
    db: AsyncSession,
    *,
    domain: str,
    task_id: str,
    action: str,
    confirm: bool = False,
    options: dict[str, Any] | None = None,
    instance_id: int | None = None,
) -> dict[str, Any]:
    _check(domain)
    options = options or {}
    if domain == "video":
        app = await _video_app(db, instance_id)
        if app.app_name == "mediary-scout":
            return await mediary_scout.download(
                db,
                task_id=task_id,
                action=action,
                confirm=confirm,
                instance_id=app.instance_id,
            )
        if action == "delete" and not confirm:
            raise APIException("删除下载任务前需要用户确认，请传 confirm=true", 400)
        return await moviepilot.download_action(
            db,
            task_id=task_id,
            action=action,
            delete_files=bool(options.get("delete_files")),
            instance_id=app.instance_id,
        )
    if domain == "comic":
        app = await _comic_app(db, instance_id)
        if app.app_name == "comic-auto":
            return await comic_auto.download(
                db,
                task_id=task_id,
                action=action,
                confirm=confirm,
                instance_id=app.instance_id,
            )
        return await suwayomi.download(
            db,
            task_id=task_id,
            action=action,
            confirm=confirm,
            instance_id=app.instance_id,
        )
    if domain == "music":
        return await musicdl.download(
            db,
            task_id=task_id,
            action=action,
            confirm=confirm,
            instance_id=instance_id,
        )
    return await reader.download(
        db,
        task_id=task_id,
        action=action,
        confirm=confirm,
        instance_id=instance_id,
    )


async def replace(
    db: AsyncSession,
    *,
    domain: str,
    subscription_id: str,
    options: dict[str, Any] | None = None,
    confirm: bool = False,
    instance_id: int | None = None,
) -> dict[str, Any]:
    _check(domain)
    options = dict(options or {})
    if domain == "video":
        app = await _video_app(db, instance_id)
        if app.app_name == "mediary-scout":
            result = await mediary_scout.replace(
                db,
                subscription_id=subscription_id,
                confirm=confirm,
                options=options,
                instance_id=app.instance_id,
            )
        else:
            if not confirm:
                raise APIException("替换资源前需要用户确认，请传 confirm=true", 400)
            try:
                numeric_id = int(subscription_id)
            except (TypeError, ValueError) as exc:
                raise APIException("影视订阅 ID 必须是整数", 400) from exc
            result = await moviepilot.replace(
                db,
                subscribe_id=numeric_id,
                resolution=options.get("resolution"),
                quality=options.get("quality"),
                instance_id=app.instance_id,
            )
    elif domain == "comic":
        app = await _comic_app(db, instance_id)
        if app.app_name == "comic-auto":
            result = await comic_auto.replace(
                db,
                subscription_id=subscription_id,
                confirm=confirm,
                options=options,
                instance_id=app.instance_id,
            )
        else:
            result = await suwayomi.replace(
                db,
                subscription_id=subscription_id,
                confirm=confirm,
                options=options,
                instance_id=app.instance_id,
            )
    elif domain == "music":
        result = await musicdl.replace(
            db,
            subscription_id=subscription_id,
            confirm=confirm,
            options=options,
            instance_id=instance_id,
        )
    else:
        result = await reader.replace(
            db,
            subscription_id=subscription_id,
            confirm=confirm,
            options=options,
            instance_id=instance_id,
        )

    try:
        await subscription_events.record_replaced(
            db,
            domain=domain,
            subscription_id=subscription_id,
            options=options,
            result=result,
            instance_id=instance_id,
        )
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        logger.warning("记录替换事件失败（%s/%s）：%s", domain, subscription_id, exc)
    return result
