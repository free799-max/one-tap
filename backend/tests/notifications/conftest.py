"""通知中心测试 fixtures。"""

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.database import Base


@pytest_asyncio.fixture
async def notification_db(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    import app.database as database_module
    import app.services.notifications.dispatcher as dispatcher_module
    import app.services.notifications.sync as sync_module

    monkeypatch.setattr(database_module, "async_session", Session)
    monkeypatch.setattr(dispatcher_module, "async_session", Session)
    monkeypatch.setattr(sync_module, "async_session", Session)

    yield Session
    await engine.dispose()
