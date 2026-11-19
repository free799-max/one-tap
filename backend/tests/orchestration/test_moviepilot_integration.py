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


def _directory_panel_handler(
    store: dict,
    posts: list[str] | None = None,
    library_errors: dict | None = None,
    write_hooks: dict | None = None,
):
    """模拟目录面板所需的设置读写与媒体库详情接口。"""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/api/v1/system/setting/"):
            key = path.rsplit("/", 1)[-1]
            if request.method == "POST":
                if posts is not None:
                    posts.append(key)
                body = json.loads(request.content or b"null")
                if isinstance(body, dict) and "value" in body:
                    body = body["value"]
                hook = (write_hooks or {}).get(key)
                store[key] = hook(body) if hook else body
                return httpx.Response(200, json={"success": True})
            return httpx.Response(
                200, json={"success": True, "data": {"value": store.get(key)}}
            )
        if path == "/api/v1/mediaserver/library":
            server = request.url.params.get("server", "")
            if server in (library_errors or {}):
                return httpx.Response(
                    200,
                    json={"success": False, "message": library_errors[server]},
                )
            return httpx.Response(
                200,
                json={"success": True, "data": store.get("_libraries", {}).get(server, [])},
            )
        return httpx.Response(404)

    return handler


@pytest.mark.asyncio
async def test_directory_panel_reads_details_and_tolerates_partial_failure(monkeypatch):
    """目录正常返回，媒体库详情归一化且单台失败只写入该服务器 error。"""
    store = {
        "Directories": [
            {
                "name": "电影",
                "download_path": "/downloads/movies",
                "library_path": "/media/movies",
                "transfer_type": "link",
                "custom": {"keep": True},
            }
        ],
        "MediaServers": [
            {
                "name": "jellyfin",
                "type": "jellyfin",
                "enabled": True,
                "sync_libraries": [1, "2"],
                "config": {"apikey": "must-not-leak"},
                "custom": "keep",
            },
            {
                "name": "emby",
                "type": "emby",
                "enabled": False,
                "sync_libraries": "all",
                "config": {"apikey": "also-secret"},
            },
        ],
        "_libraries": {
            "jellyfin": [
                {"id": 1, "name": "电影", "path": "/media/movies", "type": "movies"},
                {"id": 2, "name": "剧集", "path": ["/media/tv"], "type": None},
            ]
        },
    }
    _mock_httpx(
        monkeypatch,
        _directory_panel_handler(store, library_errors={"emby": "连接失败"}),
    )
    shared = {
        "volumes": [
            {"container_path": "/downloads"},
            {"container_path": "/media"},
        ]
    }

    ok, panel, err = await MoviePilotClient().get_directory_panel(
        "http://mp:3000", "token123", shared
    )

    assert ok is True and err is None
    assert panel["supported"] is True
    assert panel["directories"][0]["custom"] == {"keep": True}
    assert panel["detected"] == {
        "download_path": "/downloads",
        "library_path": "/media",
    }
    assert panel["directory_template"]["download_path"] == "/downloads"
    assert panel["directory_template"]["library_path"] == "/media"
    assert panel["directory_template"]["transfer_type"] == "link"
    assert panel["directory_template"]["media_type"] == ""
    assert panel["directory_template"]["media_category"] == ""

    jellyfin, emby = panel["media_servers"]
    assert jellyfin["sync_libraries"] == ["1", "2"]
    assert jellyfin["libraries"] == [
        {"id": "1", "name": "电影", "path": ["/media/movies"], "type": "movies"},
        {"id": "2", "name": "剧集", "path": ["/media/tv"], "type": None},
    ]
    assert jellyfin["error"] is None
    assert emby["enabled"] is False
    assert emby["sync_libraries"] == ["all"]
    assert emby["libraries"] == []
    assert emby["error"] == "连接失败"
    assert all("config" not in server for server in panel["media_servers"])


