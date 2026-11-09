"""本机授权管理 API。"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.custom_route import CustomAPIRoute
from app.core.exceptions import APIException
from app.core.security import get_current_user, require_admin
from app.database import get_db
from app.models.user import User
from app.schemas.integration import IntegrationSecretOut, SecretRevealRequest
from app.schemas.license import LicenseActivateRequest, LicenseStatusResponse
from app.services.integrations.secret_reveal import authorize_secret_reveal
from app.services.licensing import licensing_service

router = APIRouter(prefix="/api/license", tags=["license"], route_class=CustomAPIRoute)


@router.get("/status", response_model=LicenseStatusResponse)
async def get_license_status(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await licensing_service.get_status(db)


@router.post("/secret", response_model=IntegrationSecretOut)
async def reveal_license_secret(
    data: SecretRevealRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """通过二次验证后返回本机保存的授权码。"""
    grant = authorize_secret_reveal(
        current_user, data.password, data.reveal_token
    )
    status = await licensing_service.get_status(db, include_license_key=True)
    password = status.get("license_key") or ""
    if not password:
        raise APIException("尚未保存授权码", 404)
    return IntegrationSecretOut(
        password=password,
        reveal_token=grant.token,
        expires_at=grant.expires_at,
    )


@router.post("/activate", response_model=LicenseStatusResponse)
async def activate_license(
    data: LicenseActivateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    return await licensing_service.activate(db, data.license_key, data.device_name)


@router.post("/refresh", response_model=LicenseStatusResponse)
async def refresh_license(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    return await licensing_service.refresh(db)


@router.post("/deactivate", response_model=LicenseStatusResponse)
async def deactivate_license(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    return await licensing_service.deactivate(db)
