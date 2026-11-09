"""应用级 DNS 加速与管理端规则测试。"""

import asyncio
import ipaddress
import json
from types import SimpleNamespace

import pytest
import pytest_asyncio
import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.models.app_store import App
from app.models.docker import DockerComposeProject, DockerComposeVersion
from app.models.orchestration import AppInstance
from app.models.system_config import SystemConfig
from app.schemas.hosts_optimizer import DEFAULT_HOSTS_OPTIMIZER_DOMAINS
from app.services.compose import compose_manager
from app.services.docker import docker_manager
from app.services.system import hosts_acceleration_service, hosts_optimizer_service
from app.services.system.hosts_acceleration_service import (
    ACCEL_FALLBACK_DNS,
    ADGUARD_CONTAINER,
    ADGUARD_NETWORK,
    _dynamic_pool_range,
    _eligible_services,
    _is_ip_conflict_error,
    apply_acceleration,
    remove_acceleration,
)
from app.services.system.hosts_optimizer_service import normalize_managed_rules


async def _register_and_login(client, username: str, password: str = "pass123456") -> str:
    resp = await client.post(
        "/api/auth/register", json={"username": username, "password": password}
    )
    assert resp.status_code == 201, resp.text
    login = await client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert login.status_code == 200, login.text
    return login.json()["data"]["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_normalize_managed_rules_parses_variants():
    rules = normalize_managed_rules(
        [
            "api.themoviedb.org",
            {
                "domain": "image.tmdb.org",
                "answers": ["1.2.3.4", "1.2.3.4", "bad"],
                "priority": 10,
                "description": "TMDB 图片",
                "tier": "subscription",
            },
            {"domain": "disabled.example.com", "enabled": False},
            {"domain": "not a domain"},
            {"host": "github.com", "ips": "140.82.112.3, 140.82.113.3"},
        ]
    )

    assert [rule["domain"] for rule in rules] == [
        "image.tmdb.org",
        "api.themoviedb.org",
        "github.com",
    ]
    assert rules[0]["answers"] == ["1.2.3.4"]
    assert rules[0]["tier"] == "subscription"
    assert rules[1]["answers"] == []
    assert rules[1]["tier"] == "free"
    assert rules[2]["answers"] == ["140.82.112.3", "140.82.113.3"]


def test_normalize_managed_rules_accepts_wrapped_payload():
    rules = normalize_managed_rules(
        {"data": [{"domain": "api.tmdb.org", "answers": ["99.84.215.100"]}]}
    )
    assert rules == [
        {
            "domain": "api.tmdb.org",
            "answers": ["99.84.215.100"],
            "priority": 100,
            "description": None,
            "tier": "free",
        }
    ]


def test_effective_rules_prefers_managed_rules():
    from app.models.system_config import SystemConfig

    config = SystemConfig(
        id=1,
        hosts_optimizer_domains=["local.example.com"],
        hosts_optimizer_managed_rules=[
            {"domain": "managed.example.com", "answers": ["1.2.3.4"]}
        ],
    )
    assert hosts_optimizer_service.effective_rules(config) == [
        {
            "domain": "managed.example.com",
            "answers": ["1.2.3.4"],
            "priority": 100,
            "description": None,
            "tier": "free",
        }
    ]


def test_effective_rules_falls_back_to_local_domains():
    from app.models.system_config import SystemConfig

    config = SystemConfig(id=1, hosts_optimizer_managed_rules=[])
    rules = hosts_optimizer_service.effective_rules(config)
    assert [rule["domain"] for rule in rules] == DEFAULT_HOSTS_OPTIMIZER_DOMAINS
    assert all(rule["answers"] == [] for rule in rules)


def test_eligible_services_skips_host_and_shared_networks():
    eligible, skipped = _eligible_services(
        {
            "app": {"image": "demo:latest"},
            "sidecar": {"image": "demo:latest", "networks": ["backend"]},
            "hostnet": {"image": "demo:latest", "network_mode": "host"},
            "shared": {"image": "demo:latest", "network_mode": "service:app"},
        }
    )
    assert eligible == ["app", "sidecar"]
    assert skipped == ["hostnet", "shared"]


def test_apply_acceleration_merges_existing_networks():
    original = {
        "services": {
            "app": {"image": "demo:latest"},
            "worker": {"image": "demo:latest", "networks": ["backend"]},
            "custom": {
                "image": "demo:latest",
                "networks": {"backend": {"aliases": ["worker"]}},
            },
        },
        "networks": {"backend": {}},
    }
    content, info = apply_acceleration(
        yaml.safe_dump(original, sort_keys=False), "172.31.254.2"
    )
    data = yaml.safe_load(content)

    assert info["services"] == ["app", "worker", "custom"]
    assert data["networks"][ADGUARD_NETWORK] == {
        "external": True,
        "name": ADGUARD_NETWORK,
    }
    assert data["services"]["app"]["dns"] == ["172.31.254.2"]
    assert data["services"]["app"]["networks"] == {
        "default": {},
        ADGUARD_NETWORK: {},
    }
    assert data["services"]["worker"]["networks"] == ["backend", ADGUARD_NETWORK]
    assert data["services"]["custom"]["networks"][ADGUARD_NETWORK] == {}
    assert data["services"]["custom"]["networks"]["backend"]["aliases"] == [
        "worker"
    ]


def test_apply_acceleration_appends_fallback_dns():
    """AdGuard 之后追加去重的兜底 DNS，主 DNS 不可达时仍能解析。"""
    content, _ = apply_acceleration(
        "services:\n  app:\n    image: demo:latest\n",
        "172.31.254.2",
        ["223.5.5.5", "223.5.5.5", "119.29.29.29"],
    )
    data = yaml.safe_load(content)

    assert data["services"]["app"]["dns"] == [
        "172.31.254.2",
        "223.5.5.5",
        "119.29.29.29",
    ]


def test_apply_and_remove_acceleration_restores_original():
    original = {
        "services": {
            "app": {"image": "demo:latest"},
            "worker": {
                "image": "demo:latest",
                "dns": ["1.1.1.1"],
                "networks": ["backend"],
            },
        },
        "networks": {"backend": {}},
    }
    content, _ = apply_acceleration(
        yaml.safe_dump(original, sort_keys=False),
        "172.31.254.2",
        ["223.5.5.5"],
    )
    enabled = yaml.safe_load(content)
    assert enabled["services"]["worker"]["dns"] == [
        "172.31.254.2",
        "1.1.1.1",
        "223.5.5.5",
    ]
    assert enabled["x-onetap-accel"]["services"]["worker"]["dns"] == ["1.1.1.1"]

    restored = yaml.safe_load(remove_acceleration(content))
    assert restored == original


async def test_apply_upstream_dns_switches_from_quad9(monkeypatch):
    """初始化时把不可达的 Quad9 上游换成国内可达的 DoH。"""
    import importlib

    module = importlib.import_module(
        "app.services.system.hosts_acceleration_service"
    )

    calls: list[tuple[str, dict | None]] = []

    class _Response:
        def __init__(self, payload: dict | None = None):
            self._payload = payload or {}

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self._payload

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, path: str):
            assert path == "/control/dns_info"
            return _Response(
                {
                    "upstream_dns": ["https://dns10.quad9.net/dns-query"],
                    "bootstrap_dns": ["9.9.9.10"],
                    "fallback_dns": [],
                }
            )

        async def post(self, path: str, json: dict | None = None):
            calls.append((path, json))
            return _Response()

    monkeypatch.setattr(module.httpx, "AsyncClient", _Client)
    await module.hosts_acceleration_service._apply_upstream_dns(
        "http://adguard", "admin", "pw"
    )

    assert calls[0][0] == "/control/dns_config"
    assert calls[0][1]["upstream_dns"] == list(module.ADGUARD_UPSTREAM_DNS)
    assert calls[0][1]["bootstrap_dns"] == list(module.ADGUARD_BOOTSTRAP_DNS)
    assert calls[0][1]["fallback_dns"] == []
    assert calls[1][0] == "/control/cache_clear"


