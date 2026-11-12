"""编排应用间自动装配（wiring）测试。"""

from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.database import Base
from app.models.orchestration import AppOrchestration, AppOrchestrationInstance
from app.services.app_store.app_service import _slugify
from app.services.apps.moviepilot import MoviePilotClient
from app.services.orchestration import orchestration_service


COMPOSITION_WITH_INTEGRATIONS = [
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
    {"app_name": "emby", "relation": "optional", "group": "player"},
]


@pytest_asyncio.fixture(name="session_local")
async def _session_local(monkeypatch):
    """提供内存 SQLite 会话工厂，并接管 wiring 模块的数据库会话。"""
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


async def _seed_group(session_local, app_configs) -> int:
    """写入带 integrations 声明的影视编排模板与实例组，返回组 ID。"""
    async with session_local() as db:
        orch = AppOrchestration(
            name="media-stack",
            display_name="影视媒体栈",
            category="media",
            app_composition=COMPOSITION_WITH_INTEGRATIONS,
            shared_config_schema={},
        )
        db.add(orch)
        await db.flush()
        group = AppOrchestrationInstance(
            orchestration_id=orch.id,
            instance_name="media",
            status="running",
            app_configs=app_configs,
        )
        db.add(group)
        await db.commit()
        await db.refresh(group)
        return group.id


async def _get_integrations(session_local, group_id: int) -> dict:
    async with session_local() as db:
        group = await db.get(AppOrchestrationInstance, group_id)
        return (group.app_configs.get("moviepilot") or {}).get("integrations") or {}


def _moviepilot_cfg() -> dict:
    return {
        "url": "http://192.168.1.10:3000",
        "api_key": "mp-token",
        "provisioned": True,
    }


class FakeContainer:
    """模拟 docker SDK 容器对象。"""

    def __init__(self, name: str, networks: dict):
        self.name = name
        self.attrs = {"NetworkSettings": {"Networks": networks}}


@pytest.mark.asyncio
async def test_wire_records_error_when_source_not_provisioned(session_local):
    """来源应用未完成账号配置时记录 error，不触碰 Docker。"""
    group_id = await _seed_group(
        session_local,
        {
            "moviepilot": _moviepilot_cfg(),
            "qbittorrent": {"url": "http://192.168.1.10:8081"},
        },
    )

    await orchestration_service._wire_group(group_id)

    integrations = await _get_integrations(session_local, group_id)
    downloaders = integrations["downloaders"]
    assert len(downloaders) == 1
    assert downloaders[0]["name"] == "qbittorrent"
    assert downloaders[0]["status"] == "error"
    assert "账号自动配置未完成" in downloaders[0]["message"]


@pytest.mark.asyncio
async def test_wire_records_error_when_target_not_provisioned(session_local):
    """目标应用未完成账号配置时所有来源记 error。"""
    group_id = await _seed_group(
        session_local,
        {
            "moviepilot": {"url": "http://192.168.1.10:3000"},
            "qbittorrent": {
                "url": "http://192.168.1.10:8081",
                "provisioned": True,
                "username": "admin",
                "password": "pass",
            },
        },
    )

    await orchestration_service._wire_group(group_id)

    integrations = await _get_integrations(session_local, group_id)
    assert integrations["downloaders"][0]["status"] == "error"
    assert "目标应用账号自动配置未完成" in integrations["downloaders"][0]["message"]


@pytest.mark.asyncio
async def test_wire_prefers_shared_network_container_address(
    session_local, monkeypatch
):
    """两容器共享 Docker 网络时用容器名 + 容器内端口。"""
    group_id = await _seed_group(
        session_local,
        {
            "moviepilot": _moviepilot_cfg(),
            "qbittorrent": {
                "url": "http://192.168.1.10:8081",
                "provisioned": True,
                "username": "admin",
                "password": "pass",
            },
        },
    )
    mp_project = _slugify("media-moviepilot")
    qb_project = _slugify("media-qbittorrent")
    containers = {
        mp_project: FakeContainer(mp_project, {"stack-net": {"Gateway": "172.18.0.1"}}),
        qb_project: FakeContainer(qb_project, {"stack-net": {}}),
    }
    monkeypatch.setattr(
        orchestration_service, "_get_project_container", lambda p: containers[p]
    )
    monkeypatch.setattr(
        orchestration_service, "_get_container_port", lambda p, host_port: 8080
    )
    expected_url = f"http://{qb_project}:8080"
    monkeypatch.setattr(
        orchestration_service,
        "_probe_from_container",
        staticmethod(lambda container, url: url == expected_url),
    )
    setup = AsyncMock(return_value=(True, None))
    monkeypatch.setattr(MoviePilotClient, "setup_downloader", setup)

    await orchestration_service._wire_group(group_id)

    integrations = await _get_integrations(session_local, group_id)
    record = integrations["downloaders"][0]
    assert record["status"] == "ok"
    assert record["url"] == expected_url
    setup.assert_awaited_once()
    assert setup.await_args.kwargs["host"] == expected_url


