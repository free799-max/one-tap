"""漫画自动化客户端与装配测试。"""

import json
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.database import Base
from app.models.orchestration import AppOrchestration, AppOrchestrationInstance
from app.services.app_store.app_service import _slugify
from app.services.apps.comic_auto import ComicAutoClient
from app.services.apps.kavita import KavitaClient
from app.services.apps.komga import KomgaClient
from app.services.orchestration import orchestration_service


def _mock_httpx(monkeypatch, handler):
    """用 httpx.MockTransport 拦截客户端请求。"""
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )


@pytest.mark.asyncio
async def test_comic_auto_provision_creates_admin_and_api_key(monkeypatch):
    """首次启动创建管理员，登录后生成 API Key。"""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.url.path == "/api/v1/app/init/status/":
            return httpx.Response(
                200, json={"code": 0, "data": {"is_initialized": False, "has_admin": False}}
            )
        if request.url.path == "/api/v1/app/init/setup/":
            assert json.loads(request.content) == {
                "username": "admin",
                "password": "secret",
            }
            return httpx.Response(200, json={"code": 0, "data": None})
        if request.url.path == "/api/v1/auth/login/":
            return httpx.Response(200, json={"access": "jwt-token"})
        if request.url.path == "/api/v1/app/api-keys/":
            return httpx.Response(200, json={"code": 0, "data": None})
        if request.url.path == "/api/v1/app/api-keys/reset/":
            assert request.headers["Authorization"] == "Bearer jwt-token"
            return httpx.Response(
                200, json={"code": 0, "data": {"key": "comic-auto-key"}}
            )
        return httpx.Response(404)

    _mock_httpx(monkeypatch, handler)

    result = await ComicAutoClient().provision(
        url="http://comic-auto:80",
        username="admin",
        password="secret",
    )

    assert result.ok is True
    assert result.api_key == "comic-auto-key"
    assert result.username == "admin"
    assert "POST /api/v1/app/init/setup/" in calls


@pytest.mark.asyncio
async def test_comic_auto_setup_mediaserver_writes_settings(monkeypatch):
    """装配媒体服务器时同步写入漫画目录。"""
    captured: dict[str, dict] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/auth/login/":
            return httpx.Response(200, json={"access": "jwt-token"})
        if path == "/api/v1/app/settings/" and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "cache_dir": "/old/cache",
                        "download_dir": "/old/down",
                        "cache_size_limit": 2048,
                        "cache_retention_days": 7,
                        "proxy_enabled": False,
                        "proxy_url": "",
                    },
                },
            )
        if path == "/api/v1/app/settings/" and request.method == "PUT":
            captured["settings"] = json.loads(request.content)
            return httpx.Response(200, json={"code": 0, "data": None})
        if path == "/api/v1/app/media-servers/komga/" and request.method == "PUT":
            captured["server"] = json.loads(request.content)
            return httpx.Response(200, json={"code": 0, "data": None})
        if path == "/api/v1/app/media-servers/" and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"items": [{"server_type": "komga"}]},
                },
            )
        return httpx.Response(404)

    _mock_httpx(monkeypatch, handler)

    ok, message = await ComicAutoClient().setup_mediaserver(
        "http://comic-auto:80",
        "",
        name="komga",
        type_="komga",
        host="http://komga:25600",
        api_key_ms="komga-key",
        username="admin",
        password="secret",
        library_path="/mnt/media/down",
        download_dir="/mnt/media/down",
        cache_dir="/mnt/media/cache",
    )

    assert ok is True
    assert message
    assert captured["settings"]["download_dir"] == "/mnt/media/down"
    assert captured["settings"]["cache_dir"] == "/mnt/media/cache"
    assert captured["settings"]["cache_size_limit"] == 2048
    assert captured["server"] == {
        "server_url": "http://komga:25600",
        "api_key": "komga-key",
        "is_enabled": True,
        "library_path": "/mnt/media/down",
    }


@pytest.mark.asyncio
async def test_komga_provision_claims_server_and_creates_api_key(monkeypatch):
    """Komga 首次部署通过 claim 创建管理员并生成 API Key。"""
    claimed = {"value": False}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/claim" and request.method == "GET":
            return httpx.Response(200, json={"isClaimed": claimed["value"]})
        if path == "/api/v1/claim" and request.method == "POST":
            assert request.headers["X-Komga-Email"] == "admin@onetap.local"
            assert request.headers["X-Komga-Password"] == "secret"
            claimed["value"] = True
            return httpx.Response(200, json={})
        if path == "/api/v2/users/me" and request.method == "GET":
            assert request.headers.get("Authorization", "").startswith("Basic ")
            return httpx.Response(200, json={"email": "admin@onetap.local"})
        if path == "/api/v2/users/me/api-keys" and request.method == "GET":
            return httpx.Response(200, json=[])
        if path == "/api/v2/users/me/api-keys" and request.method == "POST":
            assert json.loads(request.content) == {"comment": "onetap"}
            return httpx.Response(
                200, json={"key": "komga-key", "comment": "onetap"}
            )
        return httpx.Response(404)

    _mock_httpx(monkeypatch, handler)

    result = await KomgaClient().provision(
        url="http://komga:25600",
        username="admin",
        password="secret",
    )

    assert result.ok is True
    assert result.api_key == "komga-key"
    assert result.username == "admin@onetap.local"


