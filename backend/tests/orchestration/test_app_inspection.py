"""应用配置健康检测（inspect/apply_fix）与插件管理客户端测试。"""

import json
from urllib.parse import parse_qs

import httpx
import pytest
from unittest.mock import AsyncMock

from app.services.apps.base import AppClient, AuthVerifyResult
from app.services.apps.jellyfin import JellyfinClient
from app.services.apps.moviepilot import MoviePilotClient
from app.services.apps.qbittorrent import QBittorrentClient


def _mock_httpx(monkeypatch, handler):
    """用 httpx.MockTransport 替换 AsyncClient，拦截客户端的 HTTP 请求。"""
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: real_client(transport=transport, **kwargs)
    )


# ---------------- base 默认行为 ----------------


class _DummyClient(AppClient):
    """未扩展检测能力的最小客户端。"""

    name = "dummy"

    async def verify_auth(self, config: dict) -> AuthVerifyResult:
        return AuthVerifyResult(valid=True)


@pytest.mark.asyncio
async def test_base_inspect_defaults_to_unsupported():
    """默认实现返回不支持检测。"""
    report = await _DummyClient().inspect({"url": "http://x"})
    assert report.supported is False
    assert report.items == []


@pytest.mark.asyncio
async def test_base_apply_fix_defaults_to_unsupported():
    """默认实现对每项返回不支持自动修复。"""
    results = await _DummyClient().apply_fix({}, ["a", "b"])
    assert [r.id for r in results] == ["a", "b"]
    assert all(r.ok is False for r in results)
    assert all("不支持自动修复" in r.message for r in results)


# ---------------- qBittorrent ----------------

_QB_CTX = {
    "url": "http://qb:8080",
    "username": "admin",
    "password": "pass",
    "shared_config": {
        "volumes": [
            {"host_path": "media", "container_path": "/media"},
            {"host_path": "downloads", "container_path": "/downloads"},
        ]
    },
    "expected_listen_port": 6881,
}


def _qb_handler(prefs: dict, captured: list | None = None, login_ok: bool = True):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v2/auth/login":
            if not login_ok:
                return httpx.Response(403, text="Forbidden")
            return httpx.Response(200, text="Ok.")
        if path == "/api/v2/app/version":
            return httpx.Response(200, text="v5.0.0")
        if path == "/api/v2/app/preferences":
            return httpx.Response(200, json=prefs)
        if path == "/api/v2/app/setPreferences":
            if captured is not None:
                form = parse_qs(request.content.decode())
                captured.append(json.loads(form["json"][0]))
            return httpx.Response(200, text="Ok.")
        return httpx.Response(404)

    return handler


@pytest.mark.asyncio
async def test_qb_inspect_auth_failure_returns_single_item(monkeypatch):
    """认证失败时只报 auth 一项，后续项跳过。"""
    _mock_httpx(monkeypatch, _qb_handler({}, login_ok=False))
    report = await QBittorrentClient().inspect(_QB_CTX)

    assert report.supported is True
    assert len(report.items) == 1
    assert report.items[0].id == "auth"
    assert report.items[0].status == "error"


@pytest.mark.asyncio
async def test_qb_inspect_builds_preference_items(monkeypatch):
    """偏好设置异常时各检测项状态与 fixable 标记正确。"""
    prefs = {
        "web_ui_host_header_validation_enabled": True,
        "default_save_path": "/root/Downloads",
        "listen_port": 8999,
        "queueing_enabled": False,
        "dht": True,
        "pex": False,
        "lsd": True,
    }
    _mock_httpx(monkeypatch, _qb_handler(prefs))
    report = await QBittorrentClient().inspect(_QB_CTX)
    items = {i.id: i for i in report.items}

    assert items["auth"].status == "ok"
    assert items["auth"].current_value == "v5.0.0"

    assert items["host_header_validation"].status == "warn"
    assert items["host_header_validation"].fixable is True

    assert items["save_path"].status == "error"
    assert items["save_path"].expected_value == "/downloads"
    assert items["save_path"].fixable is True

    assert items["listening_port"].status == "warn"
    assert items["listening_port"].current_value == "8999"
    assert items["listening_port"].expected_value == "6881"
    assert items["listening_port"].fixable is True

    assert items["queueing"].status == "warn"
    assert items["queueing"].fixable is True

    assert items["network_discovery"].status == "warn"
    assert "PEX" in items["network_discovery"].message
    assert items["network_discovery"].fixable is True


