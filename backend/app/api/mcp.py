"""MCP 聚合管理 API，协议端点独立挂载在 /mcp。"""

from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.custom_route import CustomAPIRoute
from app.core.exceptions import APIException
from app.core.security import require_admin
from app.database import get_db
from app.models.user import User
from app.schemas.mcp import (
    McpApiOperationsUpdate,
    McpAuditOut,
    McpCatalogDetailOut,
    McpCatalogOut,
    McpConfigOut,
    McpConfigUpdate,
    McpLocalToolOut,
    McpLocalToolsUpdate,
    McpTokenCreate,
    McpTokenCreatedOut,
    McpTokenOut,
    McpUpstreamOut,
    McpUpstreamToolsOut,
    McpUpstreamUpdate,
)
from app.services.mcp.audit import list_tool_call_logs
from app.services.mcp.catalog import build_mcp_catalog, get_mcp_catalog_entry
from app.services.mcp.auth import (
    create_access_token,
    list_access_tokens,
    revoke_access_token,
)
from app.services.mcp.config import UpstreamState, mcp_config_service
from app.services.mcp.upstream import discover_upstream_tools


router = APIRouter(
    prefix="/api/settings/mcp",
    tags=["settings", "mcp"],
    route_class=CustomAPIRoute,
)


def _request_host(request: Request) -> str:
    raw = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    host = raw.split(",")[0].strip()
    return urlsplit(f"//{host}").hostname or "127.0.0.1"


def _upstream_out(state: UpstreamState) -> McpUpstreamOut:
    return McpUpstreamOut(
        instance_id=state.app.instance_id,
        app_name=state.app.app_name,
        display_name=state.app.display_name,
        instance_name=state.app.instance_name,
        status=state.app.status,
        prefix=state.prefix,
        enabled=state.enabled,
        available=state.available,
        unavailable_reason=state.unavailable_reason,
        allowed_tools=list(state.allowed_tools),
    )


@router.get("", response_model=McpConfigOut)
async def get_mcp_config(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    config = await mcp_config_service.get_config(db)
    return McpConfigOut(enabled=config["enabled"])


@router.put("", response_model=McpConfigOut)
async def update_mcp_config(
    data: McpConfigUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    config = await mcp_config_service.set_enabled(db, data.enabled)
    return McpConfigOut(enabled=config["enabled"])


@router.get("/upstreams", response_model=list[McpUpstreamOut])
async def list_mcp_upstreams(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    states = await mcp_config_service.list_upstreams(db, _request_host(request))
    return [_upstream_out(state) for state in states]


@router.put("/upstreams/{instance_id}", response_model=McpUpstreamOut)
async def update_mcp_upstream(
    instance_id: int,
    data: McpUpstreamUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    state = await mcp_config_service.update_upstream(
        db,
        instance_id=instance_id,
        host=_request_host(request),
        enabled=data.enabled,
        allowed_tools=data.allowed_tools,
    )
    return _upstream_out(state)


@router.post("/upstreams/{instance_id}/tools", response_model=McpUpstreamToolsOut)
async def test_mcp_upstream(
    instance_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    state = await mcp_config_service.get_upstream(db, instance_id, _request_host(request))
    tools = await discover_upstream_tools(state)
    return McpUpstreamToolsOut(tools=tools)


@router.get("/local-tools", response_model=list[McpLocalToolOut])
async def list_mcp_local_tools(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    return await mcp_config_service.local_tools(db)


@router.put("/local-tools", response_model=list[McpLocalToolOut])
async def update_mcp_local_tools(
    data: McpLocalToolsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    return await mcp_config_service.update_local_tools(db, data.enabled_tools)


@router.get("/catalog", response_model=McpCatalogOut)
async def list_mcp_catalog(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    return await build_mcp_catalog(db, _request_host(request))


@router.get("/catalog/detail", response_model=McpCatalogDetailOut)
async def get_mcp_catalog_detail(
    request: Request,
    entry_id: str = Query(min_length=1, max_length=256),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    entry = await get_mcp_catalog_entry(db, _request_host(request), entry_id)
    if entry is None:
        raise APIException("接口目录条目不存在", 404)
    return entry


@router.put("/api-operations", response_model=list[str])
async def update_mcp_api_operations(
    data: McpApiOperationsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    config = await mcp_config_service.update_api_operations(db, data.operation_ids)
    return config["api_operation_ids"]


@router.get("/tokens", response_model=list[McpTokenOut])
async def list_mcp_tokens(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    return await list_access_tokens(db, current_user)


@router.post("/tokens", response_model=McpTokenCreatedOut)
async def create_mcp_token(
    data: McpTokenCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    record, raw_token = await create_access_token(
        db,
        user=current_user,
        name=data.name,
        expires_at=data.expires_at,
    )
    return McpTokenCreatedOut(
        **McpTokenOut.model_validate(record).model_dump(), token=raw_token
    )


@router.delete("/tokens/{token_id}", response_model=McpTokenOut)
async def revoke_mcp_token(
    token_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    return await revoke_access_token(db, token_id, current_user)


@router.get("/audit", response_model=list[McpAuditOut])
async def list_mcp_audit(
    limit: int = Query(100, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    return await list_tool_call_logs(db, user_id=current_user.id, limit=limit)
