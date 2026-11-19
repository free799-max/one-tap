"""Suwayomi simple_login 会话测试：用 MockTransport 模拟登录页与 GraphQL。"""

import json

import httpx
import pytest

from app.services.apps.suwayomi.client import (
    SuwayomiAuthError,
    SuwayomiClient,
)

LOGIN_PAGE = """
<form method="POST">
  <input type="hidden" name="csrf-token" value="csrf-123"/>
  <input type="text" name="user"/>
  <input type="password" name="pass"/>
</form>
"""


def _mock_transport(handler, monkeypatch):
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )


def _gql_unauthorized():
    return httpx.Response(
        200,
        json={
            "errors": [
                {
                    "message": (
                        "Unauthorized\\n\\n"
                        "suwayomi.tachidesk.server.user.UnauthorizedException"
                    )
                }
            ]
        },
    )


@pytest.mark.asyncio
async def test_simple_login_flow_uses_csrf_and_cookie(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/login.html" and request.method == "GET":
            return httpx.Response(
                200,
                text=LOGIN_PAGE,
                headers={"Set-Cookie": "JSESSIONID=page-session; Path=/"},
            )
        if request.url.path == "/login.html" and request.method == "POST":
            assert b"csrf-token=csrf-123" in request.content
            return httpx.Response(
                303,
                headers={
                    "Location": "/",
                    "Set-Cookie": "JSESSIONID=login-session; Path=/",
                },
            )
        assert request.url.path == "/api/graphql"
        if "JSESSIONID=login-session" not in request.headers.get("cookie", ""):
            return _gql_unauthorized()
        body = json.loads(request.content)
        return httpx.Response(200, json={"data": {"ok": True, "query": body["query"]}})

    _mock_transport(handler, monkeypatch)

    async with SuwayomiClient(
        "http://suwayomi:4567", username="admin", password="secret"
    ) as client:
        data = await client.gql("query { settings { authMode } }")

    assert data == {"ok": True, "query": "query { settings { authMode } }"}
    assert calls == [
        ("POST", "/api/graphql"),
        ("GET", "/login.html"),
        ("POST", "/login.html"),
        ("POST", "/api/graphql"),
    ]


@pytest.mark.asyncio
async def test_unauthorized_response_relogs_in(monkeypatch):
    graphql_calls = 0
    logins = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal graphql_calls, logins
        if request.url.path == "/login.html" and request.method == "GET":
            return httpx.Response(
                200,
                text=LOGIN_PAGE,
                headers={"Set-Cookie": "JSESSIONID=page-session; Path=/"},
            )
        if request.url.path == "/login.html" and request.method == "POST":
            logins += 1
            return httpx.Response(
                303,
                headers={
                    "Location": "/",
                    "Set-Cookie": f"JSESSIONID=session-{logins}; Path=/",
                },
            )
        graphql_calls += 1
        if f"JSESSIONID=session-{logins}" not in request.headers.get("cookie", ""):
            return _gql_unauthorized()
        return httpx.Response(200, json={"data": {"ok": True}})

    _mock_transport(handler, monkeypatch)

    async with SuwayomiClient(
        "http://suwayomi:4567", username="admin", password="secret"
    ) as client:
        data = await client.gql("query { settings { authMode } }")

    assert data == {"ok": True}
    assert logins == 1
    assert graphql_calls == 2


@pytest.mark.asyncio
async def test_invalid_login_raises_auth_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/graphql":
            return _gql_unauthorized()
        if request.method == "GET":
            return httpx.Response(200, text=LOGIN_PAGE)
        return httpx.Response(
            200,
            text='<div class="error">Invalid username or password</div>',
        )

    _mock_transport(handler, monkeypatch)

    async with SuwayomiClient(
        "http://suwayomi:4567", username="admin", password="bad"
    ) as client:
        with pytest.raises(SuwayomiAuthError) as exc:
            await client.gql("query { settings { authMode } }")

    assert "Invalid username or password" in str(exc.value)


@pytest.mark.asyncio
async def test_credentials_do_not_force_login_when_auth_disabled(monkeypatch):
    """authMode=NONE 时登录页仍然存在，但不应主动登录。"""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        assert request.url.path == "/api/graphql"
        return httpx.Response(200, json={"data": {"ok": True}})

    _mock_transport(handler, monkeypatch)

    async with SuwayomiClient(
        "http://suwayomi:4567", username="admin", password="secret"
    ) as client:
        data = await client.gql("query { settings { authMode } }")

    assert data == {"ok": True}
    assert calls == ["/api/graphql"]


@pytest.mark.asyncio
async def test_provision_enables_simple_login_for_legacy_instance(monkeypatch):
    """旧实例 authMode=NONE 时，自动配置补开 simple_login 并验证登录。"""
    from app.services.apps.suwayomi.app_client import SuwayomiAppClient

    state = {"mode": "NONE", "logins": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login.html" and request.method == "GET":
            return httpx.Response(200, text=LOGIN_PAGE)
        if request.url.path == "/login.html" and request.method == "POST":
            state["logins"] += 1
            if b"pass=secret" not in request.content:
                return httpx.Response(
                    200,
                    text='<div class="error">Invalid username or password</div>',
                )
            return httpx.Response(
                303,
                headers={
                    "Location": "/",
                    "Set-Cookie": "JSESSIONID=auth-session; Path=/",
                },
            )
        body = json.loads(request.content)
        query = body["query"]
        if "setSettings" in query:
            state["mode"] = "SIMPLE_LOGIN"
            return httpx.Response(
                200,
                json={
                    "data": {
                        "setSettings": {
                            "settings": {
                                "authMode": "SIMPLE_LOGIN",
                                "authUsername": "admin",
                            }
                        }
                    }
                },
            )
        if state["mode"] == "NONE":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "settings": {"authMode": "NONE", "authUsername": ""}
                    }
                },
            )
        if "JSESSIONID=auth-session" not in request.headers.get("cookie", ""):
            return _gql_unauthorized()
        return httpx.Response(
            200,
            json={
                "data": {
                    "settings": {
                        "authMode": state["mode"],
                        "authUsername": "admin",
                    }
                }
            },
        )

    _mock_transport(handler, monkeypatch)

    result = await SuwayomiAppClient().provision(
        "http://suwayomi:4567", "admin", "secret"
    )

    assert result.ok is True
    assert state["mode"] == "SIMPLE_LOGIN"
    assert state["logins"] == 1


@pytest.mark.asyncio
async def test_no_credentials_skips_login(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"data": {"ok": True}})

    _mock_transport(handler, monkeypatch)

    async with SuwayomiClient("http://suwayomi:4567") as client:
        data = await client.gql("query { settings { authMode } }")

    assert data == {"ok": True}
    assert calls == ["/api/graphql"]
