"""识别可作为反向代理后端的内网地址。

反向代理需要把应用访问地址中的主机替换成内网可达地址，
这里列出当前访问地址与本机各网卡的 IPv4 地址，供用户选择。
"""

import ipaddress
import socket
import struct

try:
    import fcntl
except ImportError:  # 非 Linux 平台退化为解析主机名
    fcntl = None

# 容器与虚拟网卡地址对反向代理没有意义，直接忽略
_IGNORED_INTERFACE_PREFIXES = ("lo", "docker", "br-", "veth", "virbr")


def _usable(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return bool(address.strip())
    return not (
        ip.is_loopback
        or ip.is_link_local
        or ip.is_unspecified
        or ip.is_multicast
    )


def _rank(address: str) -> int:
    """常见的局域网网段排前面，越可能是 NAS 的对外地址。"""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return 5
    if ip in ipaddress.ip_network("192.168.0.0/16"):
        return 0
    if ip in ipaddress.ip_network("10.0.0.0/8"):
        return 1
    if ip in ipaddress.ip_network("172.16.0.0/12"):
        return 2
    if ip.is_private:
        return 3
    return 4


def _interface_addresses() -> list[tuple[str, str]]:
    """返回 (网卡名, IPv4 地址) 列表。"""
    if fcntl is None:
        return []
    try:
        names = [name for _, name in socket.if_nameindex()]
    except OSError:
        return []

    addresses: list[tuple[str, str]] = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        for name in names:
            if name.startswith(_IGNORED_INTERFACE_PREFIXES):
                continue
            try:
                packed = fcntl.ioctl(
                    sock.fileno(),
                    0x8915,  # SIOCGIFADDR
                    struct.pack("256s", name[:15].encode("utf-8")),
                )
            except OSError:
                continue
            addresses.append((name, socket.inet_ntoa(packed[20:24])))
    return addresses


def host_candidates(current: str) -> list[dict[str, str]]:
    """生成候选地址列表，当前访问地址固定排第一位。"""
    candidates: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(value: str, label: str, kind: str) -> None:
        value = (value or "").strip()
        if not value or value in seen:
            return
        seen.add(value)
        candidates.append({"value": value, "label": label, "kind": kind})

    interfaces = [
        (name, address)
        for name, address in _interface_addresses()
        if _usable(address)
    ]
    current_interface = next(
        (name for name, address in interfaces if address == current), ""
    )
    current_label = (
        f"当前访问地址（{current} / {current_interface}）"
        if current_interface
        else f"当前访问地址（{current}）"
    )
    add(current, current_label, "current")
    for name, address in sorted(interfaces, key=lambda item: _rank(item[1])):
        add(address, f"{address}（{name}）", "lan")
    return candidates
