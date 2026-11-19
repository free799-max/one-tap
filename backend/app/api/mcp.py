"""MCP 聚合管理 API，协议端点独立挂载在 /mcp。"""

from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.custom_route import CustomAPIRoute
from app.core.exceptions import APIException
from app.core.security import require_admin
from app.database import get_db
from app.models.mcp import McpAccessToken
from app.models.user import User
from app.schemas.mcp import (
    McpAuditOut,
    McpCatalogDetailOut,
    McpCatalogOut,
    McpConfigOut,
    McpConfigUpdate,
    McpConnectOut,
    McpKeyOut,
    McpUpstreamOut,
    McpUpstreamToolsOut,
)
from app.services.mcp.audit import list_tool_call_logs
from app.services.mcp.catalog import build_mcp_catalog, get_mcp_catalog_entry
from app.services.mcp.auth import (
    McpActor,
    authenticate_access_token,
    decrypt_access_token,
    get_or_create_access_key,
    reset_access_key,
)
from app.services.mcp.connect import build_client_config_json, build_connect_prompt
from app.services.mcp.skills import build_skills_bundle
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


def _request_base_url(request: Request) -> str:
    proto = (
        request.headers.get("x-forwarded-proto") or request.url.scheme or "http"
    ).split(",")[0].strip()
    host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or request.url.netloc
        or ""
    ).split(",")[0].strip()
    return f"{proto or 'http'}://{host or '127.0.0.1'}"


def _masked_key(key: str) -> str:
    return f"{key[:12]}••••••{key[-8:]}"


def _upstream_out(state: UpstreamState) -> McpUpstreamOut:
    return McpUpstreamOut(
        instance_id=state.app.instance_id,
        app_name=state.app.app_name,
        display_name=state.app.display_name,
        instance_name=state.app.instance_name,
        status=state.app.status,
        prefix=state.prefix,
        available=state.available,
        unavailable_reason=state.unavailable_reason,
    )


def _key_out(record: McpAccessToken) -> McpKeyOut:
    key = decrypt_access_token(record)
    if not key:
        raise APIException("密钥读取失败，请重置密钥", 500)
    return McpKeyOut(
        key=key,
        masked_key=_masked_key(key),
        created_at=record.created_at,
        last_used_at=record.last_used_at,
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


@router.get("/key", response_model=McpKeyOut)
async def get_mcp_access_key(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    record = await get_or_create_access_key(db, current_user)
    return _key_out(record)


@router.post("/key/reset", response_model=McpKeyOut)
async def reset_mcp_access_key(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    record = await reset_access_key(db, current_user)
    return _key_out(record)


@router.get("/connect", response_model=McpConnectOut)
async def get_mcp_connect_info(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    record = await get_or_create_access_key(db, current_user)
    key = decrypt_access_token(record)
    if not key:
        raise APIException("密钥读取失败，请重置密钥", 500)
    endpoint = f"{_request_base_url(request)}/mcp"
    skills_url = f"{_request_base_url(request)}/api/mcp/skills"
    return McpConnectOut(
        endpoint=endpoint,
        masked_key=_masked_key(key),
        config_json=build_client_config_json(endpoint, key),
        prompt=build_connect_prompt(endpoint, key, skills_url),
    )


@router.get("/audit", response_model=list[McpAuditOut])
async def list_mcp_audit(
    limit: int = Query(100, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    return await list_tool_call_logs(
        db, user_id=current_user.id, limit=limit, local_only=True
    )


@router.get("/skills")
async def get_skills_for_admin(
    current_user: User = Depends(require_admin),
):
    return build_skills_bundle()


skills_router = APIRouter(prefix="/api/mcp", tags=["mcp"], route_class=CustomAPIRoute)


async def require_mcp_actor(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> McpActor:
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    host = (request.headers.get("host") or "").split(":")[0]
    actor = await authenticate_access_token(db, token, host) if token else None
    if actor is None:
        raise APIException("MCP 密钥无效", 401)
    return actor


@skills_router.get("/skills")
async def get_skills_bundle(actor: McpActor = Depends(require_mcp_actor)):
    return build_skills_bundle()