async def test_acceleration_overview_empty(client):
    token = await _register_and_login(client, "acceloverview")

    resp = await client.get(
        "/api/settings/hosts-optimizer/acceleration", headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["apps"] == []
    assert data["adguard"]["configured"] is False
    assert data["managed_rules"]["rules"]
    assert all(
        rule["tier"] == "free" for rule in data["managed_rules"]["rules"]
    )
    assert isinstance(data["managed_rules"]["configured"], bool)


async def test_network_effectiveness_checks_rewrites_and_running_apps(monkeypatch):
    import importlib

    module = importlib.import_module(
        "app.services.system.hosts_acceleration_service"
    )
    rewrites = [{"domain": "cdn.example.com", "answer": "1.2.3.4"}]

    class _Response:
        def raise_for_status(self):
            pass

        def json(self):
            return rewrites

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, path: str):
            assert path == "/control/rewrite/list"
            return _Response()

    monkeypatch.setattr(module.httpx, "AsyncClient", _Client)
    config = SimpleNamespace(
        hosts_optimizer_adguard_url="http://adguard",
        hosts_optimizer_adguard_username="admin",
        hosts_optimizer_adguard_password="secret",
    )
    rules = [
        {
            "domain": "cdn.example.com",
            "answers": ["1.2.3.4"],
            "tier": "subscription",
        }
    ]
    apps = [{"acceleration_enabled": True, "status": "running"}]

    result = await module.hosts_acceleration_service.network_effectiveness(
        config, rules, apps
    )
    assert result["network_effective"] is True
    assert result["network_applied_rule_count"] == 1
    assert result["network_app_count"] == 1

    apps[0]["status"] = "stopped"
    result = await module.hosts_acceleration_service.network_effectiveness(
        config, rules, apps
    )
    assert result["network_effective"] is False

    apps[0]["status"] = "running"
    rewrites[:] = [{"domain": "cdn.example.com", "answer": "5.6.7.8"}]
    result = await module.hosts_acceleration_service.network_effectiveness(
        config, rules, apps
    )
    assert result["network_effective"] is False
    assert result["network_applied_rule_count"] == 0


