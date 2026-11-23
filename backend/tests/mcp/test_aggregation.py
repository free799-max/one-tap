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


@pytest.mark.asyncio
async def test_installed_source_exposes_all_native_tools(
    mcp_env, moviepilot, monkeypatch
):
    upstream = _moviepilot_server()
    monkeypatch.setattr(
        "app.services.mcp.upstream.build_upstream_client",
        lambda state: ProxyClient(upstream),
    )
    aggregator = McpAggregator()

    async with Client(aggregator.server) as client:
        tools = await client.list_tools()
        names = {tool.name for tool in tools if tool.name.startswith('moviepilot_')}
        read_name = f"moviepilot_{moviepilot.id}_media.search"
        write_name = f"moviepilot_{moviepilot.id}_subscription.add"
        assert names == {read_name, write_name}

        result = await client.call_tool(read_name, {"query": "demo"})
        assert json.loads(result.content[0].text)["results"][0]["title"] == "result:demo"
        result = await client.call_tool(write_name, {"name": "demo"})
        assert json.loads(result.content[0].text)["id"] == 7


@pytest.mark.asyncio
async def test_unavailable_source_tools_are_hidden_and_rejected(
    mcp_env, moviepilot, monkeypatch
):
    from app.models.orchestration import AppInstance

    upstream = _moviepilot_server()
    monkeypatch.setattr(
        "app.services.mcp.upstream.build_upstream_client",
        lambda state: ProxyClient(upstream),
    )
    async with mcp_env["session"]() as db:
        instance = await db.get(AppInstance, moviepilot.id)
        assert instance is not None
        instance.status = "stopped"
        await db.commit()
    aggregator = McpAggregator()

    async with Client(aggregator.server) as client:
        assert [t for t in await client.list_tools() if t.name.startswith('moviepilot_')] == []
        with pytest.raises(ToolError, match="来源不可用"):
            await client.call_tool(
                f"moviepilot_{moviepilot.id}_subscription.add", {"name": "demo"}
            )


@pytest.mark.asyncio
async def test_successful_call_is_audited_without_arguments(
    mcp_env, moviepilot, monkeypatch
):
    upstream = _moviepilot_server()
    monkeypatch.setattr(
        "app.services.mcp.upstream.build_upstream_client",
        lambda state: ProxyClient(upstream),
    )
    actor = McpActor(user_id=mcp_env["admin"].id, token_id=1, username="admin", request_host="test")
    monkeypatch.setattr("app.services.mcp.audit.current_mcp_actor", lambda: actor)
    aggregator = McpAggregator()
    tool_name = f"moviepilot_{moviepilot.id}_media.search"

    async with Client(aggregator.server) as client:
        await client.call_tool(tool_name, {"query": "demo"})

    from app.services.mcp.audit import list_tool_call_logs

    async with mcp_env["session"]() as db:
        logs = await list_tool_call_logs(db, user_id=mcp_env["admin"].id)
    assert len(logs) == 1
    assert logs[0].tool_name == tool_name
    assert logs[0].source == "remote"
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
async def test_deleted_instance_mount_and_config_are_reconciled(mcp_env, moviepilot):
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
        states = await mcp_config_service.list_upstreams(db, "test")
        assert states == []
