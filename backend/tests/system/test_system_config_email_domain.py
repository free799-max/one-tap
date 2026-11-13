"""统一服务账号邮箱后缀配置测试。"""

import pytest

from app.services.system.config_service import (
    normalize_email_domain,
    resolve_username_for_app,
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
async def test_email_domain_defaults_to_empty(client):
    """未配置时返回空后缀，并给出需要邮箱的应用列表。"""
    token = await _register_and_login(client, "emaildomaindefault")

    resp = await client.get("/api/settings", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["default_email_domain"] == ""
    assert data["email_username_apps"] == ["komga", "ermao-library", "vaultwarden"]


@pytest.mark.asyncio
async def test_email_domain_is_normalized(client):
    """去空白、去 @ 前缀、去尾部点并转小写。"""
    token = await _register_and_login(client, "emaildomainnormalize")

    resp = await client.put(
        "/api/settings",
        json={"default_email_domain": "  @Example.COM.  "},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["default_email_domain"] == "example.com"


@pytest.mark.asyncio
async def test_email_domain_rejects_invalid_format(client):
    """非法域名返回 400。"""
    token = await _register_and_login(client, "emaildomainbad")

    resp = await client.put(
        "/api/settings",
        json={"default_email_domain": "example..com"},
        headers=_auth(token),
    )
    assert resp.status_code == 400, resp.text
    assert "邮箱后缀" in resp.json()["message"]


@pytest.mark.asyncio
async def test_email_domain_not_cleared_by_partial_update(client):
    """未提交邮箱后缀的部分更新不应清空已有值。"""
    token = await _register_and_login(client, "emaildomainpartial")

    await client.put(
        "/api/settings",
        json={"default_email_domain": "example.com"},
        headers=_auth(token),
    )
    resp = await client.put(
        "/api/settings",
        json={"service_port_start": 20000},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["default_email_domain"] == "example.com"


@pytest.mark.asyncio
async def test_partial_update_keeps_unrelated_fields(client):
    """仅提交镜像地址时，用户名、密码、端口和邮箱后缀保持不变。"""
    token = await _register_and_login(client, "emaildomainpartialall")

    await client.put(
        "/api/settings",
        json={
            "default_admin_username": "nasadmin",
            "default_admin_password": "Secret123",
            "service_port_start": 20000,
            "default_email_domain": "example.com",
            "docker_registry_mirrors": ["https://mirror.example.com"],
        },
        headers=_auth(token),
    )
    resp = await client.put(
        "/api/settings",
        json={"docker_registry_mirrors": ["https://mirror2.example.com"]},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["default_admin_username"] == "nasadmin"
    assert data["default_admin_password"] == ""
    assert data["default_admin_password_set"] is True
    assert data["service_port_start"] == 20000
    assert data["default_email_domain"] == "example.com"
    assert data["docker_registry_mirrors"] == ["https://mirror2.example.com"]

    wrong = await client.post(
        "/api/settings/secret",
        json={"password": "wrong-pass"},
        headers=_auth(token),
    )
    assert wrong.status_code == 403, wrong.text

    revealed = await client.post(
        "/api/settings/secret",
        json={"password": "pass123456"},
        headers=_auth(token),
    )
    assert revealed.status_code == 200, revealed.text
    secret = revealed.json()["data"]
    assert secret["password"] == "Secret123"

    cached = await client.post(
        "/api/settings/secret",
        json={"reveal_token": secret["reveal_token"]},
        headers=_auth(token),
    )
    assert cached.status_code == 200, cached.text
    assert cached.json()["data"]["password"] == "Secret123"


def test_resolve_username_only_for_email_apps():
    """只有需要邮箱的应用才追加后缀。"""
    assert (
        resolve_username_for_app("komga", "admin", "example.com")
        == "admin@example.com"
    )
    assert (
        resolve_username_for_app("ermao-library", "admin", "example.com")
        == "admin@example.com"
    )
    assert (
        resolve_username_for_app("vaultwarden", "admin", "example.com")
        == "admin@example.com"
    )
    assert (
        resolve_username_for_app("moviepilot", "admin", "example.com") == "admin"
    )


def test_resolve_username_keeps_existing_email():
    """用户名已是邮箱时不重复追加。"""
    assert (
        resolve_username_for_app("komga", "me@nas.local", "example.com")
        == "me@nas.local"
    )


def test_resolve_username_without_domain():
    """未配置后缀时保持原用户名，沿用应用客户端的默认行为。"""
    assert resolve_username_for_app("komga", "admin", "") == "admin"


def test_resolve_username_for_fixed_account_apps():
    """登录名固定的应用始终使用内置账号。"""
    assert (
        resolve_username_for_app("music-tag-web", "free9527", "example.com")
        == "admin"
    )


def test_normalize_email_domain_accepts_subdomains():
    assert normalize_email_domain("@Mail.Example.COM") == "mail.example.com"
    assert normalize_email_domain("") == ""
