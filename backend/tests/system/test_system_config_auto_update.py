"""自动更新开关配置测试。"""


async def _auth(client) -> dict:
    await client.post(
        "/api/auth/register", json={"username": "autoupdate", "password": "pass123456"}
    )
    login = await client.post(
        "/api/auth/login", json={"username": "autoupdate", "password": "pass123456"}
    )
    token = login.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def test_auto_update_defaults_to_disabled(client):
    headers = await _auth(client)
    data = (await client.get("/api/settings", headers=headers)).json()["data"]
    assert data["auto_update_enabled"] is False


async def test_auto_update_toggle_persists_for_licensed(client):
    headers = await _auth(client)

    enabled = await client.put(
        "/api/settings", json={"auto_update_enabled": True}, headers=headers
    )
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["data"]["auto_update_enabled"] is True

    data = (await client.get("/api/settings", headers=headers)).json()["data"]
    assert data["auto_update_enabled"] is True

    disabled = await client.put(
        "/api/settings", json={"auto_update_enabled": False}, headers=headers
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["data"]["auto_update_enabled"] is False