@pytest.mark.asyncio
async def test_save_directories_supports_crud_and_preserves_unknown_fields(monkeypatch):
    """目录整体增删改，仅写 Directories，并从写入后的状态返回面板。"""
    original_servers = [
        {
            "name": "jellyfin",
            "type": "jellyfin",
            "enabled": True,
            "sync_libraries": ["all"],
            "config": {"apikey": "secret"},
        }
    ]
    store = {
        "Directories": [
            {
                "name": "旧目录",
                "download_path": "/old",
                "library_path": "/old",
                "transfer_type": "copy",
            }
        ],
        "MediaServers": original_servers,
        "_libraries": {"jellyfin": []},
    }
    posts: list[str] = []
    _mock_httpx(monkeypatch, _directory_panel_handler(store, posts))
    directories = [
        {
            "name": "电影",
            "download_path": "/downloads/movies",
            "library_path": "/media/movies",
            "transfer_type": "link",
            "priority": 2,
            "custom": {"keep": True},
        },
        {
            "name": "剧集",
            "download_path": "/downloads/tv",
            "library_path": "/media/tv",
            "transfer_type": "move",
        },
    ]

    ok, panel, err = await MoviePilotClient().save_directories(
        "http://mp:3000", "token123", directories
    )

    assert ok is True and err is None
    assert posts == ["Directories"]
    assert store["Directories"] == directories
    assert [
        {
            key: value
            for key, value in item.items()
            if key not in ("media_type", "media_category")
        }
        for item in panel["directories"]
    ] == directories
    assert all(
        item["media_type"] == "" and item["media_category"] == ""
        for item in panel["directories"]
    )
    assert panel["directories"][0]["custom"] == {"keep": True}
    assert store["MediaServers"] == original_servers


@pytest.mark.asyncio
async def test_save_directories_readback_allows_additional_default_fields(monkeypatch):
    """MoviePilot 回读增加默认字段不影响提交字段的子集验证。"""

    def add_defaults(body):
        return [
            {**item, "storage": "local", "library_storage": "local"}
            for item in body
        ]

    store = {"Directories": [], "MediaServers": []}
    _mock_httpx(
        monkeypatch,
        _directory_panel_handler(
            store, write_hooks={"Directories": add_defaults}
        ),
    )
    directories = [
        {
            "name": "电影",
            "download_path": "/downloads",
            "library_path": "/media",
            "transfer_type": "link",
            "custom": True,
        }
    ]

    ok, panel, err = await MoviePilotClient().save_directories(
        "http://mp:3000", "token123", directories
    )

    assert ok is True and err is None
    assert panel["directories"][0]["storage"] == "local"
    assert panel["directories"][0]["library_storage"] == "local"
    assert panel["directories"][0]["custom"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["missing", "changed", "dropped"])
async def test_save_directories_rejects_inconsistent_readback(monkeypatch, mode):
    """回读缺目录、字段值不同或丢失提交扩展字段时均不报成功。"""

    def inconsistent_write(body):
        if mode == "missing":
            return []
        item = dict(body[0])
        if mode == "changed":
            item["transfer_type"] = "copy"
        else:
            item.pop("custom")
        return [item]

    store = {"Directories": [], "MediaServers": []}
    posts: list[str] = []
    _mock_httpx(
        monkeypatch,
        _directory_panel_handler(
            store, posts, write_hooks={"Directories": inconsistent_write}
        ),
    )
    directories = [
        {
            "name": "电影",
            "download_path": "/downloads",
            "library_path": "/media",
            "transfer_type": "link",
            "custom": {"keep": True},
        }
    ]

    ok, panel, err = await MoviePilotClient().save_directories(
        "http://mp:3000", "token123", directories
    )

    assert ok is False and panel is None
    assert "写入后回读验证失败" in err
    assert posts == ["Directories"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "directories",
    [
        [{"name": " ", "download_path": "/d", "library_path": "/l", "transfer_type": "link"}],
        [
            {"name": "重复", "download_path": "/d1", "library_path": "/l1", "transfer_type": "link"},
            {"name": "重复", "download_path": "/d2", "library_path": "/l2", "transfer_type": "copy"},
        ],
        [{"name": "目录", "download_path": "relative", "library_path": "/l", "transfer_type": "link"}],
        [{"name": "目录", "download_path": "/d", "library_path": "relative", "transfer_type": "link"}],
        [{"name": "目录", "download_path": "/d", "library_path": "/l", "transfer_type": "invalid"}],
    ],
)
async def test_save_directories_rejects_invalid_input(monkeypatch, directories):
    """空名称、重名、相对路径和非法整理方式均在写入前拒绝。"""
    store = {"Directories": []}
    posts: list[str] = []
    _mock_httpx(monkeypatch, _directory_panel_handler(store, posts))

    ok, panel, err = await MoviePilotClient().save_directories(
        "http://mp:3000", "token123", directories
    )

    assert ok is False and panel is None
    assert "校验失败" in err
    assert posts == []


