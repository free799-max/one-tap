"""无效应用实例清理测试。"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import app.models  # noqa: F401 — 注册所有模型到 Base.metadata
from app.database import Base
from app.models.docker.compose import DockerComposeProject
from app.models.orchestration import AppInstance
from app.services.docker import docker_manager
from app.services.orchestration.orphan_cleanup import purge_orphaned_instances


@pytest.fixture(name="session_factory")
async def _session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    yield factory
    await engine.dispose()


def _instance(
    name: str,
    *,
    status: str = "error",
    age_days: int = 10,
    config: dict | None = None,
    project_id: int | None = None,
) -> AppInstance:
    return AppInstance(
        instance_name=name,
        status=status,
        config=config or {},
        project_id=project_id,
        created_at=datetime.now() - timedelta(days=age_days),
    )


@pytest.mark.asyncio
async def test_purge_removes_orphaned_error_instances(session_factory, monkeypatch):
    monkeypatch.setattr(docker_manager, "check_available", lambda: True)
    monkeypatch.setattr(
        docker_manager,
        "list_containers",
        lambda filters=None: [
            {
                "id": "abcdef123456",
                "name": "live-app",
                "labels": {"com.docker.compose.project": "live-project"},
            }
        ],
    )

    async with session_factory() as db:
        project = DockerComposeProject(project_name="live-project")
        db.add(project)
        await db.flush()

        stale = _instance("stale")
        by_container = _instance(
            "imported", config={"container_name": "live-app"}
        )
        by_project = _instance("renamed", project_id=project.id)
        duplicate_old = _instance(
            "duplicated", age_days=20, config={"container_name": "live-app"}
        )
        duplicate_new = _instance(
            "duplicated", age_days=10, config={"container_name": "live-app"}
        )
        recent = _instance("recent", age_days=0)
        stopped = _instance("stopped", status="stopped")
        db.add_all(
            [
                stale,
                by_container,
                by_project,
                duplicate_old,
                duplicate_new,
                recent,
                stopped,
            ]
        )
        await db.commit()

        removed = await purge_orphaned_instances(
            db, min_age=timedelta(hours=1)
        )

        assert removed == [stale.id, duplicate_old.id]
        remaining = set(
            (await db.execute(select(AppInstance.instance_name))).scalars().all()
        )
        assert remaining == {
            "imported",
            "renamed",
            "duplicated",
            "recent",
            "stopped",
        }


@pytest.mark.asyncio
async def test_purge_skips_when_docker_unavailable(session_factory, monkeypatch):
    monkeypatch.setattr(docker_manager, "check_available", lambda: False)

    async with session_factory() as db:
        stale = _instance("stale")
        db.add(stale)
        await db.commit()

        assert await purge_orphaned_instances(db) == []
        remaining = (
            await db.execute(select(AppInstance.instance_name))
        ).scalars().all()
        assert remaining == ["stale"]
