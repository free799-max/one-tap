"""Vaultwarden 托管部署、运行状态与删除测试。"""

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
from app.services.integrations.vaultwarden import VaultwardenClient, VaultwardenSession
from app.services.integrations.vaultwarden_deployment import (
    VAULTWARDEN_PROJECT,
    _port_from_config,
    vaultwarden_deployment_service,
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
    token = await _register_and_login(client, "vwdeployapi")
    captured = {}

    async def _start(db, **kwargs):
        captured.update(kwargs)
        return "vaultwarden-task-id"

    monkeypatch.setattr(vaultwarden_deployment_service, "start_deploy_managed", _start)
    resp = await client.post(
        "/api/integrations/vaultwarden/deploy",
        headers=_auth(token),
        json={"email": "admin@example.com", "password": "master-pass"},
    )
    assert resp.status_code == 200, resp.text
    assert captured == {
        "email": "admin@example.com",
        "password": "master-pass",
        "web_port": None,
        "app_config": None,
    }
    assert resp.json()["data"] == {
        "task_id": "vaultwarden-task-id",
        "instance_name": VAULTWARDEN_PROJECT,
        "deploying": True,
    }


async def test_deploy_requires_email_and_password(client):
    token = await _register_and_login(client, "vwdeploycredentials")
    resp = await client.post(
        "/api/integrations/vaultwarden/deploy", headers=_auth(token), json={}
    )
    assert resp.status_code == 400
    assert "邮箱和主密码" in resp.json()["message"]


async def test_deploy_uses_unified_system_credentials(db_session, monkeypatch):
    db_session.add(
        SystemConfig(
            default_admin_username="admin",
            default_admin_password="unified-pass",
            default_email_domain="example.com",
        )
    )
    await db_session.flush()
    captured = {}

    def _run_task(task_id, email, password, *, web_port, app_config=None):
        captured.update(
            email=email, password=password, web_port=web_port, app_config=app_config
        )

        async def _noop():
            return None

        return _noop()

    from app.core import background

    monkeypatch.setattr(
        vaultwarden_deployment_service, "_run_deploy_managed_task", _run_task
    )
    monkeypatch.setattr(
        background, "create_background_task", lambda coroutine: coroutine.close()
    )
    task_id = await vaultwarden_deployment_service.start_deploy_managed(db_session)
    assert task_id
    assert captured == {
        "email": "admin@example.com",
        "password": "unified-pass",
        "web_port": None,
        "app_config": None,
    }


async def test_runtime_status_uses_real_container_state(monkeypatch):
    async def _container():
        return {"status": "running"}

    async def _host():
        return "192.168.1.20"

    monkeypatch.setattr(vaultwarden_deployment_service, "_container", _container)
    monkeypatch.setattr(vaultwarden_deployment_service, "_host", _host)
    status = await vaultwarden_deployment_service.runtime_status(
        {"managed": True, "web_port": 18080}
    )
    assert status == {
        "managed": True,
        "running": True,
        "web_port": 18080,
        "web_url": "http://192.168.1.20:18080",
    }


@pytest.fixture
def deploy_mocks(tmp_path, monkeypatch):
    captured = {"register": 0, "login": 0, "remove": [], "create": [], "edit": []}

    async def _pair(db):
        return str(tmp_path), str(tmp_path)

    async def _choose(db, config, requested):
        return 18080

    async def _host():
        return "192.168.1.20"

    async def _container():
        return None

    async def _remove(db, *, backup_data):
        captured["remove"].append(backup_data)

    async def _ready(url):
        captured["ready_url"] = url

    async def _create(db, **kwargs):
        captured["create"].append(kwargs)
        return SimpleNamespace(id=1)

    async def _edit(db, project, **kwargs):
        captured["edit"].append(kwargs)
        return SimpleNamespace(), {}

    async def _register(self, email, password):
        captured["register"] += 1

    async def _login(self, email, password):
        captured["login"] += 1
        return VaultwardenSession(token="token", user_key=bytes(64), version="1.34.3")

    monkeypatch.setattr(storage_directory_service, "get_app_data_pair", _pair)
    monkeypatch.setattr(vaultwarden_deployment_service, "_choose_web_port", _choose)
    monkeypatch.setattr(vaultwarden_deployment_service, "_host", _host)
    monkeypatch.setattr(vaultwarden_deployment_service, "_container", _container)
    monkeypatch.setattr(vaultwarden_deployment_service, "_remove_project", _remove)
    monkeypatch.setattr(vaultwarden_deployment_service, "_wait_until_ready", _ready)
    monkeypatch.setattr(compose_manager, "create_project_async", _create)
    monkeypatch.setattr(compose_manager, "edit_and_deploy_async", _edit)
    monkeypatch.setattr(VaultwardenClient, "register", _register)
    monkeypatch.setattr(VaultwardenClient, "login", _login)
    return captured


async def test_first_deploy_registers_login_and_disables_signups(
    db_session, deploy_mocks
):
    result = await vaultwarden_deployment_service.deploy_managed(
        db_session, email="admin@example.com", password="master-pass"
    )
    assert result["deployed"] is True
    assert deploy_mocks["register"] == 1
    assert deploy_mocks["login"] == 1
    assert deploy_mocks["remove"] == [False]
    initial = yaml.safe_load(deploy_mocks["create"][0]["content"])
    secured = yaml.safe_load(deploy_mocks["edit"][0]["content"])
    service = initial["services"]["vaultwarden"]
    assert service["environment"]["SIGNUPS_ALLOWED"] == "true"
    assert service["ports"] == ["18080:80"]
    assert service["volumes"][0].endswith("onetap-vaultwarden/vaultwarden/data:/data")
    assert secured["services"]["vaultwarden"]["environment"]["SIGNUPS_ALLOWED"] == "false"

    record = (
        await db_session.execute(
            select(IntegrationConfig).where(IntegrationConfig.kind == "vaultwarden")
        )
    ).scalar_one()
    assert record.enabled is True
    assert record.config["managed"] is True
    assert record.config["url"] == "http://192.168.1.20:18080"
    assert record.status["version"] == "1.34.3"


async def test_deploy_applies_dialog_config(db_session, deploy_mocks, tmp_path):
    data_host = tmp_path / "custom-data"
    await vaultwarden_deployment_service.deploy_managed(
        db_session,
        email="admin@example.com",
        password="master-pass",
        app_config={
            "volumes": [
                {
                    "host_path": str(data_host),
                    "container_path": "/data",
                    "mode": "rw",
                }
            ],
            "vaultwarden_env": [
                {"key": "TZ", "value": "UTC"},
                {"key": "LOG_LEVEL", "value": "warn"},
            ],
            "privileged": True,
        },
    )
    service = yaml.safe_load(deploy_mocks["create"][0]["content"])["services"][
        "vaultwarden"
    ]
    assert service["volumes"] == [f"{data_host}:/data"]
    assert service["environment"]["TZ"] == "UTC"
    assert service["environment"]["LOG_LEVEL"] == "warn"
    assert service["environment"]["SIGNUPS_ALLOWED"] == "true"
    assert service["privileged"] is True


def test_port_from_config_prefers_container_80():
    config = {
        "ports": [
            {"container_port": 3012, "local_port": 3012},
            {"container_port": 80, "local_port": 9090},
        ]
    }
    assert _port_from_config(config) == 9090
    assert _port_from_config(None) is None


async def test_redeploy_preserves_data_and_only_logs_in(db_session, deploy_mocks):
    db_session.add(
        IntegrationConfig(
            kind="vaultwarden",
            enabled=True,
            config={
                "managed": True,
                "url": "http://192.168.1.20:18080",
                "web_port": 18080,
                "email": "admin@example.com",
                "password": "master-pass",
            },
            status={},
        )
    )
    await db_session.flush()
    await vaultwarden_deployment_service.deploy_managed(
        db_session, email="admin@example.com", password="master-pass"
    )
    assert deploy_mocks["register"] == 0
    assert deploy_mocks["login"] == 1
    assert deploy_mocks["remove"] == [False]
    assert deploy_mocks["edit"] == []
    content = yaml.safe_load(deploy_mocks["create"][0]["content"])
    assert content["services"]["vaultwarden"]["environment"]["SIGNUPS_ALLOWED"] == "false"


async def test_delete_managed_backs_up_data_and_clears_integration(
    db_session, monkeypatch
):
    record = IntegrationConfig(
        kind="vaultwarden",
        enabled=True,
        config={
            "managed": True,
            "url": "http://192.168.1.20:18080",
            "web_port": 18080,
            "email": "admin@example.com",
            "password": "master-pass",
            "vaultwarden_items": {"1": "cipher-id"},
        },
        status={"ok": True, "message": "连接成功"},
    )
    db_session.add(record)
    await db_session.flush()
    removed = []

    async def _remove(db, *, backup_data):
        removed.append(backup_data)

    monkeypatch.setattr(vaultwarden_deployment_service, "_remove_project", _remove)
    result = await vaultwarden_deployment_service.delete_managed(db_session)
    assert result["removed"] is True
    assert removed == [True]
    assert record.enabled is False
    assert record.status == {}
    assert set(record.config) == {"email", "password"}


async def test_delete_rejects_external_instance(db_session):
    db_session.add(
        IntegrationConfig(
            kind="vaultwarden",
            config={"url": "http://external:8080"},
            status={},
        )
    )
    await db_session.flush()
    with pytest.raises(Exception) as exc:
        await vaultwarden_deployment_service.delete_managed(db_session)
    assert "不能自动删除" in str(exc.value)
