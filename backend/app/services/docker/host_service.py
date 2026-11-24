"""Docker 宿主机信息服务。"""

import os
import shutil
import time

from app.services.docker import docker_common as common
from app.services.host.filesystem_service import filesystem_service


def _read_cpu_times() -> tuple[int, int] | None:
    """读取 /proc/stat 的累计 CPU 时间，返回 (总时间, 空闲时间)。"""
    try:
        with open("/proc/stat", encoding="utf-8") as handle:
            parts = handle.readline().split()
        values = [int(value) for value in parts[1:]]
    except (OSError, ValueError):
        return None
    if len(values) < 4:
        return None
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return sum(values), idle


def _cpu_usage_percent(interval: float = 0.15) -> float:
    """采样 /proc/stat 计算 CPU 使用率（百分比）。"""
    first = _read_cpu_times()
    if first is None:
        return 0.0
    time.sleep(interval)
    second = _read_cpu_times()
    if second is None:
        return 0.0
    total_delta = second[0] - first[0]
    idle_delta = second[1] - first[1]
    if total_delta <= 0:
        return 0.0
    usage = (1 - idle_delta / total_delta) * 100
    return round(max(0.0, min(usage, 100.0)), 1)


def _read_meminfo() -> dict[str, int]:
    """读取 /proc/meminfo，返回以字节为单位的键值。"""
    values: dict[str, int] = {}
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                key, _, rest = line.partition(":")
                parts = rest.split()
                if not parts:
                    continue
                try:
                    values[key] = int(parts[0]) * 1024
                except ValueError:
                    continue
    except OSError:
        return {}
    return values


def _read_uptime_seconds() -> int:
    """读取 /proc/uptime 获取系统运行时间。"""
    try:
        with open("/proc/uptime", encoding="utf-8") as handle:
            return int(float(handle.read().split()[0]))
    except (OSError, ValueError, IndexError):
        return 0


def _disk_usage(path: str):
    """读取磁盘用量；路径不可用时回退到根目录。"""
    try:
        return shutil.disk_usage(path)
    except OSError:
        try:
            return shutil.disk_usage("/")
        except OSError:
            return None


