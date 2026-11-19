"""部署凭据解析与注入。

编排部署/修复时为各应用准备管理员账号密码与 API Key：
- 系统设置中配置了统一密码时，所有支持的应用使用同一套凭据；
- 未配置时保留用户已填写的值，或随机生成；
- 修复（重新部署）时继承实例已存储的密钥类配置，避免与
  保留的数据卷中已初始化的密码不匹配。

env 类应用（MoviePilot）在部署前把凭据写入 env 数组；
运行时装配类应用（qBittorrent / Jellyfin / Emby）只生成凭据，
由部署后的 provision 流程配置进真实服务。
"""

import re
import secrets
import string

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.system import system_config_service
from app.services.system.config_service import resolve_username_for_app

# 密钥类环境变量名（密码/密钥/令牌），修复时从已存储配置继承
SENSITIVE_KEY_PATTERN = re.compile(
    r"password|secret|token|api_key|apikey|access_key", re.IGNORECASE
)

# env 注入类应用：env 数组键 -> 用户名/密码/API Token 的环境变量名。
# extra_env 用于写入与账号配套的开关（如 Reader 的登录认证开关）。
_ENV_CREDENTIAL_SPECS: dict[str, dict[str, object]] = {
    "moviepilot": {
        "env_key": "moviepilot_env",
        "username_key": "SUPERUSER",
        "password_key": "SUPERUSER_PASSWORD",
        "api_token_key": "API_TOKEN",
    },
    "reader": {
        "env_key": "reader_env",
        "password_key": "READER_APP_SECUREKEY",
        "extra_env": {"READER_APP_SECURE": "true"},
    },
    "suwayomi": {
        "env_key": "suwayomi_env",
        "username_key": "AUTH_USERNAME",
        "password_key": "AUTH_PASSWORD",
        # 默认开启简单登录：WebUI 走登录页，客户端（Mihon / Tsumiru）
        # 用同一套账号密码换取 Cookie 会话。
        "extra_env": {"AUTH_MODE": "simple_login"},
    },
    # 巡影的账号密码走运行时注册，只有 Agent API Token 需要 env 注入，
    # 供 OneTap MCP 以 Bearer Token 读取媒体库与发起获取。
    "mediary-scout": {
        "env_key": "env",
        "api_token_key": "MEDIA_TRACK_AGENT_TOKEN",
    },
}

# 运行时装配类应用：凭据在部署后由应用客户端 provision 写入
_PROVISION_ONLY_APPS = {
    "qbittorrent",
    "jellyfin",
    "emby",
    "comic-auto",
    "komga",
    "kavita",
    "music-dl",
    "music-tag-web",
    "navidrome",
    "ermao-library",
    "openlist",
}


def generate_password(length: int = 16) -> str:
    """生成随机密码（字母+数字）。"""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _get_env(config: dict, env_key: str, key: str) -> str | None:
    for row in config.get(env_key) or []:
        if row.get("key") == key:
            return row.get("value")
    return None


def _set_env(config: dict, env_key: str, key: str, value: str) -> None:
    rows = config.setdefault(env_key, [])
    for row in rows:
        if row.get("key") == key:
            row["value"] = value
            return
    rows.append({"key": key, "value": value})


def has_credential_env(app_name: str) -> bool:
    """应用是否通过环境变量注入账号密码。"""
    return app_name in _ENV_CREDENTIAL_SPECS


def resolve_credential_env(
    app_name: str, config: dict
) -> tuple[str, dict[str, str]] | None:
    """返回应用的凭据 env 键与当前需要同步的认证变量。

    env 注入类应用（MoviePilot / Reader / Suwayomi）的登录信息只存在于
    部署配置的环境变量里，升级时据此把账号、密码与认证开关补进现有
    Compose，否则模板新增的认证配置不会生效。
    """
    spec = _ENV_CREDENTIAL_SPECS.get(app_name)
    if spec is None:
        return None
    env_key = str(spec["env_key"])
    keys: set[str] = {
        str(key)
        for key in (
            spec.get("username_key"),
            spec.get("password_key"),
            spec.get("api_token_key"),
        )
        if key
    }
    extra_env = spec.get("extra_env")
    if isinstance(extra_env, dict):
        keys.update(str(key) for key in extra_env)
    values: dict[str, str] = {}
    for row in config.get(env_key) or []:
        if not isinstance(row, dict):
            continue
        key = row.get("key")
        value = row.get("value")
        if key in keys and value not in (None, ""):
            values[str(key)] = str(value)
    return env_key, values