@pytest.mark.asyncio
async def test_wire_falls_back_to_gateway_address(session_local, monkeypatch):
    """无共享网络时用目标容器主网络 gateway + 来源宿主端口。"""
    group_id = await _seed_group(
        session_local,
        {
            "moviepilot": _moviepilot_cfg(),
            "qbittorrent": {
                "url": "http://192.168.1.10:8081",
                "provisioned": True,
                "username": "admin",
                "password": "pass",
            },
        },
    )
    mp_project = _slugify("media-moviepilot")
    qb_project = _slugify("media-qbittorrent")
    containers = {
        mp_project: FakeContainer(mp_project, {"mp-net": {"Gateway": "172.19.0.1"}}),
        qb_project: FakeContainer(qb_project, {"other-net": {}}),
    }
    monkeypatch.setattr(
        orchestration_service, "_get_project_container", lambda p: containers[p]
    )
    expected_url = "http://172.19.0.1:8081"
    monkeypatch.setattr(
        orchestration_service,
        "_probe_from_container",
        staticmethod(lambda container, url: url == expected_url),
    )
    setup = AsyncMock(return_value=(True, None))
    monkeypatch.setattr(MoviePilotClient, "setup_downloader", setup)

    await orchestration_service._wire_group(group_id)

    integrations = await _get_integrations(session_local, group_id)
    record = integrations["downloaders"][0]
    assert record["status"] == "ok"
    assert record["url"] == expected_url


@pytest.mark.asyncio
async def test_wire_uses_first_candidate_when_probe_unsupported(
    session_local, monkeypatch
):
    """docker exec 探测不可用（如容器无 python）时跳过探测，直接用内网地址。"""
    group_id = await _seed_group(
        session_local,
        {
            "moviepilot": _moviepilot_cfg(),
            "qbittorrent": {
                "url": "http://192.168.1.10:8081",
                "provisioned": True,
                "username": "admin",
                "password": "pass",
            },
        },
    )
    mp_project = _slugify("media-moviepilot")
    qb_project = _slugify("media-qbittorrent")
    containers = {
        mp_project: FakeContainer(mp_project, {"stack-net": {"Gateway": "172.18.0.1"}}),
        qb_project: FakeContainer(qb_project, {"stack-net": {}}),
    }
    monkeypatch.setattr(
        orchestration_service, "_get_project_container", lambda p: containers[p]
    )
    monkeypatch.setattr(
        orchestration_service, "_get_container_port", lambda p, host_port: 8080
    )

    def _raise(container, url):
        raise RuntimeError("OCI runtime exec failed: no python")

    monkeypatch.setattr(
        orchestration_service, "_probe_from_container", staticmethod(_raise)
    )
    setup = AsyncMock(return_value=(True, None))
    monkeypatch.setattr(MoviePilotClient, "setup_downloader", setup)

    await orchestration_service._wire_group(group_id)

    integrations = await _get_integrations(session_local, group_id)
    record = integrations["downloaders"][0]
    assert record["status"] == "ok"
    assert record["url"] == "http://192.168.1.10:8081"


