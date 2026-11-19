"""OpenList 账号自动配置测试：用 MockTransport 模拟初始化与登录接口。"""

import json

import httpx
import pytest

from app.services.apps.openlist import OpenListClient


def _mock_transport(handler, monkeypatch):
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )


def _ok(data=None):
    return httpx.Response(200, json={"code": 200, "message": "success", "data": data})


def _fail(code: int, message: str):
    return httpx.Response(200, json={"code": code, "message": message, "data": None})


@pytest.mark.asyncio
async def test_provision_initializes_admin_when_not_ready(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/public/init_status":
            return _ok({"initialized": False})
        if request.url.path == "/api/public/init/setup":
            assert json.loads(request.content) == {
                "username": "admin",
                "password": "secret-pass",
            }
            return _ok(None)
        assert request.url.path == "/api/auth/login"
        body = json.loads(request.content)
        token = "tok" if body["password"] == "secret-pass" else None
        return _ok({"token": token}) if token else _fail(401, "Invalid username or password")

    _mock_transport(handler, monkeypatch)

    result = await OpenListClient().provision(
        url="http://openlist:5244/",
        username="admin",
        password="secret-pass",
    )

    assert result.ok is True
    assert result.username == "admin"
    assert result.message == "站主账号已创建并验证通过"
    assert ("POST", "/api/public/init/setup") in calls


@pytest.mark.asyncio
async def test_provision_logs_in_existing_account(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/public/init_status":
            return _ok({"initialized": True})
        assert request.url.path == "/api/auth/login"
        return _ok({"token": "tok"})

    _mock_transport(handler, monkeypatch)

    result = await OpenListClient().provision(
        url="http://openlist:5244",
        username="admin",
        password="secret-pass",
    )

    assert result.ok is True
    assert result.message == "账号已存在并验证通过"


@pytest.mark.asyncio
async def test_provision_updates_password_with_previous(monkeypatch):
    valid = {"old-pass"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/public/init_status":
            return _ok({"initialized": True})
        if request.url.path == "/api/auth/login":
            body = json.loads(request.content)
            if body["password"] in valid:
                return _ok({"token": "tok"})
            return _fail(401, "Invalid username or password")
        assert request.url.path == "/api/me/update"
        assert request.headers["Authorization"] == "tok"
        body = json.loads(request.content)
        assert body == {"username": "admin", "password": "secret-pass"}
        valid.clear()
        valid.add(body["password"])
        return _ok(None)

    _mock_transport(handler, monkeypatch)

    result = await OpenListClient().provision(
        url="http://openlist:5244",
        username="admin",
        password="secret-pass",
        previous_password="old-pass",
    )

    assert result.ok is True
    assert result.message == "账号密码已更新并验证通过"


@pytest.mark.asyncio
async def test_provision_reports_password_mismatch(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/public/init_status":
            return _ok({"initialized": True})
        return _fail(401, "Invalid username or password")

    _mock_transport(handler, monkeypatch)

    result = await OpenListClient().provision(
        url="http://openlist:5244",
        username="admin",
        password="secret-pass",
    )

    assert result.ok is False
    assert "密码与统一密码不一致" in (result.message or "")


@pytest.mark.asyncio
async def test_verify_basic_login(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/auth/login"
        body = json.loads(request.content)
        if body == {"username": "admin", "password": "secret-pass"}:
            return _ok({"token": "tok"})
        return _fail(401, "Invalid username or password")

    _mock_transport(handler, monkeypatch)

    client = OpenListClient()
    ok = await client.verify_auth(
        {
            "url": "http://openlist:5244",
            "auth_type": "basic",
            "username": "admin",
            "password": "secret-pass",
        }
    )
    bad = await client.verify_auth(
        {
            "url": "http://openlist:5244",
            "auth_type": "basic",
            "username": "admin",
            "password": "wrong",
        }
    )

    assert ok.valid is True
    assert bad.valid is False


@pytest.mark.asyncio
async def test_verify_rejects_api_key(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("不应发起请求")

    _mock_transport(handler, monkeypatch)

    result = await OpenListClient().verify_auth(
        {"url": "http://openlist:5244", "auth_type": "api_key", "api_key": "k"}
    )

    assert result.valid is False
    assert "API Key" in (result.message or "")
