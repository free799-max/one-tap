"""应用检测/修复/插件管理 API 与调度层测试。"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.database import Base, get_db
from app.main import app
from app.models.orchestration import AppOrchestration, AppOrchestrationInstance
from app.services.apps.base import FixResultItem, InspectItem, InspectReport
from app.services.apps.jellyfin import JellyfinClient
from app.services.apps.moviepilot import MoviePilotClient
from app.services.orchestration import orchestration_service


_COMPOSITION = [
    {
        "app_name": "moviepilot",
        "relation": "required",
        "integrations": [
            {"type": "downloader", "from": "qbittorrent"},
            {"type": "mediaserver", "from": ["jellyfin", "emby"]},
        ],
    },
    {"app_name": "qbittorrent", "relation": "required"},
    {"app_name": "jellyfin", "relation": "optional", "group": "player"},
]

_GROUP_APP_CONFIGS = {
    "moviepilot": {
        "url": "http://192.168.1.10:3000",
        "api_key": "mp-token",
        "provisioned": True,
        "integrations": {
            "downloaders": [{"name": "qbittorrent", "status": "ok"}],
            "mediaservers": [
                {
                    "name": "jellyfin",
                    "status": "ok",
                    "webhook_url": "http://192.168.1.10:3000/api/v1/webhook?token=mp-token",
                }
            ],
        },
    },
    "jellyfin": {
        "url": "http://192.168.1.10:8096",
        "api_key": "jf-token",
        "provisioned": True,
    },
    # 无客户端支持的应用：检测应返回 supported=false
    "transmission": {"url": "http://192.168.1.10:9091"},
}

_SHARED_CONFIG = {
    "volumes": [
        {"host_path": "media", "container_path": "/media"},
        {"host_path": "downloads", "container_path": "/downloads"},
    ],
    "env": [{"key": "TZ", "value": "Asia/Shanghai"}],
}


@pytest_asyncio.fixture(name="client_with_group")
async def _client_with_group():
    """提供已初始化并含一个编排实例组的 HTTP 测试客户端与引擎。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    @asynccontextmanager
    async def _noop_lifespan(app):
        yield

    app.router.lifespan_context = _noop_lifespan

    SessionLocal = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async def get_test_db():
        async with SessionLocal() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = get_test_db

    async with SessionLocal() as session:
        orch = AppOrchestration(
            name="media-stack",
            display_name="影视媒体栈",
            category="media",
            app_composition=_COMPOSITION,
            shared_config_schema={},
        )
        session.add(orch)
        await session.flush()
        group = AppOrchestrationInstance(
            orchestration_id=orch.id,
            instance_name="media",
            status="running",
            shared_config=_SHARED_CONFIG,
            app_configs=_GROUP_APP_CONFIGS,
        )
        session.add(group)
        await session.commit()
        await session.refresh(group)
        group_id = group.id

    transport = ASGITransport(app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c.group_id = group_id
        c.session_local = SessionLocal
        yield c

    app.dependency_overrides.clear()
    await engine.dispose()


async def _login_admin(client) -> str:
    """注册首个用户（自动成为管理员）并登录，返回访问令牌。"""
    await client.post(
        "/api/auth/register", json={"username": "admin", "password": "admin12345"}
    )
    resp = await client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin12345"}
    )
    return resp.json()["data"]["access_token"]


# ---------------- 调度层 ctx 构建 ----------------


@pytest.mark.asyncio
async def test_inspect_app_builds_ctx(client_with_group, monkeypatch):
    """inspect_app 注入容器名/共享配置/装配记录/端口/装配回调等上下文。"""
    captured: dict = {}

    async def _fake_inspect(self, ctx):
        captured.update(ctx)
        return InspectReport(supported=True, items=[])

    monkeypatch.setattr(MoviePilotClient, "inspect", _fake_inspect)
    monkeypatch.setattr(
        orchestration_service, "_get_container_port", lambda p, port: 3000
    )

    async with client_with_group.session_local() as db:
        report = await orchestration_service.inspect_app(
            db, client_with_group.group_id, "moviepilot"
        )

    assert report.supported is True
    assert captured["url"] == "http://192.168.1.10:3000"
    assert captured["api_key"] == "mp-token"
    assert captured["container_name"] == "media-moviepilot"
    assert captured["shared_config"] == _SHARED_CONFIG
    assert captured["integrations"]["mediaservers"][0]["name"] == "jellyfin"
    assert captured["expected_listen_port"] == 3000
    assert captured["host_header_port"] == 3000
    assert callable(captured["wire"])


