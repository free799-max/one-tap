"""微信 ClawBot 扫码登录测试。"""

import app.services.notifications.channels.wechatclawbot as claw


async def _auth_headers(client, username="notify-admin-claw-login"):
    await client.post(
        "/api/auth/register",
        json={"username": username, "password": "pass123456"},
    )
    response = await client.post(
        "/api/auth/login",
        json={"username": username, "password": "pass123456"},
    )
    token = response.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _patch_qrcode(monkeypatch, statuses):
    queue = list(statuses)

    async def fake_request(base_url=claw.DEFAULT_BASE_URL):
        return {
            "qrcode": "qrcode-1",
            "content": "https://weixin.qq.com/x/abc",
            "base_url": claw.DEFAULT_BASE_URL,
        }

    async def fake_poll(qrcode, base_url=claw.DEFAULT_BASE_URL):
        status = queue.pop(0) if queue else "wait"
        if status == "confirmed":
            return {
                "status": "confirmed",
                "bot_token": "ilinkbot_test",
                "account_id": "bot@im.bot",
                "user_id": "user@im.wechat",
                "base_url": claw.DEFAULT_BASE_URL,
            }
        return {
            "status": status,
            "bot_token": "",
            "account_id": "",
            "user_id": "",
            "base_url": base_url,
        }

    async def fake_notify(token, base_url=claw.DEFAULT_BASE_URL):
        return None

    monkeypatch.setattr(claw, "request_login_qrcode", fake_request)
    monkeypatch.setattr(claw, "poll_login_qrcode", fake_poll)
    monkeypatch.setattr(claw, "notify_start", fake_notify)


async def test_scan_login_saves_channel(client, monkeypatch):
    _patch_qrcode(monkeypatch, ["wait", "confirmed"])

    async def fake_updates(bot_token, base_url=claw.DEFAULT_BASE_URL, buffer="", timeout=8.0):
        return {
            "ret": 0,
            "messages": [
                {"from_user_id": "user@im.wechat", "context_token": "ctx-1"}
            ],
            "buffer": "buf-1",
        }

    monkeypatch.setattr(claw, "fetch_updates", fake_updates)

    headers = await _auth_headers(client)
    start = await client.post(
        "/api/notifications/channels/wechatclawbot/login",
        headers=headers,
        json={"name": "微信通知"},
    )
    assert start.status_code == 200, start.text
    payload = start.json()["data"]
    assert payload["qrcode_image"].startswith("data:image/png;base64,")
    session_id = payload["session_id"]

    first = await client.get(
        f"/api/notifications/channels/wechatclawbot/login/{session_id}",
        headers=headers,
    )
    assert first.json()["data"]["status"] == "wait"

    second = await client.get(
        f"/api/notifications/channels/wechatclawbot/login/{session_id}",
        headers=headers,
    )
    data = second.json()["data"]
    assert data["status"] == "done"
    channel = data["channel"]
    assert channel["type"] == "wechatclawbot"
    assert channel["name"] == "微信通知"
    assert channel["secret_set"] is True
    assert channel["config"]["account_id"] == "bot@im.bot"
    assert channel["config"]["to_user"] == "user@im.wechat"
    assert channel["config"]["context_token"] == "ctx-1"
    assert "bot_token" not in channel["config"]

    listed = await client.get("/api/notifications/channels", headers=headers)
    assert [item["name"] for item in listed.json()["data"]] == ["微信通知"]


async def test_scan_login_waits_for_message_and_skip(client, monkeypatch):
    _patch_qrcode(monkeypatch, ["confirmed"])

    async def empty_updates(bot_token, base_url=claw.DEFAULT_BASE_URL, buffer="", timeout=8.0):
        return {"ret": 0, "messages": [], "buffer": ""}

    monkeypatch.setattr(claw, "fetch_updates", empty_updates)

    headers = await _auth_headers(client, "notify-admin-claw-skip")
    start = await client.post(
        "/api/notifications/channels/wechatclawbot/login",
        headers=headers,
        json={},
    )
    session_id = start.json()["data"]["session_id"]

    waiting = await client.get(
        f"/api/notifications/channels/wechatclawbot/login/{session_id}",
        headers=headers,
    )
    assert waiting.json()["data"]["status"] == "need_message"

    skipped = await client.get(
        f"/api/notifications/channels/wechatclawbot/login/{session_id}",
        headers=headers,
        params={"skip_message": True},
    )
    data = skipped.json()["data"]
    assert data["status"] == "done"
    assert data["channel"]["name"] == "微信 ClawBot"
    assert data["channel"]["config"]["to_user"] == "user@im.wechat"
    assert data["channel"]["config"]["context_token"] == ""


async def test_scan_login_expired(client, monkeypatch):
    _patch_qrcode(monkeypatch, ["expired"])

    headers = await _auth_headers(client, "notify-admin-claw-expired")
    start = await client.post(
        "/api/notifications/channels/wechatclawbot/login",
        headers=headers,
        json={},
    )
    session_id = start.json()["data"]["session_id"]
    status = await client.get(
        f"/api/notifications/channels/wechatclawbot/login/{session_id}",
        headers=headers,
    )
    assert status.json()["data"]["status"] == "expired"

    missing = await client.get(
        f"/api/notifications/channels/wechatclawbot/login/{session_id}",
        headers=headers,
    )
    assert missing.status_code == 404
