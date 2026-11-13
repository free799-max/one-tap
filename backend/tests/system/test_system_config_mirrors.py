"""系统配置中的 Docker 镜像加速地址列表测试。"""

import asyncio
import importlib
import time
from types import SimpleNamespace

import httpx
import pytest

from app.config import settings

# 包 __init__ 导出了同名单例，需显式取回模块对象
registry_mirror_module = importlib.import_module(
    "app.services.system.registry_mirror_service"
)


@pytest.fixture(autouse=True)
def _reset_mirror_caches():
    """每个用例前后都从干净的缓存和下载计数开始。"""
    module = registry_mirror_module
    module._active_pulls = 0
    module._deferred_invalidation = False
    module.invalidate_acceleration_cache()
    yield
    module._active_pulls = 0
    module._deferred_invalidation = False
    module.invalidate_acceleration_cache()


class _FakeResponse:
    """模拟 httpx 响应。"""

    def __init__(self, payload=None, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "http://manage.test/api/public/mirrors")
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=request,
                response=httpx.Response(self.status_code, request=request),
            )

    def json(self):
        return self._payload


class _FakeClient:
    """按 handler 返回响应的 httpx.AsyncClient 替身。"""

    def __init__(self, *args, handler=None, **kwargs):
        self._handler = handler

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, **kwargs):
        return self._handler(url)


def _patch_http(monkeypatch, handler):
    """替换镜像源服务模块内的 httpx。"""
    monkeypatch.setattr(
        registry_mirror_module,
        "httpx",
        SimpleNamespace(
            AsyncClient=lambda *a, **kw: _FakeClient(*a, handler=handler, **kw),
            HTTPError=httpx.HTTPError,
        ),
    )


