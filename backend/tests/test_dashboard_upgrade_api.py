"""服务概览的单应用升级入口测试。"""

from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.database import Base, get_db
from app.main import app as fastapi_app
from app.models.app_store import App
from app.models.docker import DockerComposeProject, DockerComposeVersion
from app.models.orchestration import AppInstance


@pytest.fixture(autouse=True)
def _no_docker(monkeypatch):
    """概览按数据库状态判断运行状态，避免依赖本机 Docker。"""
    from app.services import overview

    async def fake_snapshot():
        return overview.DockerSnapshot(available=False)

    monkeypatch.setattr(overview, "load_docker_snapshot", fake_snapshot)


@pytest_asyncio.fixture
async def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    @asynccontextmanager
    async def _noop_lifespan(app):
        yield

    fastapi_app.router.lifespan_context = _noop_lifespan
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

    fastapi_app.dependency_overrides[get_db] = get_test_db

    async with SessionLocal() as session:
        db_app = App(
            name="demo",
            display_name="Demo",
            image="demo:v2",
            version="2.0.0",
            yaml_template="services:\n  app:\n    image: demo:v2\n",
        )
        session.add(db_app)
        await session.flush()

        project = DockerComposeProject(
            project_name="demo",
            config_files='["/tmp/compose.yaml"]',
            working_dir="/tmp",
        )
        session.add(project)
        imported_project = DockerComposeProject(
            project_name="demo-imported",
            config_files='["/tmp/imported.yaml"]',
            working_dir="/tmp",
        )
        session.add(imported_project)
        await session.flush()
        session.add(
            DockerComposeVersion(
                project_id=project.id,
                version_number=1,
                content="services:\n  app:\n    image: demo:v1\n",
                is_current=True,
            )
        )
        session.add(
            AppInstance(
                app_id=db_app.id,
                instance_name="demo",
                config={},
                orchestration_version="1.0.0",
                status="running",
                project_id=project.id,
            )
        )
        session.add(
            AppInstance(
                app_id=db_app.id,
                instance_name="imported",
                config={"imported": True},
                orchestration_version="1.0.0",
                status="running",
                project_id=imported_project.id,
            )
        )
        await session.commit()

    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    fastapi_app.dependency_overrides.clear()
    await engine.dispose()


async def _auth(client) -> dict:
    await client.post(
        "/api/auth/register", json={"username": "upgrader", "password": "pass123456"}
    )
    login = await client.post(
        "/api/auth/login", json={"username": "upgrader", "password": "pass123456"}
    )
    token = login.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_overview_exposes_single_app_update(client):
    headers = await _auth(client)
    data = (await client.get("/api/dashboard/overview", headers=headers)).json()["data"]
    apps = {item["instance_name"]: item for category in data["categories"] for item in category["apps"]}

    standalone = apps["demo"]
    assert standalone["upgrade_supported"] is True
    assert standalone["update_available"] is True
    assert standalone["version"] == "1.0.0"
    assert standalone["latest_version"] == "2.0.0"

    imported = apps["imported"]
    assert imported["upgrade_supported"] is False
    assert imported["update_available"] is False


@pytest.mark.asyncio
async def test_upgrade_rejects_imported_container(client):
    headers = await _auth(client)
    data = (await client.get("/api/dashboard/overview", headers=headers)).json()["data"]
    imported = next(
        item
        for category in data["categories"]
        for item in category["apps"]
        if item["instance_name"] == "imported"
    )

    response = await client.post(
        f"/api/dashboard/apps/{imported['id']}/upgrade", headers=headers
    )

    assert response.status_code == 400
    assert "导入的外部容器" in response.json()["message"]


@pytest.mark.asyncio
async def test_app_logs_stream_uses_compose_project(client, monkeypatch):
    """有 Compose 项目时，日志流走 docker compose logs。"""
    from app.services.compose import compose_manager

    seen: dict = {}

    async def fake_stream(project, tail=100, services=None, follow=True):
        seen["project"] = project.project_name
        seen["tail"] = tail
        yield "line-1"
        yield "line-2"

    monkeypatch.setattr(compose_manager, "stream_logs", fake_stream)

    headers = await _auth(client)
    data = (await client.get("/api/dashboard/overview", headers=headers)).json()["data"]
    demo = next(
        item
        for category in data["categories"]
        for item in category["apps"]
        if item["instance_name"] == "demo"
    )

    async with client.stream(
        "GET",
        f"/api/dashboard/apps/{demo['id']}/logs/stream?tail=50&follow=false",
        headers=headers,
    ) as response:
        assert response.status_code == 200
        body = "".join([chunk async for chunk in response.aiter_text()])

    assert seen == {"project": "demo", "tail": 50}
    assert '{"line": "line-1"}' in body
    assert '{"line": "line-2"}' in body


@pytest.mark.asyncio
async def test_app_logs_stream_rejects_unknown_instance(client):
    headers = await _auth(client)

    response = await client.get(
        "/api/dashboard/apps/9999/logs/stream", headers=headers
    )

    assert response.status_code == 404
    assert "应用实例不存在" in response.json()["message"]


@pytest.mark.asyncio
async def test_overview_keeps_app_after_compose_down(client, monkeypatch):
    """compose down 后容器被移除，应用仍以停止状态出现在概览里。"""
    from app.services import overview

    async def fake_snapshot():
        # Docker 可用，但该项目的容器已全部被移除
        return overview.DockerSnapshot(available=True)

    monkeypatch.setattr(overview, "load_docker_snapshot", fake_snapshot)

    headers = await _auth(client)
    data = (await client.get("/api/dashboard/overview", headers=headers)).json()["data"]
    apps = {
        item["instance_name"]: item
        for category in data["categories"]
        for item in category["apps"]
    }

    assert apps["demo"]["status"] == "stopped"
    assert apps["imported"]["status"] == "stopped"

