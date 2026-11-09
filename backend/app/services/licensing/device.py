"""基于 Docker Engine 的授权设备指纹。"""

from __future__ import annotations

import asyncio
import hashlib
import platform
import socket
from typing import Any

import docker


def _docker_engine_info() -> dict[str, Any]:
    client = docker.from_env()
    try:
        return client.info()
    finally:
        client.close()


def _read_cpu_model() -> str | None:
    models: dict[str, str] = {}
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as cpuinfo:
            for line in cpuinfo:
                key, separator, value = line.partition(":")
                normalized_key = key.strip().lower()
                model = value.strip()
                if (
                    separator
                    and normalized_key in {"model name", "hardware", "processor"}
                    and normalized_key not in models
                    and model
                ):
                    models[normalized_key] = model
    except OSError:
        pass
    return next((models[key] for key in ("model name", "hardware", "processor") if key in models), None)


def _fallback_platform() -> str | None:
    parts = [platform.system(), platform.release(), platform.machine()]
    return " ".join(part for part in parts if part) or None


def _platform_from_engine(engine_info: dict[str, Any]) -> str | None:
    os_name = engine_info.get("OperatingSystem")
    if not isinstance(os_name, str) or not os_name.strip():
        return None
    os_name = os_name.strip()
    kernel = engine_info.get("KernelVersion")
    if os_name.lower() == "linux" and isinstance(kernel, str) and kernel.strip():
        os_name = f"Linux {kernel.strip()}"
    architecture = engine_info.get("Architecture")
    if isinstance(architecture, str) and architecture.strip():
        return f"{os_name} · {architecture.strip()}"
    return os_name


async def get_host_details() -> dict[str, Any]:
    """本机（Docker 宿主机）的主机名、系统与硬件信息，用于安装上报。"""
    hostname = socket.gethostname()
    platform_name = _fallback_platform()
    device_info = {
        "cpu_model": _read_cpu_model(),
        "cpu_cores": None,
        "memory_total_bytes": None,
    }
    try:
        engine_info = await asyncio.to_thread(_docker_engine_info)
    except Exception:
        return {
            "hostname": hostname,
            "platform": platform_name,
            "device_info": device_info,
        }
    host_name = engine_info.get("Name")
    if isinstance(host_name, str) and host_name.strip():
        hostname = host_name.strip()
    platform_name = _platform_from_engine(engine_info) or platform_name
    device_info["cpu_cores"] = engine_info.get("NCPU")
    device_info["memory_total_bytes"] = engine_info.get("MemTotal")
    return {
        "hostname": hostname,
        "platform": platform_name,
        "device_info": device_info,
    }


async def get_device_fingerprint(installation_id: str) -> dict[str, Any]:
    device_info = {
        "cpu_model": None,
        "cpu_cores": None,
        "memory_total_bytes": None,
    }
    try:
        engine_info = await asyncio.to_thread(_docker_engine_info)
        engine_id = engine_info.get("ID")
        if not isinstance(engine_id, str) or not engine_id.strip():
            raise ValueError("Docker Engine ID is missing")
        device_info["cpu_model"] = _read_cpu_model()
        device_info["cpu_cores"] = engine_info.get("NCPU")
        device_info["memory_total_bytes"] = engine_info.get("MemTotal")
        fingerprint = hashlib.sha256(f"docker:{engine_id.strip()}".encode()).hexdigest()
        source = "docker"
    except Exception:
        fingerprint = hashlib.sha256(f"installation:{installation_id}".encode()).hexdigest()
        source = "installation"
    return {
        "hardware_fingerprint": fingerprint,
        "fingerprint_source": source,
        "device_info": device_info,
    }
