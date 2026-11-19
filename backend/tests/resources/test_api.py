"""资源搜索与订阅 API 测试。"""

from app.services.resources import registry


async def _auth_headers(client, username="resource-admin"):
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


async def test_resource_search_uses_quality_sort(client, monkeypatch):
    headers = await _auth_headers(client, "resource-search")
    captured = {}

    async def fake_search(db, **kwargs):
        captured.update(kwargs)
        return {"items": [{"item_id": "qianqian:1", "title": "夜微凉"}]}

    monkeypatch.setattr(registry, "search", fake_search)

    response = await client.get(
        "/api/resources/search",
        headers=headers,
        params={"domain": "music", "keyword": "夜微凉", "count": 10},
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["items"][0]["title"] == "夜微凉"
    assert captured["domain"] == "music"
    assert captured["keyword"] == "夜微凉"
    assert captured["sort"] == "quality"


async def test_resource_subscribe_passes_options(client, monkeypatch):
    headers = await _auth_headers(client, "resource-subscribe")
    captured = {}

    async def fake_subscribe(db, **kwargs):
        captured.update(kwargs)
        return {"subscription_id": "42", "queued": True}

    monkeypatch.setattr(registry, "subscribe", fake_subscribe)

    response = await client.post(
        "/api/resources/subscribe",
        headers=headers,
        json={
            "domain": "novel",
            "item_id": "book-url",
            "options": {"name": "斗破苍穹", "author": "天蚕土豆"},
            "instance_id": 241,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["subscription_id"] == "42"
    assert captured["domain"] == "novel"
    assert captured["item_id"] == "book-url"
    assert captured["options"]["author"] == "天蚕土豆"
    assert captured["instance_id"] == 241
