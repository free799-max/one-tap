"""OneTap 托管 Mihomo 实例的运行状态与订阅同步。

部署和删除由 deployment.py 通过应用市场 Compose 流程完成；这里只操作
应用实例实际挂载的配置目录，并读取部署时分配的宿主机端口。
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import docker
import httpx
import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.docker import DockerComposeProject
from app.models.orchestration import AppInstance
from app.services.system.storage import StoragePathResolver
from app.services.system.storage_directory_service import storage_directory_service

logger = logging.getLogger(__name__)

APP_NAME = "mihomo"
PROJECT_NAME = "onetap-mihomo"
CONTAINER_NAME = PROJECT_NAME
CONFIG_MOUNT = "/root/.config/mihomo"
MIXED_PORT = 7890
CONTROLLER_PORT = 9090


def legacy_config_dir() -> Path:
    return (Path(settings.DATA_ROOT) / "acceleration" / "mihomo").resolve()


def minimal_config() -> dict:
    return {
        "mixed-port": MIXED_PORT,
        "allow-lan": True,
        "bind-address": "*",
        "mode": "rule",
        "log-level": "info",
        "external-controller": f"0.0.0.0:{CONTROLLER_PORT}",
        "proxies": [],
        "proxy-groups": [],
        "rules": ["MATCH,DIRECT"],
    }


def host_lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def _client():
    return docker.from_env()


def _find_container(client):
    try:
        return client.containers.get(CONTAINER_NAME)
    except docker.errors.NotFound:
        return None


async def _instance(db: AsyncSession) -> AppInstance | None:
    result = await db.execute(
        select(AppInstance)
        .where(AppInstance.instance_name == PROJECT_NAME)
        .order_by(AppInstance.project_id.is_not(None).desc(), AppInstance.id.desc())
    )
    return result.scalars().first()


async def _project(db: AsyncSession) -> DockerComposeProject | None:
    return await db.scalar(
        select(DockerComposeProject).where(
            DockerComposeProject.project_name == PROJECT_NAME
        )
    )


async def _resolver(db: AsyncSession) -> StoragePathResolver:
    return StoragePathResolver(
        app_data=await storage_directory_service.get_app_data_pair(db),
        data_directories=await storage_directory_service.list_enabled_pairs(db),
    )


def _config_volume(config: dict) -> dict | None:
    for volume in config.get("volumes") or []:
        if isinstance(volume, dict) and volume.get("container_path") == CONFIG_MOUNT:
            return volume
    return None


async def config_dir(
    db: AsyncSession, config: dict | None = None, *, create: bool = True
) -> Path:
    """返回后端可见的配置挂载目录，与 Compose 实际挂载保持一致。"""
    if config is None:
        instance = await _instance(db)
        config = instance.config if instance is not None else None
    if config is None:
        path = legacy_config_dir()
    else:
        resolver = await _resolver(db)
        volume = _config_volume(config)
        raw = str(volume.get("host_path") or "") if volume else ""
        if raw:
            relative = raw if raw.startswith("/") or volume.get("shared") else f"{PROJECT_NAME}/{raw}"
            path = Path(resolver.to_container_path(relative))
        else:
            path = Path(resolver.make_container_path(PROJECT_NAME, APP_NAME, "config"))
    if create:
        await asyncio.to_thread(path.mkdir, parents=True, exist_ok=True)
    return path


async def host_ports(db: AsyncSession) -> dict[int, int]:
    ports = {MIXED_PORT: MIXED_PORT, CONTROLLER_PORT: CONTROLLER_PORT}
    instance = await _instance(db)
    for item in (instance.config.get("ports") or []) if instance else []:
        if not isinstance(item, dict):
            continue
        try:
            container_port = int(item.get("container_port"))
            local_port = int(item.get("local_port"))
        except (TypeError, ValueError):
            continue
        if container_port in ports:
            ports[container_port] = local_port
    return ports


async def proxy_url(db: AsyncSession) -> str:
    ports = await host_ports(db)
    return f"http://{host_lan_ip()}:{ports[MIXED_PORT]}"


async def read_state(db: AsyncSession) -> dict:
    path = await config_dir(db, create=False)
    path = path / "state.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


async def _write_state(db: AsyncSession, **values) -> dict:
    state = await read_state(db)
    state.update(values)
    state["updated_at"] = datetime.now().isoformat(timespec="seconds")
    directory = await config_dir(db)
    (directory / "state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2), "utf-8"
    )
    return state


def _controller_version(port: int) -> str:
    try:
        response = httpx.get(f"http://127.0.0.1:{port}/version", timeout=2.0)
        if response.status_code == 200:
            return str(response.json().get("version") or "")
    except (httpx.HTTPError, ValueError):
        pass
    return ""


def _wait_ready(port: int, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _controller_version(port):
            return True
        time.sleep(0.5)
    return False


def _container_status(controller_port: int) -> dict:
    result = {"installed": False, "running": False, "version": "", "error": None}
    try:
        client = _client()
        container = _find_container(client)
    except docker.errors.DockerException as exc:
        result["error"] = f"Docker 不可用：{exc}"
        return result
    if container is None:
        return result
    result["installed"] = True
    result["running"] = container.status == "running"
    if result["running"]:
        result["version"] = _controller_version(controller_port)
        if not result["version"]:
            try:
                tag = (container.image.tags or [""])[0]
                result["version"] = tag.rsplit(":", 1)[-1] if ":" in tag else ""
            except Exception:  # noqa: BLE001 - 镜像标签缺失不影响状态
                pass
    return result


async def status_async(db: AsyncSession) -> dict:
    project = await _project(db)
    ports = await host_ports(db)
    state = await read_state(db)
    result = {
        "installed": project is not None,
        "running": False,
        "container_name": CONTAINER_NAME,
        "version": "",
        "mixed_port": ports[MIXED_PORT],
        "controller": f"127.0.0.1:{ports[CONTROLLER_PORT]}",
        "subscription_name": state.get("subscription_name", ""),
        "applied_revision": int(state.get("revision") or 0),
        "config_updated_at": state.get("updated_at"),
        "last_sync_at": state.get("last_sync_at"),
        "last_error": state.get("last_error"),
        "subscription_url": state.get("subscription_url", ""),
        "error": None,
    }
    container = await asyncio.to_thread(_container_status, ports[CONTROLLER_PORT])
    result["installed"] = result["installed"] or container["installed"]
    result["running"] = container["running"]
    result["version"] = container["version"]
    result["error"] = container["error"]
    return result


async def record_error(
    db: AsyncSession, message: str, *, trigger: str = "manual"
) -> None:
    await _write_state(
        db,
        last_error=message[:1000],
        last_sync_status="error",
        last_sync_message=message[:1000],
        last_sync_trigger=trigger,
        last_sync_changed=None,
        last_sync_at=datetime.now().isoformat(timespec="seconds"),
    )


async def record_sync_result(
    db: AsyncSession,
    *,
    status: str,
    message: str,
    trigger: str,
    changed: bool,
    rules: int,
    revision: int,
) -> None:
    await _write_state(
        db,
        last_error=None,
        last_sync_status=status,
        last_sync_message=message,
        last_sync_trigger=trigger,
        last_sync_changed=changed,
        last_sync_rules=rules,
        last_sync_revision=revision,
        last_sync_at=datetime.now().isoformat(timespec="seconds"),
    )


def _apply_subscription(content: str, directory: Path, controller_port: int) -> None:
    try:
        yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise RuntimeError(f"订阅内容不是合法 YAML：{exc}") from exc

    config_file = directory / "config.yaml"
    backup = directory / "config.yaml.bak"
    if config_file.exists():
        backup.write_text(config_file.read_text("utf-8"), "utf-8")
    config_file.write_text(content, "utf-8")

    try:
        client = _client()
        container = _find_container(client)
        if container is None:
            raise RuntimeError("Mihomo 未部署，请先部署")
        container.restart(timeout=10)
    except docker.errors.DockerException as exc:
        raise RuntimeError(f"应用订阅失败：{exc}") from exc

    if not _wait_ready(controller_port):
        if backup.exists():
            config_file.write_text(backup.read_text("utf-8"), "utf-8")
            try:
                _find_container(_client()).restart(timeout=10)
            except (docker.errors.DockerException, AttributeError):
                logger.warning("回滚 Mihomo 配置后重启失败")
        raise RuntimeError("Mihomo 重启后控制端口无响应，已回滚上一份配置")


async def apply_subscription_async(
    db: AsyncSession,
    content: str,
    *,
    subscription_name: str,
    revision: int,
    subscription_id: int = 0,
    content_sha: str = "",
    subscription_url: str = "",
) -> dict:
    directory = await config_dir(db)
    ports = await host_ports(db)
    await asyncio.to_thread(
        _apply_subscription, content, directory, ports[CONTROLLER_PORT]
    )
    await _write_state(
        db,
        subscription_name=subscription_name,
        revision=revision,
        subscription_id=subscription_id,
        content_sha=content_sha,
        subscription_url=subscription_url,
        last_error=None,
        last_sync_at=datetime.now().isoformat(timespec="seconds"),
    )
    return await status_async(db)


async def remove_legacy_container() -> bool:
    """清理没有 Compose 项目记录的旧版容器。"""

    def remove() -> bool:
        try:
            container = _find_container(_client())
            if container is None:
                return False
            container.remove(force=True)
            return True
        except docker.errors.DockerException as exc:
            raise RuntimeError(f"清理旧版 Mihomo 容器失败：{exc}") from exc

    return await asyncio.to_thread(remove)


def _moviepilot_status(expected: str) -> dict:
    result = {
        "installed": False,
        "container": "",
        "status": "unknown",
        "proxy_env": "",
        "expected_proxy": expected,
        "message": "",
    }
    try:
        containers = _client().containers.list(all=True)
    except docker.errors.DockerException as exc:
        result["status"] = "error"
        result["message"] = f"Docker 不可用：{exc}"
        return result

    for container in containers:
        names = container.name.lower()
        try:
            image = (container.image.tags or [""])[0].lower()
        except Exception:  # noqa: BLE001 - 镜像标签缺失不影响检测
            image = ""
        if "moviepilot" not in names and "moviepilot" not in image:
            continue
        result["installed"] = True
        result["container"] = container.name
        env = container.attrs.get("Config", {}).get("Env", []) or []
        proxy_env = ""
        for item in env:
            key, _, value = item.partition("=")
            if key in {"PROXY_HOST", "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"}:
                proxy_env = value
                break
        result["proxy_env"] = proxy_env
        if proxy_env.rstrip("/") == expected.rstrip("/"):
            result["status"] = "connected"
            result["message"] = "已通过本机 Mihomo 分流"
        elif proxy_env:
            result["status"] = "other"
            result["message"] = f"当前代理为 {proxy_env}，建议改为 {expected}"
        else:
            result["status"] = "missing"
            result["message"] = f"未配置代理，PROXY_HOST 建议设置为 {expected}"
        return result

    result["status"] = "not_installed"
    result["message"] = "未检测到 MoviePilot 容器"
    return result


async def moviepilot_status_async(db: AsyncSession) -> dict:
    expected = await proxy_url(db)
    return await asyncio.to_thread(_moviepilot_status, expected)
