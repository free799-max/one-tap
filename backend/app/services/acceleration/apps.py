"""按应用接入网络加速（本机 Mihomo 代理）。

支持的应用通过 Compose 注入 HTTP 代理环境变量，并重启该应用；
只改应用自己的 Compose 项目，不影响宿主机和其他应用。
"""

from __future__ import annotations

import asyncio
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import APIException
from app.models.orchestration import AppInstance, AppOrchestrationInstance
from app.services.acceleration import mihomo
from app.services.app_store.app_service import _main_container_name
from app.services.compose import compose_manager
from app.services.system.hosts_acceleration_service import (
    _config_file_paths,
    _merge_compose_services,
    hosts_acceleration_service,
)

SUPPORTED_APPS = {
    "moviepilot": "MoviePilot",
    "mediary-scout": "巡影 Mediary Scout",
    "emby": "Emby",
    "jellyfin": "Jellyfin",
    "plex": "Plex",
    "qbittorrent": "qBittorrent",
    "transmission": "Transmission",
}

LEGACY_PROXY_ENV_KEYS = (
    "PROXY_HOST",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "http_proxy",
    "https_proxy",
)
NO_PROXY_KEYS = ("NO_PROXY", "no_proxy")
NO_PROXY_VALUE = "localhost,127.0.0.1,::1"
DEFAULT_PROXY_ENV = "HTTPS_PROXY"
APP_PROXY_ENV = {"moviepilot": "PROXY_HOST"}
APP_ACCELERATION_ENV = {
    "moviepilot": {
        "BANGUMI_PROXY_ENABLE": "true",
        "MUSIC_COVER_PROXY": "/api/v1/system/img/1?imgurl=https://coverartarchive.org",
    }
}

_lock = asyncio.Lock()


def _supported_name(instance: AppInstance) -> str | None:
    if not instance.app:
        return None
    name = (instance.app.name or "").strip().lower()
    return name if name in SUPPORTED_APPS else None


def _main_service(
    instance: AppInstance, services: dict[str, Any]
) -> str | None:
    if not instance.app:
        return None
    configured = _main_container_name(instance.app)
    if configured in services:
        return configured
    if len(services) == 1:
        return str(next(iter(services)))
    return None


def _app_dict(instance: AppInstance) -> dict[str, Any]:
    project = instance.project
    app_name = _supported_name(instance)
    group = instance.orchestration_group
    template = group.orchestration if group else instance.orchestration
    all_services = _merge_compose_services(project) if project is not None else {}
    main_service = _main_service(instance, all_services)
    services = [main_service] if main_service else []
    can_enable = project is not None and main_service is not None
    reason = None
    if project is None:
        reason = "该应用没有关联 Compose 项目，无法注入代理"
    elif not all_services:
        reason = "Compose 配置不存在或没有服务"
    elif main_service is None:
        reason = "无法从应用模板识别主应用服务"
    return {
        "instance_id": instance.id,
        "instance_name": instance.instance_name,
        "display_name": instance.app.display_name if instance.app else instance.instance_name,
        "app_name": app_name,
        "icon": instance.app.icon if instance.app else None,
        "project_id": project.id if project else None,
        "project_name": project.project_name if project else None,
        "orchestration_group_id": group.id if group else None,
        "orchestration_group_name": group.instance_name if group else None,
        "orchestration_name": template.display_name if template else None,
        "status": instance.status,
        "services": services,
        "acceleration_enabled": bool(instance.acceleration_enabled),
        "proxy_enabled": bool(instance.proxy_enabled),
        "dns": [],
        "can_enable": can_enable,
        "reason": reason,
    }


async def list_apps(db: AsyncSession) -> list[dict[str, Any]]:
    """列出支持网络加速的应用实例。"""
    result = await db.execute(
        select(AppInstance)
        .options(
            selectinload(AppInstance.app),
            selectinload(AppInstance.project),
            selectinload(AppInstance.orchestration),
            selectinload(AppInstance.orchestration_group).selectinload(
                AppOrchestrationInstance.orchestration
            ),
        )
        .order_by(AppInstance.id)
    )
    return [
        _app_dict(instance)
        for instance in result.scalars()
        if _supported_name(instance) is not None
    ]


def _environment_dict(raw: Any) -> dict[str, str]:
    if isinstance(raw, dict):
        return {str(k): "" if v is None else str(v) for k, v in raw.items()}
    if isinstance(raw, list):
        result: dict[str, str] = {}
        for item in raw:
            key, sep, value = str(item).partition("=")
            if sep and key:
                result[key] = value
        return result
    return {}


