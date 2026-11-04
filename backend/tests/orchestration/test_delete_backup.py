"""删除编排实例组时备份应用数据目录的测试。"""

import json

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.core.exceptions import APIException
from app.database import Base
from app.models.app_store import App
from app.models.docker import DockerComposeProject
from app.models.orchestration import (
    AppInstance,
    AppOrchestration,
    AppOrchestrationInstance,
)
from app.services.compose.compose_service import compose_manager
from app.services.orchestration import orchestration_service
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


@pytest_asyncio.fixture
async def group(db):
    """创建带一个项目的编排实例组。"""
    orchestration = AppOrchestration(
        name="media-stack",
        display_name="影视媒体栈",
        category="media",
        app_composition=[{"app_name": "moviepilot", "relation": "required"}],
        shared_config_schema={},
    )
    app = App(
        name="moviepilot",
        display_name="MoviePilot",
        yaml_template="services:\n  app:\n    image: moviepilot:latest",
    )
    db.add_all([orchestration, app])
    await db.flush()

    item = AppOrchestrationInstance(
        orchestration_id=orchestration.id,
        instance_name="test-stack",
        shared_config={},
        status="running",
    )
    db.add(item)
    await db.flush()

    project = DockerComposeProject(
        project_name="test-stack-moviepilot",
        config_files='["/tmp/compose.yaml"]',
        working_dir="/tmp",
    )
    db.add(project)
    await db.flush()

    db.add(
        AppInstance(
            app_id=app.id,
            orchestration_id=orchestration.id,
            orchestration_group_id=item.id,
            instance_name="test-stack-moviepilot",
            config={},
            status="running",
            project_id=project.id,
        )
    )
    await db.commit()
    await db.refresh(item)
    return item


def _fake_delete_project(db):
    """模拟 compose_manager.delete_project：级联删除实例行与项目，但不动数据目录。"""

    async def fake_delete_project(db, project):
        await db.execute(
            delete(AppInstance).where(AppInstance.project_id == project.id)
        )
        await db.delete(project)
        await db.commit()

    return fake_delete_project


def _set_app_data_root(
    monkeypatch, host: str | None, mount: str | None = None
):
    async def fake_pair(db):
        if host is None:
            return None
        return (host, mount or host)

    monkeypatch.setattr(storage_directory_service, "get_app_data_pair", fake_pair)


@pytest.mark.asyncio
async def test_delete_instance_backs_up_app_data(
    db, group, monkeypatch, tmp_path
):
    """删除实例组应按容器内路径备份，并在清单中记录宿主机路径。"""
    host_root = "/srv/onetap/app-data"
    _set_app_data_root(monkeypatch, host_root, str(tmp_path))
    monkeypatch.setattr(
        compose_manager, "delete_project", _fake_delete_project(db)
    )

    app_dir = tmp_path / "test-stack-moviepilot"
    (app_dir / "postgresql" / "data").mkdir(parents=True)
    (app_dir / "postgresql" / "data" / "PG_VERSION").write_text("16")
    # 共享存储/非应用数据目录不应被移动
    shared_dir = tmp_path / "test-stack"
    shared_dir.mkdir()
    (shared_dir / "media.txt").write_text("keep")

    await orchestration_service.delete_instance(db, group.id)

    assert not app_dir.exists()
    assert (shared_dir / "media.txt").read_text() == "keep"

    backups = list((tmp_path / "_backups").iterdir())
    assert len(backups) == 1
    backup_dir = backups[0]
    assert backup_dir.name.endswith("-test-stack")
    assert (
        backup_dir
        / "test-stack-moviepilot"
        / "postgresql"
        / "data"
        / "PG_VERSION"
    ).read_text() == "16"

    manifest = json.loads((backup_dir / "backup.json").read_text())
    assert manifest["instance_name"] == "test-stack"
    assert manifest["orchestration"] == "media-stack"
    assert manifest["app_data_root"] == host_root
    assert manifest["shared_config"] == {}
    assert len(manifest["apps"]) == 1
    app_entry = manifest["apps"][0]
    assert app_entry["instance_name"] == "test-stack-moviepilot"
    assert app_entry["app_name"] == "moviepilot"
    assert app_entry["project_name"] == "test-stack-moviepilot"
    assert app_entry["path"] == "test-stack-moviepilot"

    # 实例组与实例行已删除
    assert await db.scalar(
        select(func.count()).select_from(AppOrchestrationInstance)
    ) == 0
    assert await db.scalar(select(func.count()).select_from(AppInstance)) == 0


