import json

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.proxy import ProxyClient

from app.services.mcp.auth import McpActor
from app.services.mcp.config import mcp_config_service
from app.services.mcp.server import McpAggregator


def _moviepilot_server():
    upstream = FastMCP("MoviePilot")

    @upstream.tool(name="media.search")
    async def media_search(query: str):
        return {"results": [{"title": f"result:{query}"}]}

    @upstream.tool(name="subscription.add")
    async def subscription_add(name: str):
        return {"id": 7, "name": name}

    @upstream.resource("movie://secret")
    async def secret_resource():
        return "secret-resource"

    @upstream.prompt(name="secret_prompt")
    async def secret_prompt():
        return "secret-prompt"

    return upstream


async def _enable_moviepilot(mcp_env, instance_id, allowed_tools):
    async with mcp_env["session"]() as db:
        await mcp_config_service.set_enabled(db, True)
        await mcp_config_service.update_upstream(
            db,
            instance_id=instance_id,
            host="test",
            enabled=True,
            allowed_tools=allowed_tools,
        )


@pytest.mark.asyncio
async def test_local_and_remote_tools_are_aggregated(
    mcp_env, moviepilot, monkeypatch
):
    upstream = _moviepilot_server()
    monkeypatch.setattr(
        "app.services.mcp.upstream.build_upstream_client",
        lambda state: ProxyClient(upstream),
    )
    await _enable_moviepilot(mcp_env, moviepilot.id, ["media.search"])
    aggregator = McpAggregator()

    async with Client(aggregator.server) as client:
        tools = await client.list_tools()
        names = {tool.name for tool in tools}
        remote_name = f"moviepilot_{moviepilot.id}_media.search"
        assert remote_name in names
        assert f"moviepilot_{moviepilot.id}_subscription.add" not in names
        assert "media_targets_list" in names

        result = await client.call_tool(remote_name, {"query": "demo"})
        assert json.loads(result.content[0].text)["results"][0]["title"] == "result:demo"
        with pytest.raises(ToolError, match="白名单"):
            await client.call_tool(
                f"moviepilot_{moviepilot.id}_subscription.add", {"name": "demo"}
            )


@pytest.mark.asyncio
async def test_explicitly_allowed_write_tool_can_call_remote(
    mcp_env, moviepilot, monkeypatch
):
    upstream = _moviepilot_server()
    monkeypatch.setattr(
        "app.services.mcp.upstream.build_upstream_client",
        lambda state: ProxyClient(upstream),
    )
    await _enable_moviepilot(mcp_env, moviepilot.id, ["subscription.add"])
    aggregator = McpAggregator()

    async with Client(aggregator.server) as client:
        result = await client.call_tool(
            f"moviepilot_{moviepilot.id}_subscription.add", {"name": "demo"}
        )
        assert json.loads(result.content[0].text)["id"] == 7


@pytest.mark.asyncio
async def test_global_disable_hides_and_rejects_all_tools(mcp_env, moviepilot):
    aggregator = McpAggregator()
    async with Client(aggregator.server) as client:
        assert await client.list_tools() == []
        with pytest.raises(ToolError):
            await client.call_tool("media_targets_list", {})


@pytest.mark.asyncio
async def test_successful_call_is_audited_without_arguments(
    mcp_env, monkeypatch
):
    actor = McpActor(user_id=mcp_env["admin"].id, token_id=1, username="admin", request_host="test")
    monkeypatch.setattr("app.services.mcp.audit.current_mcp_actor", lambda: actor)
    async with mcp_env["session"]() as db:
        await mcp_config_service.set_enabled(db, True)
    aggregator = McpAggregator()

    async with Client(aggregator.server) as client:
        await client.call_tool("media_targets_list", {})

    from app.services.mcp.audit import list_tool_call_logs

    async with mcp_env["session"]() as db:
        logs = await list_tool_call_logs(db, user_id=mcp_env["admin"].id)
    assert len(logs) == 1
    assert logs[0].tool_name == "media_targets_list"
    assert logs[0].source == "local"
    assert logs[0].success is True


@pytest.mark.asyncio
async def test_resources_and_prompts_are_never_exposed(
    mcp_env, moviepilot, monkeypatch
):
    upstream = _moviepilot_server()
    monkeypatch.setattr(
        "app.services.mcp.upstream.build_upstream_client",
        lambda state: ProxyClient(upstream),
    )
    await _enable_moviepilot(mcp_env, moviepilot.id, [])
    aggregator = McpAggregator()

    async with Client(aggregator.server) as client:
        assert await client.list_resources() == []
        assert await client.list_resource_templates() == []
        assert await client.list_prompts() == []
        with pytest.raises(Exception, match="未开放"):
            await client.read_resource(f"movie://moviepilot_{moviepilot.id}/secret")
        with pytest.raises(Exception, match="未开放"):
            await client.get_prompt(f"moviepilot_{moviepilot.id}_secret_prompt")


@pytest.mark.asyncio
async def test_local_call_does_not_enumerate_remote_upstreams(
    mcp_env, moviepilot, monkeypatch
):
    upstream = _moviepilot_server()
    monkeypatch.setattr(
        "app.services.mcp.upstream.build_upstream_client",
        lambda state: ProxyClient(upstream),
    )
    await _enable_moviepilot(mcp_env, moviepilot.id, ["media.search"])
    from app.services.mcp.upstream import UpstreamFastMCPProxy

    discoveries = []
    original = UpstreamFastMCPProxy.get_tools

    async def tracked(self):
        discoveries.append(self.name)
        return await original(self)

    monkeypatch.setattr(UpstreamFastMCPProxy, "get_tools", tracked)
    aggregator = McpAggregator()
    async with Client(aggregator.server) as client:
        await client.call_tool("media_targets_list", {})
    assert discoveries == []


@pytest.mark.asyncio
async def test_deleted_instance_mount_and_config_are_reconciled(mcp_env, moviepilot):
    await _enable_moviepilot(mcp_env, moviepilot.id, ["media.search"])
    aggregator = McpAggregator()
    await aggregator.ensure_upstreams()
    assert len(aggregator.server._mounted_servers) == 1

    from app.models.orchestration import AppInstance

    async with mcp_env["session"]() as db:
        instance = await db.get(AppInstance, moviepilot.id)
        assert instance is not None
        await db.delete(instance)
        await db.commit()
    await aggregator.ensure_upstreams()

    assert aggregator.server._mounted_servers == []
    assert aggregator._mounted == {}
    async with mcp_env["session"]() as db:
        config = await mcp_config_service.get_config(db)
        assert config["upstreams"] == {}
