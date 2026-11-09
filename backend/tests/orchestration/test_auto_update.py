"""自动更新服务测试。

自动更新只处理 OneTap 自己部署的实例：编排实例组与单应用部署；
用户手动创建的 Compose 项目、导入的外部容器不在范围内。
"""

import asyncio
import json

import pytest
import pytest_asyncio
import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.database import Base
from app.models.app_store import App
from app.models.docker import DockerComposeProject, DockerComposeVersion
from app.models.orchestration import (
    AppInstance,
    AppOrchestration,
    AppOrchestrationInstance,
    OrchestrationUpdateEvent,
)
from app.models.storage_directory import StorageDirectory
from app.models.system_config import SystemConfig
from app.services.compose.compose_service import compose_manager
from app.services.orchestration import auto_update as auto_update_module
from app.services.orchestration import deploy as deploy_module
from app.services.orchestration import orchestration_service
from app.services.orchestration import provisioning as provisioning_module
from app.services.orchestration import upgrade as upgrade_module
from app.services.orchestration.deploy_task_service import DeployTaskManager

YAML_TEMPLATE = """services:
  app:
    image: {{ image }}
    ports:
      - "{{ ports[0].local_port }}:{{ ports[0].container_port }}"
    volumes:
      - {{ volumes[0].host_path }}:{{ volumes[0].container_path }}
"""

CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "ports": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "local_port": {"type": "integer"},
                    "container_port": {"type": "integer"},
                },
            },
        },
        "volumes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "host_path": {"type": "string"},
                    "container_path": {"type": "string"},
                },
            },
        },
    },
}

INSTANCE_CONFIG = {
    "ports": [{"local_port": 10001, "container_port": 80}],
    "volumes": [{"host_path": "/data/app", "container_path": "/config"}],
}


def _compose_yaml(image: str) -> str:
    return (
        "services:\n"
        "  app:\n"
        f"    image: {image}\n"
        "    ports:\n"
        '      - "10001:80"\n'
        "    volumes:\n"
        "      - /data/app:/config\n"
    )


@pytest_asyncio.fixture(name="session_factory")
async def _session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    SessionLocal = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    yield SessionLocal
    await engine.dispose()


@pytest_asyncio.fixture(name="db")
async def _db(session_factory):
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def db_app(db):
    app = App(
        name="demo",
        display_name="Demo",
        image="demo:v2",
        version="2.0.0",
        config_schema=CONFIG_SCHEMA,
        yaml_template=YAML_TEMPLATE,
    )
    db.add(app)
    db.add(
        StorageDirectory(
            name="app-data",
            host_path="/data",
            mount_path="/data",
            enabled=True,
            is_app_data=True,
        )
    )
    await db.commit()
    await db.refresh(app)
    return app


@pytest_asyncio.fixture
async def single_instance(db, db_app):
    project = DockerComposeProject(
        project_name="demo",
        config_files='["/tmp/compose.yaml"]',
        working_dir="/tmp",
    )
    db.add(project)
    await db.flush()
    db.add(
        DockerComposeVersion(
            project_id=project.id,
            version_number=1,
            content=_compose_yaml("demo:v1"),
            is_current=True,
        )
    )
    instance = AppInstance(
        app_id=db_app.id,
        instance_name="demo",
        config=json.loads(json.dumps(INSTANCE_CONFIG)),
        orchestration_version="1.0.0",
        status="running",
        project_id=project.id,
    )
    db.add(instance)
    await db.commit()
    await db.refresh(instance)
    return instance


