"""Hosts 优选配置与手动运行测试。"""

from datetime import datetime, timedelta, timezone

import pytest

from app.core.exceptions import APIException
from app.models.system_config import SystemConfig
from app.schemas.hosts_optimizer import DEFAULT_HOSTS_OPTIMIZER_DOMAINS
from app.services.system import hosts_optimizer_service
from app.services.system.hosts_optimizer_service import (
    mask_run_ips,
    normalize_adguard_url,
    normalize_domains,
)


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


def _payload(**overrides) -> dict:
    data = {
        "enabled": True,
        "mode": "auto",
        "interval_minutes": 60,
        "top_n": 3,
        "domains": ["api.themoviedb.org", "image.tmdb.org"],
        "adguard_url": "http://192.168.1.23:8081/",
        "adguard_username": "admin",
        "adguard_password": "secret",
    }
    data.update(overrides)
    return data


def _fake_run() -> dict:
    return {
        "mode": "manual",
        "started_at": "2026-10-24T06:00:00+00:00",
        "finished_at": "2026-10-24T06:00:18+00:00",
        "duration_ms": 18000,
        "adguard": "ok",
        "errors": [],
        "domains": {
            "api.themoviedb.org": {
                "status": "ok",
                "ips": [{"ip": "18.244.214.89", "latency_ms": 650}],
            }
        },
    }


