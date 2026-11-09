"""应用级 DNS 加速服务。

只给单个应用（Compose 项目）注入 ``dns:`` 与加速网络，不影响宿主机或
其他应用。首次开启时自动部署一台专用 AdGuard Home：

- AdGuard 与加速应用加入同一个独立 Docker 网络 ``onetap-accel``，使用固定
  容器 IP，不需要占用宿主机的 53 端口；
- 直接改写应用自己的 Compose 文件（单一文件源），每次开启/关闭都生成一个
  新的 Compose 版本；被改动的原始值记录在 ``x-onetap-accel`` 扩展字段里，
  关闭时按记录还原；
- 加速域名规则由配套管理端下发，OneTap 负责同步并写入 AdGuard 的 DNS 重写。
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import secrets
import shutil
import socket
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import docker as docker_sdk
import httpx
import yaml
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.core.exceptions import APIException
from app.core.security import hash_password
from app.models.app_store import App
from app.models.docker import DockerComposeProject, DockerComposeVersion
from app.models.orchestration import AppInstance, AppOrchestrationInstance
from app.models.system_config import SystemConfig
from app.services.docker import docker_manager, host_files
from app.services.docker.host_network import detect_host_address
from app.services.manage_client import manage_base_urls
from app.services.system.config_service import system_config_service
from app.services.system.hosts_optimizer_service import hosts_optimizer_service
from app.services.system.port_allocator import (
    allocate_free_ports,
    get_used_ports,
    release_ports,
    reserve_ports,
)
from app.services.system.storage_directory_service import storage_directory_service

logger = logging.getLogger(__name__)

ADGUARD_PROJECT = "onetap-adguard"
ADGUARD_CONTAINER = "onetap-adguardhome"
ADGUARD_SERVICE = "adguardhome"
ADGUARD_NETWORK = "onetap-accel"
ADGUARD_IMAGE = "adguard/adguardhome:v0.107.79"
ADGUARD_APP_NAMES = ("adguardhome", "adguard-home", "adguard")
LEGACY_DATA_DIRNAME = "adguard"
OVERRIDE_FILENAME = "docker-compose.onetap-dns.yml"
"""旧版加速方案生成的覆盖文件，仅用于兼容清理。"""

ACCEL_STATE_KEY = "x-onetap-accel"
"""Compose 顶层扩展字段，记录开启加速前的原始值，供关闭时还原。"""

# AdGuard 不可达时的兜底 DNS。Docker 按顺序尝试，只有主 DNS 超时后才会用到这里。
ACCEL_FALLBACK_DNS = ("223.5.5.5", "119.29.29.29")

# AdGuard 默认上游 Quad9 在国内网络不可达，初始化时统一改成可达的加密 DNS。
# 注意：国内 DoH 的 wire 格式仍可能返回污染结果，只能保证「探测 + 证书校验
# 通过」的加速域名不被污染；全局干净解析需要代理。
ADGUARD_UPSTREAM_DNS = (
    "https://doh.pub/dns-query",
    "https://1.12.12.12/dns-query",
)
ADGUARD_BOOTSTRAP_DNS = ("223.5.5.5", "119.29.29.29")

ADGUARD_SETUP_TIMEOUT = 90.0
ADGUARD_WEB_TIMEOUT = 90.0
ADGUARD_POLL_INTERVAL = 2.0
ADGUARD_DEPLOY_TIMEOUT = 300.0


def _service_name(value: Any) -> str:
    """把 Compose 服务名统一成字符串。"""
    return str(value or "").strip()


def _is_adguard_app(app: App | None) -> bool:
    """判断应用是否为 AdGuard Home，避免让它把 DNS 指向自己。"""
    if app is None:
        return False
    if str(app.image or "").lower().startswith("adguard/adguardhome"):
        return True
    return str(app.name or "").lower() in {"adguard", "adguardhome", "adguard-home"}


def _eligible_services(services: dict[str, Any]) -> tuple[list[str], list[str]]:
    """拆分可注入 DNS 的服务与被 Docker 网络模式限制的服务。"""
    eligible: list[str] = []
    skipped: list[str] = []
    for name, config in services.items():
        if not isinstance(config, dict):
            continue
        network_mode = str(config.get("network_mode") or "").strip().lower()
        if network_mode:
            # 任何显式 network_mode 都与 networks 互斥，无法安全注入加速网络。
            skipped.append(name)
            continue
        eligible.append(name)
    return eligible, skipped


def _as_dns_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _attach_accel_network(config: dict[str, Any]) -> None:
    """把服务接入加速网络，保留原有网络声明。"""
    networks = config.get("networks")
    if isinstance(networks, dict):
        networks.setdefault(ADGUARD_NETWORK, {})
    elif isinstance(networks, list):
        if ADGUARD_NETWORK not in networks:
            networks.append(ADGUARD_NETWORK)
    else:
        # 未显式声明 networks 时服务默认在 default 网络，保留它以免
        # 破坏应用内服务名解析，再额外接入加速网络。
        config["networks"] = {"default": {}, ADGUARD_NETWORK: {}}


def _detach_accel_network(config: dict[str, Any], drop_default: bool = False) -> None:
    networks = config.get("networks")
    if isinstance(networks, dict):
        networks.pop(ADGUARD_NETWORK, None)
        if not networks or (drop_default and set(networks) == {"default"}):
            config.pop("networks", None)
    elif isinstance(networks, list):
        remaining = [item for item in networks if item != ADGUARD_NETWORK]
        if remaining:
            config["networks"] = remaining
        else:
            config.pop("networks", None)


def apply_acceleration(
    content: str,
    dns_ip: str,
    fallback_dns: tuple[str, ...] | list[str] = (),
) -> tuple[str, dict[str, Any]]:
    """把 DNS 与加速网络直接写入应用 Compose 内容。

    返回 (新内容, 结果信息)。被改动前的值记录在 ``x-onetap-accel`` 中，
    重复开启时沿用首次记录，避免把已注入的 DNS 当成用户原值。
    """
    try:
        data = yaml.safe_load(content) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Compose 内容不是有效的 YAML：{exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Compose 内容不是有效的 YAML 映射")
    services = data.get("services")
    if not isinstance(services, dict):
        raise ValueError("Compose 文件缺少 services 配置")

    eligible, skipped = _eligible_services(services)
    if not eligible:
        raise ValueError("所有服务都设置了 network_mode，无法注入加速网络")

    state = data.get(ACCEL_STATE_KEY)
    if not isinstance(state, dict):
        state = {}
    recorded = state.get("services")
    if not isinstance(recorded, dict):
        recorded = {}
    state["services"] = recorded
    state["dns_ip"] = dns_ip

    networks = data.get("networks")
    if not isinstance(networks, dict):
        networks = {}
        data["networks"] = networks
    if ADGUARD_NETWORK not in networks:
        networks[ADGUARD_NETWORK] = {"external": True, "name": ADGUARD_NETWORK}
        state["network_created"] = True

    fallback = [str(server).strip() for server in fallback_dns if str(server).strip()]
    existing_dns: list[str] = []
    for name in eligible:
        config = services[name]
        if name not in recorded:
            recorded[name] = {
                "dns": config.get("dns"),
                "networks": "networks" in config,
            }
            if config.get("dns"):
                existing_dns.append(name)
        entry = recorded[name]
        original_dns = entry.get("dns") if isinstance(entry, dict) else None
        dns_servers = [dns_ip]
        for server in [*_as_dns_list(original_dns), *fallback]:
            if server and server not in dns_servers:
                dns_servers.append(server)
        config["dns"] = dns_servers
        _attach_accel_network(config)

    data[ACCEL_STATE_KEY] = state
    return (
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        {
            "services": eligible,
            "skipped": skipped,
            "existing_dns": existing_dns,
        },
    )


def remove_acceleration(content: str) -> str:
    """移除 Compose 内容里的 DNS 注入，按 ``x-onetap-accel`` 记录还原。"""
    try:
        data = yaml.safe_load(content) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Compose 内容不是有效的 YAML：{exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Compose 内容不是有效的 YAML 映射")
    services = data.get("services")
    if not isinstance(services, dict):
        services = {}

    state = data.pop(ACCEL_STATE_KEY, None)
    recorded = state.get("services") if isinstance(state, dict) else None
    restored: set[str] = set()
    if isinstance(recorded, dict):
        for name, entry in recorded.items():
            config = services.get(name)
            if not isinstance(config, dict) or not isinstance(entry, dict):
                continue
            original_dns = entry.get("dns")
            if original_dns is None:
                config.pop("dns", None)
            else:
                config["dns"] = original_dns
            _detach_accel_network(config, drop_default=not entry.get("networks"))
            restored.add(name)

    # 记录之外、但在开启加速后新增的服务，也一并摘掉加速网络。
    for name, config in services.items():
        if isinstance(config, dict) and name not in restored:
            _detach_accel_network(config)

    networks = data.get("networks")
    if isinstance(networks, dict):
        if not isinstance(state, dict) or state.get("network_created"):
            networks.pop(ADGUARD_NETWORK, None)
        if not networks:
            data.pop("networks", None)

    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def _config_file_paths(project: DockerComposeProject) -> list[Path]:
    """解析项目的 Compose 文件路径列表。"""
    paths: list[Path] = []
    if project.config_files:
        try:
            raw = json.loads(project.config_files)
            if isinstance(raw, list):
                paths = [Path(str(item)) for item in raw if str(item).strip()]
        except (TypeError, ValueError, json.JSONDecodeError):
            paths = []
    if not paths:
        paths = [
            Path(settings.COMPOSE_WORKSPACE_DIR)
            / project.project_name
            / "docker-compose.yml"
        ]
    return paths


def _merge_compose_services(project: DockerComposeProject) -> dict[str, Any]:
    """读取并合并项目所有 Compose 文件中的服务定义。"""
    services: dict[str, Any] = {}
    for path in _config_file_paths(project):
        if not path.exists():
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            logger.warning("读取 Compose 文件失败 %s：%s", path, exc)
            continue
        raw_services = data.get("services") if isinstance(data, dict) else None
        if not isinstance(raw_services, dict):
            continue
        for name, config in raw_services.items():
            key = _service_name(name)
            if not key:
                continue
            if isinstance(config, dict) and isinstance(services.get(key), dict):
                merged = dict(services[key])
                merged.update(config)
                services[key] = merged
            else:
                services[key] = config
    return services


class HostsAccelerationService:
    """应用级 DNS 加速与 AdGuard 自动部署。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    # ---------- AdGuard 自动部署 ----------

    @staticmethod
    def _pick_subnet(client) -> str:
        """挑选一个不与现有 Docker 网络冲突的 /24 子网。"""
        used: list[ipaddress.IPv4Network] = []
        for network in client.networks.list():
            configs = (network.attrs.get("IPAM") or {}).get("Config") or []
            for item in configs:
                subnet = item.get("Subnet") if isinstance(item, dict) else None
                if not subnet:
                    continue
                try:
                    used.append(ipaddress.ip_network(subnet, strict=False))
                except ValueError:
                    continue
        for third in range(254, 15, -1):
            candidate = ipaddress.ip_network(f"172.31.{third}.0/24")
            if not any(candidate.overlaps(network) for network in used):
                return str(candidate)
        raise APIException("没有可用的 Docker 子网用于加速网络", 500)

    @staticmethod
    def _pick_network_ip(network) -> str:
        """在加速网络中挑选一个空闲容器 IP。"""
        configs = (network.attrs.get("IPAM") or {}).get("Config") or []
        subnet_raw = configs[0].get("Subnet") if configs else None
        if not subnet_raw:
            raise APIException("加速网络缺少 IPAM 配置", 500)
        subnet = ipaddress.ip_network(subnet_raw, strict=False)
        gateway = configs[0].get("Gateway") if configs else None
        used: set[str] = set()
        for item in (network.attrs.get("Containers") or {}).values():
            address = str((item or {}).get("IPv4Address") or "").split("/")[0]
            if address:
                used.add(address)
        for host in subnet.hosts():
            address = str(host)
            if address == gateway or address in used:
                continue
            return address
        raise APIException("加速网络没有空闲 IP", 500)

    def _ensure_network_sync(self, preferred_ip: str | None = None) -> str:
        """确保加速网络存在，返回 AdGuard 当前可用的 DNS IP。

        ``preferred_ip`` 为上一次写入配置的容器 IP；重建容器时优先复用它，
        避免已开启加速的应用仍指向旧地址。
        """
        client = docker_manager._client
        if client is None:
            raise APIException("Docker 不可用，请确认 OneTap 已挂载 Docker 套接字", 503)

        network = None
        for item in client.networks.list():
            if item.name == ADGUARD_NETWORK:
                network = item
                break
        if network is None:
            subnet = self._pick_subnet(client)
            try:
                client.networks.create(
                    ADGUARD_NETWORK,
                    driver="bridge",
                    ipam={"Config": [{"Subnet": subnet}]},
                    labels={"com.onetap.acceleration": "true"},
                )
            except docker_sdk.errors.APIError as exc:
                # 并发创建或同名网络已存在时复用现有网络。
                logger.info("创建加速网络失败，尝试复用已有网络：%s", exc)
            network = client.networks.get(ADGUARD_NETWORK)

        # 已有 AdGuard 容器时沿用它的 IP，避免重配后应用还指向旧地址。
        for item in (network.attrs.get("Containers") or {}).values():
            if (item or {}).get("Name") == ADGUARD_CONTAINER:
                address = str((item or {}).get("IPv4Address") or "").split("/")[0]
                if address:
                    return address

        if preferred_ip:
            configs = (network.attrs.get("IPAM") or {}).get("Config") or []
            subnet_raw = configs[0].get("Subnet") if configs else None
            gateway = configs[0].get("Gateway") if configs else None
            used = {
                str((item or {}).get("IPv4Address") or "").split("/")[0]
                for item in (network.attrs.get("Containers") or {}).values()
            }
            try:
                candidate = ipaddress.ip_address(preferred_ip)
                subnet = (
                    ipaddress.ip_network(subnet_raw, strict=False)
                    if subnet_raw
                    else None
                )
            except ValueError:
                candidate = None
                subnet = None
            if (
                candidate is not None
                and subnet is not None
                and candidate in subnet
                and str(candidate) != gateway
                and str(candidate) not in used
            ):
                return str(candidate)

        return self._pick_network_ip(network)

    async def ensure_network(self, preferred_ip: str | None = None) -> str:
        """确保加速网络存在并返回可用的 DNS IP。"""
        return await docker_manager.run_sync(
            self._ensure_network_sync, preferred_ip
        )

    def _ensure_backend_network_sync(self) -> bool:
        """把 OneTap 自身容器接入加速网络。

        接入后后端可以直接用 AdGuard 的容器 IP 访问管理接口，
        宿主机 IP 变化（DHCP、换网段）不会影响 DNS 规则同步。
        """
        client = docker_manager._client
        if client is None:
            return False
        try:
            container = client.containers.get(socket.gethostname())
        except Exception:
            # 直接运行在宿主机时无需接入，宿主机本身可访问容器 IP。
            return False
        try:
            network = client.networks.get(ADGUARD_NETWORK)
        except Exception:
            return False
        if container.id in (network.attrs.get("Containers") or {}):
            return True
        try:
            network.connect(container)
            return True
        except Exception as exc:
            logger.warning("OneTap 接入加速网络失败：%s", exc)
            return False

    async def ensure_backend_network(self) -> bool:
        """确保 OneTap 后端能通过容器网络访问 AdGuard。"""
        return await docker_manager.run_sync(self._ensure_backend_network_sync)

    def _current_dns_ip_sync(self) -> str | None:
        """读取 AdGuard 容器当前在加速网络中的 IP。"""
        client = docker_manager._client
        if client is None:
            return None
        try:
            container = client.containers.get(ADGUARD_CONTAINER)
        except Exception:
            return None
        networks = (container.attrs.get("NetworkSettings") or {}).get(
            "Networks"
        ) or {}
        address = (networks.get(ADGUARD_NETWORK) or {}).get("IPAddress")
        return str(address) if address else None

    async def refresh_managed_url(
        self, db: AsyncSession, config: SystemConfig
    ) -> None:
        """刷新托管 AdGuard 的管理地址，并在需要时接入加速网络。

        使用容器 IP 而不是宿主机地址，避免宿主机 IP 变化后同步失败；
        配置保存后各接口即时生效，不需要重启容器。
        """
        if not config.hosts_optimizer_adguard_managed:
            return
        if not await self.ensure_backend_network():
            return
        dns_ip = await docker_manager.run_sync(self._current_dns_ip_sync)
        if not dns_ip:
            return
        url = f"http://{dns_ip}:80"
        if config.hosts_optimizer_adguard_url != url:
            config.hosts_optimizer_adguard_url = url
            await db.flush()
        if config.hosts_optimizer_adguard_dns_ip != dns_ip:
            config.hosts_optimizer_adguard_dns_ip = dns_ip
            await db.flush()

    def _choose_ports_sync(self, config: SystemConfig) -> tuple[int, int]:
        """为 AdGuard 初始化端口与管理端口挑选宿主机端口。"""
        used = get_used_ports()
        # AdGuard 自己占用的端口不算冲突，否则每次启动都会换新端口。
        container = docker_manager.get_container(ADGUARD_CONTAINER)
        for binding in (container or {}).get("port_bindings") or []:
            try:
                used.discard(int(binding.get("host_port")))
            except (TypeError, ValueError):
                continue
        setup = config.hosts_optimizer_adguard_setup_port
        web = config.hosts_optimizer_adguard_web_port
        if (
            setup
            and web
            and setup != web
            and setup not in used
            and web not in used
        ):
            return int(setup), int(web)
        allocated = allocate_free_ports(used, 2, 3000)
        return allocated[0], allocated[1]

    async def _choose_ports(self, config: SystemConfig) -> tuple[int, int]:
        """异步包装宿主机端口选择。"""
        return await asyncio.to_thread(self._choose_ports_sync, config)

    @staticmethod
    def _adguard_compose_content(
        host_base: str, setup_port: int, web_port: int, dns_ip: str
    ) -> str:
        """生成专用 AdGuard Home 的 Compose 配置。"""
        data = {
            "services": {
                "adguardhome": {
                    "image": ADGUARD_IMAGE,
                    "container_name": ADGUARD_CONTAINER,
                    "restart": "unless-stopped",
                    "ports": [f"{setup_port}:3000", f"{web_port}:80"],
                    "volumes": [
                        f"{host_base}/adguard/work:/opt/adguardhome/work",
                        f"{host_base}/adguard/conf:/opt/adguardhome/conf",
                    ],
                    "networks": {
                        ADGUARD_NETWORK: {"ipv4_address": dns_ip},
                    },
                }
            },
            "networks": {
                ADGUARD_NETWORK: {
                    "external": True,
                    "name": ADGUARD_NETWORK,
                }
            },
        }
        return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)

    async def _wait_for_url(
        self,
        url: str,
        *,
        auth: tuple[str, str] | None = None,
        timeout: float,
    ) -> httpx.Response | None:
        """轮询 URL 直到返回 2xx/3xx，超时返回 None。"""
        deadline = time.monotonic() + timeout
        async with httpx.AsyncClient(
            trust_env=False, timeout=5.0, follow_redirects=True
        ) as client:
            while time.monotonic() < deadline:
                try:
                    resp = await client.get(url, auth=auth)
                    if resp.status_code < 400:
                        return resp
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(ADGUARD_POLL_INTERVAL)
        return None

    async def _apply_upstream_dns(
        self, web_base: str, username: str, password: str
    ) -> None:
        """把 AdGuard 上游切换为当前网络可达的加密 DNS。

        AdGuard 默认的 Quad9 DoH 在国内网络不可达，查询会等到超时后落到
        明文备用 DNS，可能拿到被污染的结果。初始化时统一切到国内可达的
        DoH；失败只记录日志，不影响部署。
        """
        async with httpx.AsyncClient(
            base_url=web_base,
            auth=(username, password),
            trust_env=False,
            timeout=10.0,
        ) as client:
            try:
                resp = await client.get("/control/dns_info")
                resp.raise_for_status()
                payload = resp.json()
                if (
                    payload.get("upstream_dns") == list(ADGUARD_UPSTREAM_DNS)
                    and payload.get("bootstrap_dns") == list(ADGUARD_BOOTSTRAP_DNS)
                ):
                    return
                payload["upstream_dns"] = list(ADGUARD_UPSTREAM_DNS)
                payload["bootstrap_dns"] = list(ADGUARD_BOOTSTRAP_DNS)
                payload["fallback_dns"] = []
                updated = await client.post("/control/dns_config", json=payload)
                updated.raise_for_status()
                await client.post("/control/cache_clear")
            except Exception as exc:
                logger.warning("配置 AdGuard 上游 DNS 失败：%s", exc)

    async def _configure_adguard(
        self,
        host: str,
        setup_port: int,
        web_port: int,
        username: str,
        password: str,
    ) -> dict[str, Any]:
        """完成 AdGuard Home 首次初始化并等待管理接口可用。"""
        web_base = f"http://{host}:{web_port}"
        status = await self._wait_for_url(
            f"{web_base}/control/status",
            auth=(username, password),
            timeout=5.0,
        )
        if status is not None:
            await self._apply_upstream_dns(web_base, username, password)
            return status.json() if status.content else {}

        # 管理端口能响应但拒绝了当前账号，说明实例已初始化，不应再等
        # 初始化界面（它已经关闭），直接提示密码不匹配。
        try:
            async with httpx.AsyncClient(
                trust_env=False, timeout=5.0, follow_redirects=True
            ) as client:
                resp = await client.get(
                    f"{web_base}/control/status", auth=(username, password)
                )
                if resp.status_code in (401, 403):
                    raise APIException(
                        "AdGuard Home 已初始化，但账号或密码不匹配。"
                        "请修改系统设置中的 AdGuard 密码，或删除其数据卷后重新部署",
                        400,
                    )
        except httpx.HTTPError:
            pass

        setup_base = f"http://{host}:{setup_port}"
        check = await self._wait_for_url(
            f"{setup_base}/control/install/get_addresses",
            timeout=ADGUARD_SETUP_TIMEOUT,
        )
        if check is None:
            raise APIException(
                "AdGuard Home 初始化界面启动超时，请检查容器日志", 504
            )

        payload = {
            "web": {"ip": "0.0.0.0", "port": 80},
            "dns": {"ip": "0.0.0.0", "port": 53},
            "username": username,
            "password": password,
        }
        async with httpx.AsyncClient(
            trust_env=False, timeout=10.0, follow_redirects=True
        ) as client:
            try:
                resp = await client.post(
                    f"{setup_base}/control/install/configure", json=payload
                )
                if resp.status_code >= 400 and resp.status_code not in (400, 403):
                    raise APIException(
                        f"AdGuard Home 初始化失败：HTTP {resp.status_code}",
                        502,
                    )
            except httpx.HTTPError as exc:
                raise APIException(f"AdGuard Home 初始化失败：{exc}", 502) from exc

        result = await self._wait_for_url(
            f"{web_base}/control/status",
            auth=(username, password),
            timeout=ADGUARD_WEB_TIMEOUT,
        )
        if result is None:
            raise APIException(
                "AdGuard Home 已初始化，但管理接口仍不可用，请检查账号密码",
                502,
            )
        await self._apply_upstream_dns(web_base, username, password)
        return result.json() if result.content else {}

    async def start_deploy_managed(
        self,
        db: AsyncSession,
        config: SystemConfig,
        app_config: dict[str, Any] | None = None,
    ) -> str:
        """创建后台部署任务并立即返回 task_id。

        前端拿到 task_id 后接入统一的部署进度弹窗；实际部署与初始化在
        后台协程中执行，失败原因通过任务状态回传。
        """
        from app.core.background import create_background_task
        from app.services.orchestration.deploy_task_service import (
            deploy_task_manager,
        )

        # 已有一个 AdGuard 部署任务在进行时直接复用，避免重复启动
        for task_id in deploy_task_manager.list_active_task_ids():
            task = deploy_task_manager.get_task(task_id) or {}
            if (task.get("meta") or {}).get("operation") == "adguard_deploy":
                return task_id

        task_id = deploy_task_manager.create_task(
            "adguard_deploy",
            meta={
                "app_name": "AdGuard Home",
                "instance_name": ADGUARD_PROJECT,
                "description": "部署 OneTap 网络优化专用 DNS 服务",
                "operation": "adguard_deploy",
            },
            # 实际部署会再创建应用市场部署任务占用并发槽位
            enforce_limit=False,
        )
        create_background_task(
            self._run_deploy_managed_task(
                task_id, config.id, app_config=app_config
            )
        )
        return task_id

    async def _run_deploy_managed_task(
        self,
        task_id: str,
        config_id: int,
        app_config: dict[str, Any] | None = None,
    ) -> None:
        """后台执行 AdGuard 部署，并把进度写入部署任务。"""
        from app.database import async_session
        from app.services.orchestration.deploy_task_service import (
            deploy_task_manager,
        )

        def progress(
            stage: str, percentage: int, message: str, detail: str | None = None
        ) -> None:
            deploy_task_manager.update_progress(
                task_id, stage, percentage, message, detail
            )

        async with async_session() as db:
            try:
                config = await db.get(SystemConfig, config_id)
                if config is None:
                    raise APIException("系统配置不存在，请刷新后重试", 400)
                result = await self.deploy_managed(
                    db, config, app_config=app_config, progress=progress
                )
                await db.commit()
                deploy_task_manager.complete_task(
                    task_id,
                    message=result.get("message")
                    or "AdGuard Home 已部署并完成初始化",
                )
            except Exception as exc:
                await db.rollback()
                logger.exception("AdGuard Home 部署任务失败: %s", task_id)
                deploy_task_manager.fail_task(task_id, str(exc))

    async def deploy_managed(
        self,
        db: AsyncSession,
        config: SystemConfig,
        app_config: dict[str, Any] | None = None,
        progress: Callable[[str, int, str, str | None], None] | None = None,
    ) -> dict[str, Any]:
        """部署并初始化 OneTap 专用的 AdGuard Home。

        ``app_config`` 来自应用市场的安装弹窗；传入时按其端口、存储和
        环境变量部署，否则由后端自动选择端口并填充默认值。
        ``progress`` 用于把部署进度写入部署任务，供前端进度弹窗展示。
        """
        async with self._lock:
            return await self._deploy_managed_locked(
                db, config, app_config=app_config, progress=progress
            )

    async def _deploy_managed_locked(
        self,
        db: AsyncSession,
        config: SystemConfig,
        app_config: dict[str, Any] | None = None,
        progress: Callable[[str, int, str, str | None], None] | None = None,
    ) -> dict[str, Any]:
        def report(
            stage: str, percentage: int, message: str, detail: str | None = None
        ) -> None:
            if progress is not None:
                progress(stage, percentage, message, detail)

        report("preparing", 3, "准备部署 AdGuard Home")
        pair = await storage_directory_service.get_app_data_pair(db)
        if not pair:
            raise APIException(
                "部署 AdGuard Home 需要先配置应用数据目录（系统设置 → 存储配置）",
                400,
            )
        host_base = pair[0].rstrip("/")

        # 重建时尽量沿用上一次的 DNS IP，避免已开启加速的应用指向旧地址。
        report("preparing", 5, "准备加速网络")
        dns_ip = await self.ensure_network(config.hosts_optimizer_adguard_dns_ip)
        if app_config is not None:
            setup_port = self._port_for_container(app_config, 3000)
            web_port = self._port_for_container(app_config, 80)
            if setup_port is None or web_port is None:
                raise APIException(
                    "安装配置缺少初始化端口（容器 3000）或管理端口（容器 80）", 400
                )
            if setup_port == web_port:
                raise APIException("初始化端口和管理端口不能相同", 400)
        else:
            setup_port, web_port = await self._choose_ports(config)
        username = config.hosts_optimizer_adguard_username or "admin"
        password = config.hosts_optimizer_adguard_password or secrets.token_urlsafe(12)

        app = await self._load_adguard_app(db)
        reserve_ports([setup_port, web_port])
        try:
            if app is not None:
                # 优先走应用市场的部署流程：端口检测、数据目录挂载、实例记录
                # 与部署任务都由 AppService 统一处理。
                await self._deploy_via_app_store(
                    db,
                    app,
                    host_base,
                    setup_port,
                    web_port,
                    dns_ip,
                    app_config=app_config,
                    progress=report,
                    username=username,
                    password=password,
                )
            else:
                await self._deploy_builtin(
                    db,
                    config,
                    host_base,
                    setup_port,
                    web_port,
                    dns_ip,
                    progress=report,
                    username=username,
                    password=password,
                )
        finally:
            release_ports([setup_port, web_port])

        config.hosts_optimizer_adguard_managed = True
        config.hosts_optimizer_adguard_dns_ip = dns_ip
        config.hosts_optimizer_adguard_setup_port = setup_port
        config.hosts_optimizer_adguard_web_port = web_port
        config.hosts_optimizer_adguard_username = username
        config.hosts_optimizer_adguard_password = password
        await db.flush()

        client = docker_manager._client
        host = (
            await asyncio.to_thread(detect_host_address, client)
            if client is not None
            else "127.0.0.1"
        )
        report("initializing", 85, "初始化 AdGuard Home")
        status_payload = await self._configure_adguard(
            host=host,
            setup_port=setup_port,
            web_port=web_port,
            username=username,
            password=password,
        )
        config.hosts_optimizer_adguard_url = f"http://{host}:{web_port}"
        await db.flush()
        # 优先改用容器 IP 管理 AdGuard，宿主机 IP 变化时无需重新部署。
        await self.refresh_managed_url(db, config)

        return {
            "deployed": True,
            "url": config.hosts_optimizer_adguard_url,
            "dns_ip": dns_ip,
            "version": status_payload.get("version") if status_payload else None,
            "message": "AdGuard Home 已部署并完成初始化",
        }

    async def _load_adguard_app(self, db: AsyncSession) -> App | None:
        """从应用市场目录中查找 AdGuard Home 应用定义。"""
        result = await db.execute(
            select(App).where(App.name.in_(ADGUARD_APP_NAMES))
        )
        apps = {app.name: app for app in result.scalars().all()}
        for name in ADGUARD_APP_NAMES:
            app = apps.get(name)
            if app is not None and app.yaml_template:
                return app
        return None

    @staticmethod
    def _schema_defaults(schema: dict[str, Any]) -> dict[str, Any]:
        """读取应用 config_schema 中声明的默认值。"""
        return {
            key: prop["default"]
            for key, prop in (schema.get("properties") or {}).items()
            if isinstance(prop, dict) and "default" in prop
        }

    @classmethod
    def _adguard_app_config(
        cls, app: App, setup_port: int, web_port: int
    ) -> dict[str, Any]:
        """基于应用市场模板生成 AdGuard 的部署配置。

        只暴露初始化端口与管理端口，不映射宿主机 53，避免和已有
        DNS 服务或另一个 AdGuard 冲突。
        """
        config = cls._schema_defaults(app.config_schema or {})
        # 目录里的 image 不带 tag，这里固定到与内置部署一致的稳定版本。
        config["image"] = ADGUARD_IMAGE
        config["ports"] = [
            {"protocol": "tcp", "local_port": setup_port, "container_port": 3000},
            {"protocol": "tcp", "local_port": web_port, "container_port": 80},
        ]
        return config

    @staticmethod
    def _app_service_name(app: App) -> str:
        """返回应用模板中的主容器服务名。"""
        containers = (app.config_schema or {}).get("containers") or []
        if containers and isinstance(containers[0], dict):
            return str(containers[0].get("name") or app.name)
        return app.name

    async def _deploy_via_app_store(
        self,
        db: AsyncSession,
        app: App,
        host_base: str,
        setup_port: int,
        web_port: int,
        dns_ip: str,
        app_config: dict[str, Any] | None = None,
        progress: Callable[[str, int, str, str | None], None] | None = None,
        username: str = "admin",
        password: str = "",
    ) -> None:
        """通过应用市场部署 AdGuard，并额外接入加速网络。"""
        from app.services.app_store import app_service

        if progress is not None:
            progress("preparing", 8, "清理旧部署并迁移数据")
        await self._remove_project(db)
        await asyncio.to_thread(self._migrate_legacy_data, host_base)
        # 容器已停止，此时同步密码哈希不会被 AdGuard 退出时覆盖。
        if password:
            await asyncio.to_thread(
                self._sync_data_password,
                self._app_store_conf_file(host_base, app, app_config),
                username,
                password,
            )

        service_name = self._app_service_name(app)
        config = (
            dict(app_config)
            if app_config is not None
            else self._adguard_app_config(app, setup_port, web_port)
        )
        # 安装弹窗没有改镜像时，固定到稳定版本，避免模板里的 latest。
        config.setdefault("image", ADGUARD_IMAGE)
        _, task_id = await app_service.deploy(
            db,
            app_name=app.name,
            instance_name=ADGUARD_PROJECT,
            config=config,
            compose_overrides={
                "services": {
                    service_name: {
                        "container_name": ADGUARD_CONTAINER,
                        "networks": {
                            ADGUARD_NETWORK: {"ipv4_address": dns_ip}
                        },
                    }
                },
                "networks": {
                    ADGUARD_NETWORK: {
                        "external": True,
                        "name": ADGUARD_NETWORK,
                    }
                },
            },
        )
        await self._wait_deploy_task(task_id, progress=progress)

    @staticmethod
    def _port_for_container(
        config: dict[str, Any], container_port: int
    ) -> int | None:
        """从安装配置的 ports 中取出指定容器端口对应的宿主机端口。"""
        for entry in config.get("ports") or []:
            if not isinstance(entry, dict):
                continue
            try:
                if int(entry.get("container_port")) != container_port:
                    continue
                return int(entry["local_port"])
            except (KeyError, TypeError, ValueError):
                continue
        return None

    async def _wait_deploy_task(
        self,
        task_id: str,
        timeout: float = ADGUARD_DEPLOY_TIMEOUT,
        progress: Callable[[str, int, str, str | None], None] | None = None,
    ) -> None:
        """等待应用市场部署任务结束，并把子任务进度折算到外层任务。

        子任务的 0-100% 映射到外层 10-80%，剩下的百分比留给
        AdGuard 初始化配置阶段。
        """
        from app.services.orchestration.deploy_task_service import (
            deploy_task_manager,
        )

        deadline = time.monotonic() + timeout
        last_snapshot: tuple[str, int, str, str | None] | None = None
        while time.monotonic() < deadline:
            task = deploy_task_manager.get_task(task_id)
            if task is None:
                raise APIException("AdGuard Home 部署任务不存在", 500)
            status = task.get("status")
            if status == "completed":
                return
            if status in {"failed", "cancelled"}:
                error = task.get("error") or "部署任务已终止"
                raise APIException(f"AdGuard Home 部署失败：{error}", 502)
            if progress is not None:
                current = task.get("progress") or {}
                percentage = int(
                    10 + 70 * (current.get("percentage") or 0) / 100
                )
                stage = str(current.get("stage") or "pulling_images")
                message = str(current.get("message") or "部署 AdGuard Home")
                detail = current.get("detail")
                snapshot = (stage, percentage, message, detail)
                if snapshot != last_snapshot:
                    progress(stage, percentage, message, detail)
                    last_snapshot = snapshot
            await asyncio.sleep(0.5)
        raise APIException("AdGuard Home 部署超时，请查看部署任务日志", 504)

    @classmethod
    def _app_store_conf_file(
        cls,
        host_base: str,
        app: App,
        app_config: dict[str, Any] | None,
    ) -> Path:
        """返回应用市场部署实际挂载的 AdGuardHome.yaml 路径。

        优先使用安装弹窗里声明的 conf 挂载路径；没有自定义时按应用市场
        默认目录 ``<应用数据目录>/<项目名>/<服务名>/conf`` 计算。
        """
        volumes = (app_config or {}).get("volumes")
        if isinstance(volumes, list):
            for volume in volumes:
                if not isinstance(volume, dict):
                    continue
                container_path = str(
                    volume.get("container_path") or ""
                ).rstrip("/")
                if container_path != "/opt/adguardhome/conf":
                    continue
                host_path = str(volume.get("host_path") or "").strip()
                if not host_path:
                    continue
                path = Path(host_path)
                if not path.is_absolute():
                    path = Path(host_base) / path
                return path / "AdGuardHome.yaml"
        return (
            Path(host_base)
            / ADGUARD_PROJECT
            / cls._app_service_name(app)
            / "conf"
            / "AdGuardHome.yaml"
        )

    @classmethod
    def _sync_data_password(
        cls, conf_file: str | Path, username: str, password: str
    ) -> bool:
        """把当前账号密码写回保留的 AdGuard 数据目录。

        重新部署会复用已有的 ``work/conf`` 数据。如果旧数据里的密码哈希
        与系统配置不一致，管理接口会一直返回 401/403，部署最终失败。
        部署前先同步凭据，既保留 DNS 重写、过滤规则等数据，又保证
        OneTap 能用系统配置里的账号密码接管 AdGuard。
        """
        hashed = hash_password(password)

        def updater(raw: str) -> str | None:
            try:
                content = yaml.safe_load(raw) or {}
            except yaml.YAMLError:
                logger.warning("解析 AdGuard 配置失败", exc_info=True)
                return None
            users = content.get("users")
            if not isinstance(users, list) or not users:
                # 还没有初始化用户，交给正常初始化流程处理。
                return None
            target = next(
                (
                    user
                    for user in users
                    if isinstance(user, dict) and user.get("name") == username
                ),
                None,
            )
            if target is None:
                target = users[0] if isinstance(users[0], dict) else {}
                users[0] = target
            target["name"] = username
            target["password"] = hashed
            content["users"] = users
            return yaml.safe_dump(content, sort_keys=False, allow_unicode=True)

        # update_text 兼容直接读写和容器内通过 Docker 辅助容器读写；
        # 后端容器没有挂载宿主机目录时也能改到文件。
        return host_files.update_text(conf_file, updater, mode=0o600)

    def _managed_conf_file_sync(self) -> Path | None:
        """从运行中的容器挂载反查 AdGuardHome.yaml 的真实路径。

        应用市场安装和内置部署的宿主机目录不同，直接读容器挂载最可靠。
        """
        client = docker_manager._client
        if client is None:
            return None
        try:
            container = client.containers.get(ADGUARD_CONTAINER)
        except Exception:
            return None
        for mount in container.attrs.get("Mounts") or []:
            destination = str(mount.get("Destination") or "").rstrip("/")
            if destination != "/opt/adguardhome/conf":
                continue
            source = str(mount.get("Source") or "").strip()
            if source:
                return Path(source) / "AdGuardHome.yaml"
        return None

    async def apply_managed_credentials(self, config: SystemConfig) -> None:
        """把系统设置里的账号密码写入运行中的托管 AdGuard。

        保存配置时调用。容器不存在时直接返回，下次部署会按系统设置初始化；
        配置里还没有用户（尚未完成初始化）时也无需重启。
        """
        if not config.hosts_optimizer_adguard_managed:
            return
        password = config.hosts_optimizer_adguard_password or ""
        if not password:
            return
        username = config.hosts_optimizer_adguard_username or "admin"
        container = await docker_manager.run_sync(
            docker_manager.get_container, ADGUARD_CONTAINER
        )
        if container is None:
            return
        conf_file = await docker_manager.run_sync(self._managed_conf_file_sync)
        if conf_file is None:
            raise APIException(
                "未能定位 AdGuard 配置文件，账号密码未同步，请重新部署", 500
            )
        updated = await asyncio.to_thread(
            self._sync_data_password, conf_file, username, password
        )
        if not updated or container.get("status") != "running":
            return
        # 账号密码只在启动时读取，改写配置后需要重启后台容器。
        try:
            await docker_manager.run_sync(
                docker_manager.container_action, ADGUARD_CONTAINER, "restart"
            )
        except Exception as exc:
            raise APIException(
                f"账号密码已写入，但重启 AdGuard Home 失败：{exc}", 502
            ) from exc

    @staticmethod
    def _migrate_legacy_data(host_base: str) -> None:
        """把旧版自建 Compose 的 AdGuard 数据迁移到应用市场目录。

        迁移完成后删除旧目录，避免用户主动删除 AdGuard、数据目录被备份
        移走后，重新部署又从旧目录复制回已经过期的密码和配置。
        """
        legacy = Path(host_base) / LEGACY_DATA_DIRNAME
        if not legacy.is_dir():
            return
        target = Path(host_base) / ADGUARD_PROJECT / ADGUARD_SERVICE
        failed = False
        for name in ("work", "conf"):
            src = legacy / name
            dst = target / name
            if not src.is_dir() or dst.exists():
                continue
            try:
                shutil.copytree(src, dst)
            except OSError as exc:
                failed = True
                logger.warning("迁移旧 AdGuard 数据失败 %s → %s：%s", src, dst, exc)
        if failed:
            return
        try:
            shutil.rmtree(legacy)
        except OSError as exc:
            logger.warning("清理旧 AdGuard 数据目录失败 %s：%s", legacy, exc)

    async def _deploy_builtin(
        self,
        db: AsyncSession,
        config: SystemConfig,
        host_base: str,
        setup_port: int,
        web_port: int,
        dns_ip: str,
        progress: Callable[[str, int, str, str | None], None] | None = None,
        username: str = "admin",
        password: str = "",
    ) -> None:
        """应用市场没有 AdGuard 时的兜底部署（内置 Compose）。"""
        from app.services.compose import compose_manager

        logger.info("应用市场未找到 AdGuard Home，使用内置 Compose 部署")

        project = (
            await db.execute(
                select(DockerComposeProject).where(
                    DockerComposeProject.project_name == ADGUARD_PROJECT
                )
            )
        ).scalar_one_or_none()
        container = await docker_manager.run_sync(
            docker_manager.get_container, ADGUARD_CONTAINER
        )
        same_ports = (
            config.hosts_optimizer_adguard_setup_port == setup_port
            and config.hosts_optimizer_adguard_web_port == web_port
        )
        conf_file = (
            Path(host_base) / LEGACY_DATA_DIRNAME / "conf" / "AdGuardHome.yaml"
        )
        if project is not None and container is not None and same_ports:
            updated = (
                await asyncio.to_thread(
                    self._sync_data_password,
                    conf_file,
                    username,
                    password,
                )
                if password
                else False
            )
            if progress is not None:
                progress("starting_services", 30, "启动 AdGuard Home")
            action = (
                "restart"
                if updated and container.get("status") == "running"
                else "up"
            )
            try:
                await compose_manager.action_async(db, project, action)
            except Exception as exc:
                raise APIException(f"启动 AdGuard Home 失败：{exc}", 502) from exc
            return

        if progress is not None:
            progress("creating_project", 10, "创建 AdGuard Home 项目")
        await self._remove_project(db)
        if password:
            await asyncio.to_thread(
                self._sync_data_password,
                conf_file,
                username,
                password,
            )
        await compose_manager.create_project_async(
            db,
            project_name=ADGUARD_PROJECT,
            content=self._adguard_compose_content(
                host_base=host_base,
                setup_port=setup_port,
                web_port=web_port,
                dns_ip=dns_ip,
            ),
            description="OneTap 应用加速专用 AdGuard Home",
            allocate_ports=False,
            progress_callback=progress,
        )

    async def _remove_project(
        self, db: AsyncSession, backup_data: bool = False
    ) -> None:
        """删除 AdGuard 的 Compose 项目（或残留容器）。

        ``backup_data`` 为 True 时把数据目录移入 ``_backups``，用于用户
        主动删除 AdGuard；内部重建项目时保持 False，继续复用数据。
        """
        from app.services.compose import compose_manager

        project = (
            await db.execute(
                select(DockerComposeProject).where(
                    DockerComposeProject.project_name == ADGUARD_PROJECT
                )
            )
        ).scalar_one_or_none()
        if project is not None:
            await compose_manager.delete_project(
                db, project, backup_data=backup_data
            )
            return

        container = await docker_manager.run_sync(
            docker_manager.get_container, ADGUARD_CONTAINER
        )
        client = docker_manager._client
        if container is not None and client is not None:
            # 处理没有 Compose 项目记录的遗留容器，避免名称冲突。
            try:
                await docker_manager.run_sync(
                    lambda: client.containers.get(ADGUARD_CONTAINER).remove(
                        force=True
                    )
                )
            except Exception as exc:
                logger.warning("清理遗留 AdGuard 容器失败：%s", exc)

        if backup_data:
            from app.services.orchestration.backup import (
                AppDataEntry,
                backup_app_data_dirs,
            )

            await backup_app_data_dirs(
                db,
                [
                    AppDataEntry(
                        instance_name=ADGUARD_PROJECT,
                        app_name=ADGUARD_SERVICE,
                        project_name=ADGUARD_PROJECT,
                    )
                ],
                group_name=ADGUARD_PROJECT,
            )

    async def delete_managed(
        self, db: AsyncSession, config: SystemConfig
    ) -> dict[str, Any]:
        """关闭所有应用加速并删除 OneTap 托管的 AdGuard Home。"""
        async with self._lock:
            return await self._delete_managed_locked(db, config)

    async def _delete_managed_locked(
        self, db: AsyncSession, config: SystemConfig
    ) -> dict[str, Any]:
        result = await db.execute(
            select(AppInstance)
            .options(selectinload(AppInstance.project))
            .where(AppInstance.acceleration_enabled.is_(True))
        )
        accelerated = list(result.scalars().all())

        # 先关闭所有引用该 AdGuard 的应用，避免删除后应用内 DNS 指向空地址。
        for instance in accelerated:
            if instance.project is None:
                # 应用项目已被外部删除时，只清掉标记即可。
                instance.acceleration_enabled = False
                await db.flush()
                continue
            await self._set_app_acceleration_locked(
                db, config, instance.id, False
            )

        # 先把旧版自建目录的数据合并/清理掉，避免备份后重新部署时又从
        # 旧目录复制回已过期的密码和配置。
        pair = await storage_directory_service.get_app_data_pair(db)
        if pair:
            await asyncio.to_thread(self._migrate_legacy_data, pair[0])

        # 删除项目时把数据目录移入 _backups，下次部署从空数据开始。
        await self._remove_project(db, backup_data=True)
        # 部署中途失败时可能留下没有项目的实例记录，一并清理。
        await db.execute(
            delete(AppInstance).where(AppInstance.instance_name == ADGUARD_PROJECT)
        )
        warnings = await self._remove_network_if_empty()

        config.hosts_optimizer_adguard_managed = False
        config.hosts_optimizer_adguard_url = None
        config.hosts_optimizer_adguard_dns_ip = None
        config.hosts_optimizer_adguard_setup_port = None
        config.hosts_optimizer_adguard_web_port = None
        # 账号密码保留：数据目录已移入 _backups，重新部署会全新初始化，
        # 沿用同一凭据可以避免每次删除后都换一次密码。
        config.hosts_optimizer_accel_instances = []
        await db.flush()

        disabled = [instance.instance_name for instance in accelerated]
        message = "AdGuard Home 已删除"
        if disabled:
            message += f"，已关闭 {len(disabled)} 个应用的加速"
        return {
            "removed": True,
            "disabled_apps": disabled,
            "warnings": warnings,
            "message": message,
        }

    async def _remove_network_if_empty(self) -> list[str]:
        """加速网络不再被任何容器使用时一并清理。"""
        client = docker_manager._client
        if client is None:
            return []

        def _remove() -> bool:
            network = client.networks.get(ADGUARD_NETWORK)
            containers = dict(network.attrs.get("Containers") or {})
            # 先断开 OneTap 自身，再判断是否还有加速应用在使用该网络。
            try:
                own_container = client.containers.get(socket.gethostname())
                if own_container.id in containers:
                    network.disconnect(own_container, force=True)
                    containers.pop(own_container.id, None)
            except Exception:
                pass
            if containers:
                return False
            network.remove()
            return True

        try:
            await docker_manager.run_sync(_remove)
        except Exception as exc:
            logger.warning("清理加速网络失败：%s", exc)
            return [f"加速网络 {ADGUARD_NETWORK} 未能删除：{exc}"]
        return []

    def _derive_dns_ip(self, url: str | None) -> str | None:
        """从手动配置的 AdGuard 地址推导应用应使用的 DNS IP。"""
        if not url:
            return None
        host = urlparse(url).hostname
        if not host:
            return None
        if host in {"localhost", "127.0.0.1", "::1"}:
            host = detect_host_address(docker_manager._client)
        try:
            return str(ipaddress.ip_address(host))
        except ValueError:
            try:
                import socket

                return socket.gethostbyname(host)
            except OSError:
                return host

    async def ensure_adguard(
        self, db: AsyncSession, config: SystemConfig
    ) -> dict[str, Any]:
        """确保有一个可用于应用加速的 AdGuard。

        优先复用已部署的 OneTap 专用实例；用户手动配置且连接正常时复用
        手动实例；两者都不可用时自动部署专用实例。
        """
        if config.hosts_optimizer_adguard_managed:
            container = await docker_manager.run_sync(
                docker_manager.get_container, ADGUARD_CONTAINER
            )
            if (
                container is not None
                and container.get("status") == "running"
                and config.hosts_optimizer_adguard_url
            ):
                await self.refresh_managed_url(db, config)
                return {
                    "deployed": True,
                    "url": config.hosts_optimizer_adguard_url,
                    "dns_ip": config.hosts_optimizer_adguard_dns_ip,
                    "version": None,
                    "message": "复用已部署的 AdGuard Home",
                    "warnings": [],
                }
            return {
                **(await self._deploy_managed_locked(db, config)),
                "warnings": [],
            }

        if config.hosts_optimizer_adguard_url and config.hosts_optimizer_adguard_password:
            result = await hosts_optimizer_service.test_connection(config)
            if not result["ok"]:
                raise APIException(
                    "已配置的 AdGuard Home 无法连接。请修正地址/密码，"
                    "或清空地址后使用「部署 AdGuard Home」",
                    400,
                )
            dns_ip = self._derive_dns_ip(config.hosts_optimizer_adguard_url)
            if dns_ip:
                config.hosts_optimizer_adguard_dns_ip = dns_ip
                await db.flush()
            return {
                "deployed": False,
                "url": config.hosts_optimizer_adguard_url,
                "dns_ip": dns_ip,
                "version": result.get("version"),
                "message": "使用手动配置的 AdGuard Home",
                "warnings": [
                    "手动配置的 AdGuard 需要让加速应用能访问其 53 端口；"
                    "如果使用 Docker 网络，请确认应用与 AdGuard 在同一网络。"
                ],
            }

        return {
            **(await self._deploy_managed_locked(db, config)),
            "warnings": [],
        }

    # ---------- 应用加速 ----------

    async def list_apps(
        self, db: AsyncSession, config: SystemConfig
    ) -> list[dict[str, Any]]:
        """列出可配置加速的应用实例。"""
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
        instances = result.scalars().all()
        dns_ip = config.hosts_optimizer_adguard_dns_ip
        items: list[dict[str, Any]] = []
        for instance in instances:
            if _is_adguard_app(instance.app):
                continue
            project = instance.project
            services: list[str] = []
            skipped: list[str] = []
            can_enable = True
            reason: str | None = None
            if project is None:
                can_enable = False
                reason = "未关联 Compose 项目"
            else:
                all_services = _merge_compose_services(project)
                services, skipped = _eligible_services(all_services)
                if not all_services:
                    can_enable = False
                    reason = "Compose 配置不存在或没有服务"
                elif not services:
                    can_enable = False
                    reason = "所有服务都显式设置了 network_mode，无法注入加速网络"
            app = instance.app
            group = instance.orchestration_group
            template = group.orchestration if group else instance.orchestration
            items.append(
                {
                    "instance_id": instance.id,
                    "instance_name": instance.instance_name,
                    "display_name": app.display_name if app else instance.instance_name,
                    "app_name": app.name if app else None,
                    "icon": app.icon if app else None,
                    "project_id": project.id if project else None,
                    "project_name": project.project_name if project else None,
                    "orchestration_group_id": group.id if group else None,
                    "orchestration_group_name": group.instance_name if group else None,
                    "orchestration_name": template.display_name if template else None,
                    "status": instance.status,
                    "services": services,
                    "acceleration_enabled": bool(instance.acceleration_enabled),
                    "dns": (
                        [dns_ip]
                        if instance.acceleration_enabled and dns_ip
                        else []
                    ),
                    "can_enable": can_enable,
                    "reason": reason,
                }
            )
        return items

    async def _drop_legacy_override(self, project: DockerComposeProject) -> None:
        """清理旧版覆盖文件，把 config_files 收敛回单一文件源。"""
        paths = _config_file_paths(project)
        legacy = [path for path in paths if path.name == OVERRIDE_FILENAME]
        if not legacy:
            return
        for path in legacy:
            path.unlink(missing_ok=True)
        project.config_files = json.dumps(
            [str(path) for path in paths if path.name != OVERRIDE_FILENAME],
            ensure_ascii=False,
        )

    async def _save_compose_version(
        self,
        db: AsyncSession,
        project: DockerComposeProject,
        content: str,
        comment: str,
    ) -> tuple[Path, DockerComposeVersion, str]:
        """写入单一 Compose 文件并生成新版本。

        返回 (文件路径, 新版本, 写入前的原始内容)，供失败时回滚。
        """
        path = _config_file_paths(project)[0]
        path.parent.mkdir(parents=True, exist_ok=True)
        original = path.read_text(encoding="utf-8") if path.exists() else ""

        result = await db.execute(
            select(DockerComposeVersion.version_number)
            .where(DockerComposeVersion.project_id == project.id)
            .order_by(DockerComposeVersion.version_number.desc())
        )
        max_version = result.scalars().first() or 0
        await db.execute(
            update(DockerComposeVersion)
            .where(DockerComposeVersion.project_id == project.id)
            .values(is_current=False)
        )
        version = DockerComposeVersion(
            project_id=project.id,
            version_number=max_version + 1,
            content=content,
            comment=comment,
            is_current=True,
        )
        db.add(version)
        await db.flush()
        path.write_text(content, encoding="utf-8")
        return path, version, original

    @staticmethod
    async def _discard_version(
        db: AsyncSession,
        version: DockerComposeVersion,
        path: Path,
        original: str,
    ) -> None:
        """部署失败时回滚 Compose 文件与版本记录。"""
        path.write_text(original, encoding="utf-8")
        await db.delete(version)
        await db.flush()

    async def set_app_acceleration(
        self,
        db: AsyncSession,
        config: SystemConfig,
        instance_id: int,
        enabled: bool,
    ) -> dict[str, Any]:
        """开启或关闭单个应用的 DNS 加速。"""
        async with self._lock:
            return await self._set_app_acceleration_locked(
                db, config, instance_id, enabled
            )

    @staticmethod
    def _remember_acceleration(
        config: SystemConfig, instance_name: str, enabled: bool
    ) -> None:
        """记录需要跨重新部署保持的加速偏好。"""
        names = [
            str(name)
            for name in (config.hosts_optimizer_accel_instances or [])
            if name
        ]
        if enabled:
            if instance_name not in names:
                names.append(instance_name)
        else:
            names = [name for name in names if name != instance_name]
        config.hosts_optimizer_accel_instances = names

    async def reapply_saved_preferences(
        self, db: AsyncSession, instance_id: int
    ) -> None:
        """应用重新部署完成后恢复之前开启的加速。

        自动化方案「修复/重新部署」和重新安装都会创建新的 AppInstance，
        这里按稳定的实例名恢复 DNS 覆盖，避免用户每次手动再点一次。
        """
        config = await system_config_service.get_or_create(db)
        names = set(config.hosts_optimizer_accel_instances or [])
        if not names:
            return
        result = await db.execute(
            select(AppInstance)
            .options(selectinload(AppInstance.app))
            .where(AppInstance.id == instance_id)
        )
        instance = result.scalar_one_or_none()
        if (
            instance is None
            or _is_adguard_app(instance.app)
            or instance.project_id is None
            or instance.instance_name not in names
        ):
            return
        await self.set_app_acceleration(db, config, instance_id, True)

    async def _set_app_acceleration_locked(
        self,
        db: AsyncSession,
        config: SystemConfig,
        instance_id: int,
        enabled: bool,
    ) -> dict[str, Any]:
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
        if _is_adguard_app(instance.app):
            raise APIException("AdGuard Home 本身不能开启应用加速", 400)
        project = instance.project
        if project is None:
            raise APIException("该应用没有关联 Compose 项目，无法配置 DNS", 400)

        warnings: list[str] = []
        await self._drop_legacy_override(project)
        await db.flush()

        all_services = _merge_compose_services(project)
        services, skipped = _eligible_services(all_services)
        if enabled and not services:
            raise APIException(
                "该应用没有可注入 DNS 的服务（所有服务都设置了 network_mode）", 400
            )

        paths = _config_file_paths(project)
        if len(paths) > 1:
            warnings.append(
                "项目包含多个 Compose 文件，仅改写主文件 "
                f"{paths[0].name}，其余文件未注入"
            )
        primary = paths[0]
        current_content = (
            primary.read_text(encoding="utf-8") if primary.exists() else ""
        )
        previous_config_files = project.config_files
        from app.services.compose import compose_manager

        if enabled:
            adguard = await self.ensure_adguard(db, config)
            warnings.extend(adguard.get("warnings") or [])
            dns_ip = adguard.get("dns_ip") or config.hosts_optimizer_adguard_dns_ip
            if not dns_ip:
                raise APIException("AdGuard Home 没有可用的 DNS 地址", 500)

            try:
                new_content, info = apply_acceleration(
                    current_content, dns_ip, ACCEL_FALLBACK_DNS
                )
            except ValueError as exc:
                raise APIException(str(exc), 400) from exc
            services = info["services"]
            skipped = info["skipped"]

            path, version, original = await self._save_compose_version(
                db, project, new_content, "OneTap 网络加速：注入 DNS"
            )
            try:
                await compose_manager.action_async(db, project, "up")
            except Exception as exc:
                await self._discard_version(db, version, path, original)
                project.config_files = previous_config_files
                await db.flush()
                raise APIException(f"应用 DNS 配置失败：{exc}", 502) from exc

            instance.acceleration_enabled = True
            self._remember_acceleration(config, instance.instance_name, True)
            await db.flush()

            if info["existing_dns"]:
                warnings.append(
                    "以下服务原本已有 dns 配置，已保留并在前面插入 AdGuard："
                    f"{', '.join(info['existing_dns'])}"
                )

            if (
                config.hosts_optimizer_managed_rules
                or config.hosts_optimizer_domains
            ):
                try:
                    await hosts_optimizer_service.run_and_save(
                        db, config, mode="auto"
                    )
                except Exception as exc:
                    warnings.append(f"DNS 规则同步失败：{exc}")
        else:
            try:
                new_content = remove_acceleration(current_content)
            except ValueError as exc:
                raise APIException(str(exc), 400) from exc

            path, version, original = await self._save_compose_version(
                db, project, new_content, "OneTap 网络加速：移除 DNS"
            )
            try:
                await compose_manager.action_async(db, project, "up")
            except Exception as exc:
                await self._discard_version(db, version, path, original)
                project.config_files = previous_config_files
                await db.flush()
                raise APIException(f"恢复应用网络配置失败：{exc}", 502) from exc

            instance.acceleration_enabled = False
            self._remember_acceleration(config, instance.instance_name, False)
            await db.flush()

        if skipped:
            warnings.append(
                f"以下服务设置了 network_mode，未注入 DNS：{', '.join(skipped)}"
            )

        return {
            "instance_id": instance.id,
            "enabled": bool(instance.acceleration_enabled),
            "dns_ip": config.hosts_optimizer_adguard_dns_ip,
            "services": services,
            "warnings": warnings,
            "message": "已开启应用加速" if enabled else "已关闭应用加速",
        }

    # ---------- 聚合状态 ----------

    async def network_effectiveness(
        self,
        config: SystemConfig,
        rules: list[dict[str, Any]],
        apps: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """核验专属规则已写入 AdGuard，并有运行中的应用使用加速 DNS。"""
        subscription_rules = [
            rule for rule in rules if rule.get("tier") == "subscription"
        ]
        running_apps = sum(
            bool(app.get("acceleration_enabled")) and app.get("status") == "running"
            for app in apps
        )
        result: dict[str, Any] = {
            "network_effective": False,
            "network_rule_count": len(subscription_rules),
            "network_applied_rule_count": 0,
            "network_app_count": running_apps,
            "network_message": "未获取到专属加速规则",
        }
        if not subscription_rules:
            return result
        if not (
            config.hosts_optimizer_adguard_url
            and config.hosts_optimizer_adguard_password
        ):
            result["network_message"] = "专属规则已同步，但 AdGuard 未配置"
            return result

        try:
            async with httpx.AsyncClient(
                base_url=config.hosts_optimizer_adguard_url,
                auth=(
                    config.hosts_optimizer_adguard_username or "admin",
                    config.hosts_optimizer_adguard_password,
                ),
                trust_env=False,
                timeout=8.0,
            ) as client:
                response = await client.get("/control/rewrite/list")
                response.raise_for_status()
                rewrites = response.json()
            if not isinstance(rewrites, list):
                raise ValueError("DNS 重写列表格式异常")
        except Exception as exc:
            logger.warning("核验 AdGuard 专属加速规则失败：%s", exc)
            result["network_effective"] = None
            result["network_message"] = "无法连接 AdGuard 核验专属规则"
            return result

        applied = 0
        for rule in subscription_rules:
            desired = set(rule.get("answers") or [])
            actual = {
                str(item.get("answer") or "")
                for item in rewrites
                if isinstance(item, dict) and item.get("domain") == rule["domain"]
            } - {""}
            if desired and actual == desired:
                applied += 1
        result["network_applied_rule_count"] = applied
        if applied < len(subscription_rules):
            result["network_message"] = (
                f"专属规则仅 {applied}/{len(subscription_rules)} 条写入 AdGuard，"
                "尚未完全生效"
            )
        elif running_apps == 0:
            result["network_message"] = (
                f"{applied} 条专属规则已写入 AdGuard，但还没有运行中的加速应用"
            )
        else:
            result["network_effective"] = True
            result["network_message"] = (
                f"{applied} 条专属规则已写入 AdGuard，"
                f"{running_apps} 个加速应用正在使用"
            )
        return result

    async def overview(
        self, db: AsyncSession, config: SystemConfig
    ) -> dict[str, Any]:
        """应用加速页面所需的 AdGuard、规则与应用列表。"""
        container = await docker_manager.run_sync(
            docker_manager.get_container, ADGUARD_CONTAINER
        )
        running = bool(container and container.get("status") == "running")
        client = docker_manager._client
        host = (
            await asyncio.to_thread(detect_host_address, client)
            if client is not None
            else None
        )
        web_url = (
            f"http://{host}:{config.hosts_optimizer_adguard_web_port}"
            if host and config.hosts_optimizer_adguard_web_port
            else None
        )
        managed_rules = hosts_optimizer_service.effective_rules(config)
        synced_at = config.hosts_optimizer_managed_rules_synced_at
        rules_out = [
            {
                "domain": rule["domain"],
                "answers": list(rule.get("answers") or []),
                "priority": int(rule.get("priority") or 100),
                "description": rule.get("description"),
                "tier": rule.get("tier") or "free",
            }
            for rule in managed_rules
        ]
        apps = await self.list_apps(db, config)
        network_status = await self.network_effectiveness(config, managed_rules, apps)
        return {
            "adguard": {
                "configured": bool(
                    config.hosts_optimizer_adguard_url
                    and config.hosts_optimizer_adguard_password
                ),
                "managed": bool(config.hosts_optimizer_adguard_managed),
                "running": running,
                "url": config.hosts_optimizer_adguard_url,
                "web_url": web_url,
                "dns_ip": config.hosts_optimizer_adguard_dns_ip,
                "setup_port": config.hosts_optimizer_adguard_setup_port,
                "web_port": config.hosts_optimizer_adguard_web_port,
                "version": None,
                "network": ADGUARD_NETWORK,
                "fallback_dns": list(ACCEL_FALLBACK_DNS),
                "message": (
                    "自动部署运行中"
                    if running
                    else "AdGuard Home 未运行，开启应用加速时会自动部署"
                ),
            },
            "managed_rules": {
                "configured": bool(manage_base_urls()),
                "rules": rules_out,
                "synced_at": synced_at.isoformat(timespec="seconds")
                if isinstance(synced_at, datetime)
                else None,
                "error": None,
            },
            "apps": apps,
            **network_status,
        }


hosts_acceleration_service = HostsAccelerationService()
