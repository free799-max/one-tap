"""网络集成的内网地址识别测试。"""

import pytest

from app.services.integrations import hosts as hosts_module
from app.services.integrations.hosts import host_candidates


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


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


def test_host_candidates_put_current_first_and_dedupe(monkeypatch):
    monkeypatch.setattr(
        hosts_module,
        "_interface_addresses",
        lambda: [
            ("eth1", "10.0.0.8"),
            ("eth0", "192.168.1.23"),
            ("eth0", "192.168.1.23"),
            ("lo", "127.0.0.1"),
        ],
    )

    candidates = host_candidates("192.168.1.23")

    assert [item["value"] for item in candidates] == [
        "192.168.1.23",
        "10.0.0.8",
    ]
    assert candidates[0]["kind"] == "current"
    assert candidates[0]["label"] == "当前访问地址（192.168.1.23 / eth0）"
    assert candidates[1]["label"] == "10.0.0.8（eth1）"


@pytest.mark.asyncio
async def test_network_hosts_api_returns_current_request_host(client, monkeypatch):
    monkeypatch.setattr(hosts_module, "_interface_addresses", lambda: [])
    token = await _register_and_login(client, "hostadmin")

    resp = await client.get(
        "/api/integrations/network/hosts",
        headers={**_auth_headers(token), "host": "192.168.1.50:8080"},
    )

    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["current"] == "192.168.1.50"
    assert data["candidates"] == [
        {
            "value": "192.168.1.50",
            "label": "当前访问地址（192.168.1.50）",
            "kind": "current",
        }
    ]