async def _register_and_login(client, username: str, password: str = "pass123456") -> str:
    """注册用户并登录，返回 access_token。"""
    resp = await client.post("/api/auth/register", json={
        "username": username, "password": password,
    })
    assert resp.status_code == 201, resp.text
    login_resp = await client.post("/api/auth/login", json={
        "username": username, "password": password,
    })
    assert login_resp.status_code == 200, login_resp.text
    return login_resp.json()["data"]["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_mirrors_default_to_empty_list(client):
    """未配置时返回空列表。"""
    token = await _register_and_login(client, "mirroruser")

    resp = await client.get("/api/settings", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["docker_registry_mirrors"] == []


@pytest.mark.asyncio
async def test_mirrors_are_normalized_and_deduplicated(client):
    """去空白、去尾部斜杠、去重，并保持提交顺序。"""
    token = await _register_and_login(client, "mirroradmin")

    resp = await client.put(
        "/api/settings",
        json={
            "docker_registry_mirrors": [
                " https://docker.m.daocloud.io/ ",
                "https://docker.m.daocloud.io",
                "",
                "http://mirror.example.com",
            ]
        },
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["docker_registry_mirrors"] == [
        "https://docker.m.daocloud.io",
        "http://mirror.example.com",
    ]


@pytest.mark.asyncio
async def test_mirrors_reject_invalid_scheme(client):
    """非 http(s) 地址返回 400。"""
    token = await _register_and_login(client, "mirrorbad")

    resp = await client.put(
        "/api/settings",
        json={"docker_registry_mirrors": ["docker.m.daocloud.io"]},
        headers=_auth(token),
    )
    assert resp.status_code == 400, resp.text
    assert "http://" in resp.json()["message"]


@pytest.mark.asyncio
async def test_mirrors_not_cleared_by_partial_update(client):
    """未提交 mirrors 字段的部分更新不应清空已有列表。"""
    token = await _register_and_login(client, "mirrorpartial")

    await client.put(
        "/api/settings",
        json={"docker_registry_mirrors": ["https://mirror.example.com"]},
        headers=_auth(token),
    )
    resp = await client.put(
        "/api/settings",
        json={"service_port_start": 20000},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["docker_registry_mirrors"] == [
        "https://mirror.example.com"
    ]


@pytest.mark.asyncio
async def test_remote_mirrors_requires_manage_url(client, monkeypatch):
    """未配置管理端地址时返回 configured=False。"""
    monkeypatch.setattr(settings, "MANAGE_BASE_URL", "")
    token = await _register_and_login(client, "mirrorremoteoff")

    resp = await client.get(
        "/api/settings/docker-mirrors/remote", headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == {
        "configured": False,
        "licensed": False,
        "mirrors": [],
    }


@pytest.mark.asyncio
async def test_mirror_effectiveness_requires_reachable_leased_source(
    client, monkeypatch
):
    """只有实际连通的授权专属镜像源才算生效。"""
    service = registry_mirror_module.registry_mirror_service

    async def _fake_leased(db):
        return {
            "licensed": True,
            "mirrors": [{"url": "https://leased.example.com"}],
        }

    async def _fake_test(urls):
        assert urls == ["https://leased.example.com"]
        return {"ok_count": 1, "total": 1, "results": []}

    monkeypatch.setattr(service, "fetch_leased", _fake_leased)
    monkeypatch.setattr(service, "test", _fake_test)
    token = await _register_and_login(client, "mirroreffective")

    resp = await client.get(
        "/api/settings/docker-mirrors/effectiveness", headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == {
        "effective": True,
        "available_count": 1,
        "total_count": 1,
        "message": "1/1 个专属镜像源连通，镜像拉取已接入专属加速",
    }


@pytest.mark.asyncio
async def test_remote_mirrors_filters_docker_registry(client, monkeypatch):
    """只保留启用中的 Docker Registry 镜像源，并按优先级排序。"""
    monkeypatch.setattr(settings, "MANAGE_BASE_URL", "http://manage.test/")
    payload = [
        {
            "id": 2,
            "name": "Backup",
            "kind": "docker_registry",
            "url": "https://backup.example.com",
            "priority": 20,
            "enabled": True,
            "region": "CN",
            "last_check_ok": False,
        },
        {
            "id": 1,
            "name": "DaoCloud",
            "kind": "docker_registry",
            "url": "https://docker.m.daocloud.io",
            "priority": 10,
            "enabled": True,
            "region": "CN",
            "last_check_ok": True,
            "last_latency_ms": 88,
            "last_checked_at": "2026-10-25T08:00:00Z",
        },
        {
            "id": 3,
            "name": "Off",
            "kind": "docker_registry",
            "url": "https://off.example.com",
            "priority": 1,
            "enabled": False,
        },
        {
            "id": 4,
            "name": "NPM",
            "kind": "npm",
            "url": "https://registry.npmmirror.com",
            "priority": 1,
            "enabled": True,
        },
        {"id": 5, "name": "NoKind", "url": "https://nokind.example.com"},
    ]
    _patch_http(monkeypatch, lambda url: _FakeResponse(payload=payload))
    token = await _register_and_login(client, "mirrorremoteon")

    resp = await client.get(
        "/api/settings/docker-mirrors/remote", headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["configured"] is True
    assert [m["url"] for m in data["mirrors"]] == [
        "https://docker.m.daocloud.io",
        "https://backup.example.com",
    ]
    assert data["mirrors"][0]["last_check_ok"] is True
    assert data["mirrors"][0]["last_latency_ms"] == 88


@pytest.mark.asyncio
async def test_remote_mirrors_network_error_returns_502(client, monkeypatch):
    """管理端不可达时返回 502。"""
    monkeypatch.setattr(settings, "MANAGE_BASE_URL", "http://manage.test")

    def _raise(url):
        raise httpx.ConnectError("connection refused")

    _patch_http(monkeypatch, _raise)
    token = await _register_and_login(client, "mirrorremotefail")

    resp = await client.get(
        "/api/settings/docker-mirrors/remote", headers=_auth(token)
    )
    assert resp.status_code == 502, resp.text
    assert "管理端" in resp.json()["message"]


@pytest.mark.asyncio
async def test_test_docker_mirrors_reports_each_result(client, monkeypatch):
    """401 视为可用，连接异常记为失败，并返回规范化后的地址。"""

    def _handler(url):
        if "daocloud" in url:
            return _FakeResponse(status_code=401)
        raise httpx.ConnectError("connection refused")

    _patch_http(monkeypatch, _handler)
    token = await _register_and_login(client, "mirrortest")

    resp = await client.post(
        "/api/settings/docker-mirrors/test",
        json={
            "urls": [
                " https://docker.m.daocloud.io/ ",
                "https://bad.example.com",
            ]
        },
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["total"] == 2
    assert data["ok_count"] == 1

    first = data["results"][0]
    assert first["url"] == "https://docker.m.daocloud.io"
    assert first["ok"] is True
    assert first["status_code"] == 401
    assert first["error"] is None
    assert isinstance(first["latency_ms"], int)

    assert data["results"][1]["url"] == "https://bad.example.com"
    assert data["results"][1]["ok"] is False
    assert "connection refused" in data["results"][1]["error"]


@pytest.mark.asyncio
async def test_test_docker_mirrors_rejects_invalid_url(client):
    """检测接口同样校验地址协议。"""
    token = await _register_and_login(client, "mirrortestbad")

    resp = await client.post(
        "/api/settings/docker-mirrors/test",
        json={"urls": ["docker.m.daocloud.io"]},
        headers=_auth(token),
    )
    assert resp.status_code == 400, resp.text
    assert "http://" in resp.json()["message"]


@pytest.mark.asyncio
async def test_acceleration_map_cache_invalidated_after_license_change(monkeypatch):
    """映射表命中缓存，失效后重新获取，避免授权变化后继续用旧域名。"""
    service = registry_mirror_module.registry_mirror_service
    registry_mirror_module.invalidate_acceleration_cache()
    calls: list[object] = []

    async def _fake_map(db):
        calls.append(db)
        return {"ghcr.io": [f"https://leased-{len(calls)}.example.com"]}

    monkeypatch.setattr(service, "acceleration_map", _fake_map)

    first = await service.acceleration_map_cached(object())
    again = await service.acceleration_map_cached(object())
    assert first == again == {"ghcr.io": ["https://leased-1.example.com"]}
    assert len(calls) == 1

    registry_mirror_module.invalidate_acceleration_cache()
    refreshed = await service.acceleration_map_cached(object())
    assert refreshed == {"ghcr.io": ["https://leased-2.example.com"]}
    assert len(calls) == 2
    registry_mirror_module.invalidate_acceleration_cache()


@pytest.mark.asyncio
async def test_leased_domains_refetched_after_invalidation(monkeypatch):
    """专属域名缓存失效后，下一次取用重新请求管理端。"""
    module = registry_mirror_module
    service = module.registry_mirror_service
    module.invalidate_acceleration_cache()
    monkeypatch.setattr(settings, "MANAGE_BASE_URL", "http://manage.test")
    state = SimpleNamespace(refresh_token="token-1", installation_id="install-1")

    async def _state(db):
        return state

    async def _fingerprint(installation_id):
        return {"hardware_fingerprint": "fp"}

    async def _fallback(fetch):
        return await fetch("http://manage.test"), "http://manage.test"

    requests: list[str] = []
    domains = ["https://a.example.com", "https://b.example.com"]

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, **kwargs):
            requests.append(url)
            return SimpleNamespace(
                status_code=200,
                is_error=False,
                json=lambda: {
                    "data": {
                        "mirrors": [
                            {
                                "url": domains[len(requests) - 1],
                                "source": "ghcr.io",
                            }
                        ]
                    }
                },
            )

    monkeypatch.setattr(module.licensing_service, "get_state", _state)
    monkeypatch.setattr(module, "get_device_fingerprint", _fingerprint)
    monkeypatch.setattr(module, "with_manage_fallback", _fallback)
    monkeypatch.setattr(
        module,
        "httpx",
        SimpleNamespace(
            AsyncClient=lambda *a, **kw: _Client(), HTTPError=httpx.HTTPError
        ),
    )

    first = await service.fetch_leased(object())
    cached = await service.fetch_leased(object())
    assert first == cached
    assert [m["url"] for m in cached["mirrors"]] == ["https://a.example.com"]
    assert len(requests) == 1

    module.invalidate_acceleration_cache()
    refreshed = await service.fetch_leased(object())
    assert [m["url"] for m in refreshed["mirrors"]] == ["https://b.example.com"]
    assert len(requests) == 2
    module.invalidate_acceleration_cache()


def test_cache_invalidation_deferred_while_pull_active():
    """下载进行中不清空域名，等最后一个下载结束再切换。"""
    module = registry_mirror_module
    module._leased_cache = (
        time.monotonic(),
        "token",
        {"licensed": True, "mirrors": []},
    )
    module._acceleration_map_cache = (
        time.monotonic(),
        {"docker.io": ["https://m.example.com"]},
    )

    module.begin_acceleration_pull()
    try:
        module.invalidate_acceleration_cache()
        assert module._leased_cache is not None
        assert module._acceleration_map_cache is not None
        assert module._deferred_invalidation is True
    finally:
        module.end_acceleration_pull()

    assert module._leased_cache is None
    assert module._acceleration_map_cache is None
    assert module._deferred_invalidation is False


def test_cache_invalidation_waits_for_nested_pulls():
    """多个下载重叠时，最后一个结束才清空域名。"""
    module = registry_mirror_module
    module._leased_cache = (
        time.monotonic(),
        "token",
        {"licensed": True, "mirrors": []},
    )

    module.begin_acceleration_pull()
    module.begin_acceleration_pull()
    module.invalidate_acceleration_cache()
    module.end_acceleration_pull()
    assert module._leased_cache is not None
    assert module._deferred_invalidation is True

    module.end_acceleration_pull()
    assert module._leased_cache is None
    assert module._deferred_invalidation is False


@pytest.mark.asyncio
async def test_pull_keeps_domains_until_download_finishes(monkeypatch):
    """下载过程中收到授权变化，域名保持不变，下载完成后才失效。"""
    module = registry_mirror_module
    mirror_pull = importlib.import_module("app.services.docker.mirror_pull")

    entered = asyncio.Event()
    release = asyncio.Event()

    async def _fake_docker(*args, timeout=0):
        if args[:2] == ("image", "inspect"):
            return 1, ""
        if args[0] == "pull":
            entered.set()
            await release.wait()
        return 0, ""

    monkeypatch.setattr(mirror_pull, "_docker", _fake_docker)
    module._leased_cache = (
        time.monotonic(),
        "token",
        {"licensed": True, "mirrors": []},
    )
    module._acceleration_map_cache = (
        time.monotonic(),
        {"docker.io": ["https://m.example.com"]},
    )

    task = asyncio.create_task(
        mirror_pull.pull_with_mirror(
            "app:latest", {"docker.io": ["https://m.example.com"]}
        )
    )
    await entered.wait()
    module.invalidate_acceleration_cache()
    assert module._leased_cache is not None
    assert module._acceleration_map_cache is not None

    release.set()
    assert await task is True
    assert module._leased_cache is None
    assert module._acceleration_map_cache is None
    assert module._active_pulls == 0
