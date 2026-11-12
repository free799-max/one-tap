"""网络加速 API。

客户端只保留订阅同步能力：规则与节点由管理端统一维护，
这里只保存订阅地址、展示同步结果并维护本机 Mihomo。
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.custom_route import CustomAPIRoute
from app.core.exceptions import APIException
from app.core.security import require_admin
from app.database import get_db
from app.models.user import User
from app.schemas.acceleration import (
    AccelActionResult,
    AccelDeployTaskOut,
    AccelManagedOut,
    AccelManagedUpdate,
    AccelMihomoDeploy,
    AccelMihomoStatusOut,
    AccelProxyAppOut,
    AccelProxyAppUpdate,
    AccelSyncSettingsUpdate,
)
from app.services.acceleration import apps as accel_apps
from app.services.acceleration import deployment, managed, mihomo
from app.services.acceleration.sync_scheduler import accel_sync_service

router = APIRouter(
    prefix="/api/acceleration",
    tags=["acceleration"],
    route_class=CustomAPIRoute,
)


@router.get("/managed", response_model=AccelManagedOut)
async def get_managed(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """订阅地址、规则快照与本机运行状态。"""
    return await managed.get_summary(db)


@router.put("/managed", response_model=AccelActionResult)
async def update_managed(
    payload: AccelManagedUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """保存管理端下发的订阅地址。"""
    await managed.update_subscription_url(db, payload.subscription_url)
    return {"ok": True, "message": "订阅地址已保存", "detail": {}}


@router.put("/managed/sync-settings", response_model=AccelActionResult)
async def update_sync_settings(
    payload: AccelSyncSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    await managed.update_sync_settings(
        db, enabled=payload.enabled, interval_minutes=payload.interval_minutes
    )
    await db.commit()
    accel_sync_service.wake()
    return {
        "ok": True,
        "message": "自动同步配置已保存",
        "detail": {
            "enabled": payload.enabled,
            "interval_minutes": payload.interval_minutes,
        },
    }


@router.post("/managed/sync", response_model=AccelActionResult)
async def sync_managed(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """拉取订阅并写入本机 Mihomo。"""
    return await managed.sync(db, force=True)


@router.get("/mihomo", response_model=AccelMihomoStatusOut)
async def get_mihomo(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    return await mihomo.status_async(db)


@router.post("/mihomo/deploy", response_model=AccelDeployTaskOut)
async def deploy_mihomo(
    payload: AccelMihomoDeploy | None = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    try:
        task_id = await deployment.start_deploy(
            db, app_config=payload.config if payload else None
        )
    except RuntimeError as exc:
        raise APIException(str(exc), 502) from exc
    return {"task_id": task_id, "instance_name": mihomo.PROJECT_NAME}


@router.delete("/mihomo", response_model=AccelActionResult)
async def remove_mihomo(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """关闭所有应用代理后删除 OneTap 托管的 Mihomo。"""
    try:
        result = await deployment.delete_managed(db)
    except RuntimeError as exc:
        raise APIException(str(exc), 502) from exc
    return {
        "ok": True,
        "message": result["message"],
        "detail": result,
    }


@router.get("/apps", response_model=list[AccelProxyAppOut])
async def list_proxy_apps(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """列出支持网络加速的应用实例。"""
    return await accel_apps.list_apps(db)


@router.post("/apps/{instance_id}", response_model=AccelActionResult)
async def set_proxy_app(
    instance_id: int,
    payload: AccelProxyAppUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """开启或关闭某个应用的代理接入。"""
    result = await accel_apps.set_proxy(db, instance_id, payload.enabled)
    await db.commit()
    return {
        "ok": True,
        "message": result["message"],
        "detail": result,
    }
