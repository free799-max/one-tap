"""管理端目录同步 API。

提供手动触发从配套管理端（onetap-manage）拉取应用分类、应用市场与
自动化配置（编排方案）的入口，供前端顶部栏的同步按钮调用。
"""

from fastapi import APIRouter, Depends

from app.core.custom_route import CustomAPIRoute
from app.core.exceptions import APIException
from app.core.security import require_admin
from app.models.user import User
from app.services import catalog_sync

router = APIRouter(prefix="/api/catalog", tags=["catalog"], route_class=CustomAPIRoute)


@router.post("/sync")
async def sync_catalog(
    current_user: User = Depends(require_admin),
):
    """手动从管理端同步应用市场与自动化配置（仅管理员）。

    分类、应用、方案任一部分失败不影响其余部分；全部失败时返回 502，
    部分失败时结果中的 ``errors`` 会列出失败项。
    """
    result = await catalog_sync.sync_all(mode="manual")
    errors = result.get("errors") or []
    if len(errors) >= 3:
        raise APIException("从管理端同步失败：" + "；".join(errors), 502)
    return result