@pytest.mark.asyncio
async def test_save_media_libraries_preserves_servers_config_and_directories(monkeypatch):
    """同步范围保存保留禁用/未提交服务器、config、未知字段和目录。"""
    original_directories = [
        {
            "name": "电影",
            "download_path": "/d",
            "library_path": "/l",
            "transfer_type": "link",
            "custom": True,
        }
    ]
    store = {
        "Directories": original_directories,
        "MediaServers": [
            {
                "name": "jellyfin",
                "type": "jellyfin",
                "enabled": True,
                "sync_libraries": ["1"],
                "config": {"apikey": "secret", "custom": "keep"},
                "custom": {"keep": True},
            },
            {
                "name": "disabled",
                "type": "emby",
                "enabled": False,
                "sync_libraries": ["8"],
                "config": {"apikey": "disabled-secret"},
            },
            {
                "name": "other",
                "type": "emby",
                "enabled": True,
                "sync_libraries": ["9"],
                "config": {"apikey": "other-secret"},
            },
        ],
        "_libraries": {
            "jellyfin": [{"id": 1, "name": "电影"}, {"id": 2, "name": "剧集"}],
            "disabled": [{"id": 8, "name": "禁用"}],
            "other": [{"id": 9, "name": "其他"}],
        },
    }
    posts: list[str] = []
    _mock_httpx(monkeypatch, _directory_panel_handler(store, posts))

    ok, panel, err = await MoviePilotClient().save_media_libraries(
        "http://mp:3000",
        "token123",
        [{"name": "jellyfin", "sync_libraries": ["2"]}],
    )

    assert ok is True and err is None
    assert posts == ["MediaServers"]
    assert [server["name"] for server in store["MediaServers"]] == [
        "jellyfin",
        "disabled",
        "other",
    ]
    assert store["MediaServers"][0]["sync_libraries"] == ["2"]
    assert store["MediaServers"][0]["config"] == {
        "apikey": "secret",
        "custom": "keep",
    }
    assert store["MediaServers"][0]["custom"] == {"keep": True}
    assert store["MediaServers"][1]["enabled"] is False
    assert store["MediaServers"][1]["sync_libraries"] == ["8"]
    assert store["MediaServers"][2]["sync_libraries"] == ["9"]
    assert store["Directories"] == original_directories
    assert panel["media_servers"][0]["sync_libraries"] == ["2"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "updates, expected_error",
    [
        ([{"name": "missing", "sync_libraries": []}], "媒体服务器不存在"),
        ([{"name": "jellyfin", "sync_libraries": ["missing"]}], "媒体库 ID 不存在"),
    ],
)
async def test_save_media_libraries_rejects_unknown_server_or_library(
    monkeypatch, updates, expected_error
):
    """无效服务器名或媒体库 ID 不会触发设置写入。"""
    store = {
        "MediaServers": [
            {"name": "jellyfin", "enabled": True, "sync_libraries": []}
        ],
        "_libraries": {"jellyfin": [{"id": 1, "name": "电影"}]},
    }
    posts: list[str] = []
    _mock_httpx(monkeypatch, _directory_panel_handler(store, posts))

    ok, panel, err = await MoviePilotClient().save_media_libraries(
        "http://mp:3000", "token123", updates
    )

    assert ok is False and panel is None
    assert expected_error in err
    assert posts == []


@pytest.mark.asyncio
async def test_save_media_libraries_accepts_empty_and_all(monkeypatch):
    """显式空列表与 ['all'] 均按全部同步保存并原样归一化返回。"""
    store = {
        "MediaServers": [
            {"name": "jellyfin", "enabled": True, "sync_libraries": ["1"]},
            {"name": "emby", "enabled": True, "sync_libraries": ["2"]},
        ],
        "_libraries": {"jellyfin": [], "emby": []},
    }
    _mock_httpx(monkeypatch, _directory_panel_handler(store))

    ok, panel, err = await MoviePilotClient().save_media_libraries(
        "http://mp:3000",
        "token123",
        [
            {"name": "jellyfin", "sync_libraries": []},
            {"name": "emby", "sync_libraries": ["all"]},
        ],
    )

    assert ok is True and err is None
    assert store["MediaServers"][0]["sync_libraries"] == []
    assert store["MediaServers"][1]["sync_libraries"] == ["all"]
    assert [server["sync_libraries"] for server in panel["media_servers"]] == [
        [],
        ["all"],
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["missing", "changed"])
async def test_save_media_libraries_rejects_inconsistent_readback(monkeypatch, mode):
    """回读缺少目标服务器或同步列表不同时均返回验证失败。"""

    def inconsistent_write(body):
        if mode == "missing":
            return []
        servers = [dict(server) for server in body]
        servers[0]["sync_libraries"] = ["1"]
        return servers

    store = {
        "Directories": [],
        "MediaServers": [
            {"name": "jellyfin", "enabled": True, "sync_libraries": ["1"]}
        ],
        "_libraries": {
            "jellyfin": [{"id": 1, "name": "电影"}, {"id": 2, "name": "剧集"}]
        },
    }
    posts: list[str] = []
    _mock_httpx(
        monkeypatch,
        _directory_panel_handler(
            store, posts, write_hooks={"MediaServers": inconsistent_write}
        ),
    )

    ok, panel, err = await MoviePilotClient().save_media_libraries(
        "http://mp:3000",
        "token123",
        [{"name": "jellyfin", "sync_libraries": ["2"]}],
    )

    assert ok is False and panel is None
    assert "写入后回读验证失败" in err
    assert posts == ["MediaServers"]