@pytest.mark.asyncio
async def test_qb_inspect_uses_torrenting_port_not_webui_port(
    client_with_group, monkeypatch
):
    """qBittorrent 的监听端口检测读取 BT 环境变量，而非 WebUI 映射。"""
    captured: dict = {}

    class _FakeClient:
        async def inspect(self, ctx):
            captured.update(ctx)
            return InspectReport(supported=True, items=[])

    monkeypatch.setattr(
        orchestration_service, "_resolve_app_client", lambda name: _FakeClient()
    )
    monkeypatch.setattr(
        orchestration_service, "_get_container_port", lambda p, port: 8016
    )
    monkeypatch.setattr(
        orchestration_service, "_get_qbittorrent_listen_port", lambda name: 9019
    )

    async with client_with_group.session_local() as db:
        group = await orchestration_service.get_group(db, client_with_group.group_id)
        group.app_configs = {
            **(group.app_configs or {}),
            "qbittorrent": {
                "url": "http://192.168.1.10:9036",
                "username": "admin",
                "password": "pass",
            },
        }
        await db.commit()
        await orchestration_service.inspect_app(
            db, client_with_group.group_id, "qbittorrent"
        )

    assert captured["host_header_port"] == 8016
    assert captured["expected_listen_port"] == 9019


@pytest.mark.asyncio
async def test_inspect_app_injects_mediaserver_wiring(client_with_group, monkeypatch):
    """媒体服务器应用可拿到 MoviePilot 侧的装配记录（webhook 反配检测用）。"""
    captured: dict = {}

    async def _fake_inspect(self, ctx):
        captured.update(ctx)
        return InspectReport(supported=True, items=[])

    monkeypatch.setattr(JellyfinClient, "inspect", _fake_inspect)
    monkeypatch.setattr(
        orchestration_service, "_get_container_port", lambda p, port: 8096
    )

    async with client_with_group.session_local() as db:
        await orchestration_service.inspect_app(
            db, client_with_group.group_id, "jellyfin"
        )

    wiring = captured.get("mediaserver_wiring")
    assert wiring is not None
    assert wiring["name"] == "jellyfin"
    assert wiring["webhook_url"].endswith("/api/v1/webhook?token=mp-token")
    assert callable(captured["save_app_config"])


@pytest.mark.asyncio
async def test_inspect_ctx_persists_recovered_credentials(
    client_with_group, monkeypatch
):
    """检测上下文提供的回调可把认证修复结果写回 app_configs。"""
    captured: dict = {}

    async def _fake_inspect(self, ctx):
        captured.update(ctx)
        return InspectReport(supported=True, items=[])

    monkeypatch.setattr(JellyfinClient, "inspect", _fake_inspect)
    monkeypatch.setattr(
        orchestration_service, "_get_container_port", lambda p, port: 8096
    )

    async with client_with_group.session_local() as db:
        await orchestration_service.inspect_app(
            db, client_with_group.group_id, "jellyfin"
        )
        await captured["save_app_config"](
            {
                "api_key": "new-token",
                "username": "admin",
                "password": "pass",
                "provisioned": True,
            }
        )

    async with client_with_group.session_local() as db:
        group = await orchestration_service.get_group(db, client_with_group.group_id)
        entry = group.app_configs["jellyfin"]
        assert entry["api_key"] == "new-token"
        assert entry["username"] == "admin"
        assert entry["password"] == "pass"
        assert entry["provisioned"] is True
        # 其他应用的配置不受影响
        assert group.app_configs["moviepilot"]["api_key"] == "mp-token"


# ---------------- inspect / fix API ----------------


