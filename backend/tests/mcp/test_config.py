import pytest

from app.services.mcp.config import (
    DEFAULT_LOCAL_TOOLS,
    DEFAULT_UPSTREAM_TOOLS,
    public_tool_name,
    split_public_tool_name,
    mcp_config_service,
)


@pytest.mark.asyncio
async def test_defaults_are_disabled_and_read_only(mcp_env):
    async with mcp_env["session"]() as db:
        config = await mcp_config_service.get_config(db)
        assert config["enabled"] is False
        assert tuple(config["local_tools"]) == DEFAULT_LOCAL_TOOLS
        local_tools = await mcp_config_service.local_tools(db)
        assert all(item["read_only"] for item in local_tools if item["enabled"])
        assert all(not item["enabled"] for item in local_tools if not item["read_only"])


@pytest.mark.asyncio
async def test_empty_local_tool_list_is_preserved(mcp_env):
    async with mcp_env["session"]() as db:
        await mcp_config_service.update_local_tools(db, [])
        assert (await mcp_config_service.get_config(db))["local_tools"] == []


@pytest.mark.asyncio
async def test_upstream_defaults_and_empty_allowlist(mcp_env, moviepilot):
    async with mcp_env["session"]() as db:
        state = await mcp_config_service.get_upstream(db, moviepilot.id, "test")
        assert state.enabled is False
        assert state.allowed_tools == DEFAULT_UPSTREAM_TOOLS
        assert state.prefix == f"moviepilot_{moviepilot.id}"
        assert state.available is True

        state = await mcp_config_service.update_upstream(
            db,
            instance_id=moviepilot.id,
            host="test",
            enabled=True,
            allowed_tools=[],
        )
        assert state.enabled is True
        assert state.allowed_tools == ()


def test_public_tool_names_include_instance_and_native_name():
    name = public_tool_name("moviepilot_12", "media.search")
    assert name == "moviepilot_12_media.search"
    assert split_public_tool_name(name) == ("12", "media.search")
    assert split_public_tool_name("media_targets_list") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["bad name", "bad/name", "x" * 97])
async def test_invalid_native_tool_names_are_rejected(mcp_env, moviepilot, name):
    from app.core.exceptions import APIException

    async with mcp_env["session"]() as db:
        with pytest.raises(APIException, match="工具名称格式不正确"):
            await mcp_config_service.update_upstream(
                db,
                instance_id=moviepilot.id,
                host="test",
                enabled=True,
                allowed_tools=[name],
            )