@pytest.mark.asyncio
async def test_qb_inspect_healthy_preferences(monkeypatch):
    """偏好设置健康时全部为 ok。"""
    prefs = {
        "web_ui_host_header_validation_enabled": False,
        "default_save_path": "/downloads/complete",
        "listen_port": 6881,
        "queueing_enabled": True,
        "dht": True,
        "pex": True,
        "lsd": True,
    }
    _mock_httpx(monkeypatch, _qb_handler(prefs))
    report = await QBittorrentClient().inspect(_QB_CTX)
    assert all(item.status == "ok" for item in report.items)
    assert all(item.fixable is False for item in report.items)


@pytest.mark.asyncio
async def test_qb_inspect_skips_listen_port_without_expected(monkeypatch):
    """调度方未注入 expected_listen_port 时跳过监听端口检测项。"""
    _mock_httpx(monkeypatch, _qb_handler({"listen_port": 6881}))
    ctx = {k: v for k, v in _QB_CTX.items() if k != "expected_listen_port"}
    report = await QBittorrentClient().inspect(ctx)
    assert "listening_port" not in {i.id for i in report.items}


@pytest.mark.asyncio
async def test_qb_apply_fix_writes_preferences(monkeypatch):
    """fixable 项合并为一次 setPreferences 调用，未知项报不支持。"""
    captured: list = []
    _mock_httpx(monkeypatch, _qb_handler({}, captured=captured))
    results = await QBittorrentClient().apply_fix(
        _QB_CTX, ["save_path", "queueing", "network_discovery", "unknown"]
    )
    by_id = {r.id: r for r in results}

    assert by_id["save_path"].ok is True
    assert by_id["queueing"].ok is True
    assert by_id["network_discovery"].ok is True
    assert by_id["unknown"].ok is False
    assert "不支持自动修复" in by_id["unknown"].message

    assert len(captured) == 1
    assert captured[0]["default_save_path"] == "/downloads"
    assert captured[0]["queueing_enabled"] is True
    assert captured[0]["dht"] is True and captured[0]["pex"] is True


def test_single_shared_root_derives_subdirectories():
    """单共享根目录时，下载/媒体库路径落在其 downloads / media 子目录。"""
    shared_config = {
        "volumes": [{"host_path": "movie-auto", "container_path": "/data"}]
    }
    ctx = {"shared_config": shared_config}

    assert MoviePilotClient._shared_container_path(ctx, "download") == "/data/downloads"
    assert MoviePilotClient._shared_container_path(ctx, "library") == "/data/media"
    assert QBittorrentClient._expected_download_dir(shared_config) == "/data/downloads"
    assert JellyfinClient._expected_media_dir(shared_config) == "/data/media"


# ---------------- Jellyfin ----------------

_WEBHOOK_URL = "http://media-moviepilot:3000/api/v1/webhook?token=mp-token"

_JELLYFIN_CTX = {
    "url": "http://jellyfin:8096",
    "api_key": "jf-token",
    "shared_config": {"volumes": [{"host_path": "media", "container_path": "/media"}]},
    "mediaserver_wiring": {"name": "jellyfin", "webhook_url": _WEBHOOK_URL},
}


def _jellyfin_handler(
    folders=None,
    plugins=None,
    plugin_config: str | None = None,
    auth_ok: bool = True,
):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/System/Info":
            if not auth_ok:
                return httpx.Response(401)
            return httpx.Response(200, json={"Version": "10.9.11"})
        if path == "/Library/VirtualFolders":
            return httpx.Response(200, json=folders or [])
        if path == "/Plugins":
            return httpx.Response(200, json=plugins or [])
        if path.endswith("/Configuration"):
            if plugin_config is None:
                return httpx.Response(404)
            return httpx.Response(200, text=plugin_config)
        return httpx.Response(404)

    return handler


