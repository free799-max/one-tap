import pytest


@pytest.mark.asyncio
async def test_management_config_and_token_lifecycle(mcp_client):
    response = await mcp_client.get("/api/settings/mcp")
    assert response.status_code == 200
    assert response.json()["data"] == {"enabled": False, "endpoint": "/mcp"}

    response = await mcp_client.put("/api/settings/mcp", json={"enabled": True})
    assert response.json()["data"]["enabled"] is True

    response = await mcp_client.post(
        "/api/settings/mcp/tokens", json={"name": "LibreChat", "expires_at": None}
    )
    assert response.status_code == 200
    created = response.json()["data"]
    assert created["token"].startswith("otmcp_")
    assert "token_hash" not in created

    response = await mcp_client.get("/api/settings/mcp/tokens")
    tokens = response.json()["data"]
    assert len(tokens) == 1
    assert "token" not in tokens[0]
    assert "token_hash" not in tokens[0]

    response = await mcp_client.delete(f"/api/settings/mcp/tokens/{created['id']}")
    assert response.json()["data"]["revoked_at"] is not None


@pytest.mark.asyncio
async def test_upstream_management_does_not_expose_credentials(mcp_client, moviepilot):
    response = await mcp_client.get("/api/settings/mcp/upstreams")
    assert response.status_code == 200
    upstream = response.json()["data"][0]
    assert upstream["instance_id"] == moviepilot.id
    assert "api_key" not in upstream
    assert "url" not in upstream

    response = await mcp_client.put(
        f"/api/settings/mcp/upstreams/{moviepilot.id}",
        json={"enabled": True, "allowed_tools": ["media.search"]},
    )
    assert response.json()["data"]["allowed_tools"] == ["media.search"]


@pytest.mark.asyncio
async def test_local_tools_can_all_be_disabled(mcp_client):
    response = await mcp_client.put(
        "/api/settings/mcp/local-tools", json={"enabled_tools": []}
    )
    assert response.status_code == 200
    assert all(not item["enabled"] for item in response.json()["data"])
