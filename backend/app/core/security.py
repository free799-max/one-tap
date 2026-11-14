"""安全与认证模块。

提供密码哈希（bcrypt）、JWT 令牌签发与校验、
以及 FastAPI 依赖注入用的当前用户获取。
"""

from datetime import datetime, timedelta, timezone

import bcrypt as _bcrypt
from jose import JWTError, jwt
from fastapi import Depends, Query, status

from app.core.exceptions import APIException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db

# HTTP Bearer 令牌提取器
security = HTTPBearer()
# 可选的 Bearer 提取器（无 Header 时返回 None，不自动抛 403）
security_optional = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    """使用 bcrypt 对密码进行哈希处理。"""
    return _bcrypt.hashpw(password.encode(), _bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    """验证明文密码是否与哈希值匹配。"""
    return _bcrypt.checkpw(plain.encode(), hashed.encode())


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """签发 JWT 访问令牌。

    Args:
        data: 令牌载荷数据，通常包含 {"sub": "<user_id>"}。
        expires_delta: 可选的短时有效期；缺省使用系统配置的登录有效期。

    Returns:
        编码后的 JWT 字符串。
    """
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm="HS256")


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
):
    """FastAPI 依赖注入：从请求头中提取并验证 JWT，返回当前用户对象。

    验证失败时抛出 401 异常。
    """
    from app.models.user import User

    try:
        # 解码 JWT 令牌
        payload = jwt.decode(credentials.credentials, settings.SECRET_KEY, algorithms=["HS256"])
        sub = payload.get("sub")
        if sub is None:
            raise APIException("认证失败", 401)
        # 提取用户 ID
        try:
            user_id = int(sub)
        except (ValueError, TypeError):
            raise APIException("认证失败", 401)
    except JWTError:
        raise APIException("认证失败", 401)

    # 查询用户是否存在
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise APIException("认证失败", 401)
    return user


async def require_admin(
    current_user=Depends(get_current_user),
):
    """FastAPI 依赖注入：要求当前用户为管理员。

    非管理员时抛出 403 异常。
    """
    if current_user.role != "admin":
        raise APIException("需要管理员权限", 403)
    return current_user


async def get_current_user_sse(
    credentials: HTTPAuthorizationCredentials | None = Depends(security_optional),
    token: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """FastAPI 依赖注入（SSE 专用）：从请求头或 URL 参数中提取并验证 JWT。

    EventSource 不支持自定义请求头，因此支持通过 `?token=` 查询参数传递令牌。
    验证失败时抛出 401 异常。
    """
    from app.models.user import User

    jwt_token = None
    if credentials is not None:
        jwt_token = credentials.credentials
    elif token:
        jwt_token = token

    if not jwt_token:
        raise APIException("认证失败", 401)

    try:
        payload = jwt.decode(jwt_token, settings.SECRET_KEY, algorithms=["HS256"])
        sub = payload.get("sub")
        if sub is None:
            raise APIException("认证失败", 401)
        user_id = int(sub)
    except (JWTError, ValueError, TypeError):
        raise APIException("认证失败", 401)

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    # SSE 连接会一直持有请求直到流结束，鉴权完成后立即释放数据库会话，
    # 避免长连接占用连接池
    await db.close()
    if user is None:
        raise APIException("认证失败", 401)
    return user
