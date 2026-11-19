"""通知中心 API 测试。"""

from app.services.notifications.assets import make_cover_token


async def _auth_headers(client, username="notify-admin"):
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


async def test_channel_crud_and_secret_mask(client):
    headers = await _auth_headers(client)

    created = await client.post(
        "/api/notifications/channels",
        headers=headers,
        json={
            "name": "家庭飞书",
            "type": "feishu",
            "enabled": True,
            "app_id": "cli_xxx",
            "app_secret": "secret-value",
            "receive_id": "oc_xxx",
            "receive_id_type": "chat_id",
        },
    )
    assert created.status_code == 200, created.text
    data = created.json()["data"]
    assert data["secret_set"] is True
    assert "app_secret" not in data["config"]
    assert data["config"]["app_id"] == "cli_xxx"

    listed = await client.get("/api/notifications/channels", headers=headers)
    assert len(listed.json()["data"]) == 1

    updated = await client.put(
        f"/api/notifications/channels/{data['id']}",
        headers=headers,
        json={
            "name": "家庭飞书",
            "type": "feishu",
            "enabled": False,
            "app_id": "cli_xxx",
            "receive_id": "oc_xxx",
            "receive_id_type": "chat_id",
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["enabled"] is False
    assert updated.json()["data"]["secret_set"] is True

    deleted = await client.delete(
        f"/api/notifications/channels/{data['id']}", headers=headers
    )
    assert deleted.status_code == 200


async def test_channel_requires_secret(client):
    headers = await _auth_headers(client, "notify-admin-2")
    response = await client.post(
        "/api/notifications/channels",
        headers=headers,
        json={
            "name": "没密钥",
            "type": "feishu",
            "app_id": "cli",
            "receive_id": "chat",
        },
    )
    assert response.status_code == 400


async def test_notification_settings_and_inbox(client):
    headers = await _auth_headers(client, "notify-admin-3")

    settings = await client.get("/api/notifications/settings", headers=headers)
    assert settings.status_code == 200
    assert settings.json()["data"]["event_types"] == []

    updated = await client.put(
        "/api/notifications/settings",
        headers=headers,
        json={"event_types": ["subscription.added", "organize.completed"]},
    )
    assert updated.json()["data"]["event_types"] == [
        "subscription.added",
        "organize.completed",
    ]

    inbox = await client.get("/api/notifications", headers=headers)
    assert inbox.status_code == 200
    assert inbox.json()["data"]["items"] == []
    assert inbox.json()["data"]["total"] == 0

    unread = await client.get("/api/notifications/unread-count", headers=headers)
    assert unread.json()["data"]["unread"] == 0

    events = await client.get("/api/notifications/event-types", headers=headers)
    assert any(item["value"] == "subscription.added" for item in events.json()["data"])


async def test_dingtalk_channel_config(client):
    headers = await _auth_headers(client, "notify-admin-ding")
    created = await client.post(
        "/api/notifications/channels",
        headers=headers,
        json={
            "name": "钉钉群",
            "type": "dingtalk",
            "webhook": "https://oapi.dingtalk.com/robot/send?access_token=abc",
            "secret": "SECtest",
        },
    )
    assert created.status_code == 200, created.text
    data = created.json()["data"]
    assert data["config"]["webhook"].endswith("access_token=abc")
    assert data["secret_set"] is True


async def test_dingtalk_app_channel_config(client):
    headers = await _auth_headers(client, "notify-admin-ding-app")
    created = await client.post(
        "/api/notifications/channels",
        headers=headers,
        json={
            "name": "钉钉应用",
            "type": "dingtalk_app",
            "app_key": "dingappkey",
            "app_secret": "appsecret-value",
            "robot_code": "robot1234",
            "receive_id": "manager1234",
            "receive_id_type": "user_id",
            "card_template_id": "tpl-1",
        },
    )
    assert created.status_code == 200, created.text
    data = created.json()["data"]
    assert data["config"] == {
        "app_key": "dingappkey",
        "robot_code": "robot1234",
        "receive_id": "manager1234",
        "receive_id_type": "user_id",
        "card_template_id": "tpl-1",
    }
    assert data["secret_set"] is True
    assert "app_secret" not in data["config"]

    missing = await client.post(
        "/api/notifications/channels",
        headers=headers,
        json={
            "name": "钉钉应用缺机器人",
            "type": "dingtalk_app",
            "app_key": "dingappkey",
            "app_secret": "appsecret-value",
            "receive_id": "manager1234",
        },
    )
    assert missing.status_code == 400


async def test_wechatclawbot_requires_token(client):
    headers = await _auth_headers(client, "notify-admin-claw")
    response = await client.post(
        "/api/notifications/channels",
        headers=headers,
        json={
            "name": "ClawBot",
            "type": "wechatclawbot",
            "account_id": "acc",
            "to_user": "wxid_1",
        },
    )
    assert response.status_code == 400


async def test_channel_test_endpoint(client, monkeypatch):
    import app.api.notifications as notifications_api

    class FakeChannel:
        async def test(self):
            return None

    monkeypatch.setattr(
        notifications_api, "build_channel", lambda record: FakeChannel()
    )
    headers = await _auth_headers(client, "notify-admin-4")
    created = await client.post(
        "/api/notifications/channels",
        headers=headers,
        json={
            "name": "测试渠道",
            "type": "feishu",
            "app_id": "cli",
            "app_secret": "secret",
            "receive_id": "chat",
        },
    )
    channel_id = created.json()["data"]["id"]
    response = await client.post(
        f"/api/notifications/channels/{channel_id}/test", headers=headers
    )
    assert response.json()["data"]["ok"] is True


async def test_cover_proxy(client, monkeypatch):
    import app.api.assets as assets_api

    async def fake_fetch(url):
        return b"\xff\xd8fake-jpeg", "image/jpeg"

    monkeypatch.setattr(assets_api, "fetch_image", fake_fetch)
    token = make_cover_token("https://example.com/a.jpg")
    response = await client.get(f"/api/assets/cover/{token}.jpg")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/jpeg")
    assert response.content.startswith(b"\xff\xd8")

    invalid = await client.get("/api/assets/cover/bad-token.jpg")
    assert invalid.status_code == 404

async def test_subscriptions_report_stage_from_lifecycle_events(notification_db):
    """订阅阶段取已到达的最远生命周期阶段，不被后续更新事件回退。"""
    from datetime import datetime, timedelta, timezone

    from app.api.notifications import list_subscriptions
    from app.models.notification import Notification, ResourceSubscription

    now = datetime.now(timezone.utc)
    async with notification_db() as db:
        subscription = ResourceSubscription(
            domain="video",
            subject_key="video:themoviedb:1260951:S0",
            kind="subscription",
            title="好东西",
            status="active",
        )
        db.add(subscription)
        await db.flush()
        for offset, event_type in (
            (4, "subscription.added"),
            (3, "download.completed"),
            (2, "organize.completed"),
            (1, "subscription.updated"),
        ):
            db.add(
                Notification(
                    event_id=f"event-{offset}",
                    type=event_type,
                    dedupe_key=f"dedupe-{offset}",
                    resource_subscription_id=subscription.id,
                    title=event_type,
                    created_at=now - timedelta(minutes=offset),
                )
            )
        failed = ResourceSubscription(
            domain="music",
            subject_key="music:netease:1",
            kind="request",
            title="飞舞",
            status="failed",
        )
        db.add(failed)
        await db.commit()

        rows = await list_subscriptions(
            domain=None, limit=100, db=db, current_user=None
        )

    stages = {row.subject_key: row for row in rows}
    video = stages["video:themoviedb:1260951:S0"]
    assert video.stage == "organized"
    assert video.last_event_type == "subscription.updated"
    assert video.last_event_at is not None
    assert stages["music:netease:1"].stage == "failed"
