"""资源域适配器的共用实例解析。

每个资源域模块（moviepilot / mediary_scout / reader / suwayomi / musicdl）对外暴露同名函数：
search / detail / subscriptions / subscribe / unsubscribe / downloads / download / replace。
第一个参数都是 AsyncSession，其余使用关键字参数，由 tools_resource 统一分发。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import APIException
from app.services.integrations.discovery import DeployedApp, list_deployed_apps
from app.services.mcp.auth import current_mcp_actor

# 同一资源域可能有多种部署形态，用 variant 区分语义与生命周期。
APP_VARIANTS = {
    "suwayomi": "online",
    "comic-auto": "local",
    "moviepilot": "local",
    "mediary-scout": "cloud",
}


def variant_of(app: DeployedApp) -> str:
    return APP_VARIANTS.get(app.app_name, app.app_name)


def request_host() -> str:
    actor = current_mcp_actor()
    return actor.request_host if actor is not None else "127.0.0.1"


def usable(app: DeployedApp) -> bool:
    return app.status == "running" and bool(app.url)


async def candidate_apps(
    db: AsyncSession, app_names: tuple[str, ...]
) -> list[DeployedApp]:
    apps = await list_deployed_apps(db, request_host())
    return [app for app in apps if app.app_name in app_names and usable(app)]


async def resolve_app(
    db: AsyncSession, app_names: tuple[str, ...], instance_id: int | None = None
) -> DeployedApp:
    """取可用实例：优先指定 ID，否则用第一个可用实例。"""
    apps = await candidate_apps(db, app_names)
    if instance_id is not None:
        apps = [app for app in apps if app.instance_id == instance_id]
    if not apps:
        raise APIException(
            f"没有可用的 {app_names[0]} 实例，请先部署并启动", 400
        )
    return apps[0]


def instance_summary(apps: list[DeployedApp]) -> list[dict[str, Any]]:
    return [
        {
            "instance_id": app.instance_id,
            "name": app.instance_name,
            "app": app.app_name,
            "url": app.url,
        }
        for app in apps
    ]


def variant_summary(apps: list[DeployedApp]) -> list[dict[str, Any]]:
    """多形态域（如漫画在线/本地）的实例列表。"""
    return [
        {
            **summary,
            "variant": variant_of(app),
        }
        for app, summary in zip(apps, instance_summary(apps), strict=True)
    ]


async def resolve_variant_app(
    db: AsyncSession,
    app_names: tuple[str, ...],
    instance_id: int | None = None,
) -> DeployedApp:
    """解析多形态资源域的实例，多个可用时必须显式指定。

    漫画同时部署了在线（Suwayomi）与本地（Comic Auto）时，两者的订阅、
    下载与阅读方式完全不同，不能默认取第一个，否则 Agent 会把在线条目
    提交到本地书库。
    """
    apps = await candidate_apps(db, app_names)
    if instance_id is not None:
        apps = [app for app in apps if app.instance_id == instance_id]
        if not apps:
            raise APIException(
                f"instance_id={instance_id} 不是可用的 {app_names[0]} 实例",
                400,
            )
        return apps[0]
    if not apps:
        raise APIException(
            f"没有可用的 {' / '.join(app_names)} 实例，请先部署并启动", 400
        )
    if len(apps) == 1:
        return apps[0]
    choices = "、".join(
        f"{variant_of(app)}（instance_id={app.instance_id}，"
        f"{app.instance_name}）"
        for app in apps
    )
    raise APIException(
        f"该资源有多个可用实例，请先确认使用哪一套并传 instance_id：{choices}",
        400,
    )


def base_url(app: DeployedApp) -> str:
    url = (app.url or "").strip().rstrip("/")
    if not url:
        raise APIException(f"{app.app_name} 实例缺少访问地址", 400)
    return url


def download_phase(total_chapters: int, downloaded: int) -> str:
    """把章节下载进度归一成 idle / partial / done。"""
    if total_chapters <= 0:
        return "idle"
    if downloaded >= total_chapters:
        return "done"
    if downloaded > 0:
        return "partial"
    return "idle"
