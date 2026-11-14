from datetime import datetime, timedelta, timezone

import pytest

from app.services.mcp.auth import (
    authenticate_access_token,
    create_access_token,
    revoke_access_token,
)


@pytest.mark.asyncio
async def test_token_is_hashed_and_authenticates_admin(mcp_env):
    async with mcp_env["session"]() as db:
        record, raw = await create_access_token(
            db, user=mcp_env["admin"], name="LibreChat", expires_at=None
        )
        assert raw.startswith("otmcp_")
        assert raw not in (record.token_hash, record.prefix)
        assert len(record.token_hash) == 64
        actor = await authenticate_access_token(db, raw, "nas.local")
        assert actor is not None
        assert actor.user_id == mcp_env["admin"].id
        assert actor.request_host == "nas.local"
        assert await authenticate_access_token(db, "invalid", "test") is None


@pytest.mark.asyncio
async def test_revoked_and_expired_tokens_are_rejected(mcp_env):
    async with mcp_env["session"]() as db:
        record, raw = await create_access_token(
            db, user=mcp_env["admin"], name="revoke", expires_at=None
        )
        await revoke_access_token(db, record.id, mcp_env["admin"])
        assert await authenticate_access_token(db, raw, "test") is None

        expired, expired_raw = await create_access_token(
            db, user=mcp_env["admin"], name="expired", expires_at=None
        )
        expired.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await db.commit()
        assert await authenticate_access_token(db, expired_raw, "test") is None


@pytest.mark.asyncio
async def test_create_token_rejects_past_expiration(mcp_env):
    async with mcp_env["session"]() as db:
        with pytest.raises(Exception, match="过期时间"):
            await create_access_token(
                db,
                user=mcp_env["admin"],
                name="past",
                expires_at=datetime.now(timezone.utc) - timedelta(days=1),
            )


@pytest.mark.asyncio
async def test_token_is_rejected_when_user_is_no_longer_admin(mcp_env):
    async with mcp_env["session"]() as db:
        _, raw = await create_access_token(
            db, user=mcp_env["admin"], name="role-change", expires_at=None
        )
        from app.models.user import User

        admin = await db.get(User, mcp_env["admin"].id)
        assert admin is not None
        admin.role = "user"
        await db.commit()
        assert await authenticate_access_token(db, raw, "test") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("offset_hours", [8, -5])
async def test_expiration_is_normalized_to_utc(mcp_env, offset_hours):
    from app.schemas.mcp import McpTokenOut

    expected = datetime.now(timezone.utc) + timedelta(days=1)
    supplied = expected.astimezone(timezone(timedelta(hours=offset_hours)))
    async with mcp_env["session"]() as db:
        record, _ = await create_access_token(
            db,
            user=mcp_env["admin"],
            name=f"offset-{offset_hours}",
            expires_at=supplied,
        )
        stored = record.expires_at
        assert stored is not None
        if stored.tzinfo is None:
            stored = stored.replace(tzinfo=timezone.utc)
        assert stored.astimezone(timezone.utc) == expected
        output = McpTokenOut.model_validate(record)
        assert output.expires_at is not None
        assert output.expires_at.utcoffset() == timedelta(0)
        assert output.expires_at == expected
