"""Compose YAML 相关的纯工具函数：项目名/YAML 校验、标签注入、端口重写。"""

import re
from collections.abc import Callable

import yaml

from app.models.docker import COMPOSE_PROJECT_LABEL
from app.services.system.port_allocator import allocate_free_ports

# 允许的项目名字符：小写字母、数字、下划线、连字符
_PROJECT_NAME_PATTERN = re.compile(r"^[a-z0-9_-]+$")


def validate_project_name(project_name: str) -> None:
    """校验项目名是否合法。"""
    if not project_name:
        raise ValueError("项目名不能为空")
    if not _PROJECT_NAME_PATTERN.match(project_name):
        raise ValueError("项目名只能包含小写字母、数字、下划线和连字符")


def validate_yaml(content: str) -> None:
    """校验 YAML 内容是否合法。"""
    try:
        yaml.safe_load(content)
    except yaml.YAMLError as e:
        raise ValueError(f"YAML 格式错误: {e}") from e


def _uses_default_network(service) -> bool:
    if not isinstance(service, dict) or service.get("network_mode"):
        return False
    networks = service.get("networks")
    if networks is None:
        return True
    return isinstance(networks, (list, dict)) and "default" in networks


def default_network_needs_subnet(content: str) -> bool:
    """判断 Compose 逻辑 default 网络是否需要分配子网。"""
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError:
        return False
    if not isinstance(data, dict):
        return False
    services = data.get("services")
    if not isinstance(services, dict) or not any(
        _uses_default_network(service) for service in services.values()
    ):
        return False

    networks = data.get("networks")
    if networks is None:
        return True
    if not isinstance(networks, dict):
        return False
    default = networks.get("default")
    if default is None:
        return True
    if not isinstance(default, dict) or default.get("external"):
        return False
    if default.get("driver") not in (None, "bridge"):
        return False
    ipam = default.get("ipam")
    if not isinstance(ipam, dict):
        return True
    config = ipam.get("config")
    if not isinstance(config, list):
        return True
    return not any(
        isinstance(item, dict) and item.get("subnet") for item in config
    )


def set_default_network_subnet(content: str, subnet: str) -> str:
    """为 Compose 逻辑 default 网络写入显式子网。"""
    if not default_network_needs_subnet(content):
        return content
    data = yaml.safe_load(content)
    networks = data.get("networks")
    if networks is None:
        networks = {}
        data["networks"] = networks
    default = networks.get("default")
    if default is None:
        default = {}
        networks["default"] = default
    default.setdefault("driver", "bridge")
    ipam = default.get("ipam")
    if ipam is None:
        ipam = {}
        default["ipam"] = ipam
    if not isinstance(ipam, dict):
        return content
    config = ipam.get("config")
    if config is None:
        config = []
        ipam["config"] = config
    if not isinstance(config, list):
        return content
    config.append({"subnet": subnet})
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def extract_compose_subnets(content: str) -> set[str]:
    """提取 Compose 顶层网络中显式配置的子网。"""
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError:
        return set()
    if not isinstance(data, dict) or not isinstance(data.get("networks"), dict):
        return set()
    subnets: set[str] = set()
    for network in data["networks"].values():
        if not isinstance(network, dict) or not isinstance(network.get("ipam"), dict):
            continue
        config = network["ipam"].get("config")
        if not isinstance(config, list):
            continue
        for item in config:
            if isinstance(item, dict) and item.get("subnet"):
                subnets.add(str(item["subnet"]))
    return subnets


def inject_labels(content: str, project_name: str) -> str:
    """在 YAML 中为每个服务注入 Compose 项目归属标签。"""
    data = yaml.safe_load(content) or {}
    services = data.get("services")
    if not isinstance(services, dict):
        return content

    label_key = COMPOSE_PROJECT_LABEL
    label_value = project_name
    for svc_name, svc in services.items():
        if not isinstance(svc, dict):
            continue
        labels = svc.setdefault("labels", [])
        if isinstance(labels, dict):
            labels[label_key] = label_value
        elif isinstance(labels, list):
            exists = any(
                (
                    isinstance(item, str)
                    and item.startswith(f"{label_key}=")
                )
                or (
                    isinstance(item, dict)
                    and item.get(label_key) is not None
                )
                for item in labels
            )
            if not exists:
                labels.append(f"{label_key}={label_value}")
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def rewrite_ports(
    content: str,
    used: set[int],
    start: int,
    allocator: Callable[[set[int], int, int], list[int]] = allocate_free_ports,
) -> str:
    """将 compose YAML 中各服务的宿主端口重写为顺序分配的空闲端口。

    - 短语法字符串（"8080:80"、"127.0.0.1:8080:80"、"8080:80/udp"）：
      只改宿主端口段，保留 IP 前缀和协议后缀；
    - 纯数字条目（3000 或 "3000"，等价于宿主=容器）：替换为分配端口；
    - 含范围 "-" 的条目（如 "3000-3010:3000-3010"）不改动；
    - 长语法 dict 含 published 字段的改 published；
    - 无法识别的条目跳过；YAML 解析失败时原样返回。
    """
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError:
        return content
    if not isinstance(data, dict):
        return content
    services = data.get("services")
    if not isinstance(services, dict):
        return content

    def _needs_rewrite(entry) -> bool:
        """判断 ports 条目是否需要重写宿主端口。"""
        if isinstance(entry, bool):
            return False
        if isinstance(entry, int):
            return True
        if isinstance(entry, str):
            if "-" in entry:
                return False
            segments = entry.split("/", 1)[0].split(":")
            if len(segments) == 1:
                return segments[0].isdigit()
            if len(segments) == 2:
                return segments[0].isdigit()
            if len(segments) == 3:
                return segments[1].isdigit()
            return False
        if isinstance(entry, dict):
            published = entry.get("published")
            if published is None or isinstance(published, bool):
                return False
            if isinstance(published, int):
                return True
            return (
                isinstance(published, str)
                and published.isdigit()
                and "-" not in published
            )
        return False

    # 第一遍：统计需要分配的端口数量
    count = sum(
        1
        for svc in services.values()
        if isinstance(svc, dict) and isinstance(svc.get("ports"), list)
        for entry in svc["ports"]
        if _needs_rewrite(entry)
    )
    if count == 0:
        return content

    allocated = allocator(set(used), count, start)
    alloc_iter = iter(allocated)

    def _rewrite_entry(entry):
        """重写单个 ports 条目的宿主端口为分配端口。"""
        port = next(alloc_iter)
        if isinstance(entry, int):
            return port
        if isinstance(entry, str):
            host_part, _, suffix = entry.partition("/")
            suffix = f"/{suffix}" if suffix else ""
            segments = host_part.split(":")
            if len(segments) == 1:
                return f"{port}{suffix}"
            if len(segments) == 2:
                return f"{port}:{segments[1]}{suffix}"
            # len(segments) == 3，保留 IP 前缀
            return f"{segments[0]}:{port}:{segments[2]}{suffix}"
        # 长语法 dict，published 保持原有类型
        if isinstance(entry.get("published"), str):
            entry["published"] = str(port)
        else:
            entry["published"] = port
        return entry

    for svc in services.values():
        if not isinstance(svc, dict):
            continue
        ports = svc.get("ports")
        if not isinstance(ports, list):
            continue
        svc["ports"] = [
            _rewrite_entry(entry) if _needs_rewrite(entry) else entry
            for entry in ports
        ]

    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
