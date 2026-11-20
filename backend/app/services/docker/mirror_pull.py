"""通过加速域名拉取镜像，并把镜像改回原始 tag。

Docker 的 ``registry-mirrors`` 只对 Docker Hub 生效，ghcr.io 等仓库必须
改写镜像引用。这里通过 Docker 原生事件流报告真实下载进度，再用
``docker tag`` 改回原仓库名，部署和资源镜像都复用同一套逻辑。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque
from collections.abc import Callable

from docker import auth, utils

from app.services.docker.image_service import (
    _consume_pull_stream,
    _format_bytes,
    _select_mirror,
)
from app.services.docker.manager import docker_manager
from app.services.system.registry_mirror_service import (
    begin_acceleration_pull,
    end_acceleration_pull,
)

logger = logging.getLogger(__name__)

PULL_TIMEOUT = 1800
"""单次拉取的最长耗时（秒）。"""

PULL_STALL_TIMEOUT = 60.0
"""连续多少秒没有下载进度就放弃当前镜像源。"""

PULL_MIN_SPEED = 50 * 1024
"""平均下载速度低于该值（字节/秒）时放弃当前镜像源。"""

PULL_SPEED_WINDOW = 30.0
"""计算平均下载速度的时间窗口（秒）。"""

PULL_WATCH_INTERVAL = 1.0
"""看门狗检查进度的时间间隔（秒）。"""

MIRROR_RETRY_DELAY = 15.0
"""加速地址全部失败后，等待多久再重试一轮。"""

PullProgress = Callable[[dict], None]

# 这些状态的层已经下载完，日志里打勾
_LAYER_DONE_STATUSES = {
    "Download complete",
    "Pull complete",
    "Already exists",
    "Layer already exists",
}
_LAYER_BAR_WIDTH = 20


class _PullWatcher:
    """跟踪拉取进度，识别停滞、低速和整体超时。

    Docker 的事件流在下载停滞时不会主动报错，客户端只能靠进度判断。
    这里用已下载字节的滑动窗口计算平均速度；持续低于阈值、长时间没有
    事件或超过总超时时，由调用方关闭连接并切换到下一个镜像源。
    """

    def __init__(self, timeout: float) -> None:
        self.last_activity = time.monotonic()
        self.deadline = self.last_activity + timeout
        self.timeout = timeout
        self.downloading = False
        self.samples: deque[tuple[float, int]] = deque()

    def observe(self, progress: dict) -> None:
        now = time.monotonic()
        self.last_activity = now
        self.downloading = any(
            layer.get("status") == "Downloading"
            for layer in progress.get("layers") or []
        )
        downloaded = int(progress.get("downloaded_size") or 0)
        if not self.samples or now - self.samples[-1][0] >= PULL_WATCH_INTERVAL:
            self.samples.append((now, downloaded))
        while (
            self.samples
            and now - self.samples[0][0] > PULL_SPEED_WINDOW + PULL_WATCH_INTERVAL
        ):
            self.samples.popleft()

    def check(self) -> str | None:
        now = time.monotonic()
        if now >= self.deadline:
            return f"镜像拉取超时（{int(self.timeout)} 秒），已切换镜像源"
        if now - self.last_activity >= PULL_STALL_TIMEOUT:
            return (
                f"镜像下载停滞超过 {int(PULL_STALL_TIMEOUT)} 秒，"
                "已切换镜像源"
            )
        # 只有正在下载的层才参与速度判断；解压、等待阶段不误杀
        if not self.downloading or len(self.samples) < 2:
            return None
        span = now - self.samples[0][0]
        if span < PULL_SPEED_WINDOW:
            return None
        speed = (self.samples[-1][1] - self.samples[0][1]) / span
        if speed < PULL_MIN_SPEED:
            return (
                f"镜像下载速度过低（{_format_bytes(int(speed))}/s），"
                "已切换镜像源"
            )
        return None


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
    except asyncio.CancelledError:
        proc.kill()
        await proc.wait()
        raise
    return proc.returncode or 0, stderr.decode("utf-8", errors="replace").strip()


def _open_pull_stream(client, image: str):
    api = client.api
    repository, image_tag = utils.parse_repository_tag(image)
    registry, _ = auth.resolve_repository_name(repository)
    headers = {}
    header = auth.get_config_header(api, registry)
    if header:
        headers["X-Registry-Auth"] = header
    response = api._post(
        api._url("/images/create"),
        params={"tag": image_tag or "latest", "fromImage": repository},
        headers=headers,
        stream=True,
        # 没有数据时让读线程按时超时退出，避免连接永久挂住
        timeout=PULL_STALL_TIMEOUT + 5,
    )
    api._raise_for_status(response)
    return response, api._stream_helper(response, decode=True)


def _drain_task_exception(task: asyncio.Task) -> None:
    """提前返回时取走后台任务的异常，避免“未取回异常”告警。"""
    if task.cancelled():
        return
    try:
        task.exception()
    except Exception:
        pass


class _PullAborted(Exception):
    """看门狗要求放弃当前镜像源。"""


async def _drain_worker(worker: asyncio.Task, *, timeout: float) -> None:
    """等待下载线程退出；超时也不阻塞部署。"""
    try:
        await asyncio.wait_for(asyncio.shield(worker), timeout=timeout)
    except asyncio.TimeoutError:
        pass
    except asyncio.CancelledError:
        raise
    except Exception:
        pass


async def _close_response_async(response) -> None:
    """在独立线程里关闭连接，避免读线程的锁把事件循环卡死。"""
    try:
        await asyncio.to_thread(response.close)
    except Exception:
        logger.debug("关闭下载连接失败", exc_info=True)


_closing_tasks: set[asyncio.Task] = set()


def _close_in_background(response) -> None:
    """后台关闭连接；持有任务引用，避免被 GC 时任务还没跑完。"""
    task = asyncio.create_task(_close_response_async(response))
    _closing_tasks.add(task)
    task.add_done_callback(_closing_tasks.discard)


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
    response = None
    cancelled = threading.Event()
    watcher = _PullWatcher(timeout)
    abort_reason: list[str] = []

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
        nonlocal last_progress, response
        try:
            response, stream = _open_pull_stream(client, image)
            if cancelled.is_set():
                return

            def forward(data: dict) -> None:
                nonlocal last_progress
                if cancelled.is_set():
                    raise _PullAborted
                last_progress = data
                watcher.observe(data)
                emit(data)

            _consume_pull_stream(stream, forward)
        finally:
            # 只能在读线程里关闭：其他线程在 recv 阻塞时关闭会卡住整个事件循环
            if response is not None:
                try:
                    response.close()
                except Exception:
                    logger.debug("关闭下载连接失败", exc_info=True)

    def abort(reason: str) -> None:
        # 只设取消标记，由下载线程在下一个进度事件或 socket 读超时后自行退出
        abort_reason.append(reason)
        cancelled.set()

    worker = asyncio.create_task(asyncio.to_thread(consume))
    worker.add_done_callback(_drain_task_exception)
    try:
        while True:
            done, _ = await asyncio.wait(
                {worker}, timeout=PULL_WATCH_INTERVAL
            )
            if done:
                break
            reason = watcher.check()
            if reason is None:
                continue
            abort(reason)
            # 下载线程可能还卡在 recv，最多等一会儿，不能拖住整个服务
            await _drain_worker(worker, timeout=5)
            break
    except asyncio.CancelledError:
        cancelled.set()
        current = response
        if current is not None:
            _close_in_background(current)
        await _drain_worker(worker, timeout=5)
        raise

    if abort_reason:
        return 1, abort_reason[0]
    if worker.done() and not worker.cancelled():
        error = worker.exception()
        if error is not None:
            return 1, str(error)

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


def _mirror_targets(
    image: str, acceleration: dict[str, list[str]] | None
) -> list[tuple[str, tuple[str, str]]]:
    targets: list[tuple[str, tuple[str, str]]] = []
    for source, urls in (acceleration or {}).items():
        for url in urls:
            target, tag_target = _select_mirror(image, {source: [url]})
            if (
                target != image
                and tag_target is not None
                and target not in {item[0] for item in targets}
            ):
                targets.append((target, tag_target))
    return targets


async def pull_with_mirror(
    image: str,
    acceleration: dict[str, list[str]] | None,
    *,
    timeout: int = PULL_TIMEOUT,
    progress: PullProgress | None = None,
    allow_direct: bool = True,
) -> bool:
    """依次从加速域名拉取并改回原 tag，可按调用方要求禁止回退直连。

    下载期间会保留当前加速域名：授权刷新触发的缓存失效会延后到
    本次下载结束，避免中途切换域名把正在进行的下载打断。

    单个地址下载停滞、速度过低或整体超时都会切到下一个地址；所有
    加速地址都失败时会短暂等待后重试一轮，应对新发布 tag 的缓存延迟。
    """
    begin_acceleration_pull()
    try:
        return await _pull_with_mirror(
            image,
            acceleration,
            timeout=timeout,
            progress=progress,
            allow_direct=allow_direct,
        )
    finally:
        end_acceleration_pull()


async def _pull_with_mirror(
    image: str,
    acceleration: dict[str, list[str]] | None,
    *,
    timeout: int,
    progress: PullProgress | None,
    allow_direct: bool,
) -> bool:
    code, _ = await _docker("image", "inspect", image, timeout=30)
    if code == 0:
        # 本地已有该镜像时与 compose up 的行为保持一致，不主动刷新
        return True

    targets = _mirror_targets(image, acceleration)
    if targets:
        if await _pull_targets(
            image, targets, timeout=timeout, progress=progress
        ):
            return True
        logger.info(
            "%d 个加速地址均失败，%.0f 秒后重试一次：%s",
            len(targets),
            MIRROR_RETRY_DELAY,
            image,
        )
        await asyncio.sleep(MIRROR_RETRY_DELAY)
        if await _pull_targets(
            image, targets, timeout=timeout, progress=progress
        ):
            return True
        if not allow_direct:
            logger.warning(
                "%d 个加速地址重试后仍失败，禁止回退源站：%s",
                len(targets),
                image,
            )
            return False

    code, error = await _docker_pull(image, timeout=timeout, progress=progress)
    if code != 0:
        logger.warning("拉取镜像失败 %s：%s", image, error[:200])
    return code == 0


async def _pull_targets(
    image: str,
    targets: list[tuple[str, tuple[str, str]]],
    *,
    timeout: int,
    progress: PullProgress | None,
) -> bool:
    """依次尝试加速地址，成功后把镜像改回原始 tag。"""
    for target, tag_target in targets:
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
                "通过加速域名 %s 拉取 %s 失败，尝试下一个地址：%s",
                target,
                image,
                error[:200],
            )
    return False