def inherit_stored_secrets(stored_config: dict | None, config: dict) -> None:
    """把上次部署配置中的密钥类 env 继承到新配置（原地修改 config）。

    修复（重新部署）时数据卷保留，PostgreSQL/Redis 等密码仅在首次
    初始化生效，重新随机生成必然与旧数据卷中的密码不匹配，因此
    已存储的密钥始终优先于前端重新填写的值。
    """
    if not stored_config:
        return
    for env_key, rows in stored_config.items():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            key = row.get("key")
            value = row.get("value")
            if not key or not value:
                continue
            if not SENSITIVE_KEY_PATTERN.search(str(key)):
                continue
            _set_env(config, env_key, str(key), str(value))


async def prepare_app_credentials(
    db: AsyncSession,
    app_name: str,
    config: dict,
    fallback_password: str | None = None,
) -> dict | None:
    """为单个应用准备部署凭据，必要时注入 env。

    Args:
        db: 异步数据库会话
        app_name: 应用名
        config: 合并共享配置后的部署配置（可能被原地修改）
        fallback_password: 修复场景下实例上次已配置的密码，
            未设置统一密码时优先复用（数据卷保留，旧密码仍有效）

    Returns:
        provision 所需凭据 {"username", "password", "api_key"?}；
        应用不支持自动配置时返回 None
    """
    spec = _ENV_CREDENTIAL_SPECS.get(app_name)
    if spec is None and app_name not in _PROVISION_ONLY_APPS:
        return None

    sys_config = await system_config_service.get_or_create(db)
    username = (sys_config.default_admin_username or "").strip() or "admin"
    username = resolve_username_for_app(
        app_name, username, sys_config.default_email_domain
    )
    unified_password = (sys_config.default_admin_password or "").strip() or None

    if spec is not None:
        env_key = str(spec["env_key"])
        username_key = spec.get("username_key")
        password_key = spec.get("password_key")
        api_token_key = spec.get("api_token_key")

        extra_env = spec.get("extra_env")
        if isinstance(extra_env, dict):
            for key, value in extra_env.items():
                _set_env(config, env_key, str(key), str(value))

        previous_password = (
            _get_env(config, env_key, str(password_key))
            if password_key
            else None
        )
        password = unified_password
        if password is None:
            # 未设置统一密码时保留用户已填/前端随机生成的密码
            password = previous_password
        if not password:
            # 未配置密码 env 的应用从上次配置继承密码
            password = fallback_password
        if not password:
            password = generate_password()
        if not previous_password and fallback_password and fallback_password != password:
            # 统一密码变化时，用上次落库的密码登录后再改密
            previous_password = fallback_password
        elif (
            unified_password
            and previous_password == password
            and fallback_password
            and fallback_password != password
        ):
            # 上一轮失败可能已把 env 里的密码改写成目标密码，应用实际仍在用
            # 落库的旧密码；此时以旧密码登录，否则重试会一直认证失败
            previous_password = fallback_password

        if username_key:
            _set_env(config, env_key, str(username_key), username)
        if password_key:
            _set_env(config, env_key, str(password_key), password)

        api_token = None
        if api_token_key:
            api_token = _get_env(config, env_key, str(api_token_key))
            if not api_token:
                # MoviePilot 要求 API_TOKEN 为不少于 16 位的复杂字符串
                api_token = generate_password(24)
                _set_env(config, env_key, str(api_token_key), api_token)

        creds = {"username": username, "password": password}
        if api_token:
            creds["api_key"] = api_token
        if previous_password and previous_password != password:
            # env 密码仅首次初始化生效，修复场景需用旧密码登录后改密
            creds["previous_password"] = previous_password
        return creds

    password = unified_password or fallback_password or generate_password()
    creds = {"username": username, "password": password}
    if fallback_password and fallback_password != password:
        # 配置卷复用时服务当前密码是旧密码，需用旧密码登录后改密
        creds["previous_password"] = fallback_password
    return creds