@pytest.mark.asyncio
async def test_delete_instance_backs_up_orphan_instance_dir(
    db, group, monkeypatch, tmp_path
):
    """实例无关联项目时，仍按实例名规则备份残留数据目录。"""
    _set_app_data_root(monkeypatch, str(tmp_path))
    monkeypatch.setattr(
        compose_manager, "delete_project", _fake_delete_project(db)
    )

    app_result = await db.execute(select(App).where(App.name == "moviepilot"))
    app = app_result.scalar_one()
    db.add(
        AppInstance(
            app_id=app.id,
            orchestration_id=group.orchestration_id,
            orchestration_group_id=group.id,
            instance_name="test-stack-jellyfin",
            config={},
            status="error",
            project_id=None,
        )
    )
    await db.commit()

    orphan_dir = tmp_path / "test-stack-jellyfin"
    orphan_dir.mkdir()
    (orphan_dir / "library.db").write_text("db")

    await orchestration_service.delete_instance(db, group.id)

    moved = list((tmp_path / "_backups").glob("*/test-stack-jellyfin/library.db"))
    assert len(moved) == 1
    assert moved[0].read_text() == "db"


@pytest.mark.asyncio
async def test_delete_instance_backs_up_instance_dir_layout(
    db, group, monkeypatch, tmp_path
):
    """新布局 <实例名>/<应用名>/... 按实例名目录整体备份。"""
    _set_app_data_root(monkeypatch, "/srv/onetap/app-data", str(tmp_path))
    monkeypatch.setattr(
        compose_manager, "delete_project", _fake_delete_project(db)
    )

    instance = (
        await db.execute(select(AppInstance).where(AppInstance.app_id.isnot(None)))
    ).scalars().first()
    instance.config = {
        "volumes": [
            {
                "host_path": f"{tmp_path}/comic/comic-auto/data",
                "container_path": "/var/lib/postgresql/16/main",
                "mode": "rw",
            }
        ]
    }
    await db.commit()

    data_dir = tmp_path / "comic" / "comic-auto" / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "PG_VERSION").write_text("16")
    # 组名同名目录属于共享存储，不能因为存在就被搬走
    shared_dir = tmp_path / "test-stack"
    shared_dir.mkdir()
    (shared_dir / "media.txt").write_text("keep")

    await orchestration_service.delete_instance(db, group.id)

    assert not (tmp_path / "comic").exists()
    assert (shared_dir / "media.txt").read_text() == "keep"

    backups = list((tmp_path / "_backups").iterdir())
    assert len(backups) == 1
    backup_dir = backups[0]
    assert (backup_dir / "comic" / "comic-auto" / "data" / "PG_VERSION").read_text() == "16"

    manifest = json.loads((backup_dir / "backup.json").read_text())
    assert manifest["instance_dir"] == "comic"
    kinds = {entry["kind"] for entry in manifest["apps"]}
    assert "instance" in kinds


@pytest.mark.asyncio
async def test_delete_instance_reports_progress(
    db, group, monkeypatch, tmp_path
):
    """删除时按阶段回调进度，供后台任务推送。"""
    _set_app_data_root(monkeypatch, str(tmp_path))
    monkeypatch.setattr(
        compose_manager, "delete_project", _fake_delete_project(db)
    )

    events: list[tuple[str, int]] = []

    await orchestration_service.delete_instance(
        db,
        group.id,
        progress=lambda stage, percentage, message, detail=None: events.append(
            (stage, percentage)
        ),
    )

    stages = [stage for stage, _ in events]
    assert stages[0] == "preparing"
    assert "stopping_services" in stages
    assert "backing_up_data" in stages
    assert stages[-1] == "cleaning_records"
    percentages = [percentage for _, percentage in events]
    assert percentages == sorted(percentages)


