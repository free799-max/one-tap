import json

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from httpx import ASGITransport

from app.main import app
from app.services.mcp.auth import create_access_token
from app.services.mcp.config import DEFAULT_LOCAL_TOOLS, mcp_config_service
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
            tools = await client.list_tools()
            assert "media_targets_list" in {tool.name for tool in tools}
            result = await client.call_tool("media_targets_list", {})
            assert result.is_error is False

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
async def test_protocol_catalog_schema_request_and_audit(mcp_env):
    raw = await _raw_token(mcp_env)
    health_id = "health_check_api_health_get"
    async with mcp_env["session"]() as db:
        await mcp_config_service.update_local_tools(
            db, [*DEFAULT_LOCAL_TOOLS, "onetap_api_request"]
        )
        await mcp_config_service.update_api_operations(db, [health_id])

    from app.services.mcp.server import McpAggregator

    aggregator = McpAggregator()
    async with aggregator.lifespan():
        async with Client(_transport(raw, aggregator.asgi_app, "/")) as client:
            names = {tool.name for tool in await client.list_tools()}
            assert {"onetap_api_catalog", "onetap_api_request"} <= names

            catalog_result = await client.call_tool(
                "onetap_api_catalog", {"query": "health", "method": "GET"}
            )
            catalog = json.loads(catalog_result.content[0].text)
            assert catalog["total"] == 1
            assert catalog["items"][0]["name"] == health_id
            assert catalog["items"][0]["authorized"] is True

            detail_result = await client.call_tool(
                "onetap_api_catalog", {"operation_id": health_id}
            )
            detail = json.loads(detail_result.content[0].text)
            assert detail["items"][0]["path"] == "/api/health"
            assert "components" in detail["items"][0]

            request_result = await client.call_tool(
                "onetap_api_request", {"operation_id": health_id}
            )
            response = json.loads(request_result.content[0].text)
            assert response["status_code"] == 200
            assert response["data"]["status"] == "ok"

    from app.services.mcp.audit import list_tool_call_logs

    async with mcp_env["session"]() as db:
        logs = await list_tool_call_logs(db, user_id=mcp_env["admin"].id)
    api_log = next(log for log in logs if log.source == "api")
    assert api_log.tool_name == f"api:{health_id}"
    assert api_log.success is True
    assert "health" in api_log.tool_name
    assert all("Authorization" not in log.error_message for log in logs)
