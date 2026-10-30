"""服务概览 API 集成测试。"""

import pytest


async def _auth_headers(client) -> dict[str, str]:
    await client.post(
        "/api/auth/register",
        json={"username": "overview", "password": "overview123"},
    )
    login_resp = await client.post(
        "/api/auth/login",
        json={"username": "overview", "password": "overview123"},
    )
    token = login_resp.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_overview_requires_auth(client):
    resp = await client.get("/api/dashboard/overview")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_overview_returns_empty_state(client):
    headers = await _auth_headers(client)

    resp = await client.get("/api/dashboard/overview", headers=headers)

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total"] == 0
    assert data["running"] == 0
    assert data["categories"] == []


@pytest.mark.asyncio
async def test_app_detail_returns_404_for_missing_instance(client):
    headers = await _auth_headers(client)

    resp = await client.get("/api/dashboard/apps/9999", headers=headers)

    assert resp.status_code == 404
    assert resp.json()["message"] == "应用实例不存在"


@pytest.mark.asyncio
async def test_app_metrics_requires_auth(client):
    resp = await client.get("/api/dashboard/apps/1/metrics")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_app_metrics_returns_404_for_missing_instance(client):
    headers = await _auth_headers(client)

    resp = await client.get("/api/dashboard/apps/9999/metrics", headers=headers)

    assert resp.status_code == 404
    assert resp.json()["message"] == "应用实例不存在"
