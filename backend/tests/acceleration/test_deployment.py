"""Mihomo 应用市场 Compose 部署与持久化测试。"""

import asyncio

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.database import Base
from app.models.app_store import App
from app.models.docker import DockerComposeProject
from app.models.orchestration import AppInstance
from app.models.storage_directory import StorageDirectory
from app.services.acceleration import deployment, mihomo
from app.services.compose import compose_manager


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        yield session
    await engine.dispose()


async def _storage(db: AsyncSession, tmp_path) -> str:
    mount = tmp_path / "mounted"
    mount.mkdir()
    db.add(
        StorageDirectory(
            name="app-data",
            host_path="/host/app-data",
            mount_path=str(mount),
            enabled=True,
            is_app_data=True,
        )
    )
    await db.commit()
    return str(mount)


def _app() -> App:
    return App(
        name="mihomo",
        display_name="Mihomo",
        image="metacubex/mihomo:v1.19.33",
        yaml_template="services:\n  mihomo:\n    image: {{ image }}",
        config_schema={
            "properties": {
                "ports": {
                    "default": [
                        {"protocol": "tcp", "local_port": 7890, "container_port": 7890},
                        {"protocol": "tcp", "local_port": 9090, "container_port": 9090},
                    ]
                },
                "volumes": {
                    "default": [
                        {
                            "mode": "rw",
                            "host_path": "mihomo/config",
                            "container_path": "/root/.config/mihomo",
                        }
                    ]
                },
                "privileged": {"default": False},
                "mihomo_env": {"default": [{"key": "TZ", "value": "Asia/Shanghai"}]},
            }
        },
    )


@pytest.mark.asyncio
async def test_start_deploy_uses_app_store_and_migrates_legacy_config(
    db_session, tmp_path, monkeypatch
):
    mount = await _storage(db_session, tmp_path)
    db_session.add(_app())
    await db_session.commit()
    legacy = tmp_path / "acceleration" / "mihomo"
    legacy.mkdir(parents=True)
    (legacy / "config.yaml").write_text("mixed-port: 7890\n", encoding="utf-8")
    (legacy / "state.json").write_text('{"revision": 123}', encoding="utf-8")
    monkeypatch.setattr(settings, "DATA_ROOT", str(tmp_path))
    captured = {}

    async def remove_legacy():
        captured["legacy_removed"] = True
        return True

    async def deploy(db, *, app_name, instance_name, config):
        captured.update(app_name=app_name, instance_name=instance_name, config=config)
        return None, "app-task"

    def background(coro):
        coro.close()

    monkeypatch.setattr(mihomo, "remove_legacy_container", remove_legacy)
    monkeypatch.setattr("app.services.app_store.app_service.deploy", deploy)
    monkeypatch.setattr("app.core.background.create_background_task", background)

    task_id = await deployment.start_deploy(db_session)

    assert task_id
    assert captured["app_name"] == "mihomo"
    assert captured["instance_name"] == "onetap-mihomo"
    assert captured["legacy_removed"] is True
    target = tmp_path / "mounted" / "onetap-mihomo" / "mihomo" / "config"
    assert (target / "config.yaml").read_text(encoding="utf-8") == "mixed-port: 7890\n"
    assert '"revision": 123' in (target / "state.json").read_text(encoding="utf-8")
    assert captured["config"]["volumes"][0]["host_path"] == "mihomo/config"
    assert mount


@pytest.mark.asyncio
async def test_runtime_uses_instance_mount_and_ports(db_session, tmp_path):
    mount = await _storage(db_session, tmp_path)
    db_session.add(
        AppInstance(
            instance_name="onetap-mihomo",
            status="error",
            config={"ports": [{"container_port": 7890, "local_port": 7890}]},
        )
    )
    db_session.add(
        AppInstance(
            instance_name="onetap-mihomo",
            status="running",
            config={
                "volumes": [
                    {
                        "host_path": "custom-config",
                        "container_path": "/root/.config/mihomo",
                        "mode": "rw",
                    }
                ],
                "ports": [
                    {"container_port": 7890, "local_port": 17890},
                    {"container_port": 9090, "local_port": 19090},
                ],
            },
        )
    )
    await db_session.commit()

    directory = await mihomo.config_dir(db_session)
    ports = await mihomo.host_ports(db_session)
    proxy = await mihomo.proxy_url(db_session)

    assert directory == tmp_path / "mounted" / "onetap-mihomo" / "custom-config"
    assert directory.exists()
    assert ports == {7890: 17890, 9090: 19090}
    assert proxy.endswith(":17890")
    assert mount


@pytest.mark.asyncio
async def test_delete_uses_compose_backup_after_disabling_apps(
    db_session, monkeypatch
):
    project = DockerComposeProject(
        project_name="onetap-mihomo",
        config_files="[]",
        working_dir="/tmp/onetap-mihomo",
    )
    db_session.add(project)
    await db_session.commit()
    calls = []

    async def disable_all(db):
        calls.append("disable")
        return ["moviepilot"]

    async def delete_project(db, project, backup_data=False):
        calls.append(("delete", project.project_name, backup_data))

    monkeypatch.setattr(
        "app.services.acceleration.apps.disable_all", disable_all
    )
    monkeypatch.setattr(compose_manager, "delete_project", delete_project)

    result = await deployment.delete_managed(db_session)

    assert calls == ["disable", ("delete", "onetap-mihomo", True)]
    assert result["disabled_apps"] == ["moviepilot"]
    assert "数据已备份" in result["message"]


@pytest.mark.asyncio
async def test_deploy_syncs_subscription_after_deploy(db_session, monkeypatch):
    from app.services.system.config_service import system_config_service

    config = await system_config_service.get_or_create(db_session)
    config.acceleration_subscription_url = "http://example.com/sub"
    await db_session.commit()
    captured = {}

    async def sync(db, *, force, trigger):
        captured.update(force=force, trigger=trigger)
        return {"ok": True, "message": "", "detail": {"rules": 7}}

    monkeypatch.setattr("app.services.acceleration.managed.sync", sync)

    note = await deployment._sync_subscription_after_deploy(db_session)

    assert captured == {"force": True, "trigger": "deploy"}
    assert note == "，已同步订阅（7 条规则）"


@pytest.mark.asyncio
async def test_deploy_skips_subscription_sync_without_url(
    db_session, monkeypatch
):
    from app.services.system.config_service import system_config_service

    await system_config_service.get_or_create(db_session)

    async def sync(*args, **kwargs):
        raise AssertionError("未配置订阅地址时不应同步")

    monkeypatch.setattr("app.services.acceleration.managed.sync", sync)

    assert await deployment._sync_subscription_after_deploy(db_session) == ""


@pytest.mark.asyncio
async def test_deploy_reports_subscription_sync_failure(db_session, monkeypatch):
    from app.core.exceptions import APIException
    from app.services.system.config_service import system_config_service

    config = await system_config_service.get_or_create(db_session)
    config.acceleration_subscription_url = "http://example.com/sub"
    await db_session.commit()

    async def sync(db, *, force, trigger):
        raise APIException("拉取订阅失败：timeout", 502)

    monkeypatch.setattr("app.services.acceleration.managed.sync", sync)

    note = await deployment._sync_subscription_after_deploy(db_session)
    assert "订阅同步失败" in note
    assert "timeout" in note
