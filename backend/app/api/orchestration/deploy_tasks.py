"""部署任务 API 路由模块。"""

import asyncio
import json
import logging
import queue

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.core.custom_route import CustomAPIRoute
from app.core.exceptions import APIException
from app.core.security import get_current_user, get_current_user_sse
from app.models.user import User
from app.schemas.orchestration.deploy_task import DeployTaskStatus
from app.services.orchestration.deploy_task_service import deploy_task_manager, sanitize_meta

logger = logging.getLogger(__name__)
router = APIRouter(route_class=CustomAPIRoute)


@router.get("/deploy-tasks", response_model=list[str])
async def list_deploy_tasks(
    current_user: User = Depends(get_current_user),
):
    """列出进行中的部署任务 ID，供前端刷新或重新登录后恢复进度。"""
    return deploy_task_manager.list_active_task_ids()


@router.get("/deploy-tasks/{task_id}/events")
async def deploy_task_events(
    task_id: str,
    current_user: User = Depends(get_current_user_sse),
):
    """获取部署任务实时进度（SSE）。"""
    task = deploy_task_manager.get_task(task_id)
    if not task:
        raise APIException("任务不存在", 404)

    async def event_generator():
        # 先回放历史进度，避免客户端连接较晚时错过早期日志
        history = deploy_task_manager.get_task_history(task_id)
        for entry in history:
            yield f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"
            if entry.get("_task_status") in ("completed", "failed", "cancelled"):
                return

        q = deploy_task_manager.register_listener(task_id)
        try:
            while True:
                try:
                    progress = await asyncio.to_thread(q.get, timeout=1)
                    yield f"data: {json.dumps(progress, ensure_ascii=False)}\n\n"
                    if progress.get("_task_status") in ("completed", "failed", "cancelled"):
                        return
                except queue.Empty:
                    pass

                current_task = deploy_task_manager.get_task(task_id)
                if not current_task:
                    break
                if current_task["status"] in ("completed", "failed", "cancelled"):
                    _final = current_task["progress"].copy()
                    _final["_task_status"] = current_task["status"]
                    if current_task.get("error"):
                        _final["_error"] = current_task["error"]
                    _final["_meta"] = sanitize_meta(current_task.get("meta"))
                    yield f"data: {json.dumps(_final, ensure_ascii=False)}\n\n"
                    break
        finally:
            deploy_task_manager.unregister_listener(task_id, q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/deploy-tasks/{task_id}/cancel", response_model=DeployTaskStatus)
async def cancel_deploy_task(
    task_id: str,
    current_user: User = Depends(get_current_user),
):
    """停止支持取消的部署任务。"""
    from app.services.orchestration import orchestration_service

    task = deploy_task_manager.get_task(task_id)
    if task and task.get("type") == "app_upgrade":
        await orchestration_service.cancel_upgrade(task_id)
    else:
        await orchestration_service.cancel_provisioning(task_id)
    return deploy_task_manager.get_task(task_id)


@router.get("/deploy-tasks/{task_id}/status", response_model=DeployTaskStatus)
async def deploy_task_status(
    task_id: str,
    current_user: User = Depends(get_current_user),
):
    """获取部署任务当前状态。"""
    task = deploy_task_manager.get_task(task_id)
    if not task:
        raise APIException("任务不存在", 404)
    return task
