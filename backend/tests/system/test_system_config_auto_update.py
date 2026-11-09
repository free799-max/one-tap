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
    assert data["auto_update_window_start"] == 2
    assert data["auto_update_window_end"] == 6
    assert data["auto_backup_enabled"] is True
    assert data["auto_backup_keep"] == 3
    assert data["auto_update_targets"] == []


async def test_auto_update_window_and_backup_persist(client):
    headers = await _auth(client)

    updated = await client.put(
        "/api/settings",
        json={
            "auto_update_enabled": True,
            "auto_update_window_start": 22,
            "auto_update_window_end": 5,
            "auto_backup_enabled": False,
            "auto_backup_keep": 5,
            "auto_update_targets": ["group:1", "app:2", "group:1"],
        },
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    data = updated.json()["data"]
    assert data["auto_update_window_start"] == 22
    assert data["auto_update_window_end"] == 5
    assert data["auto_backup_enabled"] is False
    assert data["auto_backup_keep"] == 5
    assert data["auto_update_targets"] == ["group:1", "app:2"]


async def test_auto_update_targets_reject_invalid_key(client):
    headers = await _auth(client)

    response = await client.put(
        "/api/settings",
        json={"auto_update_targets": ["demo:1"]},
        headers=headers,
    )
    assert response.status_code == 400, response.text


async def test_auto_update_targets_endpoint_returns_candidates(client):
    headers = await _auth(client)

    response = await client.get(
        "/api/settings/auto-update/targets", headers=headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["targets"] == []


async def test_auto_update_window_rejects_same_hours(client):
    headers = await _auth(client)

    response = await client.put(
        "/api/settings",
        json={"auto_update_window_start": 3, "auto_update_window_end": 3},
        headers=headers,
    )
    assert response.status_code == 400, response.text


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
