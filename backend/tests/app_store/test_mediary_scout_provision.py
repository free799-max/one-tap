"""巡影 Mediary Scout 账号自动配置测试：用 MockTransport 模拟认证接口。"""

import json

import httpx
import pytest

from app.services.apps.mediary_scout import MediaryScoutClient


def _mock_transport(handler, monkeypatch):
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )


@pytest.mark.asyncio
async def test_provision_registers_owner_when_unclaimed(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/auth/bootstrap":
            return httpx.Response(
                200,
                json={
                    "needsClaim": True,
                    "hasExistingLibrary": False,
                    "singleUser": False,
                    "remote": False,
                },
            )
        assert request.url.path == "/api/auth/register"
        assert json.loads(request.content) == {
            "username": "admin",
            "password": "secret-pass",
        }
        return httpx.Response(200, json={"ok": True})

    _mock_transport(handler, monkeypatch)

    result = await MediaryScoutClient().provision(
        url="http://mediary:3000/",
        username="admin",
        password="secret-pass",
    )

    assert result.ok is True
    assert result.username == "admin"
    assert ("POST", "/api/auth/register") in calls


@pytest.mark.asyncio
async def test_provision_logs_in_existing_account(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/bootstrap":
            return httpx.Response(
                200,
                json={
                    "needsClaim": False,
                    "hasExistingLibrary": True,
                    "singleUser": False,
                },
            )
        assert request.url.path == "/api/auth/login"
        assert json.loads(request.content) == {
            "username": "admin",
            "password": "secret-pass",
        }
        return httpx.Response(200, json={"ok": True})

    _mock_transport(handler, monkeypatch)

    result = await MediaryScoutClient().provision(
        url="http://mediary:3000",
        username="admin",
        password="secret-pass",
    )

    assert result.ok is True
    assert result.message == "账号已配置并验证通过"


@pytest.mark.asyncio
async def test_provision_sets_access_password_in_single_user_mode(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/bootstrap":
            return httpx.Response(
                200,
                json={
                    "needsClaim": False,
                    "singleUser": True,
                    "passwordSet": False,
                },
            )
        assert request.url.path == "/api/auth/password"
        assert json.loads(request.content) == {"password": "secret-pass"}
        return httpx.Response(200, json={"ok": True})

    _mock_transport(handler, monkeypatch)

    result = await MediaryScoutClient().provision(
        url="http://mediary:3000",
        username="admin",
        password="secret-pass",
    )

    assert result.ok is True
    assert result.message == "访问密码已设置并验证通过"


@pytest.mark.asyncio
async def test_verify_basic_login(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/auth/login"
        body = json.loads(request.content)
        if body == {"username": "admin", "password": "secret-pass"}:
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(401, json={"error": "用户名或密码不正确。"})

    _mock_transport(handler, monkeypatch)

    client = MediaryScoutClient()
    ok = await client.verify_auth(
        {
            "url": "http://mediary:3000",
            "auth_type": "basic",
            "username": "admin",
            "password": "secret-pass",
        }
    )
    bad = await client.verify_auth(
        {
            "url": "http://mediary:3000",
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

    result = await MediaryScoutClient().verify_auth(
        {"url": "http://mediary:3000", "auth_type": "api_key", "api_key": "k"}
    )

    assert result.valid is False
    assert "API Key" in (result.message or "")
