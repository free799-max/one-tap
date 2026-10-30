"""自动化组合模板 API 集成测试。"""

import pytest

from app.database import get_db
from app.models.orchestration import AppOrchestration


def _test_db(client):
    return client._transport.app.dependency_overrides[get_db]()


@pytest.mark.asyncio
async def test_list_orchestrations_requires_auth(client):
    """未认证时返回 403（HTTPBearer 默认行为）。"""
    resp = await client.get("/api/orchestrations?category=media")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_orchestration_api_returns_composition_fields(client):
    """认证后返回的组合模板包含 app_composition 和 shared_config_schema。"""
    # 注册并登录
    await client.post("/api/auth/register", json={
        "username": "autotest",
        "password": "autotest123",
    })
    login_resp = await client.post("/api/auth/login", json={
        "username": "autotest",
        "password": "autotest123",
    })
    token = login_resp.json()["data"]["access_token"]

    resp = await client.get("/api/orchestrations?category=media", headers={
        "Authorization": f"Bearer {token}",
    })
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert isinstance(data, list)
    # 如果数据库中有 media 分类的编排，验证字段结构
    for item in data:
        assert "app_composition" in item
        assert "shared_config_schema" in item
        assert "yaml_template" not in item
        assert "config_schema" not in item


@pytest.mark.asyncio
async def test_list_orchestrations_uses_manage_order(client):
    """列表接口按管理端顺序返回，相同顺序值按 id 稳定排序。"""
    await client.post(
        "/api/auth/register",
        json={"username": "ordertest", "password": "ordertest123"},
    )
    login_resp = await client.post(
        "/api/auth/login",
        json={"username": "ordertest", "password": "ordertest123"},
    )
    token = login_resp.json()["data"]["access_token"]

    async for db in _test_db(client):
        db.add_all(
            [
                AppOrchestration(
                    name="hidden",
                    display_name="已下架",
                    category="other",
                    sort_order=0,
                    is_listed=False,
                    app_composition=[],
                    shared_config_schema={},
                ),
                AppOrchestration(
                    name="media-z",
                    display_name="Z 影视",
                    category="media",
                    sort_order=20,
                    app_composition=[],
                    shared_config_schema={},
                ),
                AppOrchestration(
                    name="comics-b",
                    display_name="B 漫画",
                    category="comics",
                    sort_order=10,
                    app_composition=[],
                    shared_config_schema={},
                ),
                AppOrchestration(
                    name="comics-a",
                    display_name="A 漫画",
                    category="comics",
                    sort_order=10,
                    app_composition=[],
                    shared_config_schema={},
                ),
            ]
        )
        await db.commit()
        break

    resp = await client.get(
        "/api/orchestrations",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert [item["name"] for item in data] == [
        "comics-b",
        "comics-a",
        "media-z",
    ]
    assert [item["sort_order"] for item in data] == [10, 10, 20]
    assert all(item["is_listed"] for item in data)

    detail_resp = await client.get(
        "/api/orchestrations/hidden",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert detail_resp.status_code == 200
    assert detail_resp.json()["data"]["is_listed"] is False


@pytest.mark.asyncio
async def test_list_deploy_tasks_requires_auth(client):
    """未认证时无法查询进行中的部署任务。"""
    resp = await client.get("/api/deploy-tasks")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_cancel_deploy_task_requires_auth(client):
    """未认证时不能停止装配任务。"""
    resp = await client.post("/api/deploy-tasks/task-id/cancel")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_list_deploy_tasks_returns_active_task_ids(client):
    """认证后返回进行中的任务 ID，已完成任务不再返回。"""
    from app.services.orchestration.deploy_task_service import deploy_task_manager

    await client.post("/api/auth/register", json={
        "username": "deploylist",
        "password": "deploylist123",
    })
    login_resp = await client.post("/api/auth/login", json={
        "username": "deploylist",
        "password": "deploylist123",
    })
    token = login_resp.json()["data"]["access_token"]

    task_id = deploy_task_manager.create_task(
        "app_deploy", meta={"project_name": "deploylist"}
    )
    try:
        resp = await client.get("/api/deploy-tasks", headers={
            "Authorization": f"Bearer {token}",
        })
        assert resp.status_code == 200
        assert task_id in resp.json()["data"]
    finally:
        deploy_task_manager.complete_task(task_id)

    resp = await client.get("/api/deploy-tasks", headers={
        "Authorization": f"Bearer {token}",
    })
    assert task_id not in resp.json()["data"]


@pytest.mark.asyncio
async def test_cancel_provision_task_stops_worker_and_sse(client, monkeypatch):
    """停止接口取消真实后台协程，SSE 回放后以 cancelled 终态结束。"""
    import asyncio

    from app.services.orchestration import orchestration_service
    from app.services.orchestration.deploy_task_service import deploy_task_manager

    await client.post("/api/auth/register", json={
        "username": "provisionstop",
        "password": "provisionstop123",
    })
    login_resp = await client.post("/api/auth/login", json={
        "username": "provisionstop",
        "password": "provisionstop123",
    })
    token = login_resp.json()["data"]["access_token"]
    task_id = deploy_task_manager.create_task(
        "orchestration_provision",
        meta={"operation": "provision", "instance_name": "movie"},
        enforce_limit=False,
    )
    started = asyncio.Event()

    async def wait_forever():
        started.set()
        await asyncio.Event().wait()

    background = asyncio.create_task(wait_forever())
    monkeypatch.setattr(
        orchestration_service,
        "_provisioning_tasks",
        {task_id: background},
        raising=False,
    )
    await started.wait()

    resp = await client.post(
        f"/api/deploy-tasks/{task_id}/cancel",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "cancelled"
    assert background.cancelled()
    events = await client.get(f"/api/deploy-tasks/{task_id}/events?token={token}")
    assert events.status_code == 200
    assert '"_task_status": "cancelled"' in events.text
