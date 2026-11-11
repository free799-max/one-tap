"""编排实例组一键升级（upgrade）相关测试。

升级语义：只更新镜像，原地重写 Compose 版本；挂载、端口等配置保持不变；
升级前保存配置快照；模板已移除的应用保留运行。
"""

import asyncio
import json

import pytest
import yaml
import pytest_asyncio
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
    OrchestrationUpgradeBackup,
    OrchestrationUpdateEvent,
)
from app.models.storage_directory import StorageDirectory
from app.services.app_store import app_service
from app.services.compose.compose_service import compose_manager
from app.services.orchestration import orchestration_service
from app.services.orchestration import deploy as deploy_module
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


@pytest_asyncio.fixture(name="session_factory")
async def _session_factory():
    """提供内存 SQLite 会话工厂。"""
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
async def apps(db):
    """创建模板与旧实例涉及的应用。"""
    items = [
        App(
            name=name,
            display_name=name.title(),
            image=f"{name}:v2",
            config_schema=CONFIG_SCHEMA,
            yaml_template=YAML_TEMPLATE,
        )
        for name in ("moviepilot", "qbittorrent", "jellyfin", "emby")
    ]
    db.add_all(items)
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
    return items


@pytest_asyncio.fixture
async def template(db):
    """当前模板：相比实例组部署时新增 qbittorrent、移除 emby。"""
    item = AppOrchestration(
        name="media-stack",
        display_name="影视媒体栈",
        category="media",
        version="2.0.0",
        changelog="新增 qBittorrent，移除 Emby",
        app_composition=[
            {"app_name": "moviepilot", "relation": "required"},
            {"app_name": "qbittorrent", "relation": "required"},
            {"app_name": "jellyfin", "relation": "optional"},
        ],
        shared_config_schema={},
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


OLD_CONFIGS = {
    "moviepilot": {
        "image": "moviepilot:v1",
        "ports": [{"local_port": 3000, "container_port": 80}],
        "volumes": [
            {
                "host_path": "/data/test-stack-moviepilot/pgdata",
                "container_path": "/var/lib/postgresql/data",
            }
        ],
        "password": "old-secret",
    },
    "jellyfin": {
        "ports": [{"local_port": 8096, "container_port": 8096}],
        "volumes": [
            {
                "host_path": "/data/test-stack-jellyfin/config",
                "container_path": "/config",
            }
        ],
    },
    "emby": {
        "ports": [{"local_port": 8097, "container_port": 8096}],
    },
}


def _old_compose_yaml(app_name: str, config: dict) -> str:
    """构造旧部署的 Compose YAML（包含端口与挂载，供升级保真校验）。"""
    port = (config.get("ports") or [{}])[0]
    lines = [
        "services:",
        "  app:",
        f"    image: {app_name}:v1",
        f"    container_name: test-stack-{app_name}",
        "    ports:",
        f'      - "{port.get("local_port", 0)}:{port.get("container_port", 0)}"',
    ]
    volumes = config.get("volumes") or []
    if volumes:
        lines.extend(
            [
                "    volumes:",
                f"      - {volumes[0]['host_path']}:{volumes[0]['container_path']}",
            ]
        )
    if app_name == "moviepilot":
        lines.extend(["  redis:", "    image: redis:7"])
    return "\n".join(lines) + "\n"


@pytest_asyncio.fixture
async def legacy_group(db, apps, template):
    """按旧模板（moviepilot/jellyfin/emby）部署的实例组。"""
    group = AppOrchestrationInstance(
        orchestration_id=template.id,
        instance_name="test-stack",
        shared_config={},
        status="running",
        orchestration_version="1.0.0",
    )
    db.add(group)
    await db.flush()

    app_by_name = {a.name: a for a in apps}
    for app_name in ("moviepilot", "jellyfin", "emby"):
        project = DockerComposeProject(
            project_name=f"test-stack-{app_name}",
            config_files='["/tmp/compose.yaml"]',
            working_dir="/tmp",
        )
        db.add(project)
        await db.flush()
        db.add(
            DockerComposeVersion(
                project_id=project.id,
                version_number=1,
                content=_old_compose_yaml(app_name, OLD_CONFIGS[app_name]),
                is_current=True,
            )
        )
        db.add(
            AppInstance(
                app_id=app_by_name[app_name].id,
                orchestration_id=template.id,
                orchestration_group_id=group.id,
                instance_name=f"test-stack-{app_name}",
                config=json.loads(json.dumps(OLD_CONFIGS[app_name])),
                status="running",
                project_id=project.id,
            )
        )
    await db.commit()
    await db.refresh(group)
    return group


@pytest.fixture(name="scheduled_tasks")
def _scheduled_tasks(monkeypatch, session_factory):
    """把升级后台协程改为可等待的任务，并替换测试数据库与任务管理器。"""
    scheduled: list[asyncio.Task] = []
    manager = DeployTaskManager()

    def fake_create_background_task(coro):
        task = asyncio.create_task(coro)
        scheduled.append(task)
        return task

    monkeypatch.setattr(
        upgrade_module, "create_background_task", fake_create_background_task
    )
    monkeypatch.setattr(upgrade_module, "async_session", session_factory)
    monkeypatch.setattr(upgrade_module, "deploy_task_manager", manager)
    monkeypatch.setattr(deploy_module, "deploy_task_manager", manager)
    return scheduled


def _fake_edit_deploy(captured: dict, fail: bool = False):
    """模拟 Compose 原地更新，记录最终 YAML。"""

    async def fake_edit(
        db,
        project,
        content,
        user_id=None,
        comment=None,
        progress_callback=None,
    ):
        captured["project"] = project.project_name
        captured["content"] = content
        captured["comment"] = comment
        if progress_callback:
            progress_callback("pulling_images", 50, "拉取镜像")
        if fail:
            raise RuntimeError("镜像拉取失败")
        return object(), {"returncode": 0}

    return fake_edit


def _forbid_delete(*_args, **_kwargs):
    raise AssertionError("升级不应删除 Compose 项目")


async def _events(db) -> list[OrchestrationUpdateEvent]:
    result = await db.execute(
        select(OrchestrationUpdateEvent).order_by(OrchestrationUpdateEvent.id)
    )
    return list(result.scalars().all())


@pytest.mark.asyncio
async def test_upgrade_only_updates_image_and_preserves_config(
    db, session_factory, apps, template, legacy_group, scheduled_tasks, monkeypatch
):
    """升级只换镜像；请求里的挂载/端口改动被忽略，旧配置原样保留。"""
    captured: dict = {}
    monkeypatch.setattr(
        compose_manager, "edit_and_deploy_async", _fake_edit_deploy(captured)
    )
    monkeypatch.setattr(compose_manager, "delete_project", _forbid_delete)

    group, task_ids = await orchestration_service.upgrade_instance(
        db,
        group_id=legacy_group.id,
        selected_apps=["moviepilot"],
        # 恶意/误操作改动：升级必须忽略
        app_configs={
            "moviepilot": {
                "ports": [{"local_port": 9999, "container_port": 80}],
                "volumes": [{"host_path": "/tmp/other", "container_path": "/x"}],
            }
        },
        shared_config={"volumes": [{"host_path": "/tmp/shared", "container_path": "/y"}]},
        user_id=1,
    )
    assert len(task_ids) == 1
    await asyncio.gather(*scheduled_tasks)

    # 镜像跟随应用默认值升级，端口与挂载保持旧配置
    upgraded_yaml = yaml.safe_load(captured["content"])
    service = upgraded_yaml["services"]["app"]
    assert service["image"] == "moviepilot:v2"
    assert service["ports"] == ["3000:80"]
    assert service["volumes"] == [
        "/data/test-stack-moviepilot/pgdata:/var/lib/postgresql/data"
    ]
    # 依赖服务（Redis）的镜像不能被应用镜像覆盖
    assert upgraded_yaml["services"]["redis"]["image"] == "redis:7"
    assert "9999" not in captured["content"]
    assert "/tmp/other" not in captured["content"]
    assert "/tmp/shared" not in captured["content"]
    assert captured["project"] == "test-stack-moviepilot"

    async with session_factory() as check_db:
        instance = (
            await check_db.execute(
                select(AppInstance).where(
                    AppInstance.instance_name == "test-stack-moviepilot"
                )
            )
        ).scalar_one()
        assert instance.config["ports"] == [
            {"local_port": 3000, "container_port": 80}
        ]
        assert instance.config["volumes"] == OLD_CONFIGS["moviepilot"]["volumes"]
        # 旧模板写入的 image 覆盖被清除，后续跟随应用默认镜像
        assert "image" not in instance.config

        group = await check_db.get(AppOrchestrationInstance, legacy_group.id)
        assert group.orchestration_version == "2.0.0"
        assert group.status == "running"

        events = await _events(check_db)
        assert len(events) == 1
        assert events[0].status == "applied"
        assert events[0].detail["mode"] == "image_only"
        assert events[0].detail["apps_added"] == []
        assert events[0].detail["apps_retained"] == ["emby"]

        backup = (
            await check_db.execute(select(OrchestrationUpgradeBackup))
        ).scalar_one()
        assert backup.group_id == legacy_group.id
        assert backup.from_version == "1.0.0"
        assert backup.to_version == "2.0.0"
        assert events[0].detail["backup_id"] == backup.id
        payload_apps = {app["app_name"]: app for app in backup.payload["apps"]}
        assert (
            payload_apps["moviepilot"]["config"]["volumes"]
            == OLD_CONFIGS["moviepilot"]["volumes"]
        )
        assert "moviepilot:v1" in payload_apps["moviepilot"]["compose_yaml"]


@pytest.mark.asyncio
async def test_upgrade_keeps_removed_app_running(
    db, session_factory, apps, template, legacy_group, scheduled_tasks, monkeypatch
):
    """模板已移除的应用不删除，项目和实例仍然保留。"""
    captured: dict = {}
    monkeypatch.setattr(
        compose_manager, "edit_and_deploy_async", _fake_edit_deploy(captured)
    )
    monkeypatch.setattr(compose_manager, "delete_project", _forbid_delete)

    await orchestration_service.upgrade_instance(
        db,
        group_id=legacy_group.id,
        selected_apps=["moviepilot"],
        app_configs={},
        shared_config={},
    )
    await asyncio.gather(*scheduled_tasks)

    async with session_factory() as check_db:
        emby = (
            await check_db.execute(
                select(AppInstance).where(
                    AppInstance.instance_name == "test-stack-emby"
                )
            )
        ).scalar_one_or_none()
        assert emby is not None
        project = (
            await check_db.execute(
                select(DockerComposeProject).where(
                    DockerComposeProject.project_name == "test-stack-emby"
                )
            )
        ).scalar_one_or_none()
        assert project is not None


@pytest.mark.asyncio
async def test_upgrade_failure_rolls_back_and_keeps_version(
    db, session_factory, apps, template, legacy_group, scheduled_tasks, monkeypatch
):
    """原地升级失败时回滚 Compose 版本，组版本保持旧值并记录 failed。"""
    captured: dict = {}
    rolled_back: list[str] = []

    async def fake_rollback(db, project, version):
        rolled_back.append(project.project_name)
        return version

    monkeypatch.setattr(
        compose_manager,
        "edit_and_deploy_async",
        _fake_edit_deploy(captured, fail=True),
    )
    monkeypatch.setattr(compose_manager, "rollback_version", fake_rollback)
    monkeypatch.setattr(compose_manager, "delete_project", _forbid_delete)

    await orchestration_service.upgrade_instance(
        db,
        group_id=legacy_group.id,
        selected_apps=["moviepilot"],
        app_configs={},
        shared_config={},
    )
    await asyncio.gather(*scheduled_tasks)

    assert rolled_back == ["test-stack-moviepilot"]

    async with session_factory() as check_db:
        group = await check_db.get(AppOrchestrationInstance, legacy_group.id)
        assert group.status == "error"
        assert group.orchestration_version == "1.0.0"
        events = await _events(check_db)
        assert len(events) == 1
        assert events[0].status == "failed"
        assert "镜像拉取失败" in events[0].message
        # 失败也保留配置快照
        assert (
            await check_db.execute(select(OrchestrationUpgradeBackup))
        ).scalar_one() is not None


@pytest.mark.asyncio
async def test_upgrade_adds_new_template_app(
    db, session_factory, apps, template, legacy_group, scheduled_tasks, monkeypatch
):
    """模板新增应用走正常部署，已部署应用不受影响。"""
    captured: dict = {}
    monkeypatch.setattr(
        compose_manager, "edit_and_deploy_async", _fake_edit_deploy(captured)
    )
    monkeypatch.setattr(compose_manager, "delete_project", _forbid_delete)

    async def fake_deploy(db, app_name, instance_name, config, user_id=None):
        app = (
            await db.execute(select(App).where(App.name == app_name))
        ).scalar_one()
        instance = AppInstance(
            app_id=app.id,
            instance_name=instance_name,
            config=config,
            status="deploying",
        )
        db.add(instance)
        await db.commit()
        await db.refresh(instance)
        return instance, f"task-{app_name}"

    monkeypatch.setattr(app_service, "deploy", fake_deploy)
    monkeypatch.setattr(
        orchestration_service,
        "_wait_task_terminal",
        lambda task_id, timeout=3600: asyncio.sleep(0, result="completed"),
    )
    monkeypatch.setattr(
        orchestration_service, "_schedule_provisioning", lambda *a, **k: None
    )

    await orchestration_service.upgrade_instance(
        db,
        group_id=legacy_group.id,
        selected_apps=["moviepilot", "qbittorrent"],
        app_configs={},
        shared_config={},
    )
    await asyncio.gather(*scheduled_tasks)

    async with session_factory() as check_db:
        new_instance = (
            await check_db.execute(
                select(AppInstance).where(
                    AppInstance.instance_name == "test-stack-qbittorrent"
                )
            )
        ).scalar_one_or_none()
        assert new_instance is not None
        assert new_instance.orchestration_group_id == legacy_group.id
        group = await check_db.get(AppOrchestrationInstance, legacy_group.id)
        assert group.orchestration_version == "2.0.0"


@pytest.mark.asyncio
async def test_upgrade_rejects_app_not_in_template(
    db, apps, template, legacy_group, scheduled_tasks
):
    """所选应用不在模板组合中时报 400。"""
    from app.core.exceptions import APIException

    with pytest.raises(APIException) as exc_info:
        await orchestration_service.upgrade_instance(
            db,
            group_id=legacy_group.id,
            selected_apps=["emby"],
            app_configs={},
            shared_config={},
        )

    assert exc_info.value.status_code == 400
    assert "不在当前编排组合中" in str(exc_info.value)


@pytest.mark.asyncio
async def test_upgrade_rejected_while_deploy_in_flight(
    db, apps, template, legacy_group, scheduled_tasks
):
    """组内有进行中的部署任务时拒绝升级。"""
    from app.core.exceptions import APIException

    deploy_task_manager = upgrade_module.deploy_task_manager

    result = await db.execute(
        select(AppInstance).where(
            AppInstance.orchestration_group_id == legacy_group.id
        )
    )
    old_instance = result.scalars().first()

    task_id = deploy_task_manager.create_task(
        "app_deploy", instance_id=old_instance.id
    )
    try:
        with pytest.raises(APIException) as exc_info:
            await orchestration_service.upgrade_instance(
                db,
                group_id=legacy_group.id,
                selected_apps=["moviepilot"],
                app_configs={},
                shared_config={},
            )
        assert exc_info.value.status_code == 409
        assert "正在部署中" in str(exc_info.value)
    finally:
        deploy_task_manager.fail_task(task_id, "测试清理")


@pytest.mark.asyncio
async def test_upgrade_resumes_interrupted_compose_up(
    db, session_factory, apps, template, legacy_group, scheduled_tasks, monkeypatch
):
    """上次升级已写入目标镜像但服务未启动时，重新升级要补一次 up -d。

    升级中断（进程重启）后 Compose YAML 可能已更新、容器停在 Created，
    此时镜像比较结果相同，不能再直接跳过，否则服务永远不会启动。
    """
    async with session_factory() as prep_db:
        project = (
            await prep_db.execute(
                select(DockerComposeProject).where(
                    DockerComposeProject.project_name == "test-stack-moviepilot"
                )
            )
        ).scalar_one()
        version = (
            await prep_db.execute(
                select(DockerComposeVersion).where(
                    DockerComposeVersion.project_id == project.id
                )
            )
        ).scalar_one()
        content = yaml.safe_load(version.content)
        content["services"]["app"]["image"] = "moviepilot:v2"
        version.content = yaml.safe_dump(content, sort_keys=False)
        await prep_db.commit()

    calls: dict = {}

    async def fake_up(db_, project_, action, progress_callback=None):
        calls["project"] = project_.project_name
        calls["action"] = action
        if progress_callback:
            progress_callback("pulling_images", 50, "拉取/启动服务中")
        return {"returncode": 0}

    async def forbid_edit(*_args, **_kwargs):
        raise AssertionError("镜像未变化时不应新增 Compose 版本")

    monkeypatch.setattr(compose_manager, "action_async", fake_up)
    monkeypatch.setattr(compose_manager, "edit_and_deploy_async", forbid_edit)
    monkeypatch.setattr(compose_manager, "delete_project", _forbid_delete)

    group, task_ids = await orchestration_service.upgrade_instance(
        db,
        group_id=legacy_group.id,
        selected_apps=["moviepilot"],
        app_configs={},
        shared_config={},
        user_id=1,
    )
    assert len(task_ids) == 1
    await asyncio.gather(*scheduled_tasks)

    assert calls == {"project": "test-stack-moviepilot", "action": "up"}

    async with session_factory() as check_db:
        upgraded = await check_db.get(AppOrchestrationInstance, legacy_group.id)
        assert upgraded.orchestration_version == "2.0.0"
        assert upgraded.status == "running"


@pytest.mark.asyncio
async def test_upgrade_preview_images(db, legacy_group):
    """详情预览读取当前 Compose 镜像，并按模板/用户标签算出目标镜像。"""
    from app.services.orchestration.upgrade import (
        read_current_image,
        resolve_target_image,
    )

    instance = (
        await db.execute(
            select(AppInstance).where(
                AppInstance.instance_name == "test-stack-moviepilot"
            )
        )
    ).scalar_one()
    db_app = await db.get(App, instance.app_id)

    assert await read_current_image(db, instance, db_app) == "moviepilot:v1"

    # 模板未指定镜像时跟随应用默认镜像
    assert (
        resolve_target_image({"app_name": "moviepilot"}, db_app, instance.config)
        == "moviepilot:v2"
    )
    # 模板组合显式 image/tag 优先
    assert (
        resolve_target_image(
            {"app_name": "moviepilot", "image": "gotson/komga", "tag": "1.29.1"},
            db_app,
            instance.config,
        )
        == "gotson/komga:1.29.1"
    )
    # 浮动的 latest 跟随方案 tag，明确固定的版本标签不被覆盖
    assert (
        resolve_target_image(
            {"app_name": "kavita", "image": "lscr.io/linuxserver/kavita:latest", "tag": "v0.9.1"},
            db_app,
            instance.config,
        )
        == "lscr.io/linuxserver/kavita:v0.9.1"
    )
    assert (
        resolve_target_image(
            {"app_name": "komga", "image": "gotson/komga:1.29.0", "tag": "1.30.0"},
            db_app,
            instance.config,
        )
        == "gotson/komga:1.29.0"
    )
    # 用户显式选择的 image_tag 在模板未覆盖时保留
    assert (
        resolve_target_image({"app_name": "moviepilot"}, db_app, {"image_tag": "v1.5"})
        == "moviepilot:v1.5"
    )
