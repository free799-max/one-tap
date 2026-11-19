import json

import httpx
import pytest
from fastmcp import Client, FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.proxy import ProxyClient
from httpx import ASGITransport

from app.main import app
from app.services.mcp.auth import create_access_token
from app.services.mcp.config import mcp_config_service
from app.services.mcp.server import mcp_aggregator


async def _raw_token(mcp_env):
    async with mcp_env["session"]() as db:
        await mcp_config_service.set_enabled(db, True)
        _, raw = await create_access_token(
            db, user=mcp_env["admin"], name="protocol", expires_at=None
        )
        return raw


def _transport(raw_token, asgi_app=app, endpoint="/mcp"):
    def factory(*, headers=None, auth=None, follow_redirects=True, timeout=None, **kwargs):
        return httpx.AsyncClient(
            transport=ASGITransport(app=asgi_app),
            base_url="http://test",
            headers=headers,
            follow_redirects=follow_redirects,
            timeout=timeout or 30,
        )

    return StreamableHttpTransport(
        f"http://test{endpoint}",
        headers={"Authorization": f"Bearer {raw_token}"},
        httpx_client_factory=factory,
    )


@pytest.mark.asyncio
async def test_real_streamable_http_protocol_without_standard_response(mcp_env):
    raw = await _raw_token(mcp_env)
    async with mcp_aggregator.lifespan():
        async with Client(_transport(raw)) as client:
            assert [t for t in await client.list_tools() if t.name.startswith('moviepilot_')] == []

        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as raw_client:
            response = await raw_client.post(
                "/mcp",
                headers={
                    "Authorization": f"Bearer {raw}",
                    "Accept": "application/json, text/event-stream",
                },
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            )
    assert response.status_code == 200
    assert '"success":true' not in response.text.replace(" ", "")
    assert "jsonrpc" in response.text


@pytest.mark.asyncio
async def test_protocol_requires_valid_bearer_and_enabled_server(mcp_env):
    raw = await _raw_token(mcp_env)
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/mcp", json={})
        assert response.status_code == 401
        response = await client.post(f"/mcp?token={raw}", json={})
        assert response.status_code == 401
        response = await client.post(
            "/mcp", headers={"Authorization": "Bearer invalid"}, json={}
        )
        assert response.status_code == 401

    async with mcp_env["session"]() as db:
        await mcp_config_service.set_enabled(db, False)
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/mcp", headers={"Authorization": f"Bearer {raw}"}, json={}
        )
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_protocol_remote_tool_call_is_audited(
    mcp_env, moviepilot, monkeypatch
):
    upstream = FastMCP("MoviePilot")

    @upstream.tool(name="media.search")
    async def media_search(query: str):
        return {"results": [{"title": f"result:{query}"}]}

    monkeypatch.setattr(
        "app.services.mcp.upstream.build_upstream_client",
        lambda state: ProxyClient(upstream),
    )
    raw = await _raw_token(mcp_env)
    tool_name = f"moviepilot_{moviepilot.id}_media.search"

    from app.services.mcp.server import McpAggregator

    aggregator = McpAggregator()
    async with aggregator.lifespan():
        async with Client(_transport(raw, aggregator.asgi_app, "/")) as client:
            names = {tool.name for tool in await client.list_tools()}
            assert tool_name in names
            result = await client.call_tool(tool_name, {"query": "demo"})
            assert (
                json.loads(result.content[0].text)["results"][0]["title"]
                == "result:demo"
            )

    from app.services.mcp.audit import list_tool_call_logs

    async with mcp_env["session"]() as db:
        logs = await list_tool_call_logs(db, user_id=mcp_env["admin"].id)
    assert len(logs) == 1
    assert logs[0].tool_name == tool_name
    assert logs[0].source == "remote"
    assert logs[0].success is True
    assert "Authorization" not in logs[0].error_message
