"""服务端口分配。

集中维护宿主机端口占用检测与顺序分配逻辑，
供应用商店部署与手动 Compose 项目创建共用。
"""

import json
import logging
import threading
import time

from app.services.docker import docker_manager
from app.services.docker.host_network import run_in_host_network

logger = logging.getLogger(__name__)

# 端口上限
MAX_PORT = 65535

# 已分配但容器尚未绑定的端口预留表（port -> 预留时间戳）。
# 并发部署多个应用时，仅靠占用检测会重复分配同一端口（容器还没启动、
# 端口还没绑定），预留表让同批部署跳过已分配的端口。
_reserved: dict[int, float] = {}
_reserved_lock = threading.Lock()
# 预留兜底过期时间，与部署任务超时一致，防止异常路径泄漏预留
_RESERVATION_TTL_SECONDS = 1800


def _purge_expired_locked(now: float) -> None:
    """清理过期预留，调用方需已持有 _reserved_lock。"""
    expired = [p for p, ts in _reserved.items() if now - ts > _RESERVATION_TTL_SECONDS]
    for p in expired:
        del _reserved[p]


def reserve_ports(ports: list[int]) -> None:
    """预留端口，部署完成/失败后应调用 release_ports 释放。"""
    if not ports:
        return
    with _reserved_lock:
        now = time.monotonic()
        _purge_expired_locked(now)
        for p in ports:
            _reserved[p] = now


def release_ports(ports: list[int] | None) -> None:
    """释放预留端口（可重复调用，幂等）。"""
    if not ports:
        return
    with _reserved_lock:
        for p in ports:
            _reserved.pop(p, None)


def get_reserved_ports() -> set[int]:
    """获取当前生效的预留端口集合。"""
    with _reserved_lock:
        _purge_expired_locked(time.monotonic())
        return set(_reserved)


def _parse_proc_net_tcp(path: str) -> set[int]:
    """读取 /proc/net/tcp 或 /proc/net/tcp6，提取处于 LISTEN 状态的本地端口。

    当 OneTap 以 host network 模式运行时，读取到的是宿主机的监听端口；
    普通容器模式下读取的是容器自身命名空间内的端口，作为兜底信息使用。
    """
    listening: set[int] = set()
    try:
        with open(path, "r") as f:
            # 跳过第一行表头
            next(f, None)
            for line in f:
                parts = line.strip().split()
                if len(parts) < 4:
                    continue
                # st 列：0A 表示 LISTEN
                if parts[3] != "0A":
                    continue
                local_addr = parts[1]
                if ":" not in local_addr:
                    continue
                _, port_hex = local_addr.rsplit(":", 1)
                try:
                    listening.add(int(port_hex, 16))
                except ValueError:
                    continue
    except (OSError, StopIteration):
        pass
    return listening


_HOST_PORT_PROBE = (
    "import json; "
    "from app.services.system.port_allocator import _parse_proc_net_tcp; "
    "print(json.dumps(sorted(_parse_proc_net_tcp('/proc/net/tcp') | "
    "_parse_proc_net_tcp('/proc/net/tcp6'))))"
)


def _get_host_listening_ports(client, local_ports: set[int]) -> set[int]:
    try:
        output = run_in_host_network(client, ["python", "-c", _HOST_PORT_PROBE])
        if output is None:
            return local_ports
        ports = json.loads(output)
        if not isinstance(ports, list):
            raise ValueError("宿主机端口探测结果格式错误")
        return {int(port) for port in ports}
    except Exception:
        logger.warning("获取宿主机监听端口失败，使用容器内检测结果", exc_info=True)
        return local_ports


def _container_host_ports(container) -> set[int]:
    """读取容器映射到宿主机的端口。"""
    ports: set[int] = set()
    host_config = container.attrs.get("HostConfig") or {}
    bindings = host_config.get("PortBindings") or {}
    for binding_list in bindings.values():
        if not isinstance(binding_list, list):
            continue
        for binding in binding_list:
            host_port = binding.get("HostPort")
            if host_port:
                try:
                    ports.add(int(host_port))
                except ValueError:
                    continue
    return ports


def get_used_ports(exclude_containers: set[str] | None = None) -> set[int]:
    """获取宿主机上已被占用的端口。

    合并 Docker 容器映射、宿主机网络命名空间中的 IPv4/IPv6 监听端口，
    以及当前网络命名空间的监听端口（作为探测失败时的兜底）。

    Args:
        exclude_containers: 需要忽略映射端口的容器名集合，用于重建同一
            服务时把旧容器占用的端口视为可用。
    """
    used: set[int] = set()
    ignored_ports: set[int] = set()
    other_ports: set[int] = set()

    # 1. Docker 容器端口映射
    client = docker_manager._client
    if client is not None:
        try:
            for container in client.containers.list(all=True):
                ports = _container_host_ports(container)
                if exclude_containers and container.name in exclude_containers:
                    ignored_ports.update(ports)
                else:
                    other_ports.update(ports)
        except Exception:
            # Docker 不可用时跳过
            logger.warning("获取 Docker 容器端口映射失败，跳过该来源", exc_info=True)
    used.update(other_ports)

    # 2. 当前网络命名空间与宿主机网络命名空间的监听端口
    local_ports = _parse_proc_net_tcp("/proc/net/tcp")
    local_ports.update(_parse_proc_net_tcp("/proc/net/tcp6"))
    used.update(local_ports)
    if client is not None:
        used.update(_get_host_listening_ports(client, local_ports))

    # 宿主机监听探测无法区分 Docker 代理属于哪个容器，最后再扣除被忽略
    # 容器独占的端口；仍被其他容器映射的端口保留为占用。
    return used - (ignored_ports - other_ports)


def allocate_free_ports(used: set[int], count: int, start: int) -> list[int]:
    """从 start 开始按顺序挑选 count 个不在 used 中的空闲端口。

    已预留（已分配但尚未绑定）的端口同样会被跳过。

    Args:
        used: 已占用端口集合（不会被修改）
        count: 需要分配的端口数量
        start: 起始端口

    Returns:
        按顺序分配的端口列表

    Raises:
        ValueError: 端口候选超过 65535 仍凑不够 count 个时
    """
    return _allocate(used | get_reserved_ports(), count, start)


def allocate_and_reserve(used: set[int], count: int, start: int) -> list[int]:
    """分配并原子预留端口：与并发分配互斥，避免同批部署拿到重复端口。"""
    with _reserved_lock:
        now = time.monotonic()
        _purge_expired_locked(now)
        allocated = _allocate(used | set(_reserved), count, start)
        for p in allocated:
            _reserved[p] = now
        return allocated


def _allocate(used: set[int], count: int, start: int) -> list[int]:
    """顺序挑选空闲端口（不含预留处理，纯函数）。"""
    allocated: list[int] = []
    candidate = start
    while len(allocated) < count:
        if candidate > MAX_PORT:
            raise ValueError(f"从端口 {start} 开始没有足够的空闲端口可供分配")
        if candidate not in used and candidate not in allocated:
            allocated.append(candidate)
        candidate += 1
    return allocated