@pytest.mark.asyncio
async def test_wire_mediaserver_picks_deployed_candidate(session_local, monkeypatch):
    """from 为列表时取实际部署的媒体服务器（emby 而非未部署的 jellyfin）。"""
    group_id = await _seed_group(
        session_local,
        {
            "moviepilot": _moviepilot_cfg(),
            "emby": {
                "url": "http://192.168.1.10:8096",
                "provisioned": True,
                "api_key": "emby-key",
            },
        },
    )
    mp_project = _slugify("media-moviepilot")
    emby_project = _slugify("media-emby")
    containers = {
        mp_project: FakeContainer(mp_project, {"stack-net": {"Gateway": "172.18.0.1"}}),
        emby_project: FakeContainer(emby_project, {"stack-net": {}}),
    }
    monkeypatch.setattr(
        orchestration_service, "_get_project_container", lambda p: containers[p]
    )
    monkeypatch.setattr(
        orchestration_service, "_get_container_port", lambda p, host_port: 8096
    )
    monkeypatch.setattr(
        orchestration_service,
        "_probe_from_container",
        staticmethod(lambda container, url: True),
    )
    setup = AsyncMock(return_value=(True, None))
    monkeypatch.setattr(MoviePilotClient, "setup_mediaserver", setup)

    await orchestration_service._wire_group(group_id)

    integrations = await _get_integrations(session_local, group_id)
    mediaservers = integrations["mediaservers"]
    assert len(mediaservers) == 1
    assert mediaservers[0]["name"] == "emby"
    assert mediaservers[0]["type"] == "emby"
    assert mediaservers[0]["status"] == "ok"
    # 探测全部连通时优先使用内网地址，play_host 同为内网地址
    assert setup.await_args.kwargs["host"] == "http://192.168.1.10:8096"
    assert setup.await_args.kwargs["play_host"] == "http://192.168.1.10:8096"
    assert setup.await_args.kwargs["api_key_ms"] == "emby-key"


@pytest.mark.asyncio
async def test_wire_mediaserver_includes_external_fnos_media(
    session_local, monkeypatch
):
    """MoviePilot 同时装配本地播放器与飞牛影视外部服务。"""
    group_id = await _seed_group(
        session_local,
        {
            "moviepilot": _moviepilot_cfg(),
            "jellyfin": {
                "url": "http://192.168.1.10:8096",
                "provisioned": True,
                "api_key": "jf-key",
            },
            "fnos-media": {
                "url": "http://192.168.1.20:5666",
                "provisioned": True,
                "username": "Cacty",
                "password": "secret",
                "access_code": "access-code",
            },
        },
    )
    mp_project = _slugify("media-moviepilot")
    jellyfin_project = _slugify("media-jellyfin")
    containers = {
        mp_project: FakeContainer(mp_project, {"stack-net": {"Gateway": "172.18.0.1"}}),
        jellyfin_project: FakeContainer(jellyfin_project, {"stack-net": {}}),
    }
    monkeypatch.setattr(
        orchestration_service, "_get_project_container", lambda p: containers[p]
    )
    monkeypatch.setattr(
        orchestration_service, "_get_container_port", lambda p, host_port: 8096
    )
    monkeypatch.setattr(
        orchestration_service,
        "_probe_from_container",
        staticmethod(lambda container, url: True),
    )
    setup = AsyncMock(return_value=(True, None))
    monkeypatch.setattr(MoviePilotClient, "setup_mediaserver", setup)

    await orchestration_service._wire_group(group_id)

    integrations = await _get_integrations(session_local, group_id)
    names = [record["name"] for record in integrations["mediaservers"]]
    assert names == ["jellyfin", "fnos-media"]
    assert all(
        record["status"] == "ok" for record in integrations["mediaservers"]
    )

    fnos_call = next(
        call
        for call in setup.await_args_list
        if call.kwargs["name"] == "fnos-media"
    )
    assert fnos_call.kwargs["host"] == "http://192.168.1.20:5666"
    assert fnos_call.kwargs["source_username"] == "Cacty"
    assert fnos_call.kwargs["source_password"] == "secret"
    assert fnos_call.kwargs["access_code"] == "access-code"


@pytest.mark.asyncio
async def test_wire_single_failure_does_not_block_others(session_local, monkeypatch):
    """单个来源装配失败不影响其他来源。"""
    group_id = await _seed_group(
        session_local,
        {
            "moviepilot": _moviepilot_cfg(),
            "qbittorrent": {
                "url": "http://192.168.1.10:8081",
                "provisioned": True,
                "username": "admin",
                "password": "pass",
            },
            "jellyfin": {
                "url": "http://192.168.1.10:8096",
                "provisioned": True,
                "api_key": "jf-key",
            },
        },
    )
    mp_project = _slugify("media-moviepilot")
    containers = {
        mp_project: FakeContainer(mp_project, {"stack-net": {"Gateway": "172.18.0.1"}}),
    }
    for name in ("qbittorrent", "jellyfin"):
        project = _slugify(f"media-{name}")
        containers[project] = FakeContainer(project, {"stack-net": {}})
    monkeypatch.setattr(
        orchestration_service, "_get_project_container", lambda p: containers[p]
    )
    monkeypatch.setattr(
        orchestration_service,
        "_get_container_port",
        lambda p, host_port: host_port,
    )
    monkeypatch.setattr(
        orchestration_service,
        "_probe_from_container",
        staticmethod(lambda container, url: True),
    )
    monkeypatch.setattr(
        MoviePilotClient,
        "setup_downloader",
        AsyncMock(return_value=(False, "写入系统设置失败: HTTP 500")),
    )
    setup_ms = AsyncMock(return_value=(True, None))
    monkeypatch.setattr(MoviePilotClient, "setup_mediaserver", setup_ms)

    await orchestration_service._wire_group(group_id)

    integrations = await _get_integrations(session_local, group_id)
    assert integrations["downloaders"][0]["status"] == "error"
    assert "HTTP 500" in integrations["downloaders"][0]["message"]
    assert integrations["mediaservers"][0]["status"] == "ok"
    assert integrations["wired_at"]


