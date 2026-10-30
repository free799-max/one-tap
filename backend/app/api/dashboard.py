"""服务概览 API 路由。"""

import json
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.custom_route import CustomAPIRoute
from app.core.exceptions import APIException
from app.core.security import get_current_user, get_current_user_sse, require_admin
from app.database import get_db
from app.models.user import User
from app.schemas.dashboard import AppMetricsOut, OverviewAppDetailOut, OverviewOut
from app.schemas.orchestration.deploy_task import DeployTaskCreateResponse
from app.services.orchestration import orchestration_service
from app.services.overview import overview_service

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/dashboard",
    tags=["dashboard"],
    route_class=CustomAPIRoute,
)


@router.get("/overview", response_model=OverviewOut)
async def get_overview(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """返回按分类聚合的已部署应用与自动生成的访问地址。"""
    # 使用浏览器实际访问的主机名生成跳转链接，避免容器内网 IP 不可达
    host = request.headers.get("x-forwarded-host") or request.url.netloc
    return await overview_service.get_overview(db, host=host)


@router.get("/apps/{instance_id}", response_model=OverviewAppDetailOut)
async def get_app_detail(
    instance_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """返回单个应用的容器基本信息，监控指标由独立接口查询。"""
    host = request.headers.get("x-forwarded-host") or request.url.netloc
    return await overview_service.get_app_detail(
        db, instance_id=instance_id, host=host
    )


@router.get("/apps/{instance_id}/metrics", response_model=AppMetricsOut | None)
async def get_app_metrics(
    instance_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """返回运行中容器的实时监控指标。"""
    return await overview_service.get_app_metrics(db, instance_id=instance_id)


@router.get("/apps/{instance_id}/logs/stream")
async def stream_app_logs(
    instance_id: int,
    tail: int = 200,
    follow: bool = True,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_sse),
):
    """流式返回应用实例的部署日志（SSE）。

    有 Compose 项目时输出 ``docker compose logs``，导入的外部容器回退到容器日志。
    """
    tail = max(1, min(tail, 5000))
    stream = await overview_service.open_log_stream(
        db, instance_id, tail=tail, follow=follow
    )

    async def event_generator():
        try:
            async for line in stream:
                payload = {"line": line}
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        except APIException as exc:
            yield f"data: {json.dumps({'error': exc.message}, ensure_ascii=False)}\n\n"
        except Exception as exc:  # noqa: BLE001 — 流式接口无法再改状态码
            logger.exception("应用日志流异常")
            yield f"data: {json.dumps({'error': f'日志流异常: {exc}'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/apps/{instance_id}/upgrade", response_model=DeployTaskCreateResponse
)
async def upgrade_app_instance(
    instance_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """手动升级应用市场单独部署的应用：只替换镜像，保留运行时配置。"""
    instance, task_id = await orchestration_service.upgrade_single_instance(
        db,
        instance_id,
        mode="manual",
        user_id=current_user.id,
    )
    return DeployTaskCreateResponse(
        task_id=task_id,
        instance_id=instance.id,
        project_id=instance.project_id,
        status="deploying",
    )
