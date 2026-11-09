"""AdGuard 密码掩码、保留与限时查看接口测试。"""

import pytest


async def _register_and_login(client) -> str:
    resp = await client.post(
        "/api/auth/register",
        json={"username": "adguardrevealadmin", "password": "pass123456"},
    )
    assert resp.status_code == 201, resp.text
    login = await client.post(
        "/api/auth/login",
        json={"username": "adguardrevealadmin", "password": "pass123456"},
    )
    assert login.status_code == 200, login.text
    return login.json()["data"]["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _payload(password: str) -> dict:
    return {
        "enabled": False,
        "mode": "manual",
        "interval_minutes": 360,
        "top_n": 2,
        "domains": ["api.themoviedb.org"],
        "adguard_url": "http://192.168.1.23:8081",
        "adguard_username": "admin",
        "adguard_password": password,
    }


@pytest.mark.asyncio
async def test_adguard_password_is_masked_preserved_and_revealed(client):
    token = await _register_and_login(client)
    headers = _auth(token)

    resp = await client.put(
        "/api/settings/hosts-optimizer/config",
        headers=headers,
        json=_payload("adguard-secret"),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["adguard_password"] == ""
    assert data["adguard_password_set"] is True
    assert "adguard-secret" not in resp.text

    resp = await client.put(
        "/api/settings/hosts-optimizer/config",
        headers=headers,
        json=_payload(""),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["adguard_password_set"] is True

    resp = await client.get(
        "/api/settings/hosts-optimizer/config", headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["adguard_password"] == ""
    assert resp.json()["data"]["adguard_password_set"] is True

    resp = await client.post(
        "/api/settings/hosts-optimizer/secret",
        headers=headers,
        json={"password": "wrong-pass"},
    )
    assert resp.status_code == 403, resp.text

    resp = await client.post(
        "/api/settings/hosts-optimizer/secret",
        headers=headers,
        json={"password": "pass123456"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["password"] == "adguard-secret"
    assert data["reveal_token"]
    assert data["expires_at"]

    resp = await client.post(
        "/api/settings/hosts-optimizer/secret",
        headers=headers,
        json={"reveal_token": data["reveal_token"]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["password"] == "adguard-secret"