class HostService(common.BaseDockerService):
    """宿主机信息服务，获取 Docker 引擎与主机资源概况。"""

    def _network_usage(self) -> dict[str, list[str]]:
        """按网络名汇总关联容器名。"""
        if not self._client:
            return {}
        usage: dict[str, list[str]] = {}
        try:
            for container in self._client.containers.list(all=True):
                attached = (
                    container.attrs.get("NetworkSettings") or {}
                ).get("Networks") or {}
                for network_name in attached:
                    usage.setdefault(network_name, []).append(container.name)
        except Exception:
            common.logger.exception("统计网络关联容器失败")
        return usage

    def remove_network(self, network_id: str) -> None:
        """删除未被容器使用且非预定义的自定义网络。"""
        if not self._client:
            raise RuntimeError("Docker 不可用")
        try:
            network = self._client.networks.get(network_id)
        except common.docker.errors.NotFound:
            raise ValueError("网络不存在") from None

        if network.name in {"bridge", "host", "none"}:
            raise ValueError("Docker 预定义网络不可删除")

        attached = sorted(self._network_usage().get(network.name, []))
        if attached:
            raise ValueError(
                f"网络仍被 {len(attached)} 个容器使用：{'、'.join(attached)}"
            )

        try:
            network.remove()
        except common.docker.errors.APIError as exc:
            raise ValueError(f"删除网络失败：{exc.explanation or exc}") from exc

    def list_directories(
        self,
        path: str,
        root_paths: list[str] | None = None,
    ) -> dict:
        """列出指定路径下的目录条目。

        委托给通用文件系统服务实现，保持 Docker 宿主机的 API 入口不变。
        """
        return filesystem_service.list_directories(path, root_paths=root_paths)

    def create_directory(
        self, path: str, root_paths: list[str] | None = None
    ) -> dict:
        """创建目录。"""
        return filesystem_service.create_directory(path, root_paths=root_paths)

    def rename_directory(
        self,
        old_path: str,
        new_name: str,
        root_paths: list[str] | None = None,
    ) -> dict:
        """重命名目录。"""
        return filesystem_service.rename_directory(
            old_path,
            new_name,
            root_paths=root_paths,
        )

    def delete_directory(
        self, path: str, root_paths: list[str] | None = None
    ) -> None:
        """删除目录（递归删除非空目录）。"""
        filesystem_service.delete_directory(path, root_paths=root_paths)

    def get_host_info(self) -> dict | None:
        """获取 Docker 宿主机综合信息。"""
        if not self._client:
            return None
        try:
            version = self._client.version()
            info = self._client.info()

            docker_root_dir = info.get("DockerRootDir", "/var/lib/docker")
            disk_total = 0
            disk_free = 0
            disk_used = 0
            disk_usage_percent = 0.0
            usage = _disk_usage(docker_root_dir)
            if usage is not None:
                disk_total = usage.total
                disk_used = usage.used
                disk_free = usage.free
                if disk_total > 0:
                    disk_usage_percent = round((disk_used / disk_total) * 100, 2)

            meminfo = _read_meminfo()
            memory_total = int(info.get("MemTotal") or meminfo.get("MemTotal", 0))
            memory_available = meminfo.get("MemAvailable")
            if memory_available is None:
                memory_available = (
                    meminfo.get("MemFree", 0)
                    + meminfo.get("Buffers", 0)
                    + meminfo.get("Cached", 0)
                )
            memory_used = max(memory_total - memory_available, 0)

            try:
                load_average = [round(value, 2) for value in os.getloadavg()]
            except OSError:
                load_average = []

            networks = []
            usage = self._network_usage()
            for net in self._client.networks.list():
                names = sorted(usage.get(net.name, []))
                networks.append({
                    "id": net.id[:12],
                    "name": net.name,
                    "driver": net.attrs.get("Driver", "bridge"),
                    "scope": net.attrs.get("Scope", "local"),
                    "container_count": len(names),
                    "container_names": names,
                    "is_builtin": net.name in {"bridge", "host", "none"},
                })

            return {
                "hostname": info.get("Name", "unknown"),
                "os": version.get("Os", "unknown"),
                "arch": version.get("Arch", "unknown"),
                "kernel_version": version.get("KernelVersion", "unknown"),
                "uptime_seconds": _read_uptime_seconds(),
                "load_average": load_average,
                "docker_version": {
                    "version": version.get("Version", "unknown"),
                    "api_version": version.get("ApiVersion", "unknown"),
                    "go_version": version.get("GoVersion", "unknown"),
                    "os": version.get("Os", "unknown"),
                    "arch": version.get("Arch", "unknown"),
                    "kernel_version": version.get("KernelVersion", "unknown"),
                    "build_time": version.get("BuildTime", "unknown"),
                },
                "resources": {
                    "cpu_cores": info.get("NCPU", 0),
                    "cpu_usage_percent": _cpu_usage_percent(),
                    "memory_total": memory_total,
                    "memory_used": memory_used,
                    "disk_total": disk_total,
                    "disk_used": disk_used,
                    "disk_free": disk_free,
                    "disk_usage_percent": disk_usage_percent,
                },
                "stats": {
                    "containers_total": info.get("Containers", 0),
                    "containers_running": info.get("ContainersRunning", 0),
                    "containers_paused": info.get("ContainersPaused", 0),
                    "containers_stopped": info.get("ContainersStopped", 0),
                    "images": info.get("Images", 0),
                },
                "storage_driver": info.get("Driver", "unknown"),
                "docker_root_dir": docker_root_dir,
                "networks": networks,
            }
        except Exception:
            common.logger.exception("获取 Docker 宿主机信息失败")
            return None