@pytest.mark.asyncio
async def test_save_media_libraries_readback_normalizes_library_ids(monkeypatch):
    """回读媒体库 ID 为数字时归一为字符串后再与提交值核对。"""

    def numeric_ids(body):
        servers = [dict(server) for server in body]
        servers[0]["sync_libraries"] = [2]
        return servers

    store = {
        "Directories": [],
        "MediaServers": [
            {"name": "jellyfin", "enabled": True, "sync_libraries": ["1"]}
        ],
        "_libraries": {"jellyfin": [{"id": 2, "name": "剧集"}]},
    }
    _mock_httpx(
        monkeypatch,
        _directory_panel_handler(
            store, write_hooks={"MediaServers": numeric_ids}
        ),
    )

    ok, panel, err = await MoviePilotClient().save_media_libraries(
        "http://mp:3000",
        "token123",
        [{"name": "jellyfin", "sync_libraries": ["2"]}],
    )

    assert ok is True and err is None
    assert panel["media_servers"][0]["sync_libraries"] == ["2"]


@pytest.mark.asyncio
async def test_get_classification_policy_parses_data_wrapper(monkeypatch):
    """分类策略接口沿用 MoviePilot 的 data 包装。"""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/media/classification/policy"
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "categories": [
                        {
                            "id": "movie.animation",
                            "media_type": "电影",
                            "name": "动画电影",
                            "path": ["动画电影"],
                            "enabled": True,
                        }
                    ]
                },
            },
        )

    _mock_httpx(monkeypatch, handler)
    ok, policy, err = await MoviePilotClient().get_classification_policy(
        "http://mp:3000", "token123"
    )

    assert ok is True and err is None
    assert policy["categories"][0]["id"] == "movie.animation"


@pytest.mark.asyncio
async def test_storage_dir_exists_checks_parent_listing(monkeypatch):
    """目录存在性通过父目录列表判断，并缓存同一父目录结果。"""
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/storage/list"
        body = json.loads(request.content)
        requests.append(body["path"])
        assert body == {"storage": "local", "path": "/mnt/movie/media/电影"}
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": [
                    {
                        "name": "动画电影",
                        "path": "/mnt/movie/media/电影/动画电影/",
                        "type": "dir",
                    }
                ],
            },
        )

    _mock_httpx(monkeypatch, handler)
    client = MoviePilotClient()
    cache: dict = {}

    ok, exists, err = await client.storage_dir_exists(
        "http://mp:3000",
        "token123",
        "/mnt/movie/media/电影/动画电影",
        cache=cache,
    )
    ok_second, exists_second, _ = await client.storage_dir_exists(
        "http://mp:3000",
        "token123",
        "/mnt/movie/media/电影/动画电影",
        cache=cache,
    )

    assert ok is True and exists is True and err is None
    assert ok_second is True and exists_second is True
    assert requests == ["/mnt/movie/media/电影"]


@pytest.mark.asyncio
async def test_storage_dir_exists_does_not_treat_missing_as_empty_success(
    monkeypatch,
):
    """父目录存在但目标子目录不存在时返回 False。"""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": True, "data": []})

    _mock_httpx(monkeypatch, handler)

    ok, exists, err = await MoviePilotClient().storage_dir_exists(
        "http://mp:3000",
        "token123",
        "/mnt/movie/media/电影/动画电影",
    )

    assert ok is True
    assert exists is False
    assert err is None