@pytest.mark.asyncio
async def test_inspect_endpoint_requires_auth(client_with_group):
    """未认证时拒绝访问。"""
    resp = await client_with_group.get(
        f"/api/orchestrations/instances/{client_with_group.group_id}/apps/moviepilot/inspect"
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_inspect_endpoint_404_for_unknown_app(client_with_group):
    """应用不在组内时返回 404。"""
    token = await _login_admin(client_with_group)
    resp = await client_with_group.get(
        f"/api/orchestrations/instances/{client_with_group.group_id}/apps/unknown/inspect",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_inspect_endpoint_unsupported_app_returns_supported_false(
    client_with_group,
):
    """无客户端支持的应用返回 supported=false 而非报错。"""
    token = await _login_admin(client_with_group)
    resp = await client_with_group.get(
        f"/api/orchestrations/instances/{client_with_group.group_id}/apps/transmission/inspect",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["supported"] is False
    assert data["items"] == []


@pytest.mark.asyncio
async def test_inspect_endpoint_returns_report(client_with_group, monkeypatch):
    """检测端点序列化 InspectReport。"""
    report = InspectReport(
        supported=True,
        items=[
            InspectItem(
                id="auth", title="API Key 认证", status="ok", message="认证成功"
            )
        ],
    )
    monkeypatch.setattr(
        MoviePilotClient, "inspect", AsyncMock(return_value=report)
    )
    monkeypatch.setattr(
        orchestration_service, "_get_container_port", lambda p, port: 3000
    )
    token = await _login_admin(client_with_group)

    resp = await client_with_group.get(
        f"/api/orchestrations/instances/{client_with_group.group_id}/apps/moviepilot/inspect",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["supported"] is True
    assert data["items"][0]["id"] == "auth"
    assert data["items"][0]["fixable"] is False


@pytest.mark.asyncio
async def test_fix_endpoint_returns_results(client_with_group, monkeypatch):
    """修复端点序列化 FixResultItem 列表。"""
    results = [FixResultItem(id="save_path", ok=True, message="已修复")]
    monkeypatch.setattr(
        MoviePilotClient, "apply_fix", AsyncMock(return_value=results)
    )
    monkeypatch.setattr(
        orchestration_service, "_get_container_port", lambda p, port: 3000
    )
    token = await _login_admin(client_with_group)

    resp = await client_with_group.post(
        f"/api/orchestrations/instances/{client_with_group.group_id}/apps/moviepilot/inspect/fix",
        headers={"Authorization": f"Bearer {token}"},
        json={"item_ids": ["save_path"]},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["results"] == [{"id": "save_path", "ok": True, "message": "已修复"}]


# ---------------- MoviePilot 目录与媒体库 API ----------------


_AUTO_LIBRARY_SYNC = {
    "enabled": True,
    "interval_minutes": 1440,
    "last_run_at": None,
    "last_result": None,
}


def _directory_panel_payload():
    return {
        "supported": True,
        "directories": [
            {
                "name": "电影",
                "download_path": "/downloads/movies",
                "library_path": "/media/movies",
                "transfer_type": "link",
                "custom": "keep",
            }
        ],
        "detected": {"download_path": "/downloads", "library_path": "/media"},
        "directory_template": {
            "name": "默认",
            "download_path": "/downloads",
            "library_path": "/media",
            "transfer_type": "link",
        },
        "media_servers": [
            {
                "name": "jellyfin",
                "type": "jellyfin",
                "enabled": True,
                "sync_libraries": ["1"],
                "libraries": [
                    {
                        "id": "1",
                        "name": "电影",
                        "path": ["/media/movies"],
                        "type": "movies",
                    }
                ],
                "error": None,
            }
        ],
    }


@pytest.mark.asyncio
async def test_directory_endpoints_require_auth(client_with_group):
    """目录读取、目录保存与媒体库保存三个端点均要求管理员认证。"""
    base = (
        f"/api/orchestrations/instances/{client_with_group.group_id}"
        "/apps/moviepilot"
    )
    requests = [
        ("GET", f"{base}/directories", None),
        ("PUT", f"{base}/directories", {"directories": []}),
        ("PUT", f"{base}/media-libraries", {"media_servers": []}),
        (
            "PUT",
            f"{base}/auto-library-sync",
            {"enabled": True, "interval_minutes": 1},
        ),
        ("POST", f"{base}/auto-library-sync/run", None),
    ]

    for method, url, body in requests:
        resp = await client_with_group.request(method, url, json=body)
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_directory_endpoints_reject_unsupported_and_unknown_apps(
    client_with_group,
):
    """组内非 MoviePilot 返回 400，不在组内的应用返回 404。"""
    token = await _login_admin(client_with_group)
    headers = {"Authorization": f"Bearer {token}"}
    base = f"/api/orchestrations/instances/{client_with_group.group_id}/apps"
    requests = [
        ("GET", f"{base}/jellyfin/directories", None, 400),
        ("GET", f"{base}/unknown/directories", None, 404),
        (
            "PUT",
            f"{base}/jellyfin/directories",
            {"directories": []},
            400,
        ),
        ("PUT", f"{base}/unknown/directories", {"directories": []}, 404),
        (
            "PUT",
            f"{base}/jellyfin/media-libraries",
            {"media_servers": []},
            400,
        ),
        (
            "PUT",
            f"{base}/unknown/media-libraries",
            {"media_servers": []},
            404,
        ),
        (
            "PUT",
            f"{base}/jellyfin/auto-library-sync",
            {"enabled": True, "interval_minutes": 1},
            400,
        ),
        (
            "PUT",
            f"{base}/unknown/auto-library-sync",
            {"enabled": True, "interval_minutes": 1},
            404,
        ),
        ("POST", f"{base}/jellyfin/auto-library-sync/run", None, 400),
        ("POST", f"{base}/unknown/auto-library-sync/run", None, 404),
    ]

    for method, url, body, expected_status in requests:
        resp = await client_with_group.request(
            method, url, headers=headers, json=body
        )
        assert resp.status_code == expected_status


@pytest.mark.asyncio
async def test_get_directories_endpoint_returns_panel(client_with_group, monkeypatch):
    """GET 返回完整面板并传入组共享路径配置。"""
    panel = _directory_panel_payload()
    get_panel = AsyncMock(return_value=(True, panel, None))
    monkeypatch.setattr(MoviePilotClient, "get_directory_panel", get_panel)
    token = await _login_admin(client_with_group)

    resp = await client_with_group.get(
        f"/api/orchestrations/instances/{client_with_group.group_id}/apps/moviepilot/directories",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    assert resp.json()["data"] == {
        **panel,
        "auto_library_sync": _AUTO_LIBRARY_SYNC,
    }
    assert get_panel.await_args.args[:2] == (
        "http://192.168.1.10:3000",
        "mp-token",
    )
    assert get_panel.await_args.args[2] == _SHARED_CONFIG


@pytest.mark.asyncio
async def test_put_directories_endpoint_preserves_extra_fields(
    client_with_group, monkeypatch
):
    """PUT 只提交目录数组，扩展字段原样传递并返回写后回读面板。"""
    directories = _directory_panel_payload()["directories"]
    panel = {**_directory_panel_payload(), "directories": directories}
    save = AsyncMock(return_value=(True, panel, None))
    monkeypatch.setattr(MoviePilotClient, "save_directories", save)
    token = await _login_admin(client_with_group)

    resp = await client_with_group.put(
        f"/api/orchestrations/instances/{client_with_group.group_id}/apps/moviepilot/directories",
        headers={"Authorization": f"Bearer {token}"},
        json={"directories": directories},
    )

    assert resp.status_code == 200
    assert resp.json()["data"] == {
        **panel,
        "auto_library_sync": _AUTO_LIBRARY_SYNC,
    }
    assert save.await_args.args[2] == directories
    assert save.await_args.args[3] == _SHARED_CONFIG


@pytest.mark.asyncio
async def test_put_media_libraries_endpoint_returns_latest_panel(
    client_with_group, monkeypatch
):
    """媒体库保存只提交名称与同步 ID，响应与 GET 同构。"""
    panel = _directory_panel_payload()
    save = AsyncMock(return_value=(True, panel, None))
    monkeypatch.setattr(MoviePilotClient, "save_media_libraries", save)
    token = await _login_admin(client_with_group)
    updates = [{"name": "jellyfin", "sync_libraries": ["1"]}]

    resp = await client_with_group.put(
        f"/api/orchestrations/instances/{client_with_group.group_id}/apps/moviepilot/media-libraries",
        headers={"Authorization": f"Bearer {token}"},
        json={"media_servers": updates},
    )

    assert resp.status_code == 200
    assert resp.json()["data"] == {
        **panel,
        "auto_library_sync": _AUTO_LIBRARY_SYNC,
    }
    assert save.await_args.args[2] == updates
    assert save.await_args.args[3] == _SHARED_CONFIG


@pytest.mark.asyncio
async def test_put_directories_endpoint_rejects_invalid_directory(client_with_group):
    """API Schema 在调用客户端前拒绝相对路径等非法目录。"""
    token = await _login_admin(client_with_group)
    resp = await client_with_group.put(
        f"/api/orchestrations/instances/{client_with_group.group_id}/apps/moviepilot/directories",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "directories": [
                {
                    "name": "电影",
                    "download_path": "relative",
                    "library_path": "/media",
                    "transfer_type": "link",
                }
            ]
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_put_auto_library_sync_persists_config(client_with_group, monkeypatch):
    """自动建库开关与间隔写入实例组配置，并随面板返回。"""
    panel = _directory_panel_payload()
    monkeypatch.setattr(
        MoviePilotClient,
        "get_directory_panel",
        AsyncMock(return_value=(True, panel, None)),
    )
    token = await _login_admin(client_with_group)

    resp = await client_with_group.put(
        f"/api/orchestrations/instances/{client_with_group.group_id}/apps/moviepilot/auto-library-sync",
        headers={"Authorization": f"Bearer {token}"},
        json={"enabled": False, "interval_minutes": 5},
    )

    assert resp.status_code == 200
    auto = resp.json()["data"]["auto_library_sync"]
    assert auto["enabled"] is False
    assert auto["interval_minutes"] == 5
    async with client_with_group.session_local() as db:
        group = await db.get(AppOrchestrationInstance, client_with_group.group_id)
        stored = group.app_configs["moviepilot"]["media_library_sync"]
    assert stored["enabled"] is False
    assert stored["interval_minutes"] == 5


@pytest.mark.asyncio
async def test_run_auto_library_sync_returns_created_count(
    client_with_group, monkeypatch
):
    """立即检测端点返回新建数量与刷新后的目录面板。"""
    panel = _directory_panel_payload()
    monkeypatch.setattr(
        MoviePilotClient,
        "get_directory_panel",
        AsyncMock(return_value=(True, panel, None)),
    )
    monkeypatch.setattr(
        "app.services.orchestration.media_library_sync.MediaLibrarySyncService.sync_group",
        AsyncMock(return_value=2),
    )
    token = await _login_admin(client_with_group)

    resp = await client_with_group.post(
        f"/api/orchestrations/instances/{client_with_group.group_id}/apps/moviepilot/auto-library-sync/run",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["created"] == 2
    assert data["panel"]["auto_library_sync"] == _AUTO_LIBRARY_SYNC


@pytest.mark.asyncio
async def test_put_media_libraries_endpoint_reports_invalid_server(
    client_with_group, monkeypatch
):
    """客户端拒绝无效服务器或媒体库时 API 返回 400。"""
    save = AsyncMock(return_value=(False, None, "媒体服务器不存在: missing"))
    monkeypatch.setattr(MoviePilotClient, "save_media_libraries", save)
    token = await _login_admin(client_with_group)

    resp = await client_with_group.put(
        f"/api/orchestrations/instances/{client_with_group.group_id}/apps/moviepilot/media-libraries",
        headers={"Authorization": f"Bearer {token}"},
        json={"media_servers": [{"name": "missing", "sync_libraries": []}]},
    )

    assert resp.status_code == 400
    assert "媒体服务器不存在" in resp.json()["message"]


# ---------------- 插件管理 API ----------------


@pytest.mark.asyncio
async def test_plugins_endpoint_rejects_unsupported_app(client_with_group):
    """非 MoviePilot 应用返回 400 不支持插件管理。"""
    token = await _login_admin(client_with_group)
    resp = await client_with_group.get(
        f"/api/orchestrations/instances/{client_with_group.group_id}/apps/jellyfin/plugins",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_plugins_endpoint_returns_plugin_lists(client_with_group, monkeypatch):
    """插件列表端点返回已安装与市场两组数据。"""
    payload = {"installed": [{"id": "p1"}], "market": [{"id": "p2"}]}
    monkeypatch.setattr(
        MoviePilotClient,
        "list_plugins",
        AsyncMock(return_value=(True, payload, None)),
    )
    token = await _login_admin(client_with_group)

    resp = await client_with_group.get(
        f"/api/orchestrations/instances/{client_with_group.group_id}/apps/moviepilot/plugins",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"] == payload


@pytest.mark.asyncio
async def test_plugin_install_endpoint(client_with_group, monkeypatch):
    """插件安装端点调用客户端并透传 repo_url。"""
    install = AsyncMock(return_value=(True, None))
    monkeypatch.setattr(MoviePilotClient, "install_plugin", install)
    token = await _login_admin(client_with_group)

    resp = await client_with_group.post(
        f"/api/orchestrations/instances/{client_with_group.group_id}/apps/moviepilot/plugins/install",
        headers={"Authorization": f"Bearer {token}"},
        json={"plugin_id": "P1", "repo_url": "https://github.com/x/y"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["ok"] is True
    install.assert_awaited_once()
    assert install.await_args.args[2] == "P1"
    assert install.await_args.kwargs["repo_url"] == "https://github.com/x/y"


@pytest.mark.asyncio
async def test_plugin_install_endpoint_rejects_unsupported_app(client_with_group):
    """非 MoviePilot 应用安装插件返回 400。"""
    token = await _login_admin(client_with_group)
    resp = await client_with_group.post(
        f"/api/orchestrations/instances/{client_with_group.group_id}/apps/jellyfin/plugins/install",
        headers={"Authorization": f"Bearer {token}"},
        json={"plugin_id": "P1"},
    )
    assert resp.status_code == 400
