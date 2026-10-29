"""客户端内置的离线资源档案。

有些应用启动时需要额外下载插件依赖或浏览器内核（MoviePilot 是典型），
离线部署希望这些数据随资源镜像一次性提供。档案完全由客户端维护，
应用模板保持官方原样：

- 部署写盘时给匹配的服务注入固定镜像与离线环境变量；
- 服务启动后把资源镜像里的目录 ``docker cp`` 进应用容器，再重启应用。

这样管理端的应用模板不需要知道任何资源镜像/挂载路径。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import yaml


@dataclass(frozen=True)
class AssetCopy:
    """一条“资源镜像目录 -> 应用容器目录”的拷贝规则。"""

    source: str
    target: str
    label: str
    size: str


@dataclass(frozen=True)
class OfflineAssetsProfile:
    """某个应用的离线资源档案。"""

    app_name: str
    # 匹配的应用镜像仓库（不含 tag/digest）
    image_pattern: str
    # Compose 服务名，用于定位容器
    service: str
    # 资源镜像与拷贝规则
    assets_image: str
    copies: tuple[AssetCopy, ...]
    # 容器内完成标记；必须位于容器可写层（不能落在挂载卷里）
    marker: str
    # 档案修订号，规则变化时递增，促使已拷贝的容器重新拷贝
    revision: str = "1"
    # 写盘时替换为固定镜像，避免应用自动升级后与资源包不匹配
    pin_image: str | None = None
    # 写盘时注入的离线环境变量
    env: dict[str, str] = field(default_factory=dict)


MOVIEPILOT = OfflineAssetsProfile(
    app_name="moviepilot",
    image_pattern="jxxghp/moviepilot-v2",
    service="moviepilot",
    assets_image="free5104/moviepilot-assets:2.15.6",
    pin_image=(
        "jxxghp/moviepilot-v2@sha256:"
        "30b4de2a4e23da504f4a90ece710ed9f0787570063877bcc48e49febd340e3e8"
    ),
    env={
        "MOVIEPILOT_AUTO_UPDATE": "false",
        "AUTO_UPDATE_RESOURCE": "false",
        "CLOAKBROWSER_AUTO_UPDATE": "false",
    },
    copies=(
        AssetCopy(
            source="/assets/pysite",
            target="/opt/venv/lib/python3.12/site-packages",
            label="插件依赖",
            size="约 580 MB",
        ),
        AssetCopy(
            source="/assets/cloakbrowser",
            target="/moviepilot/.cloakbrowser",
            label="浏览器内核",
            size="约 697 MB",
        ),
    ),
    marker="/opt/venv/.onetap-assets",
    revision="1",
)

PROFILES: tuple[OfflineAssetsProfile, ...] = (MOVIEPILOT,)


def get_profile(app_name: str) -> OfflineAssetsProfile | None:
    """按应用名取档案。"""
    for profile in PROFILES:
        if profile.app_name == app_name:
            return profile
    return None


def image_repo(image: str | None) -> str:
    """去掉 tag/digest，保留仓库名。"""
    value = (image or "").strip()
    if not value:
        return ""
    value = value.split("@", 1)[0]
    last = value.rsplit("/", 1)[-1]
    if ":" in last:
        value = value.rsplit(":", 1)[0]
    return value


def _match_profile_by_image(image: str | None) -> OfflineAssetsProfile | None:
    repo = image_repo(image)
    for profile in PROFILES:
        if repo == profile.image_pattern:
            return profile
    return None


def profiles_in_compose(content: str) -> list[OfflineAssetsProfile]:
    """返回 Compose 内容里出现的离线资源档案（去重、保持顺序）。"""
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError:
        return []
    services = data.get("services") if isinstance(data, dict) else None
    if not isinstance(services, dict):
        return []

    found: list[OfflineAssetsProfile] = []
    for service in services.values():
        if not isinstance(service, dict):
            continue
        profile = _match_profile_by_image(service.get("image"))
        if profile and profile not in found:
            found.append(profile)
    return found


def _merge_env(env: object, overrides: dict[str, str]) -> object:
    """把离线环境变量合并进服务 env，保留原有写法。"""
    if not overrides:
        return env
    if isinstance(env, dict):
        merged = dict(env)
        merged.update(overrides)
        return merged

    items = list(env) if isinstance(env, list) else []
    kept: list[object] = []
    for item in items:
        if isinstance(item, str) and item.split("=", 1)[0] in overrides:
            continue
        if isinstance(item, dict) and len(item) == 1:
            key = next(iter(item))
            if key in overrides:
                continue
        kept.append(item)
    kept.extend(f"{key}={value}" for key, value in overrides.items())
    return kept


def apply_offline_profile(content: str) -> str:
    """给匹配的服务注入固定镜像与离线环境变量，其余内容保持不变。"""
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError:
        return content
    if not isinstance(data, dict):
        return content
    services = data.get("services")
    if not isinstance(services, dict):
        return content

    changed = False
    for service in services.values():
        if not isinstance(service, dict):
            continue
        profile = _match_profile_by_image(service.get("image"))
        if profile is None:
            continue
        if profile.pin_image and service.get("image") != profile.pin_image:
            service["image"] = profile.pin_image
            changed = True
        if profile.env:
            service["environment"] = _merge_env(service.get("environment"), profile.env)
            changed = True

    if not changed:
        return content
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
