"""通过加速域名拉取镜像，并把镜像改回原始 tag。

Docker 的 ``registry-mirrors`` 只对 Docker Hub 生效，ghcr.io 等仓库必须
改写镜像引用。这里统一用 CLI 拉取加速地址下的镜像，再 ``docker tag``
回原仓库名，部署和资源镜像都复用同一套逻辑。
"""

from __future__ import annotations

import asyncio
import logging

from app.services.docker.image_service import _select_mirror

logger = logging.getLogger(__name__)

PULL_TIMEOUT = 1800


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


async def pull_with_mirror(
    image: str,
    acceleration: dict[str, list[str]] | None,
    *,
    timeout: int = PULL_TIMEOUT,
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
        code, error = await _docker("pull", target, timeout=timeout)
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

    code, error = await _docker("pull", image, timeout=timeout)
    if code != 0:
        logger.warning("拉取镜像失败 %s：%s", image, error[:200])
    return code == 0
