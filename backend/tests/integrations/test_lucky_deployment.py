"""Lucky 托管部署、运行状态与删除测试。"""

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.models.integration import IntegrationConfig
from app.models.system_config import SystemConfig
from app.services.compose import compose_manager
from app.services.docker import docker_manager
from app.services.integrations import lucky_deployment as lucky_deployment_module
from app.services.integrations.lucky import LuckyClient
from app.services.integrations.lucky_deployment import (
    LUCKY_IMAGE,
    LUCKY_PROJECT,
    lucky_deployment_service,
)
from app.services.system.storage_directory_service import storage_directory_service


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        yield session
    await engine.dispose()


async def _register_and_login(client, username: str) -> str:
    resp = await client.post(
        "/api/auth/register", json={"username": username, "password": "pass123456"}
    )
    assert resp.status_code == 201, resp.text
    login = await client.post(
        "/api/auth/login", json={"username": username, "password": "pass123456"}
    )
    assert login.status_code == 200, login.text
    return login.json()["data"]["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_deploy_endpoint_returns_task(client, monkeypatch):
    token = await _register_and_login(client, "luckydeployapi")
    captured = {}

    async def _start(db, **kwargs):
        captured.update(kwargs)
        return "lucky-task-id"

    monkeypatch.setattr(lucky_deployment_service, "start_deploy_managed", _start)
    resp = await client.post(
        "/api/integrations/network/lucky/deploy",
        headers=_auth(token),
        json={"username": "onetap", "password": "lucky-pass"},
    )
    assert resp.status_code == 200, resp.text
    assert captured == {
        "username": "onetap",
        "password": "lucky-pass",
        "web_port": None,
        "config": None,
    }
    assert resp.json()["data"] == {
        "task_id": "lucky-task-id",
        "instance_name": LUCKY_PROJECT,
        "deploying": True,
    }


async def test_deploy_uses_unified_system_credentials(db_session, monkeypatch):
    db_session.add(
        SystemConfig(
            default_admin_username="admin@example.com",
            default_admin_password="unified-pass",
        )
    )
    await db_session.flush()
    captured = {}

    def _run_task(task_id, username, password, *, web_port, app_config=None):
        captured.update(
            username=username,
            password=password,
            web_port=web_port,
            app_config=app_config,
        )

        async def _noop():
            return None

        return _noop()

    from app.core import background

    monkeypatch.setattr(
        lucky_deployment_service, "_run_deploy_managed_task", _run_task
    )
    monkeypatch.setattr(
        background, "create_background_task", lambda coroutine: coroutine.close()
    )
    task_id = await lucky_deployment_service.start_deploy_managed(db_session)
    assert task_id
    assert captured == {
        "username": "admin",
        "password": "unified-pass",
        "web_port": None,
        "app_config": None,
    }


async def test_runtime_status_uses_real_container_state(monkeypatch):
    async def _container():
        return {"status": "running"}

    async def _external():
        return []

    monkeypatch.setattr(lucky_deployment_service, "_container", _container)
    monkeypatch.setattr(lucky_deployment_service, "_list_external", _external)
    status = await lucky_deployment_service.runtime_status(
        {
            "managed": True,
            "web_port": 16601,
            "url": "http://192.168.1.20:16601",
        }
    )
    assert status == {
        "managed": True,
        "running": True,
        "external": False,
        "external_name": None,
        "web_port": 16601,
        "web_url": "http://192.168.1.20:16601",
    }


@pytest.fixture
def deploy_mocks(tmp_path, monkeypatch):
    captured = {
        "login": [],
        "update": [],
        "remove": [],
        "create": [],
        "ready": [],
    }

    async def _pair(db):
        return str(tmp_path), str(tmp_path)

    async def _choose(db, lucky, requested):
        return 16601

    async def _probe(selected):
        return 16602

    async def _host():
        return "192.168.1.20"

    async def _container():
        return None

    async def _external():
        return []

    async def _remove(db, *, backup_data):
        captured["remove"].append(backup_data)

    async def _ready(urls, timeout=150.0):
        captured["ready"].append(list(urls))
        return urls[0]

    async def _create(db, **kwargs):
        captured["create"].append(kwargs)
        return SimpleNamespace(id=1)

    async def _login(self):
        captured["login"].append((self.base_url, self.username, self.password))
        self.token = "token"
        return self.token

    async def _update(
        self,
        *,
        account,
        password,
        old_password,
        listen_port=None,
        disable_tls=False,
    ):
        captured["update"].append(
            {
                "account": account,
                "password": password,
                "old_password": old_password,
                "listen_port": listen_port,
                "disable_tls": disable_tls,
            }
        )

    async def _test(self):
        return "3.6.0"

    monkeypatch.setattr(storage_directory_service, "get_app_data_pair", _pair)
    monkeypatch.setattr(lucky_deployment_service, "_choose_admin_port", _choose)
    monkeypatch.setattr(lucky_deployment_service, "_choose_probe_port", _probe)
    monkeypatch.setattr(lucky_deployment_service, "_host", _host)
    monkeypatch.setattr(lucky_deployment_service, "_container", _container)
    monkeypatch.setattr(lucky_deployment_service, "_list_external", _external)
    monkeypatch.setattr(lucky_deployment_service, "_remove_project", _remove)
    monkeypatch.setattr(lucky_deployment_service, "_wait_until_ready", _ready)
    monkeypatch.setattr(compose_manager, "create_project_async", _create)
    monkeypatch.setattr(LuckyClient, "login", _login)
    monkeypatch.setattr(LuckyClient, "update_admin", _update)
    monkeypatch.setattr(LuckyClient, "test", _test)
    return captured


async def test_first_deploy_initializes_admin_and_uses_host_network(
    db_session, deploy_mocks
):
    result = await lucky_deployment_service.deploy_managed(
        db_session, username="onetap", password="lucky-pass"
    )
    assert result["deployed"] is True
    assert result["url"] == "http://192.168.1.20:16601"
    assert deploy_mocks["login"][0] == (
        "http://192.168.1.20:16601",
        "666",
        "666",
    )
    update = deploy_mocks["update"][0]
    assert update["account"] == "onetap"
    assert update["password"] == "lucky-pass"
    assert update["old_password"] == "666"
    assert update["listen_port"] == 16601
    assert update["disable_tls"] is True

    bootstrap = yaml.safe_load(deploy_mocks["create"][0]["content"])
    service = bootstrap["services"]["lucky"]
    assert service["container_name"] == "onetap-lucky"
    assert service["ports"] == ["16601:16601", "16602:16601"]
    assert service["volumes"][0].endswith("onetap-lucky/conf:/app/conf")

    final = yaml.safe_load(deploy_mocks["create"][1]["content"])
    final_service = final["services"]["lucky"]
    assert final_service["network_mode"] == "host"
    assert "ports" not in final_service

    record = (
        await db_session.execute(
            select(IntegrationConfig).where(
                IntegrationConfig.kind == "network"
            )
        )
    ).scalar_one()
    assert record.enabled is True
    assert record.config["provider"] == "lucky"
    assert record.config["lucky"]["managed"] is True
    assert record.config["lucky"]["url"] == "http://192.168.1.20:16601"
    assert record.config["lucky"]["username"] == "onetap"
    assert record.status["version"] == "3.6.0"


async def test_deploy_uses_dialog_config_for_volume_env_and_privileged(
    db_session, deploy_mocks, tmp_path
):
    conf_dir = tmp_path / "onetap-lucky" / "conf"
    app_config = {
        "volumes": [
            {
                "host_path": str(conf_dir),
                "container_path": "/app/conf",
                "mode": "rw",
            }
        ],
        "lucky_env": [
            {"key": "TZ", "value": "UTC"},
            {"key": "EXTRA", "value": "1"},
        ],
        "privileged": True,
    }

    await lucky_deployment_service.deploy_managed(
        db_session,
        username="onetap",
        password="lucky-pass",
        app_config=app_config,
    )

    bootstrap = yaml.safe_load(deploy_mocks["create"][0]["content"])
    service = bootstrap["services"]["lucky"]
    assert service["volumes"] == [f"{conf_dir}:/app/conf"]
    assert service["environment"] == ["TZ=UTC", "EXTRA=1"]
    assert service["privileged"] is True

    final = yaml.safe_load(deploy_mocks["create"][1]["content"])
    final_service = final["services"]["lucky"]
    assert final_service["volumes"] == [f"{conf_dir}:/app/conf"]
    assert final_service["environment"] == ["TZ=UTC", "EXTRA=1"]
    assert final_service["privileged"] is True


async def test_redeploy_logs_in_with_saved_credentials(
    db_session, deploy_mocks
):
    db_session.add(
        IntegrationConfig(
            kind="network",
            enabled=True,
            config={
                "provider": "lucky",
                "lucky": {
                    "managed": True,
                    "url": "http://192.168.1.20:16601",
                    "web_port": 16601,
                    "username": "onetap",
                    "password": "old-pass",
                },
            },
            status={},
        )
    )
    await db_session.flush()
    await lucky_deployment_service.deploy_managed(
        db_session, username="onetap", password="new-pass"
    )
    assert deploy_mocks["login"][0] == (
        "http://192.168.1.20:16601",
        "onetap",
        "old-pass",
    )
    assert deploy_mocks["update"][0]["old_password"] == "old-pass"
    assert deploy_mocks["update"][0]["password"] == "new-pass"
    assert deploy_mocks["remove"] == [False, False]


async def test_deploy_migrates_external_lucky_conf(
    db_session, deploy_mocks, monkeypatch
):
    copied = []
    removed = []

    async def _external():
        return [
            {
                "name": "lucky",
                "image": "gdy666/lucky:v3_lucky",
                "labels": {"com.docker.compose.project": "lucky"},
            }
        ]

    async def _copy(name, target):
        copied.append(name)
        target.mkdir(parents=True, exist_ok=True)
        (target / "lucky_base.lkcf").write_text("migrated")

    async def _remove(db, container):
        removed.append(container["name"])

    monkeypatch.setattr(lucky_deployment_service, "_list_external", _external)
    monkeypatch.setattr(lucky_deployment_service, "_copy_container_conf", _copy)
    monkeypatch.setattr(lucky_deployment_service, "_remove_external", _remove)

    await lucky_deployment_service.deploy_managed(
        db_session, username="free9527", password="old-pass"
    )
    assert copied == ["lucky"]
    assert removed == ["lucky"]
    # 迁移后直接用现成账号登录，不再先试 666
    assert deploy_mocks["login"][0] == (
        "http://192.168.1.20:16601",
        "free9527",
        "old-pass",
    )


async def test_deploy_restores_latest_lucky_backup(
    db_session, deploy_mocks, tmp_path
):
    backup = tmp_path / "_backups" / "20260101-000000-lucky" / "conf"
    backup.mkdir(parents=True)
    (backup / "lucky_base.lkcf").write_text("backup")

    await lucky_deployment_service.deploy_managed(
        db_session, username="free9527", password="old-pass"
    )
    restored = tmp_path / "onetap-lucky" / "conf" / "lucky_base.lkcf"
    assert restored.read_text() == "backup"
    assert deploy_mocks["login"][0][1:] == ("free9527", "old-pass")


async def test_restore_backup_uses_container_when_files_unreadable(
    db_session, deploy_mocks, tmp_path, monkeypatch
):
    backup = tmp_path / "_backups" / "20260101-000000-lucky" / "conf"
    backup.mkdir(parents=True)
    (backup / "lucky_base.lkcf").write_text("root-owned")

    calls: dict = {}

    class _Containers:
        def run(self, image, **kwargs):
            calls["image"] = image
            calls["command"] = kwargs["command"]
            mounts = {item["bind"]: host for host, item in kwargs["volumes"].items()}
            calls["mounts"] = mounts
            destination = Path(mounts["/dst"])
            destination.mkdir(parents=True, exist_ok=True)
            for item in Path(mounts["/src"]).iterdir():
                target = destination / item.name
                if item.is_dir():
                    shutil.copytree(item, target, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, target)

    monkeypatch.setattr(lucky_deployment_module, "_tree_readable", lambda _: False)
    monkeypatch.setattr(
        docker_manager, "_client_obj", SimpleNamespace(containers=_Containers())
    )

    await lucky_deployment_service.deploy_managed(
        db_session, username="free9527", password="old-pass"
    )
    restored = tmp_path / "onetap-lucky" / "conf" / "lucky_base.lkcf"
    assert restored.read_text() == "root-owned"
    assert calls["image"] == LUCKY_IMAGE
    assert calls["mounts"]["/src"] == str(backup)
    assert calls["mounts"]["/dst"] == str(tmp_path / "onetap-lucky" / "conf")
    assert "chown" in calls["command"][-1]


async def test_start_deploy_generates_password_without_unified_config(
    db_session, monkeypatch
):
    db_session.add(SystemConfig(default_admin_username="admin"))
    await db_session.flush()
    captured = {}

    def _run_task(task_id, username, password, *, web_port, app_config=None):
        captured.update(
            username=username,
            password=password,
            web_port=web_port,
            app_config=app_config,
        )

        async def _noop():
            return None

        return _noop()

    from app.core import background

    monkeypatch.setattr(
        lucky_deployment_service, "_run_deploy_managed_task", _run_task
    )
    monkeypatch.setattr(
        background, "create_background_task", lambda coroutine: coroutine.close()
    )
    task_id = await lucky_deployment_service.start_deploy_managed(db_session)
    assert task_id
    assert captured["username"] == "admin"
    assert len(captured["password"]) >= 12


async def test_delete_managed_backs_up_data_and_clears_integration(
    db_session, monkeypatch
):
    record = IntegrationConfig(
        kind="network",
        enabled=True,
        config={
            "provider": "lucky",
            "lucky": {
                "managed": True,
                "url": "http://192.168.1.20:16601",
                "web_port": 16601,
                "username": "onetap",
                "password": "lucky-pass",
                "listen_port": 443,
            },
        },
        status={"ok": True, "message": "连接成功"},
    )
    db_session.add(record)
    await db_session.flush()
    removed = []

    async def _remove(db, *, backup_data):
        removed.append(backup_data)

    monkeypatch.setattr(lucky_deployment_service, "_remove_project", _remove)
    result = await lucky_deployment_service.delete_instance(db_session)
    assert result["removed"] is True
    assert removed == [True]
    assert record.enabled is False
    assert record.status == {}
    assert record.config["lucky"] == {
        "username": "onetap",
        "password": "lucky-pass",
        "listen_port": 443,
    }


async def test_delete_external_instance_backs_up_and_removes_container(
    db_session, monkeypatch
):
    record = IntegrationConfig(
        kind="network",
        enabled=True,
        config={
            "provider": "lucky",
            "lucky": {
                "url": "http://192.168.1.17:16601",
                "username": "free9527",
                "password": "lucky-pass",
            },
        },
        status={},
    )
    db_session.add(record)
    await db_session.flush()
    backed_up = []
    removed = []

    async def _external():
        return [
            {
                "name": "lucky",
                "image": "gdy666/lucky:v3_lucky",
                "labels": {"com.docker.compose.project": "lucky"},
            }
        ]

    async def _backup(db, name):
        backed_up.append(name)

    async def _remove(db, container):
        removed.append(container["name"])

    monkeypatch.setattr(lucky_deployment_service, "_list_external", _external)
    monkeypatch.setattr(lucky_deployment_service, "_backup_container_conf", _backup)
    monkeypatch.setattr(lucky_deployment_service, "_remove_external", _remove)
    result = await lucky_deployment_service.delete_instance(db_session)
    assert result["removed"] is True
    assert backed_up == ["lucky"]
    assert removed == ["lucky"]
    assert record.enabled is False
    assert "url" not in record.config["lucky"]
    assert record.config["lucky"]["username"] == "free9527"


async def test_delete_rejects_when_no_container_found(db_session, monkeypatch):
    db_session.add(
        IntegrationConfig(
            kind="network",
            config={"provider": "lucky", "lucky": {"url": "http://lucky:16601"}},
            status={},
        )
    )
    await db_session.flush()

    async def _external():
        return []

    monkeypatch.setattr(lucky_deployment_service, "_list_external", _external)
    with pytest.raises(Exception) as exc:
        await lucky_deployment_service.delete_instance(db_session)
    assert "未找到可删除的 Lucky 容器" in str(exc.value)
