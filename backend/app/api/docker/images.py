"""Docker 镜像及拉取任务相关 API。"""

import asyncio
import json
import logging
import queue
import threading
import time

import docker
from fastapi import APIRouter, Depends, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.custom_route import CustomAPIRoute
from app.core.exceptions import APIException
from app.core.security import get_current_user, get_current_user_sse, require_admin
from app.database import get_db
from app.models.docker import DockerMirrorConfig
from app.models.system_config import SystemConfig
from app.models.user import User
from app.schemas.docker import (
    BatchImageDeleteRequest,
    ImageDetail,
    ImageInfo,
    ImagePruneResult,
    ImagePullRequest,
    ImageSearchResult,
    ImageTag,
    PullTaskResponse,
    PullTaskStatus,
)
from app.services.docker import docker_manager, task_manager
from app.services.docker.image_service import ImageTagsUnavailable
from app.services.system.registry_mirror_service import registry_mirror_service

logger = logging.getLogger(__name__)
router = APIRouter(route_class=CustomAPIRoute)


@router.get("/images", response_model=list[ImageInfo])
async def list_images(current_user: User = Depends(get_current_user)):
    """获取本地镜像列表。"""
    return await docker_manager.run_sync(docker_manager.list_images)


@router.delete("/images/{image_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_image(
    image_id: str,
    force: bool = False,
    current_user: User = Depends(require_admin),
):
    """删除指定镜像。"""
    try:
        await docker_manager.run_sync(docker_manager.remove_image, image_id, force=force)
    except docker.errors.ImageNotFound:
        raise APIException("镜像不存在", 404)
    except docker.errors.APIError as e:
        raise APIException(f"删除镜像失败: {e}", 500)


@router.get("/images/{image_id}/detail", response_model=ImageDetail)
async def get_image_detail(
    image_id: str,
    current_user: User = Depends(get_current_user),
):
    """获取镜像完整元数据。"""
    if not await docker_manager.run_sync(docker_manager.check_available):
        raise APIException("Docker 不可用", 503)
    detail = await docker_manager.run_sync(docker_manager.get_image_detail, image_id)
    if not detail:
        raise APIException("镜像不存在", 404)
    return detail


@router.post("/images/prune", response_model=ImagePruneResult)
async def prune_images(current_user: User = Depends(require_admin)):
    """移除所有未使用的镜像。"""
    if not await docker_manager.run_sync(docker_manager.check_available):
        raise APIException("Docker 不可用", 503)
    try:
        return await docker_manager.run_sync(docker_manager.prune_unused_images)
    except RuntimeError:
        raise APIException("Docker 不可用", 503)
    except Exception as e:
        raise APIException(f"移除未使用镜像失败: {e}", 500)


