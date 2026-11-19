import pytest


@pytest.mark.asyncio
async def test_resource_tools_are_registered(mcp_env):
    from app.main import app  # noqa: F401
    from app.services.mcp.server import mcp_aggregator

    tools = await mcp_aggregator.server.get_tools()
    for name in (
        "onetap_capabilities",
        "onetap_search",
        "onetap_detail",
        "onetap_subscriptions",
        "onetap_subscribe",
        "onetap_unsubscribe",
        "onetap_downloads",
        "onetap_download",
        "onetap_replace",
        "onetap_present",
        "onetap_notifications",
        "onetap_set_notify_mode",
    ):
        assert name in tools

    search_count = tools["onetap_search"].parameters["properties"]["count"]
    assert search_count["default"] == 20


@pytest.mark.asyncio
async def test_moviepilot_adapter_maps_operations(mcp_env, moviepilot, monkeypatch):
    from fastmcp import FastMCP
    from fastmcp.server.proxy import ProxyClient

    upstream = FastMCP("MoviePilot")
    calls = []

    @upstream.tool(name="moviepilot_api")
    async def moviepilot_api(
        operation_id: str,
        path_params: dict | None = None,
        query: dict | None = None,
        body: dict | None = None,
    ):
        calls.append((operation_id, path_params, query, body))
        if operation_id == "media.search":
            return {
                "success": True,
                "data": [
                    {
                        "title": "沙丘",
                        "year": "2021",
                        "type": "电影",
                        "media_source": "tmdb",
                        "media_id": "438631",
                        # MoviePilot 用 TMDB 字段名返回海报与评分
                        "poster_path": "https://image.tmdb.org/t/p/original/dune.jpg",
                        "vote_average": 7.8,
                    }
                ],
            }
        return {"success": True, "data": {"ok": True}}

    @upstream.tool(name="downloader_operation")
    async def downloader_operation(action: str, arguments: dict):
        return {"success": True, "data": {"action": action}}

    monkeypatch.setattr(
        "app.services.resources.moviepilot.build_upstream_client",
        lambda state: ProxyClient(upstream),
    )

    from app.services.resources import moviepilot as adapter

    async with mcp_env["session"]() as db:
        result = await adapter.search(db, title="沙丘")
        assert result["items"][0]["media_id"] == "438631"
        # 候选推送依赖 poster / vote，MoviePilot 返回的是 TMDB 字段名
        assert result["items"][0]["poster"] == "https://image.tmdb.org/t/p/original/dune.jpg"
        assert result["items"][0]["vote"] == 7.8
        assert calls[0][0] == "media.search"
        assert calls[0][2]["media_source"] == ["themoviedb", "douban"]

        result = await adapter.download_action(db, task_id="abc", action="delete")
        assert result["result"]["action"] == "tasks.delete"

        with pytest.raises(Exception):
            await adapter.download_action(db, task_id="abc", action="bogus")


def test_moviepilot_metadata_extracts_card_fields():
    from app.services.resources import moviepilot

    meta = moviepilot.metadata(
        {
            "type": "电影",
            "year": "2005",
            "poster_path": "https://img.example/poster.jpg",
            "genres": [{"name": "历史"}, {"name": "喜剧"}, {"name": "战争"}],
            "vote_average": 7.1,
            "vote_count": 27,
            "runtime": 87,
            "directors": [{"name": "冯小宁"}, {"name": "冯小宁"}],
            "actors": [{"name": "郭达"}, {"name": "潘长江"}, {"name": "刘小微"}, {"name": "多一人"}],
            "release_date": "2005-01-02",
            "overview": "很长的简介",
        }
    )

    assert meta["poster"] == "https://img.example/poster.jpg"
    assert meta["genres"] == ["历史", "喜剧", "战争"]
    assert meta["rating"] == 7.1
    assert meta["runtime"] == 87
    assert meta["directors"] == ["冯小宁"]
    # 主演只取前三位，简介不进元数据
    assert meta["actors"] == ["郭达", "潘长江", "刘小微"]
    assert "overview" not in meta


