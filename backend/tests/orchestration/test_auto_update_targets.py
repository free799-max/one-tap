"""自动更新服务候选列表测试。"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.database import Base
from app.models.app_store import App
from app.models.docker import DockerComposeProject
from app.models.orchestration import (
    AppInstance,
    AppOrchestration,
    AppOrchestrationInstance,
)
from app.services.orchestration.auto_update_targets import (
    list_auto_update_targets,
)


@pytest_asyncio.fixture(name="db")
async def _db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    SessionLocal = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with SessionLocal() as session:
        yield session

    await engine.dispose()


def _app(name: str, *, containers: int = 1) -> App:
    return App(
        name=name,
        display_name=name.title(),
        image=f"{name}:v1",
        version="2.0.0",
        config_schema={
            "containers": [{"name": f"{name}-{index}"} for index in range(containers)]
        },
        yaml_template="services: {}",
    )


def _project(name: str) -> DockerComposeProject:
    return DockerComposeProject(
        project_name=name,
        config_files='["/tmp/compose.yaml"]',
        working_dir="/tmp",
    )


@pytest.mark.asyncio
async def test_list_targets_marks_complex_and_unselectable(db):
    single_app = _app("single", containers=2)
    group_app = _app("group-app")
    imported_app = _app("imported")
    db.add_all([single_app, group_app, imported_app])
    await db.flush()

    single_project = _project("single")
    imported_project = _project("imported")
    group_project = _project("group-app")
    db.add_all([single_project, imported_project, group_project])
    await db.flush()

    single = AppInstance(
        app_id=single_app.id,
        instance_name="single",
        config={},
        orchestration_version="1.0.0",
        status="running",
        project_id=single_project.id,
    )
    imported = AppInstance(
        app_id=imported_app.id,
        instance_name="imported",
        config={"imported": True},
        orchestration_version="1.0.0",
        status="running",
        project_id=imported_project.id,
    )
    db.add_all([single, imported])

    template = AppOrchestration(
        name="stack",
        display_name="Stack",
        category="media",
        version="2.0.0",
        app_composition=[{"app_name": "group-app", "relation": "required"}],
        shared_config_schema={},
    )
    db.add(template)
    await db.flush()
    group = AppOrchestrationInstance(
        orchestration_id=template.id,
        instance_name="stack",
        status="running",
        orchestration_version="1.0.0",
    )
    db.add(group)
    await db.flush()
    db.add(
        AppInstance(
            app_id=group_app.id,
            orchestration_id=template.id,
            orchestration_group_id=group.id,
            instance_name="stack-group-app",
            config={},
            orchestration_version="1.0.0",
            status="running",
            project_id=group_project.id,
        )
    )
    await db.commit()

    targets = {
        item["key"]: item for item in await list_auto_update_targets(db)
    }

    single_target = targets[f"app:{single.id}"]
    assert single_target["complex"] is True
    assert single_target["selectable"] is True
    assert single_target["upgrade_available"] is True
    assert single_target["icon_name"] == "single"

    imported_target = targets[f"app:{imported.id}"]
    assert imported_target["selectable"] is False
    assert "导入" in imported_target["reason"]

    group_target = targets[f"group:{group.id}"]
    assert group_target["selectable"] is True
    assert group_target["complex"] is False
    assert group_target["apps"] == ["Group-App"]
    assert group_target["icon_name"] == "group-app"