def _apply_proxy(
    content: str, proxy: str, enabled: bool, instance: AppInstance
) -> tuple[str, list[str]]:
    try:
        data = yaml.safe_load(content) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Compose 文件不是合法 YAML：{exc}") from exc
    services = data.get("services")
    if not isinstance(services, dict) or not services:
        raise ValueError("Compose 文件中没有 services")
    target = _main_service(instance, services)
    if target is None:
        raise ValueError("无法从应用模板识别主应用服务，未修改任何服务")

    app_name = _supported_name(instance)
    proxy_env = APP_PROXY_ENV.get(app_name or "", DEFAULT_PROXY_ENV)
    acceleration_env = APP_ACCELERATION_ENV.get(app_name or "", {})
    for name, service in services.items():
        if not isinstance(service, dict):
            continue
        env = _environment_dict(service.get("environment"))
        original = dict(env)
        if name == target:
            if enabled:
                env[proxy_env] = proxy
                env.update(acceleration_env)
            else:
                for key in acceleration_env:
                    env.pop(key, None)
            for key in LEGACY_PROXY_ENV_KEYS:
                if key != proxy_env or not enabled:
                    env.pop(key, None)
            for key in NO_PROXY_KEYS:
                env.pop(key, None)
        else:
            for key in LEGACY_PROXY_ENV_KEYS:
                if env.get(key) == proxy:
                    env.pop(key, None)
            for key in NO_PROXY_KEYS:
                if env.get(key) == NO_PROXY_VALUE:
                    env.pop(key, None)
        if env != original:
            service["environment"] = env
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False), [target]


async def set_proxy(
    db: AsyncSession, instance_id: int, enabled: bool
) -> dict[str, Any]:
    """开启或关闭单个应用的网络加速代理。"""
    async with _lock:
        result = await db.execute(
            select(AppInstance)
            .options(
                selectinload(AppInstance.app),
                selectinload(AppInstance.project),
            )
            .where(AppInstance.id == instance_id)
        )
        instance = result.scalar_one_or_none()
        if instance is None:
            raise APIException("应用实例不存在", 404)
        app_name = _supported_name(instance)
        if app_name is None:
            raise APIException("该应用暂不支持网络加速", 400)
        project = instance.project
        if project is None:
            raise APIException("该应用没有关联 Compose 项目，无法注入代理", 400)
        proxy = await mihomo.proxy_url(db)
        if enabled:
            status = await mihomo.status_async(db)
            if not status.get("running"):
                raise APIException("Mihomo 未运行，请先部署并启动", 400)

        primary = _config_file_paths(project)[0]
        current = primary.read_text(encoding="utf-8") if primary.exists() else ""
        try:
            new_content, services = _apply_proxy(current, proxy, enabled, instance)
        except ValueError as exc:
            raise APIException(str(exc), 400) from exc

        path, version, original = await hosts_acceleration_service._save_compose_version(
            db,
            project,
            new_content,
            "OneTap 网络加速：注入代理" if enabled else "OneTap 网络加速：移除代理",
        )
        try:
            await compose_manager.action_async(db, project, "up")
        except Exception as exc:
            await hosts_acceleration_service._discard_version(db, version, path, original)
            raise APIException(f"应用代理配置失败：{exc}", 502) from exc

        instance.proxy_enabled = enabled
        await db.flush()
        return {
            "instance_id": instance.id,
            "enabled": enabled,
            "proxy": proxy,
            "services": services,
            "warnings": [],
            "message": "已开启应用加速" if enabled else "已关闭应用加速",
        }


async def disable_all(db: AsyncSession) -> list[str]:
    """关闭全部应用的代理接入，返回已处理的应用名称。"""
    result = await db.execute(
        select(AppInstance)
        .options(selectinload(AppInstance.app), selectinload(AppInstance.project))
        .where(AppInstance.proxy_enabled.is_(True))
        .order_by(AppInstance.id)
    )
    instances = list(result.scalars())
    disabled: list[str] = []
    for instance in instances:
        if instance.project is None or _supported_name(instance) is None:
            instance.proxy_enabled = False
        else:
            await set_proxy(db, instance.id, False)
        await db.commit()
        disabled.append(instance.instance_name)
    return disabled