def test_moviepilot_metadata_falls_back_to_episode_runtime():
    from app.services.resources import moviepilot

    meta = moviepilot.metadata({"type": "电视剧", "episode_run_time": [45]})
    assert meta["runtime"] == 45
    assert meta["type"] == "电视剧"


@pytest.mark.asyncio
async def test_registry_capabilities_reports_each_domain(mcp_env, monkeypatch):
    from app.services.integrations.discovery import DeployedApp
    from app.services.resources import registry

    async def fake_candidate(db, app_names):
        if app_names == ("music-dl",):
            return [
                DeployedApp(
                    instance_id=7,
                    app_name="music-dl",
                    display_name="music-dl",
                    instance_name="music",
                    status="running",
                    url="http://music:8080",
                )
            ]
        return []

    monkeypatch.setattr(registry, "candidate_apps", fake_candidate)

    async with mcp_env["session"]() as db:
        result = await registry.capabilities(db)
        domains = result["domains"]
        assert set(domains) == {"video", "comic", "music", "novel"}
        assert domains["music"]["available"] is True
        assert domains["music"]["instances"][0]["instance_id"] == 7
        assert domains["comic"]["available"] is False
        assert "suwayomi" in domains["comic"]["reason"]


@pytest.mark.asyncio
async def test_capabilities_reports_comic_variants(mcp_env, monkeypatch):
    """漫画同时有在线与本地实例时，capabilities 给出变体与生命周期。"""
    from app.services.integrations.discovery import DeployedApp
    from app.services.resources import registry

    async def fake_candidate(db, app_names):
        if app_names == ("suwayomi", "comic-auto"):
            return [
                DeployedApp(
                    instance_id=7,
                    app_name="suwayomi",
                    display_name="Suwayomi",
                    instance_name="comic-online",
                    status="running",
                    url="http://suwayomi:4567",
                ),
                DeployedApp(
                    instance_id=9,
                    app_name="comic-auto",
                    display_name="Comic Auto",
                    instance_name="comic-local",
                    status="running",
                    url="http://comic-auto:8055",
                ),
            ]
        return []

    monkeypatch.setattr(registry, "candidate_apps", fake_candidate)

    async with mcp_env["session"]() as db:
        result = await registry.capabilities(db)

    comic = result["domains"]["comic"]
    assert comic["available"] is True
    assert comic["requires_instance_id"] is True
    variants = {item["variant"]: item for item in comic["variants"]}
    assert variants["online"]["app"] == "suwayomi"
    assert variants["online"]["lifecycle"] == ["library", "chapters"]
    assert variants["local"]["app"] == "comic-auto"
    assert variants["local"]["lifecycle"] == ["subscribe", "download", "organize"]


@pytest.mark.asyncio
async def test_comic_requires_instance_id_when_both_available(mcp_env, monkeypatch):
    """两套漫画方案并存时不能默认取第一个，必须让调用方显式选择。"""
    from app.core.exceptions import APIException
    from app.services.integrations.discovery import DeployedApp
    from app.services.resources import base, registry

    apps = [
        DeployedApp(
            instance_id=7,
            app_name="suwayomi",
            display_name="Suwayomi",
            instance_name="comic-online",
            status="running",
            url="http://suwayomi:4567",
        ),
        DeployedApp(
            instance_id=9,
            app_name="comic-auto",
            display_name="Comic Auto",
            instance_name="comic-local",
            status="running",
            url="http://comic-auto:8055",
        ),
    ]

    async def fake_candidate(db, app_names):
        return apps

    monkeypatch.setattr(base, "candidate_apps", fake_candidate)

    async with mcp_env["session"]() as db:
        with pytest.raises(APIException) as exc_info:
            await registry.search(db, domain="comic", keyword="海贼王")
        message = str(exc_info.value)
        assert "instance_id" in message
        assert "online" in message and "local" in message

        called: dict = {}

        async def fake_local_search(db_, **kwargs):
            called.update(kwargs)
            return {"variant": "local", "instance_id": kwargs["instance_id"], "items": []}

        monkeypatch.setattr(registry.comic_auto, "search", fake_local_search)
        result = await registry.search(
            db, domain="comic", keyword="海贼王", instance_id=9
        )

    assert result["variant"] == "local"
    assert called["instance_id"] == 9


