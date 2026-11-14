"""MCP 工具调用审计与错误脱敏。"""

import logging
import re
import time

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models.mcp import McpToolCallLog
from app.services.mcp.auth import McpActor, current_mcp_actor


logger = logging.getLogger(__name__)
_SECRET_PATTERN = re.compile(
    r"(?i)(api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|secret)"
    r"([\s\"']*[:=][\s\"']*)([^\s,\"'}]+)"
)
_BEARER_PATTERN = re.compile(r"(?i)Bearer\s+[^\s,]+")
_MCP_TOKEN_PATTERN = re.compile(r"otmcp_[A-Za-z0-9_-]+")
_USERINFO_PATTERN = re.compile(r"://[^\s/:@]+:[^\s/@]+@")
_QUERY_SECRET_PATTERN = re.compile(
    r"(?i)([?&](?:api[_-]?key|token|password|secret)=)[^&\s]+"
)
_COOKIE_PATTERN = re.compile(r"(?i)(?:cookie|set-cookie)\s*[:=][^\r\n]+")
_REMOTE_SAFE_ERRORS = {
    "工具未启用或不在白名单中",
    "原生 MCP 上游暂时不可用",
    "原生 MCP 上游暂时不可用，请稍后重试",
    "原生 MCP 工具调用超时",
    "调用已取消",
}


def sanitize_error(value: object, limit: int = 1000) -> str:
    text = str(value or "")
    text = _BEARER_PATTERN.sub("Bearer [REDACTED]", text)
    text = _MCP_TOKEN_PATTERN.sub("otmcp_[REDACTED]", text)
    text = _USERINFO_PATTERN.sub("://[REDACTED]@", text)
    text = _QUERY_SECRET_PATTERN.sub(r"\1[REDACTED]", text)
    text = _COOKIE_PATTERN.sub("Cookie=[REDACTED]", text)
    text = _SECRET_PATTERN.sub(r"\1\2[REDACTED]", text)
    return text[:limit]


def _audit_error(source: str, error: object) -> str:
    if source == "remote" and error:
        message = str(error)
        if message not in _REMOTE_SAFE_ERRORS:
            return f"{type(error).__name__}：上游工具调用失败"
    return sanitize_error(error)


async def record_tool_call(
    *,
    tool_name: str,
    source: str,
    upstream_instance_id: int | None,
    success: bool,
    error: object = "",
    started_at: float,
    actor: McpActor | None = None,
) -> None:
    current = actor or current_mcp_actor()
    if current is None:
        return
    duration_ms = max(0, int((time.monotonic() - started_at) * 1000))
    try:
        async with async_session() as db:
            db.add(
                McpToolCallLog(
                    user_id=current.user_id,
                    token_id=current.token_id,
                    tool_name=tool_name[:160],
                    source=source,
                    upstream_instance_id=upstream_instance_id,
                    success=success,
                    error_message=_audit_error(source, error),
                    duration_ms=duration_ms,
                )
            )
            await db.commit()
    except Exception:
        logger.warning("写入 MCP 工具调用审计失败", exc_info=True)


async def list_tool_call_logs(
    db: AsyncSession, *, user_id: int, limit: int = 100
) -> list[McpToolCallLog]:
    result = await db.execute(
        sa.select(McpToolCallLog)
        .where(McpToolCallLog.user_id == user_id)
        .order_by(McpToolCallLog.id.desc())
        .limit(limit)
    )
    return list(result.scalars().all())
