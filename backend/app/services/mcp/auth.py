"""MCP 专用访问令牌与 ASGI 鉴权。"""

import base64
import hashlib
import secrets
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit

import sqlalchemy as sa
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import settings
from app.core.exceptions import APIException
from app.database import async_session
from app.models.mcp import McpAccessToken, McpToolCallLog
from app.models.user import User


@dataclass(frozen=True)
class McpActor:
    user_id: int
    token_id: int
    username: str
    request_host: str


_actor_var: ContextVar[McpActor | None] = ContextVar("mcp_actor", default=None)


def current_mcp_actor() -> McpActor | None:
    return _actor_var.get()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _fernet() -> Fernet:
    digest = hashlib.sha256(
        f"mcp-token:{settings.SECRET_KEY}".encode("utf-8")
    ).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _encrypt_token(raw_token: str) -> str:
    return _fernet().encrypt(raw_token.encode("utf-8")).decode("ascii")


def decrypt_access_token(record: McpAccessToken) -> str | None:
    if not record.token_value:
        return None
    try:
        return _fernet().decrypt(record.token_value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None


def _request_host(scope: Scope) -> str:
    headers = {key.lower(): value for key, value in scope.get("headers", [])}
    raw = headers.get(b"x-forwarded-host", headers.get(b"host", b""))
    host = raw.decode("latin-1").split(",")[0].strip()
    return urlsplit(f"//{host}").hostname or "127.0.0.1"


async def create_access_token(
    db: AsyncSession,
    *,
    user: User,
    name: str,
    expires_at: datetime | None,
) -> tuple[McpAccessToken, str]:
    if user.role != "admin":
        raise APIException("需要管理员权限", 403)
    clean_name = name.strip()
    if not clean_name:
        raise APIException("请输入令牌名称", 400)
    if len(clean_name) > 100:
        raise APIException("令牌名称不能超过 100 个字符", 400)
    if expires_at is not None and _aware(expires_at) <= _now():
        raise APIException("过期时间必须晚于当前时间", 400)

    raw_token = f"otmcp_{secrets.token_urlsafe(36)}"
    record = McpAccessToken(
        name=clean_name,
        prefix=raw_token[:16],
        token_hash=_hash(raw_token),
        token_value=_encrypt_token(raw_token),
        user_id=user.id,
        expires_at=_aware(expires_at),
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record, raw_token


ACCESS_KEY_NAME = "MCP 访问密钥"


async def revoke_access_token(
    db: AsyncSession, token_id: int, user: User
) -> McpAccessToken:
    record = await db.get(McpAccessToken, token_id)
    if record is None or record.user_id != user.id:
        raise APIException("令牌不存在", 404)
    if record.revoked_at is None:
        record.revoked_at = _now()
        await db.commit()
    return record


async def _detach_audit_logs(db: AsyncSession, token_ids: list[int]) -> None:
    if not token_ids:
        return
    await db.execute(
        sa.update(McpToolCallLog)
        .where(McpToolCallLog.token_id.in_(token_ids))
        .values(token_id=None)
    )


async def _delete_user_tokens(
    db: AsyncSession, user: User, keep_id: int | None = None
) -> None:
    query = sa.select(McpAccessToken.id).where(McpAccessToken.user_id == user.id)
    if keep_id is not None:
        query = query.where(McpAccessToken.id != keep_id)
    token_ids = list((await db.execute(query)).scalars().all())
    if not token_ids:
        return
    await _detach_audit_logs(db, token_ids)
    await db.execute(
        sa.delete(McpAccessToken).where(McpAccessToken.id.in_(token_ids))
    )


def _is_usable_key(record: McpAccessToken) -> bool:
    if record.revoked_at is not None or decrypt_access_token(record) is None:
        return False
    expires_at = _aware(record.expires_at)
    return expires_at is None or expires_at > _now()


async def _create_access_key(
    db: AsyncSession, user: User
) -> McpAccessToken:
    raw_token = f"otmcp_{secrets.token_urlsafe(36)}"
    record = McpAccessToken(
        name=ACCESS_KEY_NAME,
        prefix=raw_token[:16],
        token_hash=_hash(raw_token),
        token_value=_encrypt_token(raw_token),
        user_id=user.id,
        expires_at=None,
    )
    db.add(record)
    return record


async def get_or_create_access_key(
    db: AsyncSession, user: User
) -> McpAccessToken:
    if user.role != "admin":
        raise APIException("需要管理员权限", 403)
    result = await db.execute(
        sa.select(McpAccessToken)
        .where(McpAccessToken.user_id == user.id)
        .order_by(McpAccessToken.id.desc())
    )
    records = list(result.scalars().all())
    current = next(
        (record for record in records if _is_usable_key(record)), None
    )
    if current is not None:
        await _delete_user_tokens(db, user, keep_id=current.id)
        await db.commit()
        return current
    await _delete_user_tokens(db, user)
    record = await _create_access_key(db, user)
    await db.commit()
    await db.refresh(record)
    return record


async def reset_access_key(db: AsyncSession, user: User) -> McpAccessToken:
    if user.role != "admin":
        raise APIException("需要管理员权限", 403)
    await _delete_user_tokens(db, user)
    record = await _create_access_key(db, user)
    await db.commit()
    await db.refresh(record)
    return record


async def authenticate_access_token(
    db: AsyncSession, raw_token: str, request_host: str
) -> McpActor | None:
    result = await db.execute(
        sa.select(McpAccessToken).where(
            McpAccessToken.token_hash == _hash(raw_token)
        )
    )
    record = result.scalar_one_or_none()
    if record is None or record.revoked_at is not None:
        return None
    expires_at = _aware(record.expires_at)
    if expires_at is not None and expires_at <= _now():
        return None
    user = await db.get(User, record.user_id)
    if user is None or user.role != "admin":
        return None
    record.last_used_at = _now()
    await db.commit()
    return McpActor(
        user_id=record.user_id,
        token_id=record.id,
        username=user.username,
        request_host=request_host,
    )


class McpAuthMiddleware:
    """在 MCP 子应用边界逐请求校验管理员令牌。"""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async with async_session() as db:
            headers = {key.lower(): value for key, value in scope.get("headers", [])}
            authorization = headers.get(b"authorization", b"").decode("latin-1")
            scheme, _, raw_token = authorization.partition(" ")
            actor = None
            if scheme.lower() == "bearer" and raw_token.strip():
                actor = await authenticate_access_token(
                    db, raw_token.strip(), _request_host(scope)
                )
            if actor is None:
                response = JSONResponse(
                    status_code=401,
                    content={"error": "unauthorized", "message": "MCP 令牌无效或已过期"},
                    headers={"WWW-Authenticate": "Bearer"},
                )
                await response(scope, receive, send)
                return

        token = _actor_var.set(actor)
        try:
            await self.app(scope, receive, send)
        finally:
            _actor_var.reset(token)
