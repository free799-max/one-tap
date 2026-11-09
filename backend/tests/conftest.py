"""测试共享 fixtures。"""

from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — 注册所有模型到 Base.metadata
from app.config import settings
from app.database import Base, get_db
from app.main import app


@pytest.fixture(autouse=True)
def _open_registration(monkeypatch):
    """测试环境默认开放注册（生产默认关闭，注册闸门测试会显式覆盖）。"""
    monkeypatch.setattr(settings, "REGISTRATION_OPEN", True)


@pytest.fixture(autouse=True)
def _valid_license_by_default(request, monkeypatch):
    """既有用例默认持有有效授权，授权专项测试使用真实门禁。"""
    if "licensing" in request.node.path.parts:
        return
    from app.services.licensing import licensing_service

    async def valid_license(db):
        return {"valid": True, "status": "active"}

    monkeypatch.setattr(licensing_service, "require_valid", valid_license)


@pytest.fixture(autouse=True)
def _restore_docker_client():
    """docker_manager 为全局单例且客户端惰性缓存，
    测试间需还原缓存的客户端，避免 mock 泄漏到后续测试。"""
    from app.services.docker import docker_manager

    original = docker_manager._client_obj
    yield
    docker_manager._client_obj = original


@pytest.fixture(autouse=True)
def _reset_host_path_aliases():
    """路径别名是全局运行时状态，测试间清空避免映射泄漏。"""
    from app.services.system.host_path_aliases import host_path_aliases

    host_path_aliases.reset()
    yield
    host_path_aliases.reset()


@pytest.fixture(autouse=True)
def _reset_deploy_tasks():
    """部署任务管理器是全局单例，测试间清空任务避免残留任务互相影响。"""
    from app.services.orchestration.deploy_task_service import deploy_task_manager

    with deploy_task_manager._lock:
        deploy_task_manager._tasks.clear()
        deploy_task_manager._listeners.clear()
    yield


@pytest_asyncio.fixture
async def client():
    """提供已初始化的异步 HTTP 测试客户端。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 跳过应用默认 lifespan 中的 alembic 迁移与外部数据库初始化
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

    transport = ASGITransport(app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()
    await engine.dispose()


@pytest_asyncio.fixture(name="client_with_seed")
async def _client_with_seed():
    """提供已初始化并内置影视编排/应用数据的 HTTP 测试客户端。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 跳过应用默认 lifespan 中的 alembic 迁移与外部数据库初始化
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

    # 填充测试数据
    async with SessionLocal() as session:
        from app.models.orchestration import AppOrchestration
        from app.models.app_store import App

        session.add(
            AppOrchestration(
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
        )
        for name, display_name, image in [
            ("moviepilot", "MoviePilot", "jxxghp/moviepilot"),
            ("qbittorrent", "qBittorrent", "linuxserver/qbittorrent"),
            ("jellyfin", "Jellyfin", "jellyfin/jellyfin"),
            ("emby", "Emby", "emby/embyserver"),
        ]:
            session.add(
                App(
                    name=name,
                    display_name=display_name,
                    image=image,
                    yaml_template=f"services:\n  app:\n    image: {name}:latest",
                )
            )
        await session.commit()

    transport = ASGITransport(app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()
    await engine.dispose()