@pytest.mark.asyncio
async def test_jellyfin_inspect_auth_failure(monkeypatch):
    """API Key 认证失败时只报 auth 一项。"""
    _mock_httpx(monkeypatch, _jellyfin_handler(auth_ok=False))
    report = await JellyfinClient().inspect(_JELLYFIN_CTX)

    assert len(report.items) == 1
    assert report.items[0].id == "auth"
    assert report.items[0].status == "error"


@pytest.mark.asyncio
async def test_jellyfin_inspect_healthy_with_webhook_configured(monkeypatch):
    """媒体库命中共享目录且 Webhook 已反配时全部通过。"""
    plugins = [{"Name": "Webhook", "Id": "abc123", "Version": "16.0.0"}]
    config = json.dumps({"Destinations": [{"WebhookUri": _WEBHOOK_URL}]})
    _mock_httpx(
        monkeypatch,
        _jellyfin_handler(
            folders=[{"Name": "movies", "Locations": ["/media/movies"]}],
            plugins=plugins,
            plugin_config=config,
        ),
    )
    report = await JellyfinClient().inspect(_JELLYFIN_CTX)
    items = {i.id: i for i in report.items}

    assert items["auth"].status == "ok"
    assert items["libraries"].status == "ok"
    assert items["webhook_plugin"].status == "ok"
    assert items["webhook_configured"].status == "ok"
    assert items["webhook_configured"].fixable is False


@pytest.mark.asyncio
async def test_jellyfin_inspect_empty_libraries(monkeypatch):
    """无媒体库时报 error 且不可自动修复。"""
    _mock_httpx(monkeypatch, _jellyfin_handler())
    report = await JellyfinClient().inspect(_JELLYFIN_CTX)
    items = {i.id: i for i in report.items}

    assert items["libraries"].status == "error"
    assert items["libraries"].fixable is False
    assert items["webhook_plugin"].status == "warn"
    assert items["webhook_configured"].status == "warn"


@pytest.mark.asyncio
async def test_jellyfin_inspect_webhook_not_configured_gives_guidance(monkeypatch):
    """插件已装但未反配时给出指引，期望值为装配记录的 webhook_url。"""
    plugins = [{"Name": "Webhook", "Id": "abc123", "Version": "16.0.0"}]
    _mock_httpx(
        monkeypatch,
        _jellyfin_handler(
            folders=[{"Name": "movies", "Locations": ["/media/movies"]}],
            plugins=plugins,
            plugin_config=json.dumps({"Destinations": []}),
        ),
    )
    report = await JellyfinClient().inspect(_JELLYFIN_CTX)
    items = {i.id: i for i in report.items}

    assert items["webhook_configured"].status == "warn"
    assert items["webhook_configured"].expected_value == _WEBHOOK_URL
    assert items["webhook_configured"].fixable is False


@pytest.mark.asyncio
async def test_jellyfin_setup_webhook_degrades_to_guidance(monkeypatch):
    """Webhook 反配为降级方案：不调用写入接口，返回手动配置指引。"""
    plugins = [{"Name": "Webhook", "Id": "abc123", "Version": "16.0.0"}]
    _mock_httpx(monkeypatch, _jellyfin_handler(plugins=plugins))
    ok, message = await JellyfinClient().setup_webhook(
        _JELLYFIN_CTX, _WEBHOOK_URL, "mp-token"
    )

    assert ok is False
    assert "手动" in message
    assert _WEBHOOK_URL in message


