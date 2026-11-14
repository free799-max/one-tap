import time

import pytest

from app.services.mcp.audit import (
    list_tool_call_logs,
    record_tool_call,
    sanitize_error,
)
from app.services.mcp.auth import McpActor


def test_error_sanitizer_covers_common_credentials():
    text = sanitize_error(
        "https://user:pass@example.com/mcp?token=query-secret "
        "api_key=key-secret Cookie: session=cookie-secret Bearer bearer-secret"
    )
    for secret in ("pass", "query-secret", "key-secret", "cookie-secret", "bearer-secret"):
        assert secret not in text
    assert "REDACTED" in text


@pytest.mark.asyncio
async def test_remote_exception_arguments_are_not_persisted(mcp_env):
    actor = McpActor(user_id=mcp_env["admin"].id, token_id=1, username="admin", request_host="test")
    await record_tool_call(
        tool_name="moviepilot_1_media.search",
        source="remote",
        upstream_instance_id=1,
        success=False,
        error=RuntimeError("search failed with token=argument-secret"),
        started_at=time.monotonic(),
        actor=actor,
    )
    async with mcp_env["session"]() as db:
        logs = await list_tool_call_logs(db, user_id=mcp_env["admin"].id)
    assert logs[0].source == "remote"
    assert logs[0].upstream_instance_id == 1
    assert "argument-secret" not in logs[0].error_message
    assert "search failed" not in logs[0].error_message
    assert "RuntimeError" in logs[0].error_message