@pytest.mark.asyncio
async def test_start_delete_instance_creates_progress_task(
    db, group, monkeypatch
):
    """删除入口应创建后台任务并立即返回任务 ID。"""
    from app.services.orchestration.deploy_task_service import deploy_task_manager

    scheduled = []

    def fake_create_background_task(coro):
        scheduled.append(coro)
        coro.close()

    # 直接替换模块属性（start_delete_instance 在函数内按模块导入）
    import app.core.background as background_module

    monkeypatch.setattr(
        background_module, "create_background_task", fake_create_background_task
    )

    task_id = await orchestration_service.start_delete_instance(db, group.id)
    try:
        task = deploy_task_manager.get_task(task_id)
        assert task is not None
        assert task["status"] == "deploying"
        assert task["meta"]["operation"] == "delete"
        assert task["meta"]["instance_name"] == "test-stack"
        assert len(scheduled) == 1
    finally:
        deploy_task_manager.fail_task(task_id, "测试清理")


@pytest.mark.asyncio
async def test_start_delete_instance_rejects_active_deploy(
    db, group, monkeypatch
):
    """组内应用正在部署时应拒绝删除。"""
    from app.services.orchestration.deploy_task_service import deploy_task_manager

    app_instance = await db.scalar(
        select(AppInstance).where(AppInstance.orchestration_group_id == group.id)
    )
    task_id = deploy_task_manager.create_task(
        "app_deploy", instance_id=app_instance.id
    )
    try:
        with pytest.raises(APIException) as exc_info:
            await orchestration_service.start_delete_instance(db, group.id)
        assert exc_info.value.status_code == 409
    finally:
        deploy_task_manager.fail_task(task_id, "测试清理")


@pytest.mark.asyncio
async def test_delete_instance_reports_backup_failure(
    db, group, monkeypatch, tmp_path
):
    """数据目录无法移入备份时应保留原数据并显性报告，而不是静默成功。"""
    _set_app_data_root(monkeypatch, str(tmp_path))
    monkeypatch.setattr(
        compose_manager, "delete_project", _fake_delete_project(db)
    )

    app_dir = tmp_path / "test-stack-moviepilot"
    (app_dir / "postgresql" / "data").mkdir(parents=True)
    (app_dir / "postgresql" / "data" / "PG_VERSION").write_text("16")

    def denied_rename(src, dst):
        raise PermissionError(13, "Permission denied", src)

    monkeypatch.setattr(
        "app.services.orchestration.backup.os.rename", denied_rename
    )

    backup_dir, failures = await orchestration_service.delete_instance(
        db, group.id
    )

    assert backup_dir is None
    assert [item.project_name for item in failures] == ["test-stack-moviepilot"]
    # 原数据原样保留，备份目录里不留空壳
    assert (
        app_dir / "postgresql" / "data" / "PG_VERSION"
    ).read_text() == "16"
    assert not (tmp_path / "_backups").exists()
    # 实例组记录仍按约定清理
    assert await db.scalar(
        select(func.count()).select_from(AppOrchestrationInstance)
    ) == 0


@pytest.mark.asyncio
async def test_complete_task_carries_warning():
    """删除成功的告警（如数据目录未能备份）应随完成事件推给前端。"""
    from app.services.orchestration.deploy_task_service import deploy_task_manager

    task_id = deploy_task_manager.create_task(
        "orchestration_delete", meta={"operation": "delete"}
    )
    deploy_task_manager.complete_task(
        task_id,
        message="删除完成，但 1 个应用数据目录未能备份",
        warning="旧数据仍保留在原路径",
    )

    latest = deploy_task_manager.get_task_history(task_id)[-1]
    assert latest["stage"] == "completed"
    assert latest["_warning"] == "旧数据仍保留在原路径"
    assert "未能备份" in latest["message"]


@pytest.mark.asyncio
async def test_delete_instance_without_app_data_root_skips_backup(
    db, group, monkeypatch, tmp_path
):
    """未配置应用数据目录时删除不报错，也不创建备份目录。"""
    _set_app_data_root(monkeypatch, None)
    monkeypatch.setattr(
        compose_manager, "delete_project", _fake_delete_project(db)
    )

    app_dir = tmp_path / "test-stack-moviepilot"
    app_dir.mkdir()

    await orchestration_service.delete_instance(db, group.id)

    assert app_dir.exists()
    assert not (tmp_path / "_backups").exists()
    assert await db.scalar(
        select(func.count()).select_from(AppOrchestrationInstance)
    ) == 0