@pytest.mark.asyncio
async def test_capabilities_reports_video_variants(mcp_env, monkeypatch):
    """影视同时有本地与网盘实例时，capabilities 给出变体、生命周期与操作。"""
    from app.services.integrations.discovery import DeployedApp
    from app.services.resources import registry

    async def fake_candidate(db, app_names):
        if app_names == ("moviepilot", "mediary-scout"):
            return [
                DeployedApp(
                    instance_id=9,
                    app_name="moviepilot",
                    display_name="MoviePilot",
                    instance_name="movie",
                    status="running",
                    url="http://moviepilot:3001",
                ),
                DeployedApp(
                    instance_id=11,
                    app_name="mediary-scout",
                    display_name="巡影 Mediary Scout",
                    instance_name="movie-cloud",
                    status="running",
                    url="http://mediary-scout:3000",
                ),
            ]
        return []

    monkeypatch.setattr(registry, "candidate_apps", fake_candidate)

    async with mcp_env["session"]() as db:
        result = await registry.capabilities(db)

    video = result["domains"]["video"]
    assert video["available"] is True
    assert video["requires_instance_id"] is True
    variants = {item["variant"]: item for item in video["variants"]}
    assert variants["local"]["app"] == "moviepilot"
    assert variants["local"]["lifecycle"] == ["subscribe", "download", "organize"]
    assert variants["cloud"]["app"] == "mediary-scout"
    assert variants["cloud"]["lifecycle"] == ["acquire", "transfer", "verify", "track"]
    assert "search" in variants["local"]["operations"]
    assert variants["cloud"]["operations"] == [
        "search",
        "subscriptions",
        "subscribe",
        "downloads",
    ]


@pytest.mark.asyncio
async def test_video_requires_instance_id_when_both_available(mcp_env, monkeypatch):
    """两套影视方案并存时不能默认查 MoviePilot，必须让调用方显式选择。"""
    from app.core.exceptions import APIException
    from app.services.integrations.discovery import DeployedApp
    from app.services.resources import base, registry

    apps = [
        DeployedApp(
            instance_id=9,
            app_name="moviepilot",
            display_name="MoviePilot",
            instance_name="movie",
            status="running",
            url="http://moviepilot:3001",
        ),
        DeployedApp(
            instance_id=11,
            app_name="mediary-scout",
            display_name="巡影 Mediary Scout",
            instance_name="movie-cloud",
            status="running",
            url="http://mediary-scout:3000",
        ),
    ]

    async def fake_candidate(db, app_names):
        return apps

    monkeypatch.setattr(base, "candidate_apps", fake_candidate)

    async with mcp_env["session"]() as db:
        with pytest.raises(APIException) as exc_info:
            await registry.search(db, domain="video", keyword="沙丘")
        message = str(exc_info.value)
        assert "instance_id" in message
        assert "local" in message and "cloud" in message

        called: dict = {}

        async def fake_cloud_subscribe(db_, **kwargs):
            called.update(kwargs)
            return {"status": "requested", "variant": "cloud", "instance_id": 11, "tmdb_id": 438631}

        monkeypatch.setattr(registry.mediary_scout, "subscribe", fake_cloud_subscribe)
        result = await registry.subscribe(
            db,
            domain="video",
            options={"name": "沙丘"},
            instance_id=11,
        )

    assert result["variant"] == "cloud"
    assert called["instance_id"] == 11
