"""通过加速域名拉取镜像，并把镜像改回原始 tag。

Docker 的 ``registry-mirrors`` 只对 Docker Hub 生效，ghcr.io 等仓库必须
改写镜像引用。这里通过 Docker 原生事件流报告真实下载进度，再用
``docker tag`` 改回原仓库名，部署和资源镜像都复用同一套逻辑。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from app.services.docker.image_service import (
    _consume_pull_stream,
    _format_bytes,
    _select_mirror,
)
from app.services.docker.manager import docker_manager

logger = logging.getLogger(__name__)

PULL_TIMEOUT = 1800
PullProgress = Callable[[dict], None]

# 这些状态的层已经下载完，日志里打勾
_LAYER_DONE_STATUSES = {
    "Download complete",
    "Pull complete",
    "Already exists",
    "Layer already exists",
}
_LAYER_BAR_WIDTH = 20


def _progress_bar(percentage: int) -> str:
    percentage = max(0, min(100, percentage))
    filled = int(percentage / 100 * _LAYER_BAR_WIDTH)
    return "[" + "=" * filled + " " * (_LAYER_BAR_WIDTH - filled) + "]"


def _format_layer_line(layer: dict) -> str:
    """单个镜像层一行，风格对齐 ``docker compose pull``。"""
    layer_id = layer.get("id", "")
    status = layer.get("status", "")
    status_text = layer.get("status_text") or status
    if status in _LAYER_DONE_STATUSES:
        return f"{layer_id} ✔ {status_text}"
    if status == "Downloading":
        size = (
            f"{_format_bytes(layer['current'])} / {_format_bytes(layer['total'])}"
            if layer.get("total")
            else "--"
        )
        line = f"{layer_id} ⠸ {status_text} {_progress_bar(layer.get('percentage', 0))} {size}"
        if layer.get("speed"):
            line += f" · {_format_bytes(layer['speed'])}/s"
        return line
    return f"{layer_id} ⠸ {status_text}"


def _format_pull_block(image: str, progress: dict) -> str:
    """把一帧拉取快照渲染成多行进度块：表头 + 每个层一行。

    前端按行覆盖同标识的旧行，所以每次推送整块内容，界面只保留
    每个镜像/每个层的最新状态，不会越滚越长。
    """
    header = [
        f"[+] 拉取 {image} "
        f"{progress.get('completed_layers', 0)}/{progress.get('total_layers', 0)}"
    ]
    size_text = progress.get("size_text", "--")
    if progress.get("determinate"):
        header.append(f"{progress.get('percentage', 0)}%")
    if size_text and size_text != "--":
        header.append(size_text)
    if progress.get("speed"):
        header.append(f"{_format_bytes(progress['speed'])}/s")
    lines = [" · ".join(header)]
    lines.extend(
        _format_layer_line(layer) for layer in progress.get("layers", [])
    )
    return "\n".join(lines)


def _format_pull_progress(progress: dict, completed: bool = False) -> str:
    size_text = progress.get("size_text", "--")
    if completed or progress.get("determinate", True):
        percentage = 100 if completed else progress.get("percentage", 0)
        text = f"镜像下载 {percentage}% · {size_text}"
    else:
        text = "镜像下载 准备中" if size_text == "--" else f"镜像下载 {size_text}"
        unknown = progress.get("unknown_layers", 0)
        if unknown:
            text += f" · {unknown} 层等待中"
    speed = 0 if completed else progress.get("speed", 0)
    if speed > 0:
        text += f" · {_format_bytes(speed)}/s"
    return text


async def _docker(*args: str, timeout: int = PULL_TIMEOUT) -> tuple[int, str]:
    """执行 docker CLI，返回 (退出码, stderr)。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return 127, "未找到 docker 命令"
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, f"docker {args[0]} 执行超时"
    return proc.returncode or 0, stderr.decode("utf-8", errors="replace").strip()


async def _docker_pull(
    image: str,
    *,
    timeout: int,
    progress: PullProgress | None,
) -> tuple[int, str]:
    if progress is None:
        return await _docker("pull", image, timeout=timeout)
    client = docker_manager._client
    if client is None:
        return 127, "Docker 不可用"
    last_progress = None
    last_text = None

    def emit(data: dict) -> None:
        nonlocal last_text
        text = _format_pull_progress(data, completed=data.get("percentage") == 100)
        if text == last_text:
            return
        try:
            progress(data)
            last_text = text
        except Exception:
            logger.exception("镜像下载进度回调异常")

    def consume() -> None:
        nonlocal last_progress
        stream = client.api.pull(image, stream=True, decode=True)

        def forward(data: dict) -> None:
            nonlocal last_progress
            last_progress = data
            emit(data)

        _consume_pull_stream(stream, forward)

    try:
        await asyncio.to_thread(consume)
    except Exception as exc:
        return 1, str(exc)

    if last_progress is None:
        emit({"percentage": 100, "size_text": "--", "speed": 0, "determinate": True})
    else:
        final = dict(last_progress)
        total_size = final.get("total_size", 0)
        if total_size:
            size = _format_bytes(total_size)
            final["size_text"] = f"{size} / {size}"
        final["percentage"] = 100
        final["determinate"] = True
        final["speed"] = 0
        emit(final)
    return 0, ""


async def pull_with_mirror(
    image: str,
    acceleration: dict[str, list[str]] | None,
    *,
    timeout: int = PULL_TIMEOUT,
    progress: PullProgress | None = None,
) -> bool:
    """优先从加速域名拉取并改回原 tag；失败时回退直连。

    返回是否成功。加速域名拉取失败不会抛出异常，交给直连或 compose
    自己处理，避免单点故障阻断部署。
    """
    code, _ = await _docker("image", "inspect", image, timeout=30)
    if code == 0:
        # 本地已有该镜像时与 compose up 的行为保持一致，不主动刷新
        return True

    target, tag_target = _select_mirror(image, acceleration)
    if target != image and tag_target is not None:
        code, error = await _docker_pull(
            target, timeout=timeout, progress=progress
        )
        if code == 0:
            repository, tag = tag_target
            tag_code, tag_error = await _docker(
                "tag", target, f"{repository}:{tag}", timeout=60
            )
            if tag_code == 0:
                # 只删除加速地址下的临时 tag，镜像本身仍由原始 tag 引用
                await _docker("rmi", target, timeout=60)
                return True
            logger.warning(
                "加速域名镜像改 tag 失败 %s -> %s:%s：%s",
                target,
                repository,
                tag,
                tag_error,
            )
        else:
            logger.info(
                "通过加速域名 %s 拉取 %s 失败，回退直连：%s",
                target,
                image,
                error[:200],
            )

    code, error = await _docker_pull(image, timeout=timeout, progress=progress)
    if code != 0:
        logger.warning("拉取镜像失败 %s：%s", image, error[:200])
    return code == 0