@pytest.mark.asyncio
async def test_jellyfin_inspect_sends_token_in_authorization_header(monkeypatch):
    """Jellyfin 12 仅接受 Authorization 头中的 Token，检测需按该形态发送。"""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/System/Info":
            seen["authorization"] = request.headers.get("Authorization")
            return httpx.Response(200, json={"Version": "12.2.0"})
        if path == "/Library/VirtualFolders":
            return httpx.Response(200, json=[])
        if path == "/Plugins":
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    _mock_httpx(monkeypatch, handler)
    report = await JellyfinClient().inspect(_JELLYFIN_CTX)
    items = {i.id: i for i in report.items}

    assert items["auth"].status == "ok"
    assert 'Token="jf-token"' in seen["authorization"]


@pytest.mark.asyncio
async def test_jellyfin_verify_api_key_sends_token_in_authorization_header(monkeypatch):
    """认证校验同样使用 Authorization 头中的 Token 形态。"""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"Version": "12.2.0"})

    _mock_httpx(monkeypatch, handler)
    result = await JellyfinClient().verify_auth(
        {
            "url": "http://jellyfin:8096",
            "auth_type": "api_key",
            "api_key": "jf-token",
        }
    )

    assert result.valid is True
    assert 'Token="jf-token"' in seen["authorization"]


def _jellyfin_auth_handler(state: dict, folders=None):
    """带账号密码状态模拟的 Jellyfin 认证接口。"""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/System/Info":
            token = request.headers.get("X-Emby-Token")
            authorization = request.headers.get("Authorization") or ""
            # Jellyfin 12 要求 Token 出现在 Authorization 头中
            if (
                token
                and token == state.get("valid_token")
                and f'Token="{token}"' in authorization
            ):
                return httpx.Response(200, json={"Version": "10.9.11"})
            return httpx.Response(401)
        if path == "/System/Info/Public":
            return httpx.Response(
                200,
                json={"StartupWizardCompleted": state.get("wizard_completed", True)},
            )
        if path == "/Users/AuthenticateByName":
            state.setdefault("login_headers", []).append(
                request.headers.get("Authorization") or ""
            )
            body = json.loads(request.content or b"{}")
            if (
                body.get("Username") == state.get("username")
                and state.get("password") is not None
                and body.get("Pw") == state.get("password")
            ):
                return httpx.Response(
                    200,
                    json={
                        "AccessToken": state["valid_token"],
                        "User": {"Id": state.get("user_id", "u1")},
                    },
                )
            return httpx.Response(401)
        if path == "/Users":
            policy = {"IsAdministrator": state.get("admin", True)}
            return httpx.Response(
                200,
                json=[
                    {
                        "Id": state.get("user_id", "u1"),
                        "Name": state.get("username", "admin"),
                        "Policy": policy,
                    }
                ],
            )
        if path == "/Users/Me":
            return httpx.Response(
                200,
                json={
                    "Id": state.get("user_id", "u1"),
                    "Name": state.get("username", "admin"),
                },
            )
        if path.endswith("/Password"):
            body = json.loads(request.content or b"{}")
            if body.get("ResetPassword"):
                state["password"] = None
                return httpx.Response(204)
            if state.get("password") is not None:
                # 已有密码时必须提供正确的当前密码
                return httpx.Response(401)
            state["password"] = body.get("NewPw")
            return httpx.Response(204)
        if path == "/Startup/User":
            if request.method == "GET":
                return httpx.Response(200, json={"Name": state.get("username") or "root"})
            body = json.loads(request.content or b"{}")
            state["username"] = body.get("Name")
            state["password"] = body.get("Password")
            state["wizard_completed"] = True
            return httpx.Response(204)
        if path == "/Startup/Complete":
            return httpx.Response(204)
        if path == "/Library/VirtualFolders":
            return httpx.Response(200, json=folders or [])
        if path == "/Plugins":
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    return handler


