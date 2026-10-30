"""系统全局配置服务。

提供系统配置的获取、初始化和更新能力。
整个系统只维护一条配置记录（id=1）。
"""

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import APIException
from app.models.system_config import SystemConfig
from app.schemas.system_config import SystemConfigUpdate

MAX_DOCKER_REGISTRY_MIRRORS = 20
"""镜像加速地址数量上限。"""

EMAIL_USERNAME_APPS: tuple[str, ...] = ("komga", "ermao-library")
"""必须使用邮箱作为登录账号的应用。"""

_EMAIL_DOMAIN_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
"""邮箱后缀中单个域名段的合法格式。"""


def normalize_email_domain(raw: str | None) -> str:
    """规范化邮箱后缀：去空白、去 @ 前缀、转小写并校验格式。

    Args:
        raw: 用户填写的邮箱后缀，可带 ``@``

    Returns:
        str: 不含 ``@`` 的域名；未填写时返回空字符串

    Raises:
        APIException: 域名格式不合法或长度超限
    """
    value = (raw or "").strip().lower().lstrip("@").strip().strip(".")
    if not value:
        return ""
    if len(value) > 255:
        raise APIException("邮箱后缀长度不能超过 255 个字符", 400)
    if not all(_EMAIL_DOMAIN_LABEL.match(label) for label in value.split(".")):
        raise APIException("邮箱后缀格式不正确，例如 example.com", 400)
    return value


def resolve_username_for_app(
    app_name: str, username: str, email_domain: str | None
) -> str:
    """按应用要求组装登录用户名。

    需要邮箱登录的应用在用户名不含 ``@`` 时追加配置的后缀；
    其他应用或未配置后缀时原样返回。

    Args:
        app_name: 应用标识
        username: 统一管理员用户名
        email_domain: 已规范化的邮箱后缀（不含 @）

    Returns:
        str: 实际使用的登录用户名
    """
    raw = (username or "").strip() or "admin"
    domain = (email_domain or "").strip()
    if app_name not in EMAIL_USERNAME_APPS or "@" in raw or not domain:
        return raw
    return f"{raw}@{domain}"


def normalize_docker_registry_mirrors(urls: list[str]) -> list[str]:
    """规范化镜像加速地址：去空白、去尾部斜杠、去重并校验协议。

    保持原有顺序（顺序即优先级），空字符串会被忽略。
    """
    normalized: list[str] = []
    for raw in urls:
        url = (raw or "").strip().rstrip("/")
        if not url:
            continue
        if not url.lower().startswith(("http://", "https://")):
            raise APIException(f"镜像加速地址必须以 http:// 或 https:// 开头：{url}", 400)
        if len(url) > 500:
            raise APIException("镜像加速地址长度不能超过 500 个字符", 400)
        if url not in normalized:
            normalized.append(url)

    if len(normalized) > MAX_DOCKER_REGISTRY_MIRRORS:
        raise APIException(
            f"镜像加速地址最多 {MAX_DOCKER_REGISTRY_MIRRORS} 个", 400
        )
    return normalized


class SystemConfigService:
    """系统全局配置服务。"""

    async def get_or_create(self, db: AsyncSession) -> SystemConfig:
        """获取系统配置，不存在则创建默认记录。

        Args:
            db: 异步数据库会话

        Returns:
            SystemConfig: 系统配置对象
        """
        result = await db.execute(select(SystemConfig).where(SystemConfig.id == 1))
        config = result.scalar_one_or_none()
        if config is None:
            config = SystemConfig(id=1)
            db.add(config)
            await db.flush()
            await db.refresh(config)
        return config

    async def update(
        self, db: AsyncSession, data: SystemConfigUpdate
    ) -> SystemConfig:
        """更新系统配置。

        Args:
            db: 异步数据库会话
            data: 配置更新数据

        Returns:
            SystemConfig: 更新后的配置对象
        """
        config = await self.get_or_create(db)

        if data.proxy_enabled is not None:
            config.proxy_enabled = bool(data.proxy_enabled)

        update_fields = [
            "http_proxy",
            "https_proxy",
            "no_proxy",
        ]
        for field in update_fields:
            value = getattr(data, field)
            if value is not None:
                setattr(config, field, value.strip() if value else None)

        if data.service_port_start is not None:
            config.service_port_start = data.service_port_start

        if data.default_admin_username is not None:
            username = data.default_admin_username.strip()
            config.default_admin_username = username or "admin"
        if data.default_admin_password is not None:
            config.default_admin_password = data.default_admin_password.strip()
        if data.default_email_domain is not None:
            config.default_email_domain = normalize_email_domain(
                data.default_email_domain
            )

        if data.auto_update_enabled is not None:
            config.auto_update_enabled = bool(data.auto_update_enabled)

        if data.docker_registry_mirrors is not None:
            config.docker_registry_mirrors = normalize_docker_registry_mirrors(
                data.docker_registry_mirrors
            )

        await db.flush()
        await db.refresh(config)
        return config

    def to_dict(self, config: SystemConfig) -> dict:
        """将配置对象转换为前端友好的字典。

        Args:
            config: 系统配置对象

        Returns:
            dict: 包含 id、代理配置、端口分配与管理员工凭据的字典
        """
        return {
            "id": config.id,
            "proxy_enabled": bool(config.proxy_enabled),
            "http_proxy": config.http_proxy,
            "https_proxy": config.https_proxy,
            "no_proxy": config.no_proxy,
            "service_port_start": config.service_port_start,
            "default_admin_username": config.default_admin_username,
            "default_admin_password": config.default_admin_password,
            "default_email_domain": config.default_email_domain or "",
            "auto_update_enabled": bool(config.auto_update_enabled),
            "email_username_apps": list(EMAIL_USERNAME_APPS),
            "docker_registry_mirrors": config.docker_registry_mirrors or [],
        }


# 全局服务单例
system_config_service = SystemConfigService()