# ---------------- wire API 端点 ----------------


@pytest_asyncio.fixture(name="client_with_group")
async def _client_with_group():
    """提供已初始化并含一个编排实例组的 HTTP 测试客户端。"""
    from contextlib import asynccontextmanager

    from httpx import ASGITransport, AsyncClient

    from app.database import get_db
    from app.main import app

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
            app_composition=COMPOSITION_WITH_INTEGRATIONS,
            shared_config_schema={},
        )
        session.add(orch)
        await session.flush()
        group = AppOrchestrationInstance(
            orchestration_id=orch.id,
            instance_name="media",
            status="running",
            app_configs={"moviepilot": _moviepilot_cfg()},
        )
        session.add(group)
        await session.commit()
        await session.refresh(group)
        group_id = group.id

    transport = ASGITransport(app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c.group_id = group_id
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


@pytest.mark.asyncio
async def test_wire_endpoint_requires_auth(client_with_group):
    """未认证时拒绝访问。"""
    resp = await client_with_group.post(
        f"/api/orchestrations/instances/{client_with_group.group_id}/wire"
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_wire_endpoint_starts_background_wiring(
    client_with_group, monkeypatch
):
    """认证管理员触发后台装配并立即返回。"""
    schedule = MagicMock(return_value="task-wire")
    monkeypatch.setattr(orchestration_service, "schedule_wiring", schedule)
    token = await _login_admin(client_with_group)

    resp = await client_with_group.post(
        f"/api/orchestrations/instances/{client_with_group.group_id}/wire",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["ok"] is True
    assert body["data"]["message"] == "装配已开始"
    assert body["data"]["task_id"] == "task-wire"
    schedule.assert_called_once_with(client_with_group.group_id, "media")


@pytest.mark.asyncio
async def test_wire_endpoint_404_for_missing_group(client_with_group, monkeypatch):
    """实例组不存在时返回 404。"""
    monkeypatch.setattr(orchestration_service, "schedule_wiring", MagicMock())
    token = await _login_admin(client_with_group)

    resp = await client_with_group.post(
        "/api/orchestrations/instances/99999/wire",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_wire_records_webhook_url_for_mediaserver(session_local, monkeypatch):
    """媒体服务器装配成功后记录建议的 webhook 回调地址（供手动反配）。"""
    group_id = await _seed_group(
        session_local,
        {
            "moviepilot": _moviepilot_cfg(),
            "jellyfin": {
                "url": "http://192.168.1.10:8096",
                "provisioned": True,
                "api_key": "jf-key",
            },
        },
    )
    mp_project = _slugify("media-moviepilot")
    jf_project = _slugify("media-jellyfin")
    containers = {
        mp_project: FakeContainer(mp_project, {"stack-net": {"Gateway": "172.18.0.1"}}),
        jf_project: FakeContainer(jf_project, {"stack-net": {}}),
    }
    monkeypatch.setattr(
        orchestration_service, "_get_project_container", lambda p: containers[p]
    )
    monkeypatch.setattr(
        orchestration_service, "_get_container_port", lambda p, host_port: host_port
    )
    monkeypatch.setattr(
        orchestration_service,
        "_probe_from_container",
        staticmethod(lambda container, url: True),
    )
    monkeypatch.setattr(
        MoviePilotClient, "setup_mediaserver", AsyncMock(return_value=(True, None))
    )

    await orchestration_service._wire_group(group_id)

    integrations = await _get_integrations(session_local, group_id)
    record = integrations["mediaservers"][0]
    assert record["status"] == "ok"
    assert record["webhook_url"] == (
        "http://192.168.1.10:3000/api/v1/webhook?token=mp-token"
    )