@pytest.mark.asyncio
async def test_jellyfin_inspect_reports_stale_api_key_as_fixable(monkeypatch):
    """API Key 失效但账号密码可登录时，认证项可修复、账号项通过。"""
    state = {"username": "admin", "password": "pass", "valid_token": "fresh"}
    _mock_httpx(monkeypatch, _jellyfin_auth_handler(state))
    ctx = {
        **_JELLYFIN_CTX,
        "api_key": "stale",
        "username": "admin",
        "password": "pass",
    }
    report = await JellyfinClient().inspect(ctx)
    items = {i.id: i for i in report.items}

    assert items["auth"].status == "error"
    assert items["auth"].fixable is True
    assert "账号密码" in items["auth"].message
    assert items["credentials"].status == "ok"
    # 校验登录必须使用独立设备，避免顶掉已保存的 API Token
    assert 'DeviceId="onetap-inspect"' in state["login_headers"][0]


@pytest.mark.asyncio
async def test_jellyfin_fix_refreshes_api_key_via_password(monkeypatch):
    """修复认证项：用账号密码重新登录并回写新 API Key。"""
    state = {"username": "admin", "password": "pass", "valid_token": "fresh"}
    _mock_httpx(monkeypatch, _jellyfin_auth_handler(state))
    save = AsyncMock()
    wire = AsyncMock()
    ctx = {
        **_JELLYFIN_CTX,
        "api_key": "stale",
        "username": "admin",
        "password": "pass",
        "save_app_config": save,
        "wire": wire,
    }

    results = await JellyfinClient().apply_fix(ctx, ["auth"])

    assert results[0].ok is True
    saved = save.await_args.args[0]
    assert saved["api_key"] == "fresh"
    assert saved["username"] == "admin"
    assert saved["password"] == "pass"
    assert saved["provisioned"] is True
    wire.assert_awaited_once()
    # 保存 Token 的登录使用持久化设备，而不是检测设备
    assert 'DeviceId="onetap"' in state["login_headers"][-1]


@pytest.mark.asyncio
async def test_jellyfin_inspect_detects_wrong_password_with_valid_api_key(monkeypatch):
    """API Key 有效但账号密码错误时，账号项可修复且 API 认证仍通过。"""
    state = {"username": "admin", "password": "old-real", "valid_token": "good"}
    _mock_httpx(monkeypatch, _jellyfin_auth_handler(state))
    ctx = {
        **_JELLYFIN_CTX,
        "api_key": "good",
        "username": "admin",
        "password": "desired",
    }
    report = await JellyfinClient().inspect(ctx)
    items = {i.id: i for i in report.items}

    assert items["auth"].status == "ok"
    assert items["credentials"].status == "warn"
    assert items["credentials"].fixable is True


@pytest.mark.asyncio
async def test_jellyfin_fix_resets_password_with_valid_api_key(monkeypatch):
    """修复账号项：用管理员 Token 清除并重设密码，再回写新凭据。"""
    state = {"username": "admin", "password": "old-real", "valid_token": "good"}
    _mock_httpx(monkeypatch, _jellyfin_auth_handler(state))
    save = AsyncMock()
    ctx = {
        **_JELLYFIN_CTX,
        "api_key": "good",
        "username": "admin",
        "password": "desired",
        "save_app_config": save,
    }

    results = await JellyfinClient().apply_fix(ctx, ["auth", "credentials"])

    assert all(result.ok for result in results)
    assert state["password"] == "desired"
    assert save.await_args.args[0]["password"] == "desired"


@pytest.mark.asyncio
async def test_jellyfin_fix_creates_admin_when_wizard_incomplete(monkeypatch):
    """全新实例（启动向导未完成）可通过向导重建管理员。"""
    state = {
        "username": None,
        "password": None,
        "valid_token": "boot-token",
        "wizard_completed": False,
    }
    _mock_httpx(monkeypatch, _jellyfin_auth_handler(state))
    save = AsyncMock()
    ctx = {
        **_JELLYFIN_CTX,
        "api_key": "",
        "username": "admin",
        "password": "pass",
        "save_app_config": save,
    }

    report = await JellyfinClient().inspect(ctx)
    items = {i.id: i for i in report.items}
    assert items["auth"].fixable is True

    results = await JellyfinClient().apply_fix(ctx, ["auth"])
    assert results[0].ok is True
    assert state["username"] == "admin"
    assert state["password"] == "pass"
    assert save.await_args.args[0]["api_key"] == "boot-token"


