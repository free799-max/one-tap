"""应用配置模块。

使用 pydantic-settings 从环境变量或 .env 文件加载配置，
所有配置项均有默认值，可通过环境变量覆盖。
"""

import os
import secrets
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings

# 未显式配置 SECRET_KEY 时，启动阶段会从数据目录读取或自动生成。
DEFAULT_SECRET_KEY = "change-me-in-production"


class Settings(BaseSettings):
    """全局应用配置。"""

    APP_NAME: str = "OneTap"
    """应用名称。"""

    APP_VERSION: str = "dev"
    """应用版本，构建镜像时由 CI 以标签注入，用于安装记录展示。"""

    DATABASE_URL: str = "postgresql+asyncpg://onetap:onetap@localhost:5434/onetap"
    """数据库连接字符串，默认使用本机 Docker PostgreSQL 异步驱动。"""

    SECRET_KEY: str = DEFAULT_SECRET_KEY
    """JWT 签名密钥，可通过环境变量覆盖，否则自动持久化到数据目录。"""

    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440
    """访问令牌过期时间（分钟），默认 24 小时。"""

    REGISTRATION_OPEN: bool = False
    """是否开放新用户注册。首个用户始终允许注册并成为管理员。"""

    CORS_ORIGINS: str = "http://localhost:5173"
    """允许的跨域来源，逗号分隔。"""

    DATA_ROOT: str = "./data"
    """数据根目录，Compose 工作区与应用图标默认存放于其下。"""

    COMPOSE_WORKSPACE_DIR: str = ""
    """Compose 项目工作目录，默认为 DATA_ROOT/compose，可单独覆盖。"""

    HOST_PATH_ALIAS_ENABLED: bool = True
    """是否建立宿主机路径软链别名，让 Compose 标签与相对挂载使用宿主机真实路径。"""

    HOST_DATA_ROOT: str = ""
    """DATA_ROOT 对应的宿主机路径；留空时通过自身容器挂载信息自动探测。"""

    APP_ICON_DIR: str = ""
    """图标文件存储目录，默认为 DATA_ROOT/app-icons，可单独覆盖。"""

    MANAGE_BASE_URL: str = ""

    MANAGE_SYNC_INTERVAL_MINUTES: int = 30
    """从管理端定时同步应用与编排方案的间隔（分钟）。"""

    MANAGE_SYNC_ON_STARTUP: bool = True
    """启动时是否立即从管理端同步一次应用与编排方案。"""

    LICENSE_PUBLIC_KEY: str = ""
    """授权证书 Ed25519 公钥（PEM）；配置后不再从管理端获取。"""

    LICENSE_ISSUER: str = "onetap-manage"
    """授权证书签发方，需与管理端 LICENSE_ISSUER 一致。"""

    LICENSE_REFRESH_INTERVAL_HOURS: float = 24
    """后台刷新授权证书的间隔（小时）。"""

    LICENSE_HTTP_TIMEOUT_SECONDS: float = 15
    """授权管理端请求超时（秒）。"""

    LICENSE_PURCHASE_URL: str = ""
    """专属加速购买页地址；配置后前端展示购买入口。"""

    @model_validator(mode="after")
    def _fill_data_subdirs(self) -> "Settings":
        """未显式配置时，将子目录默认值填充到 DATA_ROOT 之下。"""
        if not self.COMPOSE_WORKSPACE_DIR:
            self.COMPOSE_WORKSPACE_DIR = os.path.join(self.DATA_ROOT, "compose")
        if not self.APP_ICON_DIR:
            self.APP_ICON_DIR = os.path.join(self.DATA_ROOT, "app-icons")
        return self

    def cors_origin_list(self) -> list[str]:
        """解析 CORS_ORIGINS 为来源列表。"""
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    model_config = {"env_file": ".env", "extra": "ignore"}
    """指定 .env 文件路径用于加载环境变量；忽略已废弃的未知变量，避免旧 .env 导致启动失败。"""


# 全局配置单例
settings = Settings()


def ensure_secret_key() -> None:
    """读取或生成持久化的 JWT 密钥，显式配置的环境变量优先。"""
    if settings.SECRET_KEY and settings.SECRET_KEY != DEFAULT_SECRET_KEY:
        return

    secret_path = Path(settings.DATA_ROOT) / ".secret_key"
    secret_key = ""
    if secret_path.exists():
        secret_key = secret_path.read_text(encoding="utf-8").strip()

    if not secret_key:
        secret_path.parent.mkdir(parents=True, exist_ok=True)
        secret_key = secrets.token_urlsafe(48)
        secret_path.write_text(secret_key, encoding="utf-8")
        secret_path.chmod(0o600)

    settings.SECRET_KEY = secret_key
