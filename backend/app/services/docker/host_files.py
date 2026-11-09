"""宿主机文件读写（必要时通过 Docker 辅助容器）。

OneTap 后端通常以容器方式运行，系统设置中的宿主机存储目录不一定挂载
到后端容器内。直接使用 ``pathlib`` 读写会因路径不存在而静默失败。

这里先尝试直接读写；失败时创建一个一次性的辅助容器，把目标目录以
bind mount 挂载进去，再用 Docker 的 archive API 完成读取和写入。
辅助容器复用 OneTap 自身镜像，不需要额外拉取镜像或修改部署挂载。
"""

from __future__ import annotations

import io
import logging
import socket
import tarfile
import time
from collections.abc import Callable
from pathlib import Path

from app.services.docker.manager import docker_manager

logger = logging.getLogger(__name__)

_MOUNT_TARGET = "/onetap-host"
_HELPER_LABEL = "onetap.host-files"
_HELPER_LABEL_VALUE = "1"


def _client():
    try:
        return docker_manager._client
    except Exception:
        logger.warning("获取 Docker 客户端失败", exc_info=True)
        return None


def _helper_image(client) -> str | None:
    """选一个本地已有镜像创建辅助容器，避免额外拉取。

    后端运行在容器内时用 OneTap 自身镜像；直接在宿主机运行（开发模式）
    时没有同名容器，退化为任意本地镜像。
    """
    try:
        own = client.containers.get(socket.gethostname())
        return own.image.id
    except Exception:
        pass
    try:
        for image in client.images.list():
            if image.id:
                return image.id
    except Exception as exc:
        logger.warning("获取本地镜像失败，无法访问宿主机文件：%s", exc)
    return None


def _create_helper(client, directory: Path):
    image = _helper_image(client)
    if image is None:
        return None
    return client.containers.create(
        image=image,
        volumes={str(directory): {"bind": _MOUNT_TARGET, "mode": "rw"}},
        labels={_HELPER_LABEL: _HELPER_LABEL_VALUE},
    )


def _read_from_helper(helper, name: str) -> str | None:
    stream, _ = helper.get_archive(f"{_MOUNT_TARGET}/{name}")
    raw = b"".join(stream)
    if not raw:
        return None
    with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            extracted = tar.extractfile(member)
            if extracted is not None:
                return extracted.read().decode("utf-8")
    return None


def _write_to_helper(helper, name: str, content: str, mode: int) -> None:
    data = content.encode("utf-8")
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        info = tarfile.TarInfo(name=name)
        info.size = len(data)
        info.mode = mode
        info.uid = 0
        info.gid = 0
        info.mtime = int(time.time())
        tar.addfile(info, io.BytesIO(data))
    buffer.seek(0)
    helper.put_archive(_MOUNT_TARGET, buffer)


def _update_via_helper(
    path: Path, updater: Callable[[str], str | None], mode: int
) -> bool:
    client = _client()
    if client is None:
        return False
    helper = None
    try:
        helper = _create_helper(client, path.parent)
        if helper is None:
            return False
        try:
            original = _read_from_helper(helper, path.name)
        except Exception:
            # 文件不存在是正常情况（conf 路径可能还没初始化），不告警
            logger.debug("宿主机文件不存在或不可读：%s", path)
            return False
        if original is None:
            return False
        updated = updater(original)
        if updated is None or updated == original:
            return False
        _write_to_helper(helper, path.name, updated, mode)
        return True
    except Exception as exc:
        logger.warning("通过辅助容器更新宿主机文件失败 %s：%s", path, exc)
        return False
    finally:
        if helper is not None:
            try:
                helper.remove(force=True)
            except Exception:
                logger.warning("清理宿主机文件辅助容器失败", exc_info=True)


def update_text(
    path: str | Path,
    updater: Callable[[str], str | None],
    mode: int = 0o600,
) -> bool:
    """读取宿主机文本文件并写回更新结果。

    ``updater`` 返回 None 表示放弃更新，返回相同内容表示无需写入。
    Returns:
        是否真正写入了文件。
    """
    target = Path(path)
    try:
        original = target.read_text(encoding="utf-8")
    except OSError:
        return _update_via_helper(target, updater, mode)

    updated = updater(original)
    if updated is None or updated == original:
        return False
    try:
        target.write_text(updated, encoding="utf-8")
        return True
    except OSError:
        return _update_via_helper(target, updater, mode)