@pytest.mark.asyncio
async def test_jellyfin_fix_reports_unrecoverable_credentials(monkeypatch):
    """向导已完成且两种凭据都无效时，明确报告无法自动修复。"""
    state = {"username": "admin", "password": "other", "valid_token": "other-token"}
    _mock_httpx(monkeypatch, _jellyfin_auth_handler(state))
    save = AsyncMock()
    ctx = {
        **_JELLYFIN_CTX,
        "api_key": "stale",
        "username": "admin",
        "password": "desired",
        "save_app_config": save,
    }

    report = await JellyfinClient().inspect(ctx)
    items = {i.id: i for i in report.items}
    assert items["auth"].fixable is False
    assert items["credentials"].fixable is False

    results = await JellyfinClient().apply_fix(ctx, ["auth"])
    assert results[0].ok is False
    assert "重置管理员密码" in results[0].message
    save.assert_not_awaited()


@pytest.mark.asyncio
async def test_jellyfin_inspect_handles_server_error(monkeypatch):
    """服务端 5xx 时返回检测项而不是抛出异常。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    _mock_httpx(monkeypatch, handler)
    report = await JellyfinClient().inspect(_JELLYFIN_CTX)

    assert report.items[0].id == "auth"
    assert report.items[0].status == "error"
    assert "500" in report.items[0].message


# ---------------- MoviePilot ----------------

_MP_CTX = {
    "url": "http://mp:3000",
    "api_key": "mp-token",
    "integrations": {
        "downloaders": [{"name": "qbittorrent", "status": "ok"}],
        "mediaservers": [{"name": "jellyfin", "status": "ok"}],
    },
    "shared_config": {
        "volumes": [
            {"host_path": "media", "container_path": "/media"},
            {"host_path": "downloads", "container_path": "/downloads"},
        ]
    },
}

_MP_ENV = {
    "AUTH_SITE": "iyuu",
    "SUBSCRIBE_SEARCH": True,
    "PROXY_HOST": "http://proxy:7890",
    "GITHUB_TOKEN": "ghp-token",
    "API_TOKEN": "0123456789abcdef01234567",
}

_MP_LIBRARIES = {
    "jellyfin": [{"id": 1, "name": "电影"}, {"id": 2, "name": "电视剧"}]
}


def _mp_handler(store: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/user/":
            return httpx.Response(200, json=[])
        if path == "/api/v1/system/env":
            return httpx.Response(
                200, json={"success": True, "data": store.get("_env", _MP_ENV)}
            )
        if path == "/api/v1/site/":
            return httpx.Response(200, json=store.get("_sites", [{"id": 1}]))
        if path == "/api/v1/plugin/installed":
            return httpx.Response(200, json=store.get("_plugins", ["P1"]))
        if path == "/api/v1/mediaserver/library":
            server = parse_qs(request.url.query.decode()).get("server", [""])[0]
            libraries = store.get("_libraries", _MP_LIBRARIES).get(server)
            if libraries is None:
                return httpx.Response(
                    200, json={"success": False, "message": "媒体服务器请求失败"}
                )
            return httpx.Response(
                200, json={"success": True, "data": libraries}
            )
        if path.startswith("/api/v1/system/setting/"):
            key = path.rsplit("/", 1)[-1]
            if request.method == "POST":
                body = json.loads(request.content or b"null")
                if isinstance(body, dict) and "value" in body:
                    body = body["value"]
                store[key] = body
                return httpx.Response(200, json={"success": True})
            return httpx.Response(
                200, json={"success": True, "data": {"value": store.get(key)}}
            )
        return httpx.Response(404)

    return handler


@pytest.mark.asyncio
async def test_mp_inspect_wired_items(monkeypatch):
    """下载器按装配结果核对，媒体服务器缺失时报 error 且 fixable。"""
    store = {
        "Downloaders": [{"name": "qbittorrent", "enabled": True}],
        "MediaServers": [],
        "Directories": [
            {"name": "下载", "download_path": "/downloads", "library_path": "/media"}
        ],
    }
    _mock_httpx(monkeypatch, _mp_handler(store))
    report = await MoviePilotClient().inspect(_MP_CTX)
    items = {i.id: i for i in report.items}

    assert items["auth"].status == "ok"
    assert items["downloader_configured"].status == "ok"
    assert items["downloader_configured"].current_value == "qbittorrent"

    assert items["mediaserver_configured"].status == "error"
    assert "jellyfin" in items["mediaserver_configured"].message
    assert items["mediaserver_configured"].fixable is True

    assert items["directories_configured"].status == "ok"
    assert items["transfer_mode"].status == "warn"


@pytest.mark.asyncio
async def test_mp_inspect_directories_empty_warns(monkeypatch):
    """目录设置为空时报 warn 且可自动补全。"""
    _mock_httpx(monkeypatch, _mp_handler({"Directories": []}))
    report = await MoviePilotClient().inspect(
        {k: v for k, v in _MP_CTX.items() if k != "integrations"}
    )
    items = {i.id: i for i in report.items}

    assert items["directories_configured"].status == "warn"
    assert items["directories_configured"].fixable is True
    assert items["transfer_mode"].status == "warn"


@pytest.mark.asyncio
async def test_mp_inspect_optimization_items(monkeypatch):
    """站点/认证/订阅/通知/代理等优化项按配置状态给出结论。"""
    store = {
        "Downloaders": [{"name": "qbittorrent", "enabled": True}],
        "MediaServers": [{"name": "jellyfin", "enabled": True, "sync_libraries": [1, 2]}],
        "Directories": [
            {
                "name": "下载",
                "download_path": "/downloads",
                "library_path": "/media",
                "storage": "local",
                "transfer_type": "link",
            }
        ],
        "Notifications": [],
        "_env": {
            **_MP_ENV,
            "AUTH_SITE": "",
            "SUBSCRIBE_SEARCH": False,
            "PROXY_HOST": None,
            "GITHUB_TOKEN": None,
            "API_TOKEN": "short",
        },
        "_sites": [],
    }
    _mock_httpx(monkeypatch, _mp_handler(store))
    report = await MoviePilotClient().inspect(_MP_CTX)
    items = {i.id: i for i in report.items}

    assert items["mediaserver_connection"].status == "ok"
    assert items["mediaserver_library_sync"].status == "ok"
    assert items["sites_configured"].status == "warn"
    assert items["site_auth"].status == "warn"
    assert items["subscribe_search"].status == "warn"
    assert items["subscribe_search"].fixable is True
    assert items["notifications_configured"].status == "warn"
    assert items["proxy_configured"].status == "warn"
    assert items["github_token"].status == "warn"
    assert items["plugins_installed"].status == "ok"
    assert items["api_token_strength"].status == "warn"


@pytest.mark.asyncio
async def test_mp_apply_fix_writes_settings(monkeypatch):
    """目录、硬链接和订阅搜索检测项写入设置后回读验证。"""
    store = {"Directories": [], "SUBSCRIBE_SEARCH": False}
    _mock_httpx(monkeypatch, _mp_handler(store))
    results = await MoviePilotClient().apply_fix(
        _MP_CTX,
        ["directories_configured", "transfer_mode", "subscribe_search"],
    )
    by_id = {r.id: r for r in results}

    assert all(result.ok for result in results), by_id
    directories = store["Directories"]
    assert directories[0]["download_path"] == "/downloads"
    assert directories[0]["library_path"] == "/media"
    assert directories[0]["transfer_type"] == "link"
    assert store["SUBSCRIBE_SEARCH"] is True


@pytest.mark.asyncio
async def test_mp_fix_mediaserver_library_sync(monkeypatch):
    """媒体库同步修复会把全部媒体库 ID 写入 sync_libraries。"""
    store = {
        "MediaServers": [
            {"name": "jellyfin", "enabled": True, "sync_libraries": []}
        ]
    }
    _mock_httpx(monkeypatch, _mp_handler(store))
    results = await MoviePilotClient().apply_fix(
        _MP_CTX, ["mediaserver_library_sync"]
    )

    assert results[0].ok is True
    assert store["MediaServers"][0]["sync_libraries"] == ["1", "2"]


@pytest.mark.asyncio
async def test_mp_apply_fix_triggers_wire_callback(monkeypatch):
    """装配类检测项修复触发 wire 回调并重验，多个装配项只重跑一次。"""
    store = {"Downloaders": [], "MediaServers": []}

    async def _wire():
        # 模拟装配生效
        store["Downloaders"] = [{"name": "qbittorrent", "enabled": True}]
        store["MediaServers"] = [{"name": "jellyfin", "enabled": True}]

    wire = AsyncMock(side_effect=_wire)
    _mock_httpx(monkeypatch, _mp_handler(store))
    ctx = {**_MP_CTX, "wire": wire}
    results = await MoviePilotClient().apply_fix(
        ctx,
        ["downloader_configured", "mediaserver_configured", "unknown_item"],
    )
    by_id = {r.id: r for r in results}

    wire.assert_awaited_once()
    assert by_id["downloader_configured"].ok is True
    assert by_id["mediaserver_configured"].ok is True
    assert by_id["unknown_item"].ok is False
    assert "不支持自动修复" in by_id["unknown_item"].message


# ---------------- MoviePilot 插件管理 ----------------


@pytest.mark.asyncio
async def test_mp_list_plugins_tolerates_bare_list_and_market_failure(monkeypatch):
    """已安装列表容错裸列表解析，市场接口失败时降级为空列表。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/plugin/":
            return httpx.Response(200, json=[{"id": "p1", "plugin_name": "已装插件"}])
        if request.url.path == "/api/v1/plugin/market":
            return httpx.Response(500)
        return httpx.Response(404)

    _mock_httpx(monkeypatch, handler)
    ok, data, err = await MoviePilotClient().list_plugins("http://mp:3000", "tok")

    assert ok is True
    assert err is None
    assert data["installed"] == [{"id": "p1", "plugin_name": "已装插件"}]
    assert data["market"] == []


