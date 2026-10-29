"""把离线资源镜像里的目录拷贝进应用容器。

资源镜像只用来提供数据：``docker create`` 后通过 ``docker cp``
直接复制到目标容器，随后删除临时容器。目标容器如果正在运行，会先
停止、拷贝完成后再启动，避免运行中的进程读到半份依赖。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import settings
from app.services.apps.offline_assets import AssetCopy, OfflineAssetsProfile

logger = logging.getLogger(__name__)

ProgressCallback = Callable[..., Any]


class AssetCopyError(RuntimeError):
    """离线资源拷贝失败。"""


async def _docker_exec(*args: str, timeout: int = 300) -> tuple[int, str, str]:
    """执行 docker CLI，返回 (退出码, stdout, stderr)。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        raise AssetCopyError("未找到 docker 命令") from None
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise AssetCopyError(f"docker {args[0]} 执行超时") from None
    return (
        proc.returncode or 0,
        stdout.decode("utf-8", errors="replace").strip(),
        stderr.decode("utf-8", errors="replace").strip(),
    )


async def find_container(project_name: str, service: str) -> str | None:
    """按 Compose 项目名与服务名定位容器（含已停止的容器）。"""
    code, out, _ = await _docker_exec(
        "ps",
        "-aq",
        "--filter",
        f"label=com.docker.compose.project={project_name}",
        "--filter",
        f"label=com.docker.compose.service={service}",
        timeout=30,
    )
    ids = out.split() if code == 0 else []
    if ids:
        return ids[0]

    # 兼容极老的项目：没有 compose 标签时按容器名兜底
    for pattern in (f"name=^/{project_name}$", f"name=^{project_name}$"):
        code, out, _ = await _docker_exec(
            "ps", "-aq", "--filter", pattern, timeout=30
        )
        if code == 0 and out.strip():
            return out.split()[0]
    return None


async def is_running(container: str) -> bool:
    """容器是否处于运行状态。"""
    code, out, _ = await _docker_exec(
        "inspect", "-f", "{{.State.Running}}", container, timeout=30
    )
    return code == 0 and out.strip().lower() == "true"


