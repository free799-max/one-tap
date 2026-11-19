"""网络加速：订阅地址、订阅同步与规则解析测试。"""

from types import SimpleNamespace

import httpx
import pytest
import yaml

from app.config import settings
from app.core.exceptions import APIException
from app.services.acceleration import apps as accel_apps
from app.services.acceleration import managed, mihomo

SUBSCRIPTION_YAML = """\
mixed-port: 7890
mode: rule
proxies:
- name: us-llgm
  type: vless
  server: 45.205.25.61
  port: 8443
  uuid: 0f167da2-1add-46fa-95fc-d656145a2d84
proxy-groups:
- name: PROXY
  type: select
  proxies:
  - us-llgm
rules:
- DOMAIN-SUFFIX,bangumi.tv,PROXY
- DOMAIN,api.github.com,PROXY
- MATCH,DIRECT
"""


async def _admin_token(client, username: str = "accel_admin") -> str:
    register = await client.post(
        "/api/auth/register", json={"username": username, "password": "pass123456"}
    )
    assert register.status_code == 201, register.text
    login = await client.post(
        "/api/auth/login", json={"username": username, "password": "pass123456"}
    )
    assert login.status_code == 200, login.text
    return login.json()["data"]["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_normalize_url_and_extract_rules():
    assert managed.normalize_url(" https://m.one-tab.vip/api/public/x ") == (
        "https://m.one-tab.vip/api/public/x"
    )
    with pytest.raises(APIException):
        managed.normalize_url("ftp://example.com/sub")
    with pytest.raises(APIException):
        managed.normalize_url("")

    rules = managed.extract_rules(
        {
            "rules": [
                "DOMAIN-SUFFIX,bangumi.tv,PROXY",
                "DOMAIN,api.github.com,PROXY",
                "MATCH,DIRECT",
            ]
        }
    )
    assert rules == [
        {"kind": "DOMAIN-SUFFIX", "value": "bangumi.tv"},
        {"kind": "DOMAIN", "value": "api.github.com"},
    ]


@pytest.mark.asyncio
async def test_managed_config_and_sync(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "DATA_ROOT", str(tmp_path))
    token = await _admin_token(client)

    async def fake_status(db):
        return {"installed": True, "running": True}

    async def fake_moviepilot(db):
        return {"installed": False, "status": "not_installed", "message": "未检测到"}

    monkeypatch.setattr(mihomo, "status_async", fake_status)
    monkeypatch.setattr(mihomo, "moviepilot_status_async", fake_moviepilot)

    initial = await client.get("/api/acceleration/managed", headers=_auth(token))
    assert initial.status_code == 200, initial.text
    assert initial.json()["data"]["configured"] is False
    assert initial.json()["data"]["rules"] == []
    assert initial.json()["data"]["auto_sync_enabled"] is True
    assert initial.json()["data"]["auto_sync_interval_minutes"] == 30
    assert initial.json()["data"]["last_sync_result"] is None

    saved = await client.put(
        "/api/acceleration/managed",
        json={"subscription_url": "https://manage.example.com/sub/abc"},
        headers=_auth(token),
    )
    assert saved.status_code == 200, saved.text

    settings_saved = await client.put(
        "/api/acceleration/managed/sync-settings",
        json={"enabled": False, "interval_minutes": 60},
        headers=_auth(token),
    )
    assert settings_saved.status_code == 200, settings_saved.text

    summary = await client.get("/api/acceleration/managed", headers=_auth(token))
    assert summary.json()["data"]["auto_sync_enabled"] is False
    assert summary.json()["data"]["auto_sync_interval_minutes"] == 60

    invalid = await client.put(
        "/api/acceleration/managed/sync-settings",
        json={"enabled": True, "interval_minutes": 1},
        headers=_auth(token),
    )
    assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_sync_saves_rules_and_applies(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "DATA_ROOT", str(tmp_path))
    token = await _admin_token(client, "accel_admin2")
    applied: dict = {}

    async def fake_fetch(url: str) -> str:
        assert url.endswith("/sub/abc")
        return SUBSCRIPTION_YAML

    async def fake_rule_apps(url: str) -> dict:
        return {("domain-suffix", "bangumi.tv"): "moviepilot"}

    async def fake_apply(db, content, **kwargs):
        applied["content"] = content
        applied["kwargs"] = kwargs
        return {"running": True}

    async def fake_proxy_url(db):
        return "http://192.168.1.10:7890"

    monkeypatch.setattr(managed, "_fetch", fake_fetch)
    monkeypatch.setattr(managed, "_fetch_rule_apps", fake_rule_apps)
    monkeypatch.setattr(mihomo, "apply_subscription_async", fake_apply)
    monkeypatch.setattr(mihomo, "proxy_url", fake_proxy_url)

    await client.put(
        "/api/acceleration/managed",
        json={"subscription_url": "https://manage.example.com/sub/abc"},
        headers=_auth(token),
    )
    result = await client.post(
        "/api/acceleration/managed/sync", headers=_auth(token)
    )
    assert result.status_code == 200, result.text
    assert result.json()["data"]["ok"] is True
    assert applied["kwargs"]["subscription_url"] == "https://manage.example.com/sub/abc"
    assert "bangumi.tv" in applied["content"]

    async def fake_status(db):
        return {"installed": True, "running": True}

    async def fake_moviepilot(db):
        return {"installed": False, "status": "not_installed", "message": "未检测到"}

    monkeypatch.setattr(mihomo, "status_async", fake_status)
    monkeypatch.setattr(mihomo, "moviepilot_status_async", fake_moviepilot)

    summary = await client.get("/api/acceleration/managed", headers=_auth(token))
    data = summary.json()["data"]
    assert data["rules"] == [
        {"kind": "DOMAIN-SUFFIX", "value": "bangumi.tv", "app": "moviepilot"},
        {"kind": "DOMAIN", "value": "api.github.com", "app": "custom"},
    ]
    assert data["rules_synced_at"]
    assert data["last_sync_result"]["status"] == "success"
    assert data["last_sync_result"]["trigger"] == "manual"
    assert data["last_sync_result"]["changed"] is True
    assert data["last_sync_result"]["rules"] == 2
    assert data["last_sync_result"]["synced_at"]


async def _install_sync_stubs(monkeypatch, proxy_url: str):
    async def fake_fetch(url: str) -> str:
        return SUBSCRIPTION_YAML

    async def fake_rule_apps(url: str) -> dict:
        return {}

    async def fake_apply(db, content, **kwargs):
        return {"running": True}

    async def fake_proxy_url(db):
        return proxy_url

    monkeypatch.setattr(managed, "_fetch", fake_fetch)
    monkeypatch.setattr(managed, "_fetch_rule_apps", fake_rule_apps)
    monkeypatch.setattr(mihomo, "apply_subscription_async", fake_apply)
    monkeypatch.setattr(mihomo, "proxy_url", fake_proxy_url)


@pytest.mark.asyncio
async def test_sync_fills_empty_system_proxy(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "DATA_ROOT", str(tmp_path))
    token = await _admin_token(client, "accel_proxy_admin")
    await _install_sync_stubs(monkeypatch, "http://192.168.1.10:7890")

    await client.put(
        "/api/settings",
        json={"proxy_enabled": False, "http_proxy": "", "https_proxy": ""},
        headers=_auth(token),
    )
    await client.put(
        "/api/acceleration/managed",
        json={"subscription_url": "https://manage.example.com/sub/abc"},
        headers=_auth(token),
    )
    result = await client.post("/api/acceleration/managed/sync", headers=_auth(token))
    assert result.status_code == 200, result.text
    assert "已自动配置系统代理" in result.json()["data"]["message"]
    assert result.json()["data"]["detail"]["system_proxy"] == "http://192.168.1.10:7890"

    config = (await client.get("/api/settings", headers=_auth(token))).json()["data"]
    assert config["proxy_enabled"] is True
    assert config["http_proxy"] == "http://192.168.1.10:7890"
    assert config["https_proxy"] == "http://192.168.1.10:7890"


@pytest.mark.asyncio
async def test_sync_keeps_existing_system_proxy(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "DATA_ROOT", str(tmp_path))
    token = await _admin_token(client, "accel_proxy_admin2")
    await _install_sync_stubs(monkeypatch, "http://192.168.1.10:7890")

    await client.put(
        "/api/settings",
        json={
            "proxy_enabled": True,
            "http_proxy": "http://127.0.0.1:1080",
            "https_proxy": "http://127.0.0.1:1080",
        },
        headers=_auth(token),
    )
    await client.put(
        "/api/acceleration/managed",
        json={"subscription_url": "https://manage.example.com/sub/abc"},
        headers=_auth(token),
    )
    result = await client.post("/api/acceleration/managed/sync", headers=_auth(token))
    assert result.status_code == 200, result.text
    assert "已自动配置系统代理" not in result.json()["data"]["message"]

    config = (await client.get("/api/settings", headers=_auth(token))).json()["data"]
    assert config["http_proxy"] == "http://127.0.0.1:1080"
    assert config["https_proxy"] == "http://127.0.0.1:1080"


@pytest.mark.asyncio
async def test_sync_rejects_invalid_payload(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "DATA_ROOT", str(tmp_path))
    token = await _admin_token(client, "accel_admin3")
    errors = {}

    async def fake_fetch(url: str) -> str:
        return "proxies: []\nrules:\n- MATCH,DIRECT\n"

    async def fake_apply(db, content, **kwargs):  # pragma: no cover - 不应被调用
        raise AssertionError("无效订阅不应写入 Mihomo")

    async def fake_record_error(db, message, *, trigger):
        errors.update(message=message, trigger=trigger)

    monkeypatch.setattr(managed, "_fetch", fake_fetch)
    monkeypatch.setattr(mihomo, "apply_subscription_async", fake_apply)
    monkeypatch.setattr(mihomo, "record_error", fake_record_error)

    await client.put(
        "/api/acceleration/managed",
        json={"subscription_url": "https://manage.example.com/sub/empty"},
        headers=_auth(token),
    )
    result = await client.post("/api/acceleration/managed/sync", headers=_auth(token))
    assert result.status_code == 502
    assert "代理节点" in result.json()["message"]
    assert errors["trigger"] == "manual"
    assert "代理节点" in errors["message"]


@pytest.mark.asyncio
async def test_sync_rejected_when_license_invalid(client, monkeypatch, tmp_path):
    """授权无效时手动同步返回 403，并清理已下发的规则。"""
    monkeypatch.setattr(settings, "DATA_ROOT", str(tmp_path))
    token = await _admin_token(client, "accel_nolicense")
    cleared: list[bool] = []

    async def _allowed(db):
        return False

    async def _stop_and_clear(db):
        cleared.append(True)
        return True

    async def _fetch(url):  # pragma: no cover - 不应触发
        raise AssertionError("授权无效时不应拉取订阅")

    monkeypatch.setattr(managed.licensing_service, "acceleration_allowed", _allowed)
    monkeypatch.setattr(managed, "stop_and_clear", _stop_and_clear)
    monkeypatch.setattr(managed, "_fetch", _fetch)

    result = await client.post("/api/acceleration/managed/sync", headers=_auth(token))
    assert result.status_code == 403, result.text
    assert "授权无效" in result.json()["message"]
    assert cleared == [True]


@pytest.mark.asyncio
async def test_stop_and_clear_clears_subscription_and_rules(monkeypatch):
    """清理动作清空订阅地址、规则快照，并重置本机 Mihomo。"""
    config = SimpleNamespace(
        acceleration_subscription_url="https://manage.example.com/sub/abc",
        acceleration_managed_rules=[{"kind": "DOMAIN", "value": "api.github.com"}],
        acceleration_rules_synced_at="2026-01-01T00:00:00Z",
    )
    calls: list[bool] = []
    flushed: list[bool] = []

    async def _get_config(db):
        return config

    async def _clear_subscription(db):
        calls.append(True)

    db = SimpleNamespace()

    async def _flush():
        flushed.append(True)

    db.flush = _flush

    monkeypatch.setattr(managed.system_config_service, "get_or_create", _get_config)
    monkeypatch.setattr(managed.mihomo, "clear_subscription", _clear_subscription)

    assert await managed.stop_and_clear(db) is True
    assert calls == [True]
    assert flushed == [True]
    assert config.acceleration_subscription_url == ""
    assert config.acceleration_managed_rules == []
    assert config.acceleration_rules_synced_at is None


@pytest.mark.asyncio
async def test_stop_and_clear_skips_without_applied_subscription(monkeypatch):
    """没有已应用的订阅时不触碰 Mihomo。"""
    config = SimpleNamespace(
        acceleration_subscription_url="",
        acceleration_managed_rules=[],
        acceleration_rules_synced_at=None,
    )

    async def _get_config(db):
        return config

    async def _clear_subscription(db):  # pragma: no cover - 不应触发
        raise AssertionError("没有订阅时不应重置 Mihomo")

    monkeypatch.setattr(managed.system_config_service, "get_or_create", _get_config)
    monkeypatch.setattr(managed.mihomo, "clear_subscription", _clear_subscription)

    assert await managed.stop_and_clear(object()) is False


@pytest.mark.asyncio
async def test_clear_subscription_resets_mihomo_to_direct(monkeypatch):
    """清理时把运行中的 Mihomo 配置替换为直连最小配置。"""
    applied: dict = {}

    async def _status(db):
        return {"installed": True}

    async def _apply(db, content, **kwargs):
        applied["content"] = content
        applied["kwargs"] = kwargs

    async def _record(db, **kwargs):
        applied["record"] = kwargs

    monkeypatch.setattr(mihomo, "status_async", _status)
    monkeypatch.setattr(mihomo, "apply_subscription_async", _apply)
    monkeypatch.setattr(mihomo, "record_sync_result", _record)

    assert await mihomo.clear_subscription(object()) is True
    config = yaml.safe_load(applied["content"])
    assert config["rules"] == ["MATCH,DIRECT"]
    assert config["proxies"] == []
    assert applied["kwargs"]["subscription_url"] == ""
    assert applied["kwargs"]["subscription_name"] == ""
    assert applied["record"]["status"] == "cleared"
    assert applied["record"]["trigger"] == "license"


@pytest.mark.asyncio
async def test_remove_mihomo_uses_compose_deployment(client, monkeypatch):
    token = await _admin_token(client, "accel_admin4")

    async def fake_delete(db):
        return {
            "removed": True,
            "disabled_apps": ["moviepilot"],
            "message": "Mihomo 已删除，数据已备份，已关闭 1 个应用的加速",
        }

    monkeypatch.setattr(
        "app.services.acceleration.deployment.delete_managed", fake_delete
    )
    response = await client.delete("/api/acceleration/mihomo", headers=_auth(token))

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["ok"] is True
    assert data["detail"]["disabled_apps"] == ["moviepilot"]
    assert "数据已备份" in data["message"]


@pytest.mark.asyncio
async def test_deploy_mihomo_returns_unified_task(client, monkeypatch):
    token = await _admin_token(client, "accel_admin5")

    async def fake_deploy(db, app_config=None):
        assert app_config == {"ports": []}
        return "task-123"

    monkeypatch.setattr(
        "app.services.acceleration.deployment.start_deploy", fake_deploy
    )
    response = await client.post(
        "/api/acceleration/mihomo/deploy",
        json={"config": {"ports": []}},
        headers=_auth(token),
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"] == {
        "task_id": "task-123",
        "instance_name": "onetap-mihomo",
    }


def test_controller_candidates_prefer_local_and_include_host(monkeypatch):
    monkeypatch.setattr(mihomo, "default_gateway", lambda: "172.18.0.1")
    monkeypatch.setattr(mihomo, "host_lan_ip", lambda: "192.168.1.17")
    monkeypatch.setattr(mihomo, "_find_container", lambda client: None)

    bases = mihomo._controller_candidates(9006)
    assert bases[0] == "http://127.0.0.1:9006"
    assert "http://172.18.0.1:9006" in bases
    assert "http://192.168.1.17:9006" in bases


def test_controller_probe_falls_back_to_host_gateway(monkeypatch):
    monkeypatch.setattr(
        mihomo,
        "_controller_candidates",
        lambda port: [
            f"http://127.0.0.1:{port}",
            f"http://172.18.0.1:{port}",
        ],
    )

    def fake_get(url, timeout=None):
        if url.startswith("http://172.18.0.1"):
            return httpx.Response(200, json={"version": "v1.19.34"})
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(mihomo.httpx, "get", fake_get)

    base, version = mihomo._probe_controller(8006)
    assert base == "http://172.18.0.1:8006"
    assert version == "v1.19.34"


def test_proxy_host_uses_gateway_inside_container(monkeypatch):
    monkeypatch.setattr(mihomo, "inside_container", lambda: True)
    monkeypatch.setattr(mihomo, "default_gateway", lambda: "172.18.0.1")
    monkeypatch.setattr(mihomo, "host_lan_ip", lambda: "172.18.0.5")
    monkeypatch.setattr(
        mihomo, "_tcp_open", lambda host, port, timeout=0.5: host == "172.18.0.1"
    )

    assert mihomo.proxy_host(8009) == "172.18.0.1"


def test_proxy_host_uses_lan_ip_on_host(monkeypatch):
    monkeypatch.setattr(mihomo, "inside_container", lambda: False)
    monkeypatch.setattr(mihomo, "host_lan_ip", lambda: "192.168.1.17")
    monkeypatch.setattr(mihomo, "_tcp_open", lambda host, port, timeout=0.5: True)

    assert mihomo.proxy_host(9002) == "192.168.1.17"
