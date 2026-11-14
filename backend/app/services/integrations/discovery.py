"""已部署应用发现。

从实例表与实例组配置中整理出可供集成使用的访问地址和凭据。
凭据仅提供明文给服务端内部使用，API 输出时会先脱敏。
"""

import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.orchestration import AppInstance
from app.services.overview import build_app_url


_USERNAME_PATTERN = re.compile(r"(^|_)(user(name)?|account|login)($|_)", re.I)
_PASSWORD_PATTERN = re.compile(r"(^|_)(pass(word|wd)?|pwd|secret)($|_)", re.I)
_API_KEY_PATTERN = re.compile(r"(^|_)(api_?key|token|access_?token)($|_)", re.I)


@dataclass
class DeployedApp:
    """一个已部署应用及其集成所需信息。"""

    instance_id: int
    app_name: str
    display_name: str
    instance_name: str
    status: str
    url: str | None = None
    has_persisted_url: bool = False
    icon: str | None = None
    project_id: int | None = None
    container_id: str | None = None
    container_name: str | None = None
    username: str | None = None
    password: str | None = None
    api_key: str | None = None
    env_secrets: dict[str, str] = field(default_factory=dict)

    @property
    def has_credentials(self) -> bool:
        return bool(self.username or self.password or self.api_key)


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _first(*values: object) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return ""


def _iter_env_entries(value: object):
    """遍历配置中的 env 数组，产出 (key, value)。"""
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                key = _text(item.get("key") or item.get("name"))
                val = _text(item.get("value"))
                if key and val:
                    yield key, val
    elif isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, (str, int, float)) and _text(item):
                yield str(key), _text(item)


def _extract_env_secrets(config: dict) -> dict[str, str]:
    """从部署配置的 env 数组中提取账号、密码与 API Key。

    仅用于应用市场单独部署、没有实例组 app_configs 的场景。
    同名敏感项保留第一次出现的值。
    """
    secrets: dict[str, str] = {}
    for value in config.values():
        for key, val in _iter_env_entries(value):
            if _USERNAME_PATTERN.search(key) and "username" not in secrets:
                secrets["username"] = val
            elif _PASSWORD_PATTERN.search(key) and "password" not in secrets:
                secrets["password"] = val
            elif _API_KEY_PATTERN.search(key) and "api_key" not in secrets:
                secrets["api_key"] = val
    return secrets


def _url_for_host_port(host: str, port: int) -> str:
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{port}"


def _resolve_url(
    instance: AppInstance,
    config: dict,
    group_entry: dict,
    host: str,
) -> str | None:
    """解析应用访问地址，优先显式配置，其次按当前主机名生成。"""
    explicit = _first(
        group_entry.get("url"),
        config.get("url"),
        config.get("access_url"),
        config.get("suggested_url"),
    )
    if explicit:
        return explicit

    if instance.app is not None:
        generated = build_app_url(instance.app, config, host)
        if generated:
            return generated

    host_port = config.get("host_port")
    if host_port:
        try:
            return _url_for_host_port(host, int(host_port))
        except (TypeError, ValueError):
            pass

    network_ip = _first(config.get("network_ip"))
    container_port = config.get("container_port")
    if network_ip and container_port:
        try:
            return _url_for_host_port(network_ip, int(container_port))
        except (TypeError, ValueError):
            pass
    return None


def _to_deployed_app(instance: AppInstance, host: str) -> DeployedApp:
    app = instance.app
    app_name = app.name if app else instance.instance_name
    display_name = app.display_name if app else instance.instance_name
    config = instance.config or {}
    if not isinstance(config, dict):
        config = {}

    group = instance.orchestration_group
    group_entry = {}
    if group is not None:
        group_entry = (group.app_configs or {}).get(app_name) or {}
        if not isinstance(group_entry, dict):
            group_entry = {}

    env_secrets = _extract_env_secrets(config)
    username = _first(group_entry.get("username"), config.get("username"), env_secrets.get("username"))
    password = _first(group_entry.get("password"), config.get("password"), env_secrets.get("password"))
    api_key = _first(group_entry.get("api_key"), config.get("api_key"), env_secrets.get("api_key"))
    url = _resolve_url(instance, config, group_entry, host)
    has_persisted_url = bool(
        _first(
            group_entry.get("url"),
            config.get("url"),
            config.get("access_url"),
            config.get("suggested_url"),
        )
        or (_first(config.get("network_ip")) and config.get("container_port"))
    )

    return DeployedApp(
        instance_id=instance.id,
        app_name=app_name,
        display_name=display_name,
        instance_name=instance.instance_name,
        status=instance.status,
        url=url,
        has_persisted_url=has_persisted_url,
        icon=app.icon if app else None,
        project_id=instance.project_id,
        container_id=_text(config.get("container_id")) or None,
        container_name=_text(config.get("container_name")) or None,
        username=username or None,
        password=password or None,
        api_key=api_key or None,
        env_secrets=env_secrets,
    )


async def list_deployed_apps(db: AsyncSession, host: str) -> list[DeployedApp]:
    """列出全部已部署应用（含停止或异常状态，便于统一处理）。"""
    result = await db.execute(
        select(AppInstance)
        .options(
            selectinload(AppInstance.app),
            selectinload(AppInstance.orchestration_group),
        )
        .order_by(AppInstance.id)
    )
    return [_to_deployed_app(instance, host) for instance in result.scalars().all()]


async def get_deployed_app(
    db: AsyncSession, instance_id: int, host: str
) -> DeployedApp | None:
    """按实例 ID 获取应用。"""
    result = await db.execute(
        select(AppInstance)
        .where(AppInstance.id == instance_id)
        .options(
            selectinload(AppInstance.app),
            selectinload(AppInstance.orchestration_group),
        )
    )
    instance = result.scalar_one_or_none()
    if instance is None:
        return None
    return _to_deployed_app(instance, host)