async def test_sync_managed_rules_endpoint(client, monkeypatch):
    token = await _register_and_login(client, "accelsync")

    async def _fake_fetch():
        return {
            "configured": True,
            "rules": [
                {
                    "domain": "api.themoviedb.org",
                    "answers": ["18.244.214.89"],
                    "priority": 10,
                    "description": "TMDB",
                }
            ],
            "synced_at": "2026-11-08T10:00:00+00:00",
        }

    monkeypatch.setattr(hosts_optimizer_service, "fetch_managed_rules", _fake_fetch)

    resp = await client.post(
        "/api/settings/hosts-optimizer/managed-rules/sync", headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["configured"] is True
    assert data["rules"][0]["domain"] == "api.themoviedb.org"
    assert data["rules"][0]["answers"] == ["18.244.214.89"]

    config = await client.get(
        "/api/settings/hosts-optimizer/config", headers=_auth(token)
    )
    stored = config.json()["data"]
    assert stored["managed_rules"] == data["rules"]
    assert stored["managed_rules_synced_at"]


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        yield session
    await engine.dispose()


async def test_toggle_app_acceleration_rewrites_compose(
    db_session, tmp_path, monkeypatch
):
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text(
        "services:\n"
        "  app:\n"
        "    image: demo:latest\n"
        "  worker:\n"
        "    image: demo:latest\n"
        "    networks:\n"
        "      - backend\n"
        "networks:\n"
        "  backend: {}\n",
        encoding="utf-8",
    )

    app = App(name="demo", display_name="Demo", yaml_template="services: {}")
    db_session.add(app)
    await db_session.flush()
    project = DockerComposeProject(
        project_name="demo-project",
        config_files=json.dumps([str(compose_file)]),
        working_dir=str(tmp_path),
    )
    db_session.add(project)
    await db_session.flush()
    instance = AppInstance(
        app_id=app.id,
        project_id=project.id,
        instance_name="demo",
        status="running",
    )
    db_session.add(instance)
    await db_session.flush()

    async def _fake_ensure_adguard(db, config):
        return {"dns_ip": "172.31.254.2", "warnings": []}

    async def _fake_action(db, target, action, progress_callback=None):
        assert action == "up"
        return {"returncode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(
        hosts_acceleration_service, "ensure_adguard", _fake_ensure_adguard
    )
    monkeypatch.setattr(compose_manager, "action_async", _fake_action)

    config = SystemConfig(id=1, hosts_optimizer_adguard_dns_ip="172.31.254.2")

    enabled = await hosts_acceleration_service.set_app_acceleration(
        db_session, config, instance.id, True
    )
    assert enabled["enabled"] is True
    assert enabled["dns_ip"] == "172.31.254.2"
    assert set(enabled["services"]) == {"app", "worker"}

    # 单一文件源：DNS 直接写进应用自己的 compose，不再生成覆盖文件。
    data = yaml.safe_load(compose_file.read_text(encoding="utf-8"))
    assert data["services"]["app"]["dns"] == [
        "172.31.254.2",
        "223.5.5.5",
        "119.29.29.29",
    ]
    assert data["services"]["worker"]["networks"] == ["backend", "onetap-accel"]
    assert data["networks"]["onetap-accel"]["external"] is True
    assert not (tmp_path / "docker-compose.onetap-dns.yml").exists()
    assert json.loads(project.config_files or "[]") == [str(compose_file)]
    assert instance.acceleration_enabled is True
    assert config.hosts_optimizer_accel_instances == ["demo"]

    versions = (
        await db_session.execute(
            select(DockerComposeVersion)
            .where(DockerComposeVersion.project_id == project.id)
            .order_by(DockerComposeVersion.version_number)
        )
    ).scalars().all()
    assert versions[-1].comment == "OneTap 网络加速：注入 DNS"
    assert versions[-1].is_current is True

    disabled = await hosts_acceleration_service.set_app_acceleration(
        db_session, config, instance.id, False
    )
    assert disabled["enabled"] is False
    restored = yaml.safe_load(compose_file.read_text(encoding="utf-8"))
    assert "x-onetap-accel" not in restored
    assert "dns" not in restored["services"]["app"]
    assert restored["services"]["worker"]["networks"] == ["backend"]
    assert "onetap-accel" not in restored.get("networks", {})
    assert json.loads(project.config_files or "[]") == [str(compose_file)]
    assert instance.acceleration_enabled is False
    assert config.hosts_optimizer_accel_instances == []


async def test_list_apps_includes_orchestration_group(db_session):
    from app.models.orchestration import (
        AppOrchestration,
        AppOrchestrationInstance,
    )

    orchestration = AppOrchestration(
        name="media", display_name="影视方案", category="media"
    )
    db_session.add(orchestration)
    await db_session.flush()
    group = AppOrchestrationInstance(
        orchestration_id=orchestration.id, instance_name="media-1"
    )
    db_session.add(group)
    await db_session.flush()

    app = App(name="demo", display_name="Demo", yaml_template="services: {}")
    db_session.add(app)
    await db_session.flush()
    project = DockerComposeProject(
        project_name="demo-project", config_files="[]", working_dir="/tmp"
    )
    db_session.add(project)
    await db_session.flush()
    instance = AppInstance(
        app_id=app.id,
        project_id=project.id,
        instance_name="demo",
        status="running",
        orchestration_group_id=group.id,
    )
    db_session.add(instance)
    await db_session.flush()

    items = await hosts_acceleration_service.list_apps(
        db_session, SystemConfig(id=1)
    )

    assert items[0]["orchestration_group_id"] == group.id
    assert items[0]["orchestration_group_name"] == "media-1"
    assert items[0]["orchestration_name"] == "影视方案"


async def test_optimize_uses_managed_answers(monkeypatch):
    captured: dict = {}

    async def _fake_sync(url, username, password, domains, best):
        captured["domains"] = domains
        captured["best"] = best
        return []

    monkeypatch.setattr(hosts_optimizer_service, "_sync_adguard", _fake_sync)

    result = await hosts_optimizer_service.optimize(
        {
            "adguard_url": "http://192.168.1.23:8081",
            "adguard_username": "admin",
            "adguard_password": "secret",
            "top_n": 2,
            "proxy": None,
            "managed_rules": [
                {
                    "domain": "api.themoviedb.org",
                    "answers": ["18.244.214.89", "18.244.214.88"],
                }
            ],
        },
        mode="manual",
    )

    assert result["adguard"] == "ok"
    assert result["domains"]["api.themoviedb.org"]["status"] == "managed"
    assert result["domains"]["api.themoviedb.org"]["source"] == "managed"
    assert captured["domains"] == ["api.themoviedb.org"]
    assert captured["best"]["api.themoviedb.org"] == [
        ("18.244.214.89", 0.0),
        ("18.244.214.88", 0.0),
    ]


def test_adguard_app_config_uses_app_store_and_skips_host_dns_port():
    app = App(
        name="adguardhome",
        display_name="AdGuard Home",
        image="adguard/adguardhome",
        config_schema={
            "type": "object",
            "properties": {
                "ports": {
                    "type": "array",
                    "default": [
                        {"local_port": 53, "container_port": 53, "protocol": "udp"}
                    ],
                },
                "volumes": {
                    "type": "array",
                    "default": [
                        {
                            "host_path": "adguardhome/work",
                            "container_path": "/opt/adguardhome/work",
                            "mode": "rw",
                        }
                    ],
                },
            },
        },
        yaml_template="services: {}",
    )

    config = hosts_acceleration_service._adguard_app_config(app, 3010, 3011)

    assert config["image"] == "adguard/adguardhome:v0.107.79"
    assert config["volumes"] == [
        {
            "host_path": "adguardhome/work",
            "container_path": "/opt/adguardhome/work",
            "mode": "rw",
        }
    ]
    assert config["ports"] == [
        {"protocol": "tcp", "local_port": 3010, "container_port": 3000},
        {"protocol": "tcp", "local_port": 3011, "container_port": 80},
    ]
    assert all(port["container_port"] != 53 for port in config["ports"])


def test_merge_compose_overrides_injects_acceleration_network():
    from app.services.app_store.app_service import _merge_compose_overrides

    rendered = (
        "services:\n"
        "  adguardhome:\n"
        "    image: adguard/adguardhome:v0.107.79\n"
        "    ports:\n"
        "      - '3010:3000/tcp'\n"
    )
    merged = yaml.safe_load(
        _merge_compose_overrides(
            rendered,
            {
                "services": {
                    "adguardhome": {
                        "networks": {ADGUARD_NETWORK: {"ipv4_address": "172.31.254.2"}}
                    }
                },
                "networks": {
                    ADGUARD_NETWORK: {"external": True, "name": ADGUARD_NETWORK}
                },
            },
        )
    )

    assert merged["services"]["adguardhome"]["networks"] == {
        ADGUARD_NETWORK: {"ipv4_address": "172.31.254.2"}
    }
    assert merged["networks"][ADGUARD_NETWORK]["external"] is True


async def test_delete_managed_disables_apps_and_clears_config(
    db_session, tmp_path, monkeypatch
):
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text(
        "services:\n  app:\n    image: demo:latest\n", encoding="utf-8"
    )

    app = App(name="demo", display_name="Demo", yaml_template="services: {}")
    db_session.add(app)
    await db_session.flush()
    project = DockerComposeProject(
        project_name="demo-project",
        config_files=json.dumps([str(compose_file)]),
        working_dir=str(tmp_path),
    )
    db_session.add(project)
    await db_session.flush()
    instance = AppInstance(
        app_id=app.id,
        project_id=project.id,
        instance_name="demo",
        status="running",
        acceleration_enabled=True,
    )
    db_session.add(instance)
    await db_session.flush()

    async def _fake_action(db, target, action, progress_callback=None):
        assert action == "up"
        return {"returncode": 0, "stdout": "", "stderr": ""}

    async def _fake_remove_project(db, backup_data: bool = False):
        assert backup_data is True
        return None

    async def _fake_remove_network():
        return []

    monkeypatch.setattr(compose_manager, "action_async", _fake_action)
    monkeypatch.setattr(
        hosts_acceleration_service, "_remove_project", _fake_remove_project
    )
    monkeypatch.setattr(
        hosts_acceleration_service, "_remove_network_if_empty", _fake_remove_network
    )

    config = SystemConfig(
        id=1,
        hosts_optimizer_adguard_managed=True,
        hosts_optimizer_adguard_url="http://192.168.1.17:3002",
        hosts_optimizer_adguard_dns_ip="172.31.254.2",
        hosts_optimizer_adguard_setup_port=3000,
        hosts_optimizer_adguard_web_port=3002,
        hosts_optimizer_adguard_password="secret",
    )

    result = await hosts_acceleration_service.delete_managed(db_session, config)

    assert result["removed"] is True
    assert result["disabled_apps"] == ["demo"]
    assert instance.acceleration_enabled is False
    assert json.loads(project.config_files or "[]") == [str(compose_file)]
    assert config.hosts_optimizer_adguard_managed is False
    assert config.hosts_optimizer_adguard_url is None
    assert config.hosts_optimizer_adguard_dns_ip is None
    assert config.hosts_optimizer_adguard_setup_port is None
    assert config.hosts_optimizer_adguard_web_port is None
    # 数据目录默认保留，凭据也保留，重新部署时继续使用同一套账号密码。
    assert config.hosts_optimizer_adguard_password == "secret"


async def test_remove_adguard_endpoint(client, monkeypatch):
    token = await _register_and_login(client, "accelremove")

    async def _fake_delete(db, config):
        return {
            "removed": True,
            "disabled_apps": ["demo"],
            "warnings": [],
            "message": "AdGuard Home 已删除",
        }

    monkeypatch.setattr(
        hosts_acceleration_service, "delete_managed", _fake_delete
    )

    resp = await client.delete(
        "/api/settings/hosts-optimizer/acceleration/adguard",
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["removed"] is True
    assert resp.json()["data"]["disabled_apps"] == ["demo"]


def test_port_for_container_reads_install_dialog_config():
    config = {
        "ports": [
            {"protocol": "tcp", "container_port": 3000, "local_port": 3100},
            {"protocol": "tcp", "container_port": 80, "local_port": 3101},
        ]
    }
    assert hosts_acceleration_service._port_for_container(config, 3000) == 3100
    assert hosts_acceleration_service._port_for_container(config, 80) == 3101
    assert hosts_acceleration_service._port_for_container(config, 53) is None


async def test_deploy_adguard_endpoint_passes_install_config(client, monkeypatch):
    token = await _register_and_login(client, "accelinstall")
    captured: dict = {}

    async def _fake_start(db, config, app_config=None):
        captured["app_config"] = app_config
        return "adguard-task-id"

    monkeypatch.setattr(
        hosts_acceleration_service, "start_deploy_managed", _fake_start
    )

    payload = {
        "instance_name": "onetap-adguard",
        "config": {
            "ports": [
                {"protocol": "tcp", "container_port": 3000, "local_port": 3100},
                {"protocol": "tcp", "container_port": 80, "local_port": 3101},
            ]
        },
    }
    resp = await client.post(
        "/api/settings/hosts-optimizer/acceleration/adguard/deploy",
        json=payload,
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert captured["app_config"] == payload["config"]
    data = resp.json()["data"]
    assert data["task_id"] == "adguard-task-id"
    assert data["instance_name"] == "onetap-adguard"
    assert data["status"] == "deploying"


async def test_reapply_saved_preferences_restores_acceleration(
    db_session, tmp_path, monkeypatch
):
    """自动化方案重建实例后，按持久化偏好自动恢复加速。"""
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text(
        "services:\n  app:\n    image: demo:latest\n", encoding="utf-8"
    )

    app = App(name="demo", display_name="Demo", yaml_template="services: {}")
    db_session.add(app)
    await db_session.flush()
    project = DockerComposeProject(
        project_name="demo-project",
        config_files=json.dumps([str(compose_file)]),
        working_dir=str(tmp_path),
    )
    db_session.add(project)
    await db_session.flush()
    instance = AppInstance(
        app_id=app.id,
        project_id=project.id,
        instance_name="demo",
        status="running",
        acceleration_enabled=False,
    )
    db_session.add(instance)
    config = SystemConfig(
        id=1,
        hosts_optimizer_adguard_dns_ip="172.31.254.2",
        hosts_optimizer_accel_instances=["demo"],
    )
    db_session.add(config)
    await db_session.flush()

    async def _fake_ensure_adguard(db, target_config):
        return {"dns_ip": "172.31.254.2", "warnings": []}

    async def _fake_action(db, target, action, progress_callback=None):
        assert action == "up"
        return {"returncode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(
        hosts_acceleration_service, "ensure_adguard", _fake_ensure_adguard
    )
    monkeypatch.setattr(compose_manager, "action_async", _fake_action)

    await hosts_acceleration_service.reapply_saved_preferences(
        db_session, instance.id
    )

    data = yaml.safe_load(compose_file.read_text(encoding="utf-8"))
    assert data["services"]["app"]["dns"] == [
        "172.31.254.2",
        "223.5.5.5",
        "119.29.29.29",
    ]
    assert instance.acceleration_enabled is True
    assert config.hosts_optimizer_accel_instances == ["demo"]


async def test_wait_deploy_task_forwards_progress():
    """应用市场子任务进度折算到外层部署任务，并去重重复快照。"""
    from app.services.orchestration.deploy_task_service import deploy_task_manager

    inner_task_id = deploy_task_manager.create_task(
        "app_deploy", meta={"app_name": "AdGuard Home"}
    )
    events: list[tuple] = []

    waiter = asyncio.create_task(
        hosts_acceleration_service._wait_deploy_task(
            inner_task_id,
            progress=lambda *args: events.append(args),
        )
    )

    deploy_task_manager.update_progress(
        inner_task_id,
        "pulling_images",
        50,
        "拉取镜像",
        "abc123 Downloading",
    )
    await asyncio.sleep(1.2)
    deploy_task_manager.complete_task(inner_task_id)
    await waiter

    assert (
        "pulling_images",
        45,
        "拉取镜像",
        "abc123 Downloading",
    ) in events
    # 完成后不再推送子任务的 100% 快照，剩余进度留给 AdGuard 初始化
    assert events[-1] == (
        "pulling_images",
        45,
        "拉取镜像",
        "abc123 Downloading",
    )
    # 同一快照不会被重复推送
    assert (
        events.count(
            ("pulling_images", 45, "拉取镜像", "abc123 Downloading")
        )
        == 1
    )


def test_sync_data_password_updates_existing_adguard_config(tmp_path):
    """重新部署前把系统配置里的凭据写回保留的数据目录。"""
    from app.core.security import verify_password

    conf_dir = tmp_path / "onetap-adguard" / "adguardhome" / "conf"
    conf_dir.mkdir(parents=True)
    conf_file = conf_dir / "AdGuardHome.yaml"
    conf_file.write_text(
        "users:\n"
        "  - name: admin\n"
        "    password: old-hash\n"
        "schema_version: 34\n"
        "dns:\n"
        "  rewrites:\n"
        "    - domain: example.com\n"
        "      answer: 1.2.3.4\n",
        encoding="utf-8",
    )

    updated = hosts_acceleration_service._sync_data_password(
        conf_file, "admin", "new-password"
    )

    assert updated is True
    data = yaml.safe_load(conf_file.read_text(encoding="utf-8"))
    assert data["schema_version"] == 34
    assert data["users"][0]["name"] == "admin"
    assert verify_password("new-password", data["users"][0]["password"])
    # 其他数据（DNS 重写等）保持不变
    assert data["dns"]["rewrites"][0]["domain"] == "example.com"


def test_sync_data_password_uses_install_volume_path(tmp_path):
    """安装弹窗自定义 conf 挂载路径时，按该路径同步。"""
    from app.core.security import verify_password

    custom_conf = tmp_path / "custom-conf"
    custom_conf.mkdir()
    conf_file = custom_conf / "AdGuardHome.yaml"
    conf_file.write_text(
        "users:\n  - name: admin\n    password: old-hash\n",
        encoding="utf-8",
    )
    app_config = {
        "volumes": [
            {
                "host_path": str(custom_conf),
                "container_path": "/opt/adguardhome/conf",
            }
        ]
    }

    resolved = hosts_acceleration_service._app_store_conf_file(
        str(tmp_path), None, app_config
    )
    assert resolved == conf_file

    updated = hosts_acceleration_service._sync_data_password(
        resolved, "admin", "another-password"
    )

    assert updated is True
    data = yaml.safe_load(conf_file.read_text(encoding="utf-8"))
    assert verify_password("another-password", data["users"][0]["password"])


def test_sync_data_password_skips_uninitialized_data(tmp_path):
    """还没有 users 的配置交给首次初始化流程，不强行改写。"""
    conf_dir = tmp_path / "onetap-adguard" / "adguardhome" / "conf"
    conf_dir.mkdir(parents=True)
    conf_file = conf_dir / "AdGuardHome.yaml"
    conf_file.write_text("schema_version: 34\n", encoding="utf-8")

    updated = hosts_acceleration_service._sync_data_password(
        conf_file, "admin", "new-password"
    )

    assert updated is False
    assert conf_file.read_text(encoding="utf-8") == "schema_version: 34\n"


@pytest_asyncio.fixture(name="fake_docker_runner")
def _fake_docker_runner(monkeypatch):
    """把 docker_manager.run_sync 替换为直接调用，便于断言容器操作。"""

    async def _run_sync(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(docker_manager, "run_sync", _run_sync)


def _managed_config(**overrides):
    values = {
        "hosts_optimizer_adguard_managed": True,
        "hosts_optimizer_adguard_username": "admin",
        "hosts_optimizer_adguard_password": "new-password",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_apply_managed_credentials_writes_conf_and_restarts(
    tmp_path, monkeypatch, fake_docker_runner
):
    """保存托管实例的账号密码时写入配置文件并重启容器。"""
    conf_file = tmp_path / "AdGuardHome.yaml"
    conf_file.write_text("users: []\n", encoding="utf-8")
    actions: list[tuple[str, str]] = []
    synced: list[tuple] = []

    monkeypatch.setattr(
        docker_manager, "get_container", lambda name: {"status": "running"}
    )
    monkeypatch.setattr(
        docker_manager,
        "container_action",
        lambda name, action: actions.append((name, action)),
    )
    monkeypatch.setattr(
        hosts_acceleration_service,
        "_managed_conf_file_sync",
        lambda: conf_file,
    )

    def _sync(path, username, password):
        synced.append((path, username, password))
        return True

    monkeypatch.setattr(
        hosts_acceleration_service, "_sync_data_password", _sync
    )

    await hosts_acceleration_service.apply_managed_credentials(_managed_config())

    assert synced == [(conf_file, "admin", "new-password")]
    assert actions == [(ADGUARD_CONTAINER, "restart")]


@pytest.mark.asyncio
async def test_apply_managed_credentials_skips_unmanaged(
    monkeypatch, fake_docker_runner
):
    """手动填写的 AdGuard 不受 OneTap 管理，保存时不应重启。"""
    actions: list[tuple[str, str]] = []

    monkeypatch.setattr(
        docker_manager,
        "container_action",
        lambda name, action: actions.append((name, action)),
    )

    await hosts_acceleration_service.apply_managed_credentials(
        _managed_config(
            hosts_optimizer_adguard_managed=False,
            hosts_optimizer_adguard_password="existing",
        )
    )

    assert actions == []


@pytest.mark.asyncio
async def test_apply_managed_credentials_skips_restart_before_init(
    tmp_path, monkeypatch, fake_docker_runner
):
    """配置里还没有用户（尚未初始化）时只需保留系统配置，不重启。"""
    conf_file = tmp_path / "AdGuardHome.yaml"
    conf_file.write_text("schema_version: 34\n", encoding="utf-8")
    actions: list[tuple[str, str]] = []

    monkeypatch.setattr(
        docker_manager, "get_container", lambda name: {"status": "running"}
    )
    monkeypatch.setattr(
        docker_manager,
        "container_action",
        lambda name, action: actions.append((name, action)),
    )
    monkeypatch.setattr(
        hosts_acceleration_service,
        "_managed_conf_file_sync",
        lambda: conf_file,
    )

    await hosts_acceleration_service.apply_managed_credentials(_managed_config())

    assert actions == []


def test_dynamic_pool_range_keeps_low_addresses_free():
    """动态分配池从子网后半段开始，低地址留给 AdGuard 固定 IP。"""
    assert (
        _dynamic_pool_range(ipaddress.ip_network("172.31.254.0/24"))
        == "172.31.254.128/25"
    )
    assert _dynamic_pool_range(ipaddress.ip_network("172.31.254.0/30")) is None


def _accel_network(
    containers: dict[str, str] | None = None,
    *,
    ip_range: str | None = "172.31.254.128/25",
) -> SimpleNamespace:
    containers = containers or {}
    return SimpleNamespace(
        attrs={
            "IPAM": {
                "Config": [
                    {
                        "Subnet": "172.31.254.0/24",
                        "Gateway": "172.31.254.1",
                        "IPRange": ip_range or "",
                    }
                ]
            },
            "Containers": {
                f"id-{name}": {"Name": name, "IPv4Address": f"{ip}/24"}
                for name, ip in containers.items()
            },
        }
    )


def test_pick_network_ip_avoids_dynamic_pool():
    """固定地址优先使用动态池之外的地址，且跳过已占用地址。"""
    assert (
        hosts_acceleration_service._pick_network_ip(_accel_network())
        == "172.31.254.2"
    )
    assert (
        hosts_acceleration_service._pick_network_ip(
            _accel_network({"other": "172.31.254.2"})
        )
        == "172.31.254.3"
    )


def test_pick_network_ip_skips_avoided_addresses():
    """冲突后重新分配时排除刚失败的地址。"""
    assert (
        hosts_acceleration_service._pick_network_ip(
            _accel_network(), avoid_ips={"172.31.254.2"}
        )
        == "172.31.254.3"
    )


def test_pick_network_ip_prefers_upper_half_without_ip_range():
    """旧网络没有 IPRange 时用子网后半段，避开 Docker 的低地址动态分配。"""
    assert (
        hosts_acceleration_service._pick_network_ip(
            _accel_network(ip_range=None)
        )
        == "172.31.254.128"
    )


def test_network_containers_reloads_list_summary():
    """list() 概要对象没有 Containers，读取占用前必须先 reload。"""
    network = _accel_network(ip_range=None)
    network.attrs["Containers"] = None

    def reload():
        network.attrs["Containers"] = {
            "id-other": {"Name": "other", "IPv4Address": "172.31.254.2/24"}
        }

    network.reload = reload

    assert hosts_acceleration_service._network_containers(network) == {
        "id-other": {"Name": "other", "IPv4Address": "172.31.254.2/24"}
    }
    assert (
        hosts_acceleration_service._pick_network_ip(network)
        == "172.31.254.128"
    )


def test_is_ip_conflict_error_detects_docker_message():
    assert _is_ip_conflict_error(
        "Error response from daemon: failed to set up container networking: "
        "Address already in use"
    )
    assert not _is_ip_conflict_error("Bind for 0.0.0.0:9007 failed: port is already allocated")


@pytest.mark.asyncio
async def test_ensure_network_creates_dynamic_pool(monkeypatch):
    """新建加速网络时为动态分配划出地址池。"""
    created: dict = {}

    class FakeNetworks:
        def list(self):
            return []

        def create(self, name, driver=None, ipam=None, labels=None):
            created["ipam"] = ipam

        def get(self, name):
            ipam = {
                "Config": [
                    {
                        **created["ipam"]["Config"][0],
                        "Gateway": "172.31.254.1",
                    }
                ]
            }
            return SimpleNamespace(attrs={"IPAM": ipam, "Containers": {}})

    class FakeClient:
        networks = FakeNetworks()

    monkeypatch.setattr(docker_manager, "_client_obj", FakeClient())

    ip = await hosts_acceleration_service.ensure_network()

    assert ip == "172.31.254.2"
    assert created["ipam"]["Config"][0]["IPRange"] == "172.31.254.128/25"


@pytest.mark.asyncio
async def test_ensure_network_reads_containers_from_detail(monkeypatch):
    """list() 的概要对象不含容器端点，必须换成详情再挑地址。"""
    detail = _accel_network({"other": "172.31.254.2"})
    summary = SimpleNamespace(
        name=ADGUARD_NETWORK,
        attrs={**_accel_network().attrs, "Containers": None},
    )

    class FakeNetworks:
        def list(self):
            return [summary]

        def get(self, name):
            return detail

    class FakeClient:
        networks = FakeNetworks()

    monkeypatch.setattr(docker_manager, "_client_obj", FakeClient())

    # .2 已被其它容器占用，不能因为概要对象看不到就再次选中它。
    assert await hosts_acceleration_service.ensure_network() == "172.31.254.3"


@pytest.mark.asyncio
async def test_recover_conflicting_ip_removes_project_and_reallocates(monkeypatch):
    """地址冲突时先删除旧项目，再挑选新的空闲地址并避开冲突地址。"""
    calls: list = []

    async def fake_remove(db, backup_data=False):
        calls.append("remove")

    async def fake_ensure(preferred_ip=None, avoid_ips=None):
        calls.append(("ensure", preferred_ip, set(avoid_ips or ())))
        return "172.31.254.7"

    monkeypatch.setattr(hosts_acceleration_service, "_remove_project", fake_remove)
    monkeypatch.setattr(hosts_acceleration_service, "ensure_network", fake_ensure)

    ip = await hosts_acceleration_service._recover_conflicting_ip(
        SimpleNamespace(), "172.31.254.2"
    )

    assert ip == "172.31.254.7"
    assert calls == ["remove", ("ensure", None, {"172.31.254.2"})]


def _adguard_app() -> App:
    return App(
        id=7,
        name="adguardhome",
        image="adguard/adguardhome",
        config_schema={"containers": [{"name": "adguardhome"}]},
        yaml_template="services:\n  adguardhome:\n    image: adguard/adguardhome\n",
        display_name="AdGuard Home",
    )


@pytest.mark.asyncio
async def test_deploy_via_app_store_retries_on_ip_conflict(monkeypatch):
    """部署遇到地址冲突时重新分配 DNS 地址并重试一次。"""
    from app.core.exceptions import APIException
    from app.services.app_store import app_service

    app = _adguard_app()
    deployed_ips: list[str] = []
    actions: list = []

    async def fake_deploy(db, **kwargs):
        deployed_ips.append(
            kwargs["compose_overrides"]["services"]["adguardhome"]["networks"][
                ADGUARD_NETWORK
            ]["ipv4_address"]
        )
        return object(), f"task-{len(deployed_ips)}"

    async def fake_wait(task_id, timeout=None, progress=None):
        if task_id == "task-1":
            raise APIException(
                "AdGuard Home 部署失败：failed to set up container networking: "
                "Address already in use",
                502,
            )

    async def fake_remove(db, backup_data=False):
        actions.append("remove")

    async def fake_ensure(preferred_ip=None, avoid_ips=None):
        return preferred_ip or "172.31.254.2"

    async def fake_recover(db, dns_ip, avoid_ips=None):
        actions.append(("recover", dns_ip, set(avoid_ips or ())))
        return "172.31.254.7"

    monkeypatch.setattr(app_service, "deploy", fake_deploy)
    monkeypatch.setattr(hosts_acceleration_service, "_wait_deploy_task", fake_wait)
    monkeypatch.setattr(hosts_acceleration_service, "_remove_project", fake_remove)
    monkeypatch.setattr(hosts_acceleration_service, "ensure_network", fake_ensure)
    monkeypatch.setattr(
        hosts_acceleration_service, "_recover_conflicting_ip", fake_recover
    )
    monkeypatch.setattr(
        hosts_acceleration_service,
        "_migrate_legacy_data",
        lambda host_base: None,
    )

    ip = await hosts_acceleration_service._deploy_via_app_store(
        None, app, "/tmp/host", 9006, 9007, "172.31.254.2"
    )

    assert ip == "172.31.254.7"
    assert deployed_ips == ["172.31.254.2", "172.31.254.7"]
    assert ("recover", "172.31.254.2", {"172.31.254.2"}) in actions


@pytest.mark.asyncio
async def test_deploy_via_app_store_retries_multiple_conflicts(monkeypatch):
    """连续冲突时每次都用新地址，最多尝试 3 次。"""
    from app.core.exceptions import APIException
    from app.services.app_store import app_service

    app = _adguard_app()
    deployed_ips: list[str] = []
    recovered: list[str] = []
    replacements = iter(["172.31.254.7", "172.31.254.8"])

    async def fake_deploy(db, **kwargs):
        deployed_ips.append(
            kwargs["compose_overrides"]["services"]["adguardhome"]["networks"][
                ADGUARD_NETWORK
            ]["ipv4_address"]
        )
        return object(), f"task-{len(deployed_ips)}"

    async def fake_wait(task_id, timeout=None, progress=None):
        if task_id in {"task-1", "task-2"}:
            raise APIException("Address already in use", 502)

    async def fake_ensure(preferred_ip=None, avoid_ips=None):
        return preferred_ip or "172.31.254.2"

    async def fake_recover(db, dns_ip, avoid_ips=None):
        recovered.append(dns_ip)
        return next(replacements)

    async def fake_remove(db, backup_data=False):
        return None

    monkeypatch.setattr(app_service, "deploy", fake_deploy)
    monkeypatch.setattr(hosts_acceleration_service, "_wait_deploy_task", fake_wait)
    monkeypatch.setattr(hosts_acceleration_service, "_remove_project", fake_remove)
    monkeypatch.setattr(hosts_acceleration_service, "ensure_network", fake_ensure)
    monkeypatch.setattr(
        hosts_acceleration_service, "_recover_conflicting_ip", fake_recover
    )
    monkeypatch.setattr(
        hosts_acceleration_service,
        "_migrate_legacy_data",
        lambda host_base: None,
    )

    ip = await hosts_acceleration_service._deploy_via_app_store(
        None, app, "/tmp/host", 9006, 9007, "172.31.254.2"
    )

    assert ip == "172.31.254.8"
    assert deployed_ips == ["172.31.254.2", "172.31.254.7", "172.31.254.8"]
    assert recovered == ["172.31.254.2", "172.31.254.7"]


@pytest.mark.asyncio
async def test_deploy_via_app_store_rechecks_ip_after_cleanup(monkeypatch):
    """旧项目删除后地址已被占用时，启动前就换成新地址。"""
    from app.services.app_store import app_service

    app = _adguard_app()
    deployed_ips: list[str] = []

    async def fake_deploy(db, **kwargs):
        deployed_ips.append(
            kwargs["compose_overrides"]["services"]["adguardhome"]["networks"][
                ADGUARD_NETWORK
            ]["ipv4_address"]
        )
        return object(), "task-1"

    async def fake_wait(task_id, timeout=None, progress=None):
        return None

    async def fake_ensure(preferred_ip=None, avoid_ips=None):
        return "172.31.254.9"

    async def fake_remove(db, backup_data=False):
        return None

    monkeypatch.setattr(app_service, "deploy", fake_deploy)
    monkeypatch.setattr(hosts_acceleration_service, "_wait_deploy_task", fake_wait)
    monkeypatch.setattr(hosts_acceleration_service, "_remove_project", fake_remove)
    monkeypatch.setattr(hosts_acceleration_service, "ensure_network", fake_ensure)
    monkeypatch.setattr(
        hosts_acceleration_service,
        "_migrate_legacy_data",
        lambda host_base: None,
    )

    ip = await hosts_acceleration_service._deploy_via_app_store(
        None, app, "/tmp/host", 9006, 9007, "172.31.254.2"
    )

    assert ip == "172.31.254.9"
    assert deployed_ips == ["172.31.254.9"]


@pytest.mark.asyncio
async def test_deploy_via_app_store_does_not_retry_other_errors(monkeypatch):
    """非地址冲突的部署错误直接抛出，不做重试。"""
    from app.core.exceptions import APIException
    from app.services.app_store import app_service

    app = _adguard_app()
    deployed = []

    async def fake_deploy(db, **kwargs):
        deployed.append(1)
        return object(), "task-1"

    async def fake_wait(task_id, timeout=None, progress=None):
        raise APIException("拉取镜像失败", 502)

    async def fake_remove(db, backup_data=False):
        return None

    async def fake_ensure(preferred_ip=None, avoid_ips=None):
        return preferred_ip or "172.31.254.2"

    monkeypatch.setattr(app_service, "deploy", fake_deploy)
    monkeypatch.setattr(hosts_acceleration_service, "_wait_deploy_task", fake_wait)
    monkeypatch.setattr(hosts_acceleration_service, "_remove_project", fake_remove)
    monkeypatch.setattr(hosts_acceleration_service, "ensure_network", fake_ensure)
    monkeypatch.setattr(
        hosts_acceleration_service,
        "_migrate_legacy_data",
        lambda host_base: None,
    )

    with pytest.raises(APIException, match="拉取镜像失败"):
        await hosts_acceleration_service._deploy_via_app_store(
            None, app, "/tmp/host", 9006, 9007, "172.31.254.2"
        )

    assert deployed == [1]