@pytest.mark.asyncio
async def test_kavita_provision_registers_first_user(monkeypatch):
    """Kavita 首次部署注册管理员，登录接口返回 API Key。"""
    registered = {"value": False}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/health":
            return httpx.Response(200, text="Ok")
        if path == "/api/Account/login":
            if not registered["value"]:
                return httpx.Response(401)
            return httpx.Response(200, json={"apiKey": "kavita-key"})
        if path == "/api/Account/register":
            assert json.loads(request.content) == {
                "username": "admin",
                "password": "secret",
            }
            registered["value"] = True
            return httpx.Response(200, json={})
        return httpx.Response(404)

    _mock_httpx(monkeypatch, handler)

    result = await KavitaClient().provision(
        url="http://kavita:5000",
        username="admin",
        password="secret",
    )

    assert result.ok is True
    assert result.api_key == "kavita-key"


# ---------------- 编排自动装配 ----------------


COMICS_COMPOSITION = [
    {
        "app_name": "comic-auto",
        "relation": "required",
        "integrations": [
            {
                "type": "mediaserver",
                "from": "komga",
                "library_path": "/mnt/media/down",
                "download_dir": "/mnt/media/down",
                "cache_dir": "/mnt/media/cache",
            }
        ],
    },
    {"app_name": "komga", "relation": "optional", "group": "reader"},
]


class FakeContainer:
    """模拟 docker SDK 容器对象。"""

    def __init__(self, name: str, networks: dict):
        self.name = name
        self.attrs = {"NetworkSettings": {"Networks": networks}}


@pytest_asyncio.fixture(name="session_local")
async def _session_local(monkeypatch):
    """提供内存 SQLite 会话，并接管 wiring 模块的数据库会话。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    monkeypatch.setattr(
        "app.services.orchestration.wiring.async_session", SessionLocal
    )
    yield SessionLocal
    await engine.dispose()


@pytest.mark.asyncio
async def test_wire_comic_auto_with_komga(session_local, monkeypatch):
    """Comic Auto 使用账号密码装配 Komga，并传递目录参数。"""
    async with session_local() as db:
        orch = AppOrchestration(
            name="comics-stack",
            display_name="漫画自动化",
            category="comics",
            app_composition=COMICS_COMPOSITION,
            shared_config_schema={},
        )
        db.add(orch)
        await db.flush()
        group = AppOrchestrationInstance(
            orchestration_id=orch.id,
            instance_name="comics",
            status="running",
            app_configs={
                "comic-auto": {
                    "url": "http://192.168.1.10:8055",
                    "provisioned": True,
                    "username": "admin",
                    "password": "secret",
                },
                "komga": {
                    "url": "http://192.168.1.10:25600",
                    "provisioned": True,
                    "api_key": "komga-key",
                },
            },
        )
        db.add(group)
        await db.commit()
        await db.refresh(group)
        group_id = group.id

    ca_project = _slugify("comics-comic-auto")
    komga_project = _slugify("comics-komga")
    containers = {
        ca_project: FakeContainer(ca_project, {"stack-net": {"Gateway": "172.18.0.1"}}),
        komga_project: FakeContainer(komga_project, {"stack-net": {}}),
    }
    monkeypatch.setattr(
        orchestration_service, "_get_project_container", lambda p: containers[p]
    )
    monkeypatch.setattr(
        orchestration_service, "_get_container_port", lambda p, host_port: 25600
    )
    monkeypatch.setattr(
        orchestration_service,
        "_probe_from_container",
        staticmethod(lambda container, url: True),
    )
    setup = AsyncMock(return_value=(True, "已装配 komga（komga）"))
    monkeypatch.setattr(ComicAutoClient, "setup_mediaserver", setup)

    await orchestration_service._wire_group(group_id)

    async with session_local() as db:
        group = await db.get(AppOrchestrationInstance, group_id)
        record = group.app_configs["comic-auto"]["integrations"]["mediaservers"][0]

    assert record["status"] == "ok"
    assert record["name"] == "komga"
    assert record["url"] == "http://192.168.1.10:25600"
    kwargs = setup.await_args.kwargs
    assert kwargs["username"] == "admin"
    assert kwargs["password"] == "secret"
    assert kwargs["library_path"] == "/mnt/media/down"
    assert kwargs["download_dir"] == "/mnt/media/down"
    assert kwargs["cache_dir"] == "/mnt/media/cache"
    assert kwargs["api_key_ms"] == "komga-key"
