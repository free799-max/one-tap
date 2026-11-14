"""MCP 测试数据库与客户端 fixtures。"""

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.core.security import create_access_token
from app.database import Base, get_db
from app.main import app
from app.models.app_store import App
from app.models.orchestration import AppInstance
from app.models.user import User


@pytest_asyncio.fixture
async def mcp_env(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    for module_name in ("server", "local_tools", "auth", "audit", "api_invoker"):
        module = __import__(f"app.services.mcp.{module_name}", fromlist=["*"])
        monkeypatch.setattr(module, "async_session", Session)

    async with Session() as db:
        admin = User(username="mcp-admin", hashed_password="hash", role="admin")
        db.add(admin)
        await db.commit()
        await db.refresh(admin)

    yield {"session": Session, "admin": admin}
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest_asyncio.fixture
async def moviepilot(mcp_env):
    Session = mcp_env["session"]
    async with Session() as db:
        app_record = App(
            name="moviepilot",
            display_name="MoviePilot",
            yaml_template="services: {}",
        )
        db.add(app_record)
        await db.flush()
        instance = AppInstance(
            app_id=app_record.id,
            instance_name="movie",
            status="running",
            config={"url": "http://moviepilot:3001", "api_key": "mp-secret"},
        )
        db.add(instance)
        await db.commit()
        await db.refresh(instance)
        return instance


@pytest_asyncio.fixture
async def mcp_client(mcp_env):
    Session = mcp_env["session"]

    async def test_db():
        async with Session() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = test_db
    jwt_token = create_access_token({"sub": str(mcp_env["admin"].id)})
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {jwt_token}"},
    ) as client:
        yield client
