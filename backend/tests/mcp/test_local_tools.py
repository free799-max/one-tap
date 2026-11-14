import json

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from app.services.mcp.config import mcp_config_service
from app.services.mcp.server import McpAggregator


@pytest.mark.asyncio
async def test_media_targets_and_panels_do_not_leak_secrets(
    mcp_env, moviepilot, monkeypatch
):
    async def music_panel(db, group_id):
        return {
            "running": False,
            "progress": {"message": "idle"},
            "stats": {"done": 3},
            "config": {"password": "must-not-leak"},
        }

    async def reader_panel(db, group_id):
        return {
            "running": True,
            "progress": {"processed": 2},
            "stats": {"done": 2},
            "config": {"reader_password": "must-not-leak", "reader_token": "secret"},
        }

    monkeypatch.setattr(
        "app.services.mcp.local_tools.music_auto_service.get_panel", music_panel
    )
    monkeypatch.setattr(
        "app.services.mcp.local_tools.reader_auto_service.get_panel", reader_panel
    )
    async with mcp_env["session"]() as db:
        await mcp_config_service.set_enabled(db, True)
    aggregator = McpAggregator()

    async with Client(aggregator.server) as client:
        targets = await client.call_tool("media_targets_list", {})
        music = await client.call_tool("music_organize_status", {"group_id": 9})
        reader = await client.call_tool("reader_sync_status", {"group_id": 9})

    combined = "".join(
        item.text
        for result in (targets, music, reader)
        for item in result.content
    )
    assert "must-not-leak" not in combined
    assert "mp-secret" not in combined
    assert json.loads(music.content[0].text)["stats"] == {"done": 3}
    assert json.loads(reader.content[0].text)["running"] is True


@pytest.mark.asyncio
async def test_run_tools_require_explicit_enable(mcp_env, monkeypatch):
    calls = []

    async def start_run(db, group_id, **kwargs):
        calls.append((group_id, kwargs))
        return {"running": True, "stage": "queued"}

    monkeypatch.setattr(
        "app.services.mcp.local_tools.music_auto_service.start_run", start_run
    )
    async with mcp_env["session"]() as db:
        await mcp_config_service.set_enabled(db, True)
    aggregator = McpAggregator()

    async with Client(aggregator.server) as client:
        with pytest.raises(ToolError, match="白名单"):
            await client.call_tool("music_organize_run", {"group_id": 9})
        async with mcp_env["session"]() as db:
            await mcp_config_service.update_local_tools(
                db, ["media_targets_list", "music_organize_run"]
            )
        result = await client.call_tool("music_organize_run", {"group_id": 9})

    assert json.loads(result.content[0].text)["started"] is True
    assert calls == [(9, {"trigger": "mcp", "force": True})]