async def read_marker(container: str, marker: str) -> dict | None:
    """读取容器内的完成标记；不存在或格式异常时返回 None。

    注意不能用 ``docker cp ... -``：该形式输出的是 tar 归档而不是
    原始文件内容，因此先复制到临时目录再读取。
    """
    with tempfile.TemporaryDirectory(prefix="onetap-marker-") as tmp:
        dest = Path(tmp) / "marker"
        code, _, _ = await _docker_exec(
            "cp", f"{container}:{marker}", str(dest), timeout=30
        )
        if code != 0 or not dest.is_file():
            return None
        try:
            data = json.loads(dest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    return data if isinstance(data, dict) else None


def marker_matches(marker: dict | None, profile: OfflineAssetsProfile) -> bool:
    """标记是否对应当前资源镜像与档案修订号。"""
    if not marker:
        return False
    return (
        marker.get("image") == profile.assets_image
        and marker.get("revision") == profile.revision
    )


async def _ensure_assets_container(
    profile: OfflineAssetsProfile,
    progress: ProgressCallback | None,
) -> str:
    """创建（必要时先拉取）资源镜像的临时容器。"""
    code, _, _ = await _docker_exec(
        "image", "inspect", profile.assets_image, timeout=30
    )
    if code != 0:
        if progress:
            progress(
                "copying_assets", 10, "拉取离线资源镜像", profile.assets_image
            )
        code, _, err = await _docker_exec(
            "pull", profile.assets_image, timeout=1800
        )
        if code != 0:
            raise AssetCopyError(
                f"拉取资源镜像失败（{profile.assets_image}）: {err or 'docker pull 失败'}"
            )

    code, out, err = await _docker_exec(
        "create", profile.assets_image, timeout=1800
    )
    if code != 0 or not out:
        raise AssetCopyError(
            f"创建资源临时容器失败（{profile.assets_image}）: {err or '未返回容器 ID'}"
        )
    return out.splitlines()[-1].strip()


async def _write_marker(
    container: str,
    profile: OfflineAssetsProfile,
) -> dict:
    """把完成标记写进容器可写层。"""
    payload = {
        "image": profile.assets_image,
        "revision": profile.revision,
        "app": profile.app_name,
        "copied_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    fd, tmp_path = tempfile.mkstemp(prefix="onetap-assets-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        code, _, err = await _docker_exec(
            "cp", tmp_path, f"{container}:{profile.marker}", timeout=60
        )
        if code != 0:
            raise AssetCopyError(f"写入完成标记失败: {err or 'docker cp 失败'}")
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
    return payload


_CACHE_READY = ".ready"
"""本地缓存目录里的完成标记。"""

_cache_locks: dict[str, asyncio.Lock] = {}


def _cache_root() -> Path:
    """离线资源在宿主机上的缓存根目录。"""
    return Path(settings.DATA_ROOT).resolve() / "offline-assets"


def _cache_dir(profile: OfflineAssetsProfile) -> Path:
    """按资源镜像区分的缓存目录。"""
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", profile.assets_image)
    return _cache_root() / name


def _cache_lock(profile: OfflineAssetsProfile) -> asyncio.Lock:
    key = str(_cache_dir(profile))
    lock = _cache_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _cache_locks[key] = lock
    return lock


def _source_rel(source: str) -> str:
    """资源镜像内的路径转成缓存目录内的相对路径。"""
    prefix = "/assets/"
    return source[len(prefix):] if source.startswith(prefix) else source.lstrip("/")


async def _ensure_cache(
    profile: OfflineAssetsProfile,
    progress: ProgressCallback | None,
) -> Path:
    """把资源镜像的 /assets 解包到宿主机缓存，已存在时直接复用。

    docker 不支持容器之间直接 docker cp，因此先从资源容器复制到本地
    缓存，再从缓存复制进应用容器；容器重建时不必重新拉取/解包镜像。
    """
    async with _cache_lock(profile):
        cache = _cache_dir(profile)
        ready = cache / _CACHE_READY
        if ready.is_file():
            try:
                if ready.read_text(encoding="utf-8").strip() == profile.assets_image:
                    return cache
            except OSError:
                pass

        cache.mkdir(parents=True, exist_ok=True)
        # 先占位，避免 docker cp 后目录变成 root 所有、后端无法创建标记
        try:
            ready.touch(exist_ok=True)
        except OSError:
            logger.warning("无法创建离线资源缓存标记: %s", ready)

        assets_container = await _ensure_assets_container(profile, progress)
        try:
            if progress:
                progress(
                    "copying_assets",
                    25,
                    "解包离线资源到本地缓存",
                    profile.assets_image,
                )
            code, _, err = await _docker_exec(
                "cp",
                f"{assets_container}:/assets/.",
                str(cache),
                timeout=1800,
            )
            if code != 0:
                raise AssetCopyError(
                    f"解包离线资源失败（{profile.assets_image}）: "
                    f"{err or 'docker cp 失败'}"
                )
        finally:
            await _docker_exec("rm", "-f", assets_container, timeout=60)

        try:
            ready.write_text(profile.assets_image + "\n", encoding="utf-8")
        except OSError:
            logger.warning("写入离线资源缓存标记失败: %s", ready)
        return cache


_container_locks: dict[str, asyncio.Lock] = {}


def _container_lock(container: str) -> asyncio.Lock:
    """同一容器的拷贝串行执行，避免并存任务互相 stop/start。"""
    lock = _container_locks.get(container)
    if lock is None:
        lock = asyncio.Lock()
        _container_locks[container] = lock
    return lock


async def copy_assets(
    container: str,
    profile: OfflineAssetsProfile,
    progress: ProgressCallback | None = None,
    force: bool = False,
) -> dict:
    """把档案里的资源目录拷贝进容器。

    Returns:
        {"copied": bool, "marker": dict|None, "restarted": bool}
    """
    async with _container_lock(container):
        return await _copy_assets_locked(container, profile, progress, force)


async def _copy_assets_locked(
    container: str,
    profile: OfflineAssetsProfile,
    progress: ProgressCallback | None = None,
    force: bool = False,
) -> dict:
    """``copy_assets`` 的实际执行体（调用方已持有容器锁）。"""
    existing = await read_marker(container, profile.marker)
    if not force and marker_matches(existing, profile):
        return {"copied": False, "marker": existing, "restarted": False}

    running = await is_running(container)
    if running:
        if progress:
            progress("copying_assets", 15, "停止应用", "拷贝完成后会自动启动")
        code, _, err = await _docker_exec("stop", container, timeout=120)
        if code != 0:
            raise AssetCopyError(f"停止应用容器失败: {err or 'docker stop 失败'}")

    restarted = False
    try:
        cache = await _ensure_cache(profile, progress)
        total = len(profile.copies)
        for index, copy in enumerate(profile.copies):
            percent = 50 + int(35 * (index + 0.5) / max(total, 1))
            if progress:
                progress(
                    "copying_assets",
                    percent,
                    f"拷贝{copy.label}",
                    f"{copy.source} -> {copy.target}",
                )
            await _copy_one(cache, container, copy)
        marker = await _write_marker(container, profile)
        if progress:
            progress("copying_assets", 90, "离线数据拷贝完成")
    finally:
        if running:
            if progress:
                progress("copying_assets", 92, "启动应用", "让插件依赖生效")
            code, _, err = await _docker_exec("start", container, timeout=120)
            restarted = code == 0
            if code != 0:
                logger.warning("启动应用容器失败: %s", err)

    return {
        "copied": True,
        "marker": marker,
        "restarted": restarted,
    }


async def _copy_one(
    cache: Path,
    container: str,
    copy: AssetCopy,
) -> None:
    """单条目录拷贝：本地缓存 -> 应用容器。"""
    source = cache / _source_rel(copy.source)
    if not source.exists():
        raise AssetCopyError(f"本地缓存缺少目录: {source}")
    code, _, err = await _docker_exec(
        "cp",
        f"{source}/.",
        f"{container}:{copy.target}",
        timeout=1800,
    )
    if code != 0:
        raise AssetCopyError(
            f"拷贝{copy.label}失败（{copy.source} -> {copy.target}）: "
            f"{err or 'docker cp 失败'}"
        )
