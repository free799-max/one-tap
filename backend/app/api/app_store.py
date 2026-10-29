"""应用商店 API 路由模块。

应用定义由配套管理端（onetap-manage）同步到本地只读缓存，
本模块只提供查询、部署预览与一键部署能力。
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user, require_admin
from app.database import get_db
from app.models.user import User
from app.schemas.app_store import (
    AppDeployRequest,
    AppDetailOut,
    AppOut,
    AppPreviewResponse,
)
from app.schemas.orchestration.deploy_task import DeployTaskCreateResponse
from app.services import catalog_sync
from app.services.app_store import app_service

from app.core.custom_route import CustomAPIRoute

router = APIRouter(prefix="/api/apps", tags=["apps"], route_class=CustomAPIRoute)


@router.get("", response_model=list[AppOut])
async def list_apps(
    category: str | None = None,
    tag: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出应用商店应用。"""
    return await app_service.list_apps(db, category=category, tag=tag)


@router.get("/categories")
async def list_categories(current_user: User = Depends(get_current_user)):
    """应用分类字典，来源为管理端同步结果；未同步时返回空列表。"""
    return catalog_sync.get_categories()


@router.get("/{name}", response_model=AppDetailOut)
async def get_app(
    name: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取应用详情。"""
    return await app_service.get_app(db, name)


@router.post("/{name}/preview", response_model=AppPreviewResponse)
async def preview_app(
    name: str,
    data: AppDeployRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """预览应用渲染后的 Compose YAML。"""
    result = await app_service.preview(
        db,
        app_name=name,
        instance_name=data.instance_name,
        config=data.config,
    )
    return result


@router.post("/{name}/deploy", response_model=DeployTaskCreateResponse)
async def deploy_app(
    name: str,
    data: AppDeployRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """一键部署应用（仅管理员），返回部署任务 ID。"""
    instance, task_id = await app_service.deploy(
        db,
        app_name=name,
        instance_name=data.instance_name,
        config=data.config,
        user_id=current_user.id,
    )

    return DeployTaskCreateResponse(
        task_id=task_id,
        instance_id=instance.id,
        status="deploying",
    )
