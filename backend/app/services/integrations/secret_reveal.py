"""已保存敏感信息的二次验证与限时查看授权。"""

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt

from app.config import settings
from app.core.exceptions import APIException
from app.core.security import verify_password
from app.models.user import User

SECRET_REVEAL_TTL_SECONDS = 3 * 60 * 60
_SECRET_REVEAL_TOKEN_TYPE = "integration-secret-reveal"
_SECRET_REVEAL_KEY_PURPOSE = "OneTap integration secret reveal"


@dataclass(frozen=True)
class SecretRevealGrant:
    token: str
    expires_at: datetime


def _signing_key() -> bytes:
    return hashlib.sha256(
        f"{settings.SECRET_KEY}:{_SECRET_REVEAL_KEY_PURPOSE}".encode()
    ).digest()


def _invalid_grant() -> APIException:
    return APIException("查看授权无效或已过期，请重新输入账号密码", 403)


def authorize_secret_reveal(
    user: User,
    login_password: str | None = None,
    reveal_token: str | None = None,
) -> SecretRevealGrant:
    """验证登录密码或已有授权，返回固定三小时有效的查看凭证。"""
    if reveal_token:
        try:
            payload = jwt.decode(
                reveal_token, _signing_key(), algorithms=["HS256"]
            )
            if (
                payload.get("token_type") != _SECRET_REVEAL_TOKEN_TYPE
                or payload.get("sub") != str(user.id)
            ):
                raise _invalid_grant()
            expires_at = datetime.fromtimestamp(payload["exp"], timezone.utc)
            return SecretRevealGrant(reveal_token, expires_at)
        except (JWTError, KeyError, TypeError, ValueError, OSError) as exc:
            raise _invalid_grant() from exc

    if not login_password:
        raise APIException("请输入当前账号密码", 400)
    if not verify_password(login_password, user.hashed_password):
        raise APIException("账号密码错误", 403)

    now = datetime.now(timezone.utc).replace(microsecond=0)
    expires_at = now + timedelta(seconds=SECRET_REVEAL_TTL_SECONDS)
    token = jwt.encode(
        {
            "sub": str(user.id),
            "token_type": _SECRET_REVEAL_TOKEN_TYPE,
            "iat": now,
            "exp": expires_at,
        },
        _signing_key(),
        algorithm="HS256",
    )
    return SecretRevealGrant(token, expires_at)