@pytest_asyncio.fixture
async def group_instance(db, db_app):
    template = AppOrchestration(
        name="media-stack",
        display_name="影视媒体栈",
        category="media",
        version="2.0.0",
        app_composition=[{"app_name": "demo", "relation": "required"}],
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
    project = DockerComposeProject(
        project_name="stack-demo",
        config_files='["/tmp/compose.yaml"]',
        working_dir="/tmp",
    )
    db.add(project)
    await db.flush()
    db.add(
        DockerComposeVersion(
            project_id=project.id,
            version_number=1,
            content=_compose_yaml("demo:v1"),
            is_current=True,
        )
    )
    db.add(
        AppInstance(
            app_id=db_app.id,
            orchestration_id=template.id,
            orchestration_group_id=group.id,
            instance_name="stack-demo",
            config=json.loads(json.dumps(INSTANCE_CONFIG)),
            orchestration_version="1.0.0",
            status="running",
            project_id=project.id,
        )
    )
    await db.commit()
    await db.refresh(group)
    return group


@pytest.fixture(name="updater")
def _updater(monkeypatch, session_factory):
    """替换后台任务、数据库会话与任务管理器，并记录 Compose 调用。"""
    scheduled: list[asyncio.Task] = []
    manager = DeployTaskManager()
    calls: list[dict] = []

    def fake_create_background_task(coro):
        task = asyncio.create_task(coro)
        scheduled.append(task)
        return task

    async def fake_edit(db, project, content, user_id=None, comment=None, progress_callback=None):
        calls.append({"project": project.project_name, "content": content})
        if progress_callback:
            progress_callback("pulling_images", 50, "拉取镜像")
        return object(), {"returncode": 0}

    async def fake_wait(task_id, timeout=3600):
        if scheduled:
            await asyncio.gather(*scheduled)
        task = manager.get_task(task_id)
        return (task or {}).get("status") or "failed"

    for module in (upgrade_module, auto_update_module):
        monkeypatch.setattr(module, "async_session", session_factory)
    for module in (upgrade_module, auto_update_module, provisioning_module, deploy_module):
        monkeypatch.setattr(module, "deploy_task_manager", manager)
    monkeypatch.setattr(
        upgrade_module, "create_background_task", fake_create_background_task
    )
    monkeypatch.setattr(compose_manager, "edit_and_deploy_async", fake_edit)
    monkeypatch.setattr(
        orchestration_service, "_wait_task_terminal", fake_wait
    )
    return {"calls": calls, "scheduled": scheduled}


async def _events(db) -> list[OrchestrationUpdateEvent]:
    result = await db.execute(
        select(OrchestrationUpdateEvent).order_by(OrchestrationUpdateEvent.id)
    )
    return list(result.scalars().all())


async def _select(db, *keys: str) -> None:
    """写入自动更新目标选择。"""
    config = await db.get(SystemConfig, 1)
    if config is None:
        config = SystemConfig(id=1)
        db.add(config)
    config.auto_update_targets = list(keys)
    await db.commit()


@pytest.mark.asyncio
async def test_auto_update_upgrades_single_app(
    db, session_factory, single_instance, updater
):
    await _select(db, f"app:{single_instance.id}")
    stats = await auto_update_module.run_auto_update()

    assert stats["apps"] == 1
    assert len(updater["calls"]) == 1
    updated = yaml.safe_load(updater["calls"][0]["content"])
    assert updated["services"]["app"]["image"] == "demo:v2"
    assert updated["services"]["app"]["ports"] == ["10001:80"]
    assert updated["services"]["app"]["volumes"] == ["/data/app:/config"]

    async with session_factory() as check_db:
        instance = await check_db.get(AppInstance, single_instance.id)
        assert instance.orchestration_version == "2.0.0"
        assert instance.status == "running"
        events = await _events(check_db)
        assert len(events) == 1
        assert events[0].mode == "auto"
        assert events[0].status == "applied"
        assert events[0].detail["mode"] == "image_only"
        assert events[0].detail["app_instance_id"] == instance.id


@pytest.mark.asyncio
async def test_auto_update_skips_imported_container(
    db, session_factory, single_instance, updater
):
    config = dict(single_instance.config or {})
    config["imported"] = True
    single_instance.config = config
    await db.commit()
    await _select(db, f"app:{single_instance.id}")

    stats = await auto_update_module.run_auto_update()

    assert stats["apps"] == 0
    assert updater["calls"] == []
    async with session_factory() as check_db:
        instance = await check_db.get(AppInstance, single_instance.id)
        assert instance.orchestration_version == "1.0.0"


@pytest.mark.asyncio
async def test_auto_update_syncs_version_when_image_current(
    db, session_factory, db_app, updater
):
    project = DockerComposeProject(
        project_name="demo",
        config_files='["/tmp/compose.yaml"]',
        working_dir="/tmp",
    )
    db.add(project)
    await db.flush()
    db.add(
        DockerComposeVersion(
            project_id=project.id,
            version_number=1,
            content=_compose_yaml("demo:v2"),
            is_current=True,
        )
    )
    instance = AppInstance(
        app_id=db_app.id,
        instance_name="demo",
        config=json.loads(json.dumps(INSTANCE_CONFIG)),
        orchestration_version="1.0.0",
        status="running",
        project_id=project.id,
    )
    db.add(instance)
    await db.commit()
    instance_id = instance.id
    await _select(db, f"app:{instance_id}")

    await auto_update_module.run_auto_update()

    assert updater["calls"] == []
    async with session_factory() as check_db:
        instance = await check_db.get(AppInstance, instance_id)
        assert instance.orchestration_version == "2.0.0"
        events = await _events(check_db)
        assert len(events) == 1
        assert events[0].mode == "auto"
        assert events[0].detail["mode"] == "version_only"


@pytest.mark.asyncio
async def test_auto_update_upgrades_group(
    db, session_factory, group_instance, updater
):
    await _select(db, f"group:{group_instance.id}")
    stats = await auto_update_module.run_auto_update()

    assert stats["groups"] == 1
    assert len(updater["calls"]) == 1
    async with session_factory() as check_db:
        group = await check_db.get(AppOrchestrationInstance, group_instance.id)
        assert group.orchestration_version == "2.0.0"
        assert group.status == "running"
        events = await _events(check_db)
        assert len(events) == 1
        assert events[0].mode == "auto"
        assert events[0].group_id == group.id
        assert events[0].detail["selected_apps"] == ["demo"]


@pytest.mark.asyncio
async def test_auto_update_skips_group_with_new_template_app(
    db, session_factory, group_instance, updater
):
    """模板新增应用时保留「可升级」提示，由用户手动确认安装。"""
    db.add(
        App(
            name="newapp",
            display_name="New App",
            image="newapp:v1",
            version="2.0.0",
            config_schema=CONFIG_SCHEMA,
            yaml_template=YAML_TEMPLATE,
        )
    )
    template = await db.get(AppOrchestration, group_instance.orchestration_id)
    template.app_composition = [
        *(template.app_composition or []),
        {"app_name": "newapp", "relation": "optional"},
    ]
    await db.commit()
    await _select(db, f"group:{group_instance.id}")

    stats = await auto_update_module.run_auto_update()

    assert stats["groups"] == 0
    assert stats["groups_skipped"] == 1
    assert updater["calls"] == []
    async with session_factory() as check_db:
        group = await check_db.get(AppOrchestrationInstance, group_instance.id)
        assert group.orchestration_version == "1.0.0"


@pytest.mark.asyncio
async def test_auto_update_marks_failed_version_and_stops_retry(
    db, session_factory, single_instance, updater, monkeypatch
):
    """自动升级失败后记录失败版本，下一轮不再重试同一版本。"""

    async def boom(*_args, **_kwargs):
        raise RuntimeError("镜像拉取失败")

    async def fake_rollback(db, project, version):
        return version

    monkeypatch.setattr(compose_manager, "edit_and_deploy_async", boom)
    monkeypatch.setattr(compose_manager, "rollback_version", fake_rollback)
    await _select(db, f"app:{single_instance.id}")

    stats = await auto_update_module.run_auto_update()

    assert stats["apps_failed"] == 1
    async with session_factory() as check_db:
        instance = await check_db.get(AppInstance, single_instance.id)
        assert instance.orchestration_version == "1.0.0"
        assert instance.status == "running"
        assert instance.config["auto_update_failed_version"] == "2.0.0"
        events = await _events(check_db)
        assert len(events) == 1
        assert events[0].mode == "auto"
        assert events[0].status == "failed"
        assert "镜像拉取失败" in events[0].message

    stats = await auto_update_module.run_auto_update()
    assert stats["apps"] == 0
    assert stats["apps_skipped"] == 0


@pytest.mark.asyncio
async def test_auto_update_requires_selected_targets(
    db, session_factory, single_instance, updater
):
    """未勾选任何服务时不执行自动更新。"""
    stats = await auto_update_module.run_auto_update()

    assert stats["skipped"] is True
    assert stats["reason"] == "no_targets"
    assert updater["calls"] == []
    async with session_factory() as check_db:
        instance = await check_db.get(AppInstance, single_instance.id)
        assert instance.orchestration_version == "1.0.0"


@pytest.mark.asyncio
async def test_auto_update_only_updates_selected_target(
    db, session_factory, single_instance, group_instance, updater
):
    """只勾选单应用时不会升级未勾选的组合部署。"""
    await _select(db, f"app:{single_instance.id}")

    stats = await auto_update_module.run_auto_update()

    assert stats["apps"] == 1
    assert stats["groups"] == 0
    async with session_factory() as check_db:
        group = await check_db.get(AppOrchestrationInstance, group_instance.id)
        assert group.orchestration_version == "1.0.0"
