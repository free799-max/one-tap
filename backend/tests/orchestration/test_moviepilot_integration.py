"""MoviePilot 集成配置（系统设置读写与下载器/媒体服务器装配）测试。"""

import json

import httpx
import pytest

from app.services.apps.moviepilot import MoviePilotClient


def _mock_httpx(monkeypatch, handler):
    """用 httpx.MockTransport 替换 AsyncClient，拦截客户端的 HTTP 请求。"""
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: real_client(transport=transport, **kwargs)
    )


@pytest.mark.asyncio
async def test_get_setting_parses_data_value_wrapper(monkeypatch):
    """标准包装 {"success": true, "data": {"value": ...}} 解析出 value。"""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-API-KEY"] == "token123"
        assert request.url.path == "/api/v1/system/setting/Downloaders"
        return httpx.Response(
            200, json={"success": True, "data": {"value": [{"name": "qb"}]}}
        )

    _mock_httpx(monkeypatch, handler)
    client = MoviePilotClient()
    ok, value, err = await client.get_setting("http://mp:3000", "token123", "Downloaders")

    assert ok is True
    assert err is None
    assert value == [{"name": "qb"}]


@pytest.mark.asyncio
async def test_get_setting_tolerates_bare_data(monkeypatch):
    """容错：data 直接是值时也能解析。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": True, "data": [{"name": "qb"}]})

    _mock_httpx(monkeypatch, handler)
    client = MoviePilotClient()
    ok, value, err = await client.get_setting("http://mp:3000", "token123", "Downloaders")

    assert ok is True
    assert value == [{"name": "qb"}]


@pytest.mark.asyncio
async def test_set_setting_retries_with_value_wrapper(monkeypatch):
    """裸值被拒（422）时自动包 {"value": ...} 重试。"""
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if isinstance(body, list):
            return httpx.Response(422, json={"detail": "unprocessable"})
        return httpx.Response(200, json={"success": True})

    _mock_httpx(monkeypatch, handler)
    client = MoviePilotClient()
    ok, err = await client.set_setting(
        "http://mp:3000", "token123", "Downloaders", [{"name": "qb"}]
    )

    assert ok is True
    assert err is None
    assert bodies == [[{"name": "qb"}], {"value": [{"name": "qb"}]}]


@pytest.mark.asyncio
async def test_setup_downloader_appends_new_entry(monkeypatch):
    """Downloaders 为空/非 list 时追加新条目，写回后回读验证。"""
    store = {"Downloaders": None}

    def handler(request: httpx.Request) -> httpx.Response:
        key = request.url.path.rsplit("/", 1)[-1]
        if request.method == "GET":
            return httpx.Response(200, json={"success": True, "data": {"value": store.get(key)}})
        store[key] = json.loads(request.content)
        return httpx.Response(200, json={"success": True})

    _mock_httpx(monkeypatch, handler)
    client = MoviePilotClient()
    ok, err = await client.setup_downloader(
        "http://mp:3000",
        "token123",
        name="qbittorrent",
        type_="qbittorrent",
        host="http://media-qbittorrent:8080",
        username="admin",
        password="pass",
    )

    assert ok is True
    assert err is None
    assert store["Downloaders"] == [
        {
            "name": "qbittorrent",
            "type": "qbittorrent",
            "default": True,
            "enabled": True,
            "config": {
                "host": "http://media-qbittorrent",
                "port": 8080,
                "username": "admin",
                "password": "pass",
            },
            "path_mapping": [],
        }
    ]


@pytest.mark.asyncio
async def test_setup_downloader_upserts_preserving_extra_fields(monkeypatch):
    """同名条目保留自定义字段，仅覆盖装配相关字段（幂等重跑）。"""
    store = {
        "Downloaders": [
            {
                "name": "qbittorrent",
                "type": "qbittorrent",
                "enabled": False,
                "config": {
                    "host": "http://old",
                    "port": 8080,
                    "username": "old",
                    "password": "old",
                    "category": True,
                },
                "custom": "keep",
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        key = request.url.path.rsplit("/", 1)[-1]
        if request.method == "GET":
            return httpx.Response(200, json={"success": True, "data": {"value": store.get(key)}})
        store[key] = json.loads(request.content)
        return httpx.Response(200, json={"success": True})

    _mock_httpx(monkeypatch, handler)
    client = MoviePilotClient()
    ok, err = await client.setup_downloader(
        "http://mp:3000",
        "token123",
        name="qbittorrent",
        type_="qbittorrent",
        host="http://media-qbittorrent:8080",
        username="admin",
        password="pass",
    )

    assert ok is True
    assert err is None
    assert len(store["Downloaders"]) == 1
    entry = store["Downloaders"][0]
    assert entry["config"]["host"] == "http://media-qbittorrent"
    assert entry["config"]["port"] == 8080
    assert entry["config"]["username"] == "admin"
    assert entry["enabled"] is True
    # config 内用户自加字段与顶层自定义字段保留
    assert entry["config"]["category"] is True
    assert entry["custom"] == "keep"


@pytest.mark.asyncio
async def test_setup_mediaserver_upserts_entry(monkeypatch):
    """MediaServers 按 name upsert jellyfin/emby 条目（config 内 host + apikey）。"""
    store = {"MediaServers": []}

    def handler(request: httpx.Request) -> httpx.Response:
        key = request.url.path.rsplit("/", 1)[-1]
        if request.method == "GET":
            return httpx.Response(200, json={"success": True, "data": {"value": store.get(key)}})
        store[key] = json.loads(request.content)
        return httpx.Response(200, json={"success": True})

    _mock_httpx(monkeypatch, handler)
    client = MoviePilotClient()
    ok, err = await client.setup_mediaserver(
        "http://mp:3000",
        "token123",
        name="jellyfin",
        type_="jellyfin",
        host="http://media-jellyfin:8096",
        api_key_ms="jf-api-key",
        play_host="http://192.168.1.17:8096",
    )

    assert ok is True
    assert err is None
    assert store["MediaServers"] == [
        {
            "name": "jellyfin",
            "type": "jellyfin",
            "enabled": True,
            "config": {
                "host": "http://media-jellyfin:8096",
                "apikey": "jf-api-key",
                "play_host": "http://192.168.1.17:8096",
            },
            "sync_libraries": [],
        }
    ]


@pytest.mark.asyncio
async def test_setup_mediaserver_supports_trimemedia(monkeypatch):
    """飞牛影视按 trimemedia 类型写入账号密码与可选访问码。"""
    store = {"MediaServers": []}

    def handler(request: httpx.Request) -> httpx.Response:
        key = request.url.path.rsplit("/", 1)[-1]
        if request.method == "GET":
            return httpx.Response(
                200, json={"success": True, "data": {"value": store.get(key)}}
            )
        store[key] = json.loads(request.content)
        return httpx.Response(200, json={"success": True})

    _mock_httpx(monkeypatch, handler)
    client = MoviePilotClient()
    ok, err = await client.setup_mediaserver(
        "http://mp:3000",
        "token123",
        name="fnos-media",
        type_="fnos-media",
        host="http://192.168.1.20:5666",
        api_key_ms="",
        play_host="http://192.168.1.20:5666",
        source_username="Cacty",
        source_password="secret",
        access_code="code-1",
    )

    assert ok is True
    assert err is None
    assert store["MediaServers"] == [
        {
            "name": "fnos-media",
            "type": "trimemedia",
            "enabled": True,
            "config": {
                "host": "http://192.168.1.20:5666",
                "username": "Cacty",
                "password": "secret",
                "access_code": "code-1",
                "play_host": "http://192.168.1.20:5666",
            },
            "sync_libraries": [],
        }
    ]


@pytest.mark.asyncio
async def test_setup_downloader_returns_error_when_readback_missing(monkeypatch):
    """写入后回读不到条目时报验证失败。"""

    def handler(request: httpx.Request) -> httpx.Response:
        # 模拟写入不落库的异常服务：读总是空、写总是成功
        if request.method == "GET":
            return httpx.Response(200, json={"success": True, "data": {"value": []}})
        return httpx.Response(200, json={"success": True})

    _mock_httpx(monkeypatch, handler)
    client = MoviePilotClient()
    ok, err = await client.setup_downloader(
        "http://mp:3000",
        "token123",
        name="qbittorrent",
        type_="qbittorrent",
        host="http://media-qbittorrent:8080",
        username="admin",
        password="pass",
    )

    assert ok is False
    assert "回读验证失败" in err
