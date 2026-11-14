import pytest

from app.services.mcp.config import mcp_config_service
from app.services.mcp.upstream import _http_client_factory, build_upstream_client, upstream_url


@pytest.mark.asyncio
async def test_moviepilot_url_and_headers_are_isolated(mcp_env, moviepilot):
    async with mcp_env["session"]() as db:
        state = await mcp_config_service.get_upstream(db, moviepilot.id, "test")
    assert upstream_url(state) == "http://moviepilot:3001/api/v1/mcp"

    factory = _http_client_factory("server-side-key")
    async with factory(
        headers={
            "Authorization": "Bearer user-token",
            "Cookie": "session=secret",
            "X-API-KEY": "server-side-key",
        }
    ) as client:
        assert client.headers["X-API-KEY"] == "server-side-key"
        assert "Authorization" not in client.headers
        assert "Cookie" not in client.headers
        assert client.follow_redirects is False

    upstream_client = build_upstream_client(state)
    assert upstream_client.transport.headers["X-API-KEY"] == "mp-secret"


@pytest.mark.asyncio
async def test_host_cannot_be_used_to_redirect_upstream_credentials(
    mcp_env, moviepilot
):
    from app.models.orchestration import AppInstance
    from app.core.exceptions import APIException

    async with mcp_env["session"]() as db:
        instance = await db.get(AppInstance, moviepilot.id)
        assert instance is not None
        instance.config = {"host_port": 3001, "api_key": "mp-secret"}
        await db.commit()
        state = await mcp_config_service.get_upstream(db, moviepilot.id, "attacker.example")
        assert state.app.url == "http://attacker.example:3001"
        assert state.available is False
        with pytest.raises(APIException, match="持久化访问地址"):
            upstream_url(state)


@pytest.mark.asyncio
async def test_failed_discovery_has_deadline_and_backoff(monkeypatch):
    import time
    from fastmcp.exceptions import ToolError
    from app.services.mcp.upstream import UpstreamFastMCPProxy

    monkeypatch.setattr("app.services.mcp.upstream.UPSTREAM_DISCOVERY_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr("app.services.mcp.upstream.UPSTREAM_FAILURE_BACKOFF_SECONDS", 1)

    async def unavailable_client():
        import asyncio

        await asyncio.sleep(1)
        raise AssertionError("不应到达这里")

    proxy = UpstreamFastMCPProxy(client_factory=unavailable_client)
    started = time.monotonic()
    with pytest.raises(ToolError, match="暂时不可用"):
        await proxy.get_tools()
    with pytest.raises(ToolError, match="请稍后重试"):
        await proxy.get_tools()
    assert time.monotonic() - started < 0.6
