"""影视自动化编排导入功能测试。"""

from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.database import Base
from app.models.app_store import App
from app.models.orchestration import (
    AppInstance,
    AppOrchestration,
    AppOrchestrationInstance,
)
from app.services.orchestration import orchestration_service


@pytest_asyncio.fixture(name="db")
async def _db():
    """提供内存 SQLite 会话。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    SessionLocal = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with SessionLocal() as session:
        yield session

    await engine.dispose()


@pytest_asyncio.fixture
async def media_orchestration(db):
    """创建影视组合模板。"""
    orchestration = AppOrchestration(
        name="media-stack",
        display_name="影视媒体栈",
        category="media",
        app_composition=[
            {"app_name": "moviepilot", "relation": "required"},
            {"app_name": "qbittorrent", "relation": "required"},
            {"app_name": "jellyfin", "relation": "optional", "group": "player"},
            {"app_name": "emby", "relation": "optional", "group": "player"},
        ],
        shared_config_schema={},
    )
    db.add(orchestration)
    await db.commit()
    await db.refresh(orchestration)
    return orchestration


@pytest_asyncio.fixture
async def media_apps(db):
    """创建影视相关应用。"""
    apps = [
        App(
            name="moviepilot",
            display_name="MoviePilot",
            image="jxxghp/moviepilot",
            yaml_template="services:\n  app:\n    image: moviepilot:latest",
        ),
        App(
            name="qbittorrent",
            display_name="qBittorrent",
            image="linuxserver/qbittorrent",
            yaml_template="services:\n  app:\n    image: qbittorrent:latest",
        ),
        App(
            name="jellyfin",
            display_name="Jellyfin",
            image="jellyfin/jellyfin",
            yaml_template="services:\n  app:\n    image: jellyfin:latest",
        ),
        App(
            name="emby",
            display_name="Emby",
            image="emby/embyserver",
            yaml_template="services:\n  app:\n    image: emby:latest",
        ),
    ]
    for app in apps:
        db.add(app)
    await db.commit()
    return apps


def _make_container(
    cid: str,
    name: str,
    image: str,
    network_ip: str | None = None,
    host_port: int | None = None,
    container_port: str | None = None,
):
    """构造一个模拟的 Docker 容器对象。"""
    port_bindings = {}
    if host_port and container_port:
        port_bindings[f"{container_port}/tcp"] = [
            {"HostIp": "0.0.0.0", "HostPort": str(host_port)}
        ]

    networks = {}
    if network_ip:
        networks["bridge"] = {
            "IPAddress": network_ip,
            "Gateway": "172.17.0.1",
            "MacAddress": "02:42:ac:11:00:02",
        }

    exposed_ports = {}
    if container_port:
        exposed_ports[f"{container_port}/tcp"] = {}

    container = MagicMock()
    container.id = cid
    container.name = name
    container.image.tags = [image]
    container.attrs = {
        "Config": {"ExposedPorts": exposed_ports},
        "HostConfig": {"NetworkMode": "bridge"},
        "NetworkSettings": {
            "Ports": port_bindings,
            "Networks": networks,
        },
    }
    return container


def test_build_access_url_uses_host_network_address(monkeypatch):
    """桥接容器内部署时，访问地址使用宿主机 IP 而非容器 IP。"""
    monkeypatch.setattr(
        "app.services.orchestration.importer.detect_host_address",
        lambda client: "192.168.1.50",
    )
    app = App(
        name="moviepilot",
        default_ports=[{"port": 3000, "protocol": "tcp", "description": "Web UI"}],
    )
    config = {
        "ports": [
            {"local_port": 8001, "container_port": 3000, "protocol": "tcp"}
        ]
    }

    url = orchestration_service._build_access_url(app, config)

    assert url == "http://192.168.1.50:8001"


@pytest.mark.asyncio
async def test_scan_import_candidates_match_running_containers(
    db, media_apps, media_orchestration
):
    """扫描应正确匹配运行中容器并解析网络信息。"""
    fake_containers = [
        _make_container(
            "abc123", "moviepilot", "jxxghp/moviepilot:latest",
            network_ip="172.17.0.2", host_port=3000, container_port="3000"
        ),
        _make_container(
            "def456", "qbittorrent", "linuxserver/qbittorrent:latest",
            network_ip="172.17.0.3", host_port=8080, container_port="8080"
        ),
        _make_container(
            "ghi789", "jellyfin", "jellyfin/jellyfin:latest",
            network_ip="172.17.0.4", host_port=8096, container_port="8096"
        ),
    ]

    orchestration_service._list_running_containers = lambda: fake_containers
    orchestration_service._get_host_ip = lambda: "192.168.1.100"

    candidates = await orchestration_service.scan_import_candidates(db, "media-stack")
    candidate_map = {c.app_name: c for c in candidates}

    assert candidate_map["moviepilot"].matched is True
    assert len(candidate_map["moviepilot"].candidates) == 1
    assert candidate_map["moviepilot"].candidates[0].suggested_url == "http://192.168.1.100:3000"

    assert candidate_map["qbittorrent"].matched is True
    assert candidate_map["qbittorrent"].candidates[0].network_ip == "172.17.0.3"

    assert candidate_map["jellyfin"].matched is True
    assert candidate_map["emby"].matched is False


@pytest.mark.asyncio
async def test_scan_import_candidates_returns_empty_when_docker_unavailable(
    db, media_apps, media_orchestration
):
    """Docker 不可用时返回未匹配列表，不抛异常。"""
    orchestration_service._list_running_containers = lambda: []

    candidates = await orchestration_service.scan_import_candidates(db, "media-stack")
    assert len(candidates) == 4
    assert all(not c.matched for c in candidates)


@pytest.mark.asyncio
async def test_import_orchestration_creates_group_and_instances(
    db, media_apps, media_orchestration
):
    """导入成功时应创建编排组和应用实例，并保存认证配置。"""
    fake_containers = [
        _make_container(
            "abc123", "moviepilot", "jxxghp/moviepilot:latest",
            network_ip="172.17.0.2", host_port=3000, container_port="3000"
        ),
        _make_container(
            "def456", "qbittorrent", "linuxserver/qbittorrent:latest",
            network_ip="172.17.0.3", host_port=8080, container_port="8080"
        ),
    ]
    orchestration_service._list_running_containers = lambda: fake_containers

    group, created_instance_ids = await orchestration_service.import_orchestration(
        db,
        orchestration_name="media-stack",
        instance_name="my-import",
        selected_apps=["moviepilot", "qbittorrent"],
        app_configs={
            "moviepilot": {"url": "http://localhost:3000", "api_key": "mp-key"},
            "qbittorrent": {"url": "http://localhost:8080", "username": "admin", "password": "admin"},
        },
        shared_config={},
        user_id=1,
    )

    assert group.instance_name == "my-import"
    assert group.status == "running"
    assert group.app_configs["moviepilot"]["api_key"] == "mp-key"
    assert len(created_instance_ids) == 2

    result = await db.execute(
        select(AppInstance).where(AppInstance.orchestration_group_id == group.id)
    )
    instances = list(result.scalars().all())
    assert len(instances) == 2

    instance_names = {i.instance_name for i in instances}
    assert instance_names == {"my-import-moviepilot", "my-import-qbittorrent"}

    moviepilot = next(i for i in instances if i.instance_name == "my-import-moviepilot")
    assert moviepilot.config["imported"] is True
    assert moviepilot.config["container_id"] == "abc123"
    assert moviepilot.config["host_port"] == 3000
    assert moviepilot.config["api_key"] == "mp-key"


@pytest.mark.asyncio
async def test_import_orchestration_missing_required_raises(
    db, media_apps, media_orchestration
):
    """未选择必选应用时应抛出异常。"""
    orchestration_service._list_running_containers = lambda: []

    with pytest.raises(Exception) as exc_info:
        await orchestration_service.import_orchestration(
            db,
            orchestration_name="media-stack",
            instance_name="bad-import",
            selected_apps=["qbittorrent"],
            app_configs={},
            shared_config={},
        )
    assert "必选应用" in str(exc_info.value)


@pytest.mark.asyncio
async def test_import_orchestration_conflicting_raises(
    db, media_apps, media_orchestration
):
    """导入时同时选择互斥应用应抛出异常。"""
    media_orchestration.app_composition = [
        {"app_name": "moviepilot", "relation": "required"},
        {"app_name": "qbittorrent", "relation": "required"},
        {"app_name": "jellyfin", "relation": "optional", "conflict_with": ["emby"]},
        {"app_name": "emby", "relation": "optional", "conflict_with": ["jellyfin"]},
    ]
    await db.commit()
    orchestration_service._list_running_containers = lambda: []

    with pytest.raises(Exception) as exc_info:
        await orchestration_service.import_orchestration(
            db,
            orchestration_name="media-stack",
            instance_name="conflict-import",
            selected_apps=["moviepilot", "qbittorrent", "jellyfin", "emby"],
            app_configs={},
            shared_config={},
        )
    assert "互斥" in str(exc_info.value)


@pytest.mark.asyncio
async def test_import_api_endpoint(client):
    """导入 API 端点需要认证并按标准格式返回。"""
    # 注册并登录
    await client.post("/api/auth/register", json={
        "username": "importtest",
        "password": "importtest123",
    })
    login_resp = await client.post("/api/auth/login", json={
        "username": "importtest",
        "password": "importtest123",
    })
    token = login_resp.json()["data"]["access_token"]

    # 未认证应 403
    resp = await client.get("/api/orchestrations/media-stack/import-candidates")
    assert resp.status_code == 403

    # 认证后访问不存在编排返回 404
    resp = await client.get(
        "/api/orchestrations/not-exist/import-candidates",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_import_candidates_endpoint_with_data(
    client_with_seed, monkeypatch
):
    """认证后扫描接口返回候选列表。"""
    await client_with_seed.post("/api/auth/register", json={
        "username": "importtest2",
        "password": "importtest123",
    })
    login_resp = await client_with_seed.post("/api/auth/login", json={
        "username": "importtest2",
        "password": "importtest123",
    })
    token = login_resp.json()["data"]["access_token"]

    fake_containers = [
        _make_container(
            "abc123", "moviepilot", "jxxghp/moviepilot:latest",
            network_ip="172.17.0.2", host_port=3000, container_port="3000"
        ),
    ]
    monkeypatch.setattr(
        orchestration_service,
        "_list_running_containers",
        lambda: fake_containers,
    )

    resp = await client_with_seed.get(
        "/api/orchestrations/media-stack/import-candidates",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert isinstance(data, list)
    assert any(item["app_name"] == "moviepilot" and item["matched"] for item in data)


@pytest.mark.asyncio
async def test_instance_crud_endpoints(client_with_seed, monkeypatch):
    """实例组详情、更新、删除接口应按标准格式工作。"""
    await client_with_seed.post("/api/auth/register", json={
        "username": "crudtest",
        "password": "crudtest123",
    })
    login_resp = await client_with_seed.post("/api/auth/login", json={
        "username": "crudtest",
        "password": "crudtest123",
    })
    token = login_resp.json()["data"]["access_token"]

    fake_containers = [
        _make_container(
            "abc123", "moviepilot", "jxxghp/moviepilot:latest",
            network_ip="172.17.0.2", host_port=3000, container_port="3000"
        ),
        _make_container(
            "def456", "qbittorrent", "linuxserver/qbittorrent:latest",
            network_ip="172.17.0.3", host_port=8080, container_port="8080"
        ),
    ]
    monkeypatch.setattr(
        orchestration_service,
        "_list_running_containers",
        lambda: fake_containers,
    )

    # 导入创建实例组
    resp = await client_with_seed.post(
        "/api/orchestrations/media-stack/import",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "instance_name": "crud-group",
            "selected_apps": ["moviepilot", "qbittorrent"],
            "app_configs": {
                "moviepilot": {"url": "http://localhost:3000"},
                "qbittorrent": {"url": "http://localhost:8080"},
            },
            "shared_config": {},
        },
    )
    assert resp.status_code == 200
    group_id = resp.json()["data"]["group_id"]

    # GET 详情
    resp = await client_with_seed.get(
        f"/api/orchestrations/instances/{group_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    detail = resp.json()["data"]
    assert detail["instance_name"] == "crud-group"
    assert len(detail["apps"]) == 2
    assert detail["app_configs"]["moviepilot"]["url"] == "http://localhost:3000"

    # PATCH 更新
    resp = await client_with_seed.patch(
        f"/api/orchestrations/instances/{group_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "instance_name": "crud-group-renamed",
            "app_configs": {
                "moviepilot": {"url": "http://localhost:3001"},
            },
        },
    )
    assert resp.status_code == 200
    updated = resp.json()["data"]
    assert updated["instance_name"] == "crud-group-renamed"
    assert updated["app_configs"]["moviepilot"]["url"] == "http://localhost:3001"

    # DELETE 改为后台任务：立即返回 task_id，实际删除由后台执行
    import app.core.background as background_module
    from app.services.orchestration.deploy_task_service import deploy_task_manager

    scheduled = []

    def fake_create_background_task(coro):
        scheduled.append(coro)
        coro.close()

    monkeypatch.setattr(
        background_module, "create_background_task", fake_create_background_task
    )

    resp = await client_with_seed.delete(
        f"/api/orchestrations/instances/{group_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    task_id = resp.json()["data"]["task_id"]
    assert task_id
    assert scheduled

    task = deploy_task_manager.get_task(task_id)
    try:
        assert task is not None
        assert task["meta"]["operation"] == "delete"
    finally:
        deploy_task_manager.fail_task(task_id, "测试清理")