@pytest.mark.asyncio
async def test_mp_list_plugins_parses_wrapped_market(monkeypatch):
    """市场列表容错包装结构解析。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/plugin/":
            return httpx.Response(200, json={"success": True, "data": [{"id": "p1"}]})
        if request.url.path == "/api/v1/plugin/market":
            return httpx.Response(
                200, json={"success": True, "data": {"value": [{"id": "p2"}]}}
            )
        return httpx.Response(404)

    _mock_httpx(monkeypatch, handler)
    ok, data, err = await MoviePilotClient().list_plugins("http://mp:3000", "tok")

    assert ok is True
    assert data["installed"] == [{"id": "p1"}]
    assert data["market"] == [{"id": "p2"}]


@pytest.mark.asyncio
async def test_mp_install_plugin_retries_param_forms(monkeypatch):
    """安装插件时 422 触发参数形态重试。"""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "repo_url=" in str(request.url):
            return httpx.Response(422, json={"detail": "unprocessable"})
        return httpx.Response(200, json={"success": True})

    _mock_httpx(monkeypatch, handler)
    ok, err = await MoviePilotClient().install_plugin(
        "http://mp:3000", "tok", "PluginID", repo_url="https://github.com/x/y"
    )

    assert ok is True
    assert err is None
    assert len(calls) == 2
    assert "repo_url=" in calls[0]
    assert calls[1].endswith("/api/v1/plugin/install/PluginID")


@pytest.mark.asyncio
async def test_mp_install_plugin_reports_failure_message(monkeypatch):
    """安装失败时透传 MoviePilot 返回的 message。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": False, "message": "插件包下载失败"})

    _mock_httpx(monkeypatch, handler)
    ok, err = await MoviePilotClient().install_plugin("http://mp:3000", "tok", "P1")

    assert ok is False
    assert err == "插件包下载失败"