@pytest.mark.asyncio
async def test_hosts_optimizer_defaults(client):
    token = await _register_and_login(client, "hostsdefault")

    resp = await client.get(
        "/api/settings/hosts-optimizer/config", headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["enabled"] is False
    assert data["mode"] == "manual"
    assert data["domains"] == DEFAULT_HOSTS_OPTIMIZER_DOMAINS
    assert data["interval_minutes"] == 720
    assert data["top_n"] == 2


@pytest.mark.asyncio
async def test_hosts_optimizer_config_normalizes(client):
    token = await _register_and_login(client, "hostsadmin")

    payload = _payload(
        domains=[" API.TMDB.ORG ", "api.tmdb.org", "image.tmdb.org"],
        adguard_url="http://192.168.1.23:8081/",
    )
    resp = await client.put(
        "/api/settings/hosts-optimizer/config",
        json=payload,
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["domains"] == ["api.tmdb.org", "image.tmdb.org"]
    assert data["adguard_url"] == "http://192.168.1.23:8081"
    assert data["mode"] == "auto"
    assert data["top_n"] == 3

    # 重新读取应保持规范化后的值
    resp = await client.get(
        "/api/settings/hosts-optimizer/config", headers=_auth(token)
    )
    assert resp.json()["data"]["domains"] == ["api.tmdb.org", "image.tmdb.org"]


@pytest.mark.asyncio
async def test_hosts_optimizer_rejects_invalid_domain(client):
    token = await _register_and_login(client, "hostsbad")

    resp = await client.put(
        "/api/settings/hosts-optimizer/config",
        json=_payload(domains=["not a domain"]),
        headers=_auth(token),
    )
    assert resp.status_code == 400, resp.text
    assert "域名格式" in resp.json()["message"]


@pytest.mark.asyncio
async def test_hosts_optimizer_rejects_invalid_url(client):
    token = await _register_and_login(client, "hostsbadurl")

    resp = await client.put(
        "/api/settings/hosts-optimizer/config",
        json=_payload(adguard_url="192.168.1.23:8081"),
        headers=_auth(token),
    )
    assert resp.status_code == 400, resp.text
    assert "http://" in resp.json()["message"]


@pytest.mark.asyncio
async def test_hosts_optimizer_run_requires_adguard_config(client):
    token = await _register_and_login(client, "hostsrunempty")

    resp = await client.post("/api/settings/hosts-optimizer/run", headers=_auth(token))
    assert resp.status_code == 400, resp.text
    assert "AdGuard" in resp.json()["message"]


@pytest.mark.asyncio
async def test_hosts_optimizer_manual_run_saves_status(client, monkeypatch):
    token = await _register_and_login(client, "hostsrun")

    captured: dict = {}

    async def _fake_optimize(config, mode="manual"):
        captured.update(config)
        captured["mode"] = mode
        return _fake_run()

    monkeypatch.setattr(hosts_optimizer_service, "optimize", _fake_optimize)

    resp = await client.put(
        "/api/settings/hosts-optimizer/config",
        json=_payload(mode="manual"),
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text

    resp = await client.post(
        "/api/settings/hosts-optimizer/run", headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["adguard"] == "ok"
    masked = data["domains"]["api.themoviedb.org"]
    assert "ips" not in masked
    assert masked["ip_count"] == 1
    assert masked["best_latency_ms"] == 650
    assert captured["adguard_url"] == "http://192.168.1.23:8081"
    assert captured["mode"] == "manual"

    status = await client.get(
        "/api/settings/hosts-optimizer/status", headers=_auth(token)
    )
    assert status.status_code == 200, status.text
    status_data = status.json()["data"]
    assert status_data["running"] is False
    assert status_data["last_run"]["duration_ms"] == 18000
    status_domain = status_data["last_run"]["domains"]["api.themoviedb.org"]
    assert status_domain["status"] == "ok"
    assert "ips" not in status_domain
    assert status_domain["ip_count"] == 1


def test_mask_run_ips_keeps_counts_only():
    masked = mask_run_ips(_fake_run())
    assert masked is not None
    domain = masked["domains"]["api.themoviedb.org"]
    assert "ips" not in domain
    assert domain["ip_count"] == 1
    assert domain["best_latency_ms"] == 650
    assert masked["duration_ms"] == 18000
    assert mask_run_ips(None) is None


def test_normalize_domains_validates_and_deduplicates():
    assert normalize_domains([" API.TMDB.ORG ", "api.tmdb.org", "", "image.tmdb.org"]) == [
        "api.tmdb.org",
        "image.tmdb.org",
    ]
    with pytest.raises(APIException):
        normalize_domains([])
    with pytest.raises(APIException):
        normalize_domains(["bad_domain"])


def test_normalize_adguard_url():
    assert normalize_adguard_url(" http://192.168.1.23:8081/ ") == (
        "http://192.168.1.23:8081"
    )
    assert normalize_adguard_url("") is None
    with pytest.raises(APIException):
        normalize_adguard_url("192.168.1.23:8081")


def test_next_run_at_uses_last_finished_time():
    finished = datetime.now(timezone.utc) - timedelta(minutes=10)
    config = SystemConfig(
        id=1,
        hosts_optimizer_enabled=True,
        hosts_optimizer_mode="auto",
        hosts_optimizer_interval_minutes=60,
        hosts_optimizer_status={"finished_at": finished.isoformat()},
    )

    next_at = hosts_optimizer_service.next_run_at(config)
    assert next_at is not None
    parsed = datetime.fromisoformat(next_at)
    assert abs((parsed - finished) - timedelta(minutes=60)) <= timedelta(seconds=1)


def test_next_run_at_is_none_when_auto_disabled():
    config = SystemConfig(
        id=1,
        hosts_optimizer_enabled=False,
        hosts_optimizer_mode="auto",
        hosts_optimizer_interval_minutes=60,
    )
    assert hosts_optimizer_service.next_run_at(config) is None


def test_runtime_config_respects_proxy_enabled():
    config = SystemConfig(
        id=1,
        proxy_enabled=True,
        http_proxy="http://127.0.0.1:7890",
        https_proxy="http://127.0.0.1:7891",
    )
    assert (
        hosts_optimizer_service._runtime_config(config)["proxy"]
        == "http://127.0.0.1:7891"
    )

    config.proxy_enabled = False
    assert hosts_optimizer_service._runtime_config(config)["proxy"] is None


async def test_sync_adguard_prunes_stale_managed_domains(monkeypatch):
    """管理端删掉的域名不再保留 AdGuard 重写。"""
    import importlib

    module = importlib.import_module("app.services.system.hosts_optimizer_service")

    calls: list[tuple[str, dict | None]] = []
    existing = [
        {"domain": "keep.example.com", "answer": "1.1.1.1"},
        {"domain": "old.example.com", "answer": "2.2.2.2"},
        {"domain": "old.example.com", "answer": "3.3.3.3"},
    ]

    class _Response:
        def __init__(self, payload=None):
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return self._payload

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, path: str):
            assert path == "/control/rewrite/list"
            return _Response(existing)

        async def post(self, path: str, json: dict | None = None):
            calls.append((path, json))
            return _Response()

    monkeypatch.setattr(module.httpx, "AsyncClient", _Client)

    errors = await module.hosts_optimizer_service._sync_adguard(
        "http://adguard",
        "admin",
        "secret",
        ["keep.example.com"],
        {"keep.example.com": [("1.1.1.1", 0.0)]},
        stale_domains=["old.example.com"],
    )

    assert errors == []
    assert calls == [
        (
            "/control/rewrite/delete",
            {"domain": "old.example.com", "answer": "2.2.2.2"},
        ),
        (
            "/control/rewrite/delete",
            {"domain": "old.example.com", "answer": "3.3.3.3"},
        ),
    ]
