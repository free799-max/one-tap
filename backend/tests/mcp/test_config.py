import pytest

from app.services.mcp.config import (
    public_tool_name,
    split_public_tool_name,
    mcp_config_service,
)


@pytest.mark.asyncio
async def test_defaults_are_enabled(mcp_env):
    async with mcp_env["session"]() as db:
        config = await mcp_config_service.get_config(db)
        assert config == {"enabled": True}


@pytest.mark.asyncio
async def test_installed_upstream_is_registered_without_switch(mcp_env, moviepilot):
    async with mcp_env["session"]() as db:
        state = await mcp_config_service.get_upstream(db, moviepilot.id, "test")
        assert state.prefix == f"moviepilot_{moviepilot.id}"
        assert state.available is True

        config = await mcp_config_service.set_enabled(db, True)
        assert config == {"enabled": True}

        states = await mcp_config_service.list_upstreams(db, "test")
        assert [item.app.instance_id for item in states] == [moviepilot.id]


@pytest.mark.asyncio
async def test_legacy_config_is_cleared_when_toggling(mcp_env):
    async with mcp_env["session"]() as db:
        record = await mcp_config_service._record(db)
        assert record is not None
        record.config = {
            "api_operation_ids": ["list_apps_api_apps_get"],
            "local_tools": ["onetap_api_request"],
            "upstreams": {"1": {"enabled": True, "allowed_tools": []}},
        }
        await db.commit()

        await mcp_config_service.set_enabled(db, True)
        record = await mcp_config_service._record(db)
        assert record is not None
        assert record.config == {}


def test_public_tool_names_include_instance_and_native_name():
    name = public_tool_name("moviepilot_12", "media.search")
    assert name == "moviepilot_12_media.search"
    assert split_public_tool_name(name) == ("12", "media.search")
    assert split_public_tool_name("media_targets_list") is None


def test_comic_auto_public_tool_name_is_recognized():
    name = public_tool_name("comic_auto_257", "search_comics")
    assert name == "comic_auto_257_search_comics"
    assert split_public_tool_name(name) == ("257", "search_comics")
