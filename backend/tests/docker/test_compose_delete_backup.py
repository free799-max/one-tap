"""删除 Compose 项目时的数据目录备份测试。"""

import json

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.database import Base
from app.models.app_store import App
from app.models.docker import DockerComposeProject
from app.models.orchestration import AppInstance
from app.services.compose import compose_manager
from app.services.system.storage_directory_service import storage_directory_service


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


async def _make_project(db, tmp_path):
    app = App(
        name="demo",
        display_name="Demo",
        yaml_template="services: {}",
    )
    db.add(app)
    await db.flush()

    data_conf = tmp_path / "appdata" / "demo-app" / "demo" / "conf"
    data_conf.mkdir(parents=True)
    (data_conf / "AdGuardHome.yaml").write_text("users: []", encoding="utf-8")

    project = DockerComposeProject(
        project_name="demo-app",
        config_files="[]",
        working_dir=str(tmp_path / "compose-workspace"),
    )
    db.add(project)
    await db.flush()

    instance = AppInstance(
        app_id=app.id,
        project_id=project.id,
        instance_name="demo-app",
        config={
            "volumes": [
                {
                    "host_path": str(data_conf),
                    "container_path": "/opt/adguardhome/conf",
                }
            ]
        },
        status="running",
    )
    db.add(instance)
    await db.commit()
    return project, instance, data_conf


def _patch_compose_runtime(monkeypatch):
    async def fake_run(project, *args, **kwargs):
        return {"returncode": 0, "stdout": "", "stderr": ""}

    async def fake_remove_containers(project_name):
        return None

    monkeypatch.setattr(compose_manager, "_run", fake_run)
    monkeypatch.setattr(
        compose_manager, "_remove_project_containers", fake_remove_containers
    )


@pytest.mark.asyncio
async def test_delete_project_backs_up_data_and_starts_fresh(
    db, tmp_path, monkeypatch
):
    project, instance, data_conf = await _make_project(db, tmp_path)
    appdata = tmp_path / "appdata"

    async def fake_pair(_db):
        return str(appdata), str(appdata)

    monkeypatch.setattr(storage_directory_service, "get_app_data_pair", fake_pair)
    _patch_compose_runtime(monkeypatch)

    await compose_manager.delete_project(db, project, backup_data=True)

    instance_dir = appdata / "demo-app"
    assert not instance_dir.exists()
    assert not data_conf.exists()

    backups = list((appdata / "_backups").iterdir())
    assert len(backups) == 1
    backup_dir = backups[0]
    assert (backup_dir / "demo-app" / "demo" / "conf" / "AdGuardHome.yaml").exists()
    manifest = json.loads(
        (backup_dir / "backup.json").read_text(encoding="utf-8")
    )
    assert manifest["instance_name"] == "demo-app"
    assert manifest["apps"][0]["project_name"] == "demo-app"

    assert await db.get(DockerComposeProject, project.id) is None
    assert await db.get(AppInstance, instance.id) is None


@pytest.mark.asyncio
async def test_delete_project_keeps_data_when_backup_disabled(
    db, tmp_path, monkeypatch
):
    project, _instance, data_conf = await _make_project(db, tmp_path)
    appdata = tmp_path / "appdata"

    async def fake_pair(_db):
        return str(appdata), str(appdata)

    monkeypatch.setattr(storage_directory_service, "get_app_data_pair", fake_pair)
    _patch_compose_runtime(monkeypatch)

    await compose_manager.delete_project(db, project)

    assert data_conf.exists()
    assert not (appdata / "_backups").exists()
