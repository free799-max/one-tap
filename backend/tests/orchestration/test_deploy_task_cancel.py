"""部署任务停止能力测试。"""

import asyncio

import pytest

from app.services.orchestration.deploy_task_service import (
    DeployTaskManager,
    deploy_task_manager,
)


async def _register_and_login(client, username: str = "stopper") -> dict:
    await client.post(
        "/api/auth/register", json={"username": username, "password": "stopper123"}
    )
    resp = await client.post(
        "/api/auth/login", json={"username": username, "password": "stopper123"}
    )
    token = resp.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def test_can_stop_follows_tracked_job():
    """任务只有登记了执行协程后才允许停止，结束后自动收回。"""
    manager = DeployTaskManager()
    task_id = manager.create_task("app_deploy", meta={"app_name": "openlist"})

    assert manager.can_stop(manager.get_task(task_id)) is False
    assert manager.get_task(task_id)["progress_history"][-1]["_can_stop"] is False

    async def runner():
        await asyncio.sleep(30)

    job = asyncio.create_task(runner())
    manager.track_job(task_id, job)
    manager.update_progress(task_id, "pulling_images", 47, "拉取镜像")

    assert manager.can_stop(manager.get_task(task_id)) is True
    assert manager.get_task(task_id)["progress_history"][-1]["_can_stop"] is True

    manager.cancel_task(task_id, "已停止部署")
    assert manager.can_stop(manager.get_task(task_id)) is False

    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job


@pytest.mark.asyncio
async def test_cancel_app_deploy_api(client):
    """停止部署任务：标记为已停止并中断执行协程。"""
    headers = await _register_and_login(client)
    task_id = deploy_task_manager.create_task(
        "app_deploy", meta={"app_name": "openlist"}
    )
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def runner():
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    job = asyncio.create_task(runner())
    deploy_task_manager.track_job(task_id, job)
    await started.wait()

    resp = await client.post(f"/api/deploy-tasks/{task_id}/cancel", headers=headers)

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "cancelled"
    assert "已停止部署" in data["progress"]["message"]
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_cancel_without_job_rejected(client):
    """没有登记执行协程的任务不能停止。"""
    headers = await _register_and_login(client, "stopper2")
    task_id = deploy_task_manager.create_task("compose_deploy")

    resp = await client.post(f"/api/deploy-tasks/{task_id}/cancel", headers=headers)

    assert resp.status_code == 400
