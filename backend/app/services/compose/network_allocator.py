"""Compose 项目默认网络的私有子网分配。"""

import ipaddress
import threading
import time
from collections.abc import Iterable

from app.services.docker import docker_manager

DEFAULT_NETWORK_PREFIX = 28
DEFAULT_NETWORK_POOLS = (
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("10.0.0.0/8"),
)

_reserved: dict[str, float] = {}
_reserved_lock = threading.Lock()
_RESERVATION_TTL_SECONDS = 1800


def normalize_subnets(values: Iterable[str]) -> set[ipaddress.IPv4Network]:
    subnets: set[ipaddress.IPv4Network] = set()
    for value in values:
        try:
            network = ipaddress.ip_network(str(value), strict=False)
        except ValueError:
            continue
        if network.version == 4:
            subnets.add(network)
    return subnets


def find_available_subnet(
    used: Iterable[ipaddress.IPv4Network],
    pools: Iterable[ipaddress.IPv4Network] = DEFAULT_NETWORK_POOLS,
) -> ipaddress.IPv4Network:
    """在候选私网池中返回第一个不与已用网段重叠的 /28。"""
    used_networks = list(used)
    for pool in pools:
        for candidate in pool.subnets(new_prefix=DEFAULT_NETWORK_PREFIX):
            if not any(candidate.overlaps(network) for network in used_networks):
                return candidate
    raise RuntimeError("没有可用于 Compose 默认网络的空闲 /28 子网")


def _purge_expired_locked(now: float) -> None:
    expired = [
        subnet
        for subnet, timestamp in _reserved.items()
        if now - timestamp > _RESERVATION_TTL_SECONDS
    ]
    for subnet in expired:
        del _reserved[subnet]


def reserve_subnet(subnet: str) -> None:
    with _reserved_lock:
        now = time.monotonic()
        _purge_expired_locked(now)
        _reserved[str(ipaddress.ip_network(subnet))] = now


def release_subnet(subnet: str | None) -> None:
    if not subnet:
        return
    with _reserved_lock:
        _reserved.pop(str(ipaddress.ip_network(subnet)), None)


def get_reserved_subnets() -> set[ipaddress.IPv4Network]:
    with _reserved_lock:
        _purge_expired_locked(time.monotonic())
        return {
            ipaddress.ip_network(subnet)
            for subnet in _reserved
        }


def get_docker_subnets() -> set[ipaddress.IPv4Network]:
    """读取 Docker 现有网络的 IPv4 子网。"""
    client = docker_manager._client
    if client is None:
        return set()
    values: list[str] = []
    for network in client.networks.list():
        ipam = network.attrs.get("IPAM") or {}
        for config in ipam.get("Config") or []:
            if isinstance(config, dict) and config.get("Subnet"):
                values.append(str(config["Subnet"]))
    return normalize_subnets(values)