@router.get("/images/search")
async def search_images(
    q: str,
    page: int = 1,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """搜索镜像。"""
    try:
        result = await db.execute(
            select(DockerMirrorConfig).where(DockerMirrorConfig.is_default == True)
        )
        config = result.scalar_one_or_none()
        if config:
            return await docker_manager.search_images(
                q,
                page=page,
                api_url=config.search_api_url,
                mirror_url=config.mirror_url if config.enable_mirror else None,
                username=config.username,
                password=config.password,
            )
        return await docker_manager.search_images(q, page=page)
    except RuntimeError as e:
        raise APIException(str(e), 503) from e
    except Exception as e:
        raise APIException(f"镜像搜索失败: {e}", 500) from e


_MANAGED_MIRRORS_TTL = 600
"""管理端推荐镜像源的内存缓存时长（秒）。"""

_managed_mirrors_cache: tuple[float, list[str]] | None = None
"""最近一次从管理端获取的镜像加速地址。"""


async def _managed_tag_mirrors() -> list[str]:
    """从管理端获取推荐镜像加速地址，未配置管理端时返回空列表。"""
    global _managed_mirrors_cache
    now = time.monotonic()
    if _managed_mirrors_cache and now - _managed_mirrors_cache[0] < _MANAGED_MIRRORS_TTL:
        return _managed_mirrors_cache[1]

    try:
        payload = await registry_mirror_service.fetch_remote()
    except Exception:
        logger.info("从管理端获取镜像加速地址失败", exc_info=True)
        return []

    urls = [
        str(item.get("url"))
        for item in payload.get("mirrors") or []
        if item.get("url")
    ]
    _managed_mirrors_cache = (now, urls)
    return urls


async def _tag_mirrors(db: AsyncSession) -> list[str]:
    """标签获取的备用源：优先用设置里的镜像加速地址，未配置时用管理端推荐。"""
    try:
        result = await db.execute(select(SystemConfig).where(SystemConfig.id == 1))
        config = result.scalar_one_or_none()
    except Exception:
        logger.warning("读取镜像加速配置失败", exc_info=True)
        config = None

    configured = list(config.docker_registry_mirrors or []) if config else []
    if configured:
        return configured
    return await _managed_tag_mirrors()


@router.get("/images/tags", response_model=list[ImageTag])
async def get_image_tags(
    image: str,
    refresh: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取指定镜像的可用标签列表。

    Docker Hub 不可达时按镜像加速地址重试；仍失败则返回 502 与具体原因，
    避免前端只看到空列表而不知道发生了什么。
    """
    try:
        return await docker_manager.get_image_tags(
            image,
            refresh=refresh,
            mirror_provider=lambda: _tag_mirrors(db),
        )
    except ImageTagsUnavailable as e:
        raise APIException(f"获取镜像标签失败：{e}", 502) from e


@router.post("/images/pull", response_model=PullTaskResponse)
async def pull_image(
    data: ImagePullRequest,
    current_user: User = Depends(require_admin),
):
    """启动镜像拉取任务。"""
    if not await docker_manager.run_sync(docker_manager.check_available):
        raise APIException("Docker 不可用", 503)

    try:
        task_id = task_manager.create_task(data.image)
    except RuntimeError as e:
        raise APIException(str(e), 429)

    thread = threading.Thread(
        target=docker_manager.pull_image_async,
        args=(data.image, task_id, task_manager),
        daemon=True,
    )
    thread.start()

    return {
        "task_id": task_id,
        "image": data.image,
        "status": "pulling",
    }


@router.get("/images/pull/{task_id}/events")
async def pull_image_events(
    task_id: str,
    current_user: User = Depends(get_current_user_sse),
):
    """获取镜像拉取任务实时进度（SSE）。"""
    task = task_manager.get_task(task_id)
    if not task:
        raise APIException("任务不存在", 404)

    async def event_generator():
        _initial = task["progress"].copy()
        if task["status"] in ("completed", "failed"):
            _initial["_task_status"] = task["status"]
            if task.get("error"):
                _initial["_error"] = task["error"]
        yield f"data: {json.dumps(_initial, ensure_ascii=False)}\n\n"

        q = task_manager.register_listener(task_id)
        try:
            while True:
                try:
                    progress = await asyncio.to_thread(q.get, True, 1)
                    yield f"data: {json.dumps(progress, ensure_ascii=False)}\n\n"
                except queue.Empty:
                    pass

                current_task = task_manager.get_task(task_id)
                if not current_task:
                    break
                if current_task["status"] in ("completed", "failed"):
                    _final = current_task["progress"].copy()
                    _final["_task_status"] = current_task["status"]
                    if current_task.get("error"):
                        _final["_error"] = current_task["error"]
                    yield f"data: {json.dumps(_final, ensure_ascii=False)}\n\n"
                    break

                await asyncio.sleep(0.1)
        finally:
            task_manager.unregister_listener(task_id, q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/images/pull/{task_id}/status", response_model=PullTaskStatus)
async def pull_image_status(
    task_id: str,
    current_user: User = Depends(get_current_user),
):
    """获取镜像拉取任务当前状态。"""
    task = task_manager.get_task(task_id)
    if not task:
        raise APIException("任务不存在", 404)
    return task


@router.post("/images/batch-delete")
async def batch_delete_images(
    data: BatchImageDeleteRequest,
    current_user: User = Depends(require_admin),
):
    """批量删除本地镜像。"""
    if not await docker_manager.run_sync(docker_manager.check_available):
        raise APIException("Docker 不可用", 503)
    if not data.ids:
        return {"deleted": [], "failed": []}
    try:
        return await docker_manager.run_sync(docker_manager.remove_images, data.ids, force=data.force)
    except RuntimeError:
        raise APIException("Docker 不可用", 503)
    except Exception as e:
        raise APIException(f"批量删除镜像失败: {e}", 500)
