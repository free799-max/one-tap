"""管理端下发的网络加速订阅。

客户端不再维护节点与规则：只保存订阅地址，按需拉取 Mihomo 配置并写入
本机，规则从订阅内容中解析后用于只读展示。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import APIException
from app.services.acceleration import mihomo
from app.services.system.config_service import system_config_service

SUBSCRIPTION_TIMEOUT = 30.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_url(url: str) -> str:
    """校验订阅地址，只允许 http/https。"""
    value = (url or "").strip()
    if not value:
        raise APIException("订阅地址不能为空", 400)
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise APIException("订阅地址必须是 http 或 https 地址", 400)
    if len(value) > 500:
        raise APIException("订阅地址过长", 400)
    return value


def extract_rules(config: dict) -> list[dict]:
    """从 Mihomo 配置中提取规则，供页面只读展示。"""
    result: list[dict] = []
    for line in config.get("rules") or []:
        if not isinstance(line, str):
            continue
        parts = line.split(",")
        kind = parts[0]
        if kind in {"MATCH", "FINAL"}:
            continue
        value = parts[1] if len(parts) > 1 else ""
        result.append({"kind": kind, "value": value})
    return result


async def _fetch(url: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=SUBSCRIPTION_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.text
    except httpx.HTTPError as exc:
        raise APIException(f"拉取订阅失败：{exc}", 502) from exc


async def _fetch_rule_apps(url: str) -> dict[tuple[str, str], str]:
    """从管理端公开规则接口取规则所属应用；失败时返回空表。"""
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return {}
    rules_url = f"{parsed.scheme}://{parsed.netloc}/api/public/acceleration/rules"
    try:
        async with httpx.AsyncClient(
            timeout=SUBSCRIPTION_TIMEOUT, follow_redirects=True
        ) as client:
            response = await client.get(rules_url)
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError):
        return {}
    items = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return {}
    mapping: dict[tuple[str, str], str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip().lower()
        value = str(item.get("value") or "").strip().lower()
        app = str(item.get("app") or "").strip()
        if kind and value and app:
            mapping[(kind, value)] = app
    return mapping


def _parse(content: str) -> tuple[dict, list[dict]]:
    try:
        config = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise APIException(f"订阅内容不是合法 YAML：{exc}", 502) from exc
    if not isinstance(config, dict):
        raise APIException("订阅内容格式不正确", 502)
    if not config.get("proxies"):
        raise APIException("订阅中没有可用的代理节点", 502)
    return config, extract_rules(config)


def _last_sync_result(state: dict) -> dict | None:
    synced_at = state.get("last_sync_at")
    error = state.get("last_error")
    if not synced_at and not error:
        return None
    status = state.get("last_sync_status") or ("error" if error else "success")
    message = state.get("last_sync_message") or error or "订阅已同步到本机 Mihomo"
    return {
        "status": status,
        "message": message,
        "trigger": state.get("last_sync_trigger") or "manual",
        "changed": state.get("last_sync_changed"),
        "rules": int(state.get("last_sync_rules") or 0),
        "revision": int(state.get("last_sync_revision") or state.get("revision") or 0),
        "synced_at": synced_at,
    }


async def get_summary(db: AsyncSession) -> dict:
    """页面初始化数据：订阅地址、规则快照、本机运行状态。"""
    config = await system_config_service.get_or_create(db)
    state = await mihomo.read_state(db)
    return {
        "configured": bool(config.acceleration_subscription_url),
        "subscription_url": config.acceleration_subscription_url or "",
        "rules": list(config.acceleration_managed_rules or []),
        "rules_synced_at": config.acceleration_rules_synced_at,
        "last_sync_at": state.get("last_sync_at"),
        "last_sync_error": state.get("last_error"),
        "auto_sync_enabled": config.acceleration_auto_sync_enabled,
        "auto_sync_interval_minutes": config.acceleration_sync_interval_minutes,
        "last_sync_result": _last_sync_result(state),
        "mihomo": await mihomo.status_async(db),
        "moviepilot": await mihomo.moviepilot_status_async(db),
    }


async def update_subscription_url(db: AsyncSession, url: str) -> dict:
    config = await system_config_service.get_or_create(db)
    config.acceleration_subscription_url = normalize_url(url)
    return {"subscription_url": config.acceleration_subscription_url}


async def update_sync_settings(
    db: AsyncSession, *, enabled: bool, interval_minutes: int
) -> None:
    config = await system_config_service.get_or_create(db)
    config.acceleration_auto_sync_enabled = enabled
    config.acceleration_sync_interval_minutes = interval_minutes


async def sync(
    db: AsyncSession,
    url: str | None = None,
    *,
    force: bool = True,
    trigger: str = "manual",
) -> dict:
    """拉取订阅并写入本机 Mihomo。"""
    config = await system_config_service.get_or_create(db)
    target = normalize_url(url or config.acceleration_subscription_url)
    try:
        content = await _fetch(target)
        _, rules = _parse(content)
        apps = await _fetch_rule_apps(target)
        for rule in rules:
            rule["app"] = apps.get(
                (str(rule["kind"]).lower(), str(rule["value"]).lower()), "custom"
            )
    except APIException as exc:
        await mihomo.record_error(db, exc.message, trigger=trigger)
        raise

    sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
    state = await mihomo.read_state(db)
    if (
        not force
        and state.get("content_sha") == sha
        and state.get("subscription_url") == target
    ):
        config.acceleration_subscription_url = target
        config.acceleration_managed_rules = rules
        config.acceleration_rules_synced_at = _now()
        revision = int(state.get("revision") or 0)
        message = "订阅无变化"
        await mihomo.record_sync_result(
            db,
            status="unchanged",
            message=message,
            trigger=trigger,
            changed=False,
            rules=len(rules),
            revision=revision,
        )
        return {
            "ok": True,
            "message": message,
            "detail": {"revision": revision, "changed": False, "rules": len(rules)},
        }

    revision = int(sha[:8], 16)
    try:
        await mihomo.apply_subscription_async(
            db,
            content,
            subscription_name="管理端下发",
            revision=revision,
            content_sha=sha,
            subscription_url=target,
        )
    except RuntimeError as exc:
        await mihomo.record_error(db, str(exc), trigger=trigger)
        raise APIException(str(exc), 502) from exc

    config.acceleration_subscription_url = target
    config.acceleration_managed_rules = rules
    config.acceleration_rules_synced_at = _now()
    message = "订阅已同步到本机 Mihomo"
    await mihomo.record_sync_result(
        db,
        status="success",
        message=message,
        trigger=trigger,
        changed=True,
        rules=len(rules),
        revision=revision,
    )
    return {
        "ok": True,
        "message": message,
        "detail": {"revision": revision, "changed": True, "rules": len(rules)},
    }
