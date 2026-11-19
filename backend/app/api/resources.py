"""资源域 API：供管理页面搜索资源并创建订阅。"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.custom_route import CustomAPIRoute
from app.core.exceptions import APIException
from app.core.security import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.resources import ResourceSubscribeIn
from app.services.resources import registry

router = APIRouter(
    prefix="/api/resources",
    tags=["resources"],
    route_class=CustomAPIRoute,
)


@router.get("/capabilities")
async def get_capabilities(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await registry.capabilities(db)


@router.get("/search")
async def search_resources(
    domain: str,
    keyword: str = Query(min_length=1, max_length=200),
    instance_id: int | None = None,
    page: int = Query(1, ge=1),
    count: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    term = keyword.strip()
    if not term:
        raise APIException("请输入搜索关键词", 400)
    return await registry.search(
        db,
        domain=domain,
        keyword=term,
        instance_id=instance_id,
        page=page,
        count=count,
        sort="quality",
    )


@router.post("/subscribe")
async def subscribe_resource(
    payload: ResourceSubscribeIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await registry.subscribe(
        db,
        domain=payload.domain,
        item_id=payload.item_id,
        options=payload.options,
        instance_id=payload.instance_id,
    )
