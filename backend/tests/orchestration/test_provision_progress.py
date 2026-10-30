"""部署后账号自动配置与装配的进度任务测试。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.exceptions import APIException
from app.services.apps import ProvisionResult
from app.services.orchestration import orchestration_service
from app.services.orchestration import provisioning as provisioning_module
from app.services.orchestration.deploy_task_service import DeployTaskManager


class _FakeSession:
    """替换真实数据库会话上下文，仅用于单元测试。"""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def commit(self):
        return None


def _patch_group(monkeypatch, app_configs: dict) -> None:
    monkeypatch.setattr(provisioning_module, "async_session", lambda: _FakeSession())
    monkeypatch.setattr(
        orchestration_service,
        "get_group",
        AsyncMock(return_value=SimpleNamespace(app_configs=app_configs)),
    )


def _items() -> list[dict]:
    return [
        {
            "app_name": "moviepilot",
            "task_id": "task-moviepilot",
            "project_name": "test-stack-moviepilot",
            "credentials": {"username": "admin", "password": "pass"},
        },
        {
            "app_name": "qbittorrent",
            "task_id": "task-qbittorrent",
            "project_name": "test-stack-qbittorrent",
            "credentials": {"username": "admin", "password": "pass"},
        },
    ]


@pytest.mark.asyncio
async def test_provision_group_reports_progress_and_completes(monkeypatch):
    """配置与装配各阶段应推送到跟踪任务，全部成功后标记完成。"""
    manager = DeployTaskManager()
    monkeypatch.setattr(provisioning_module, "deploy_task_manager", manager)
    _patch_group(
        monkeypatch,
        {
            "moviepilot": {"url": "http://host:3000"},
            "qbittorrent": {"url": "http://host:8080"},
        },
    )
    monkeypatch.setattr(
        orchestration_service,
        "_wait_task_terminal",
        AsyncMock(return_value="completed"),
    )
    provision = AsyncMock(return_value=ProvisionResult(ok=True, message="ok"))
    monkeypatch.setattr(orchestration_service, "_provision_app", provision)
    wire = AsyncMock(return_value=None)
    monkeypatch.setattr(orchestration_service, "_wire_group", wire)

    task_id = manager.create_task(
        "orchestration_provision",
        meta={"operation": "provision", "instance_name": "test-stack"},
        enforce_limit=False,
    )

    await orchestration_service._provision_group(1, _items(), task_id)

    task = manager.get_task(task_id)
    assert task is not None
    assert task["status"] == "completed"
    assert task["progress"]["message"] == "账号与集成已自动配置完成"
    assert task["progress"]["percentage"] == 100

    stages = [entry["stage"] for entry in manager.get_task_history(task_id)]
    assert "waiting_services" in stages
    assert "provisioning" in stages
    assert "wiring" in stages
    assert stages[-1] == "completed"
    assert provision.await_count == 2
    wire.assert_awaited_once_with(1)


@pytest.mark.asyncio
async def test_provision_group_reports_partial_failure(monkeypatch):
    """有应用配置失败时任务仍完成，但提示未配置成功的数量。"""
    manager = DeployTaskManager()
    monkeypatch.setattr(provisioning_module, "deploy_task_manager", manager)
    _patch_group(
        monkeypatch,
        {
            "moviepilot": {"url": "http://host:3000"},
            "qbittorrent": {"url": "http://host:8080"},
        },
    )
    monkeypatch.setattr(
        orchestration_service,
        "_wait_task_terminal",
        AsyncMock(return_value="completed"),
    )
    monkeypatch.setattr(
        orchestration_service,
        "_provision_app",
        AsyncMock(return_value=ProvisionResult(ok=False, message="登录失败")),
    )
    monkeypatch.setattr(
        orchestration_service, "_wire_group", AsyncMock(return_value=None)
    )

    task_id = manager.create_task(
        "orchestration_provision",
        meta={"operation": "provision", "instance_name": "test-stack"},
        enforce_limit=False,
    )

    await orchestration_service._provision_group(1, _items(), task_id)

    task = manager.get_task(task_id)
    assert task is not None
    assert task["status"] == "completed"
    assert "2 个应用未配置成功" in task["progress"]["message"]


@pytest.mark.asyncio
async def test_provision_tracking_task_does_not_consume_deploy_slots(monkeypatch):
    """跟踪任务不占用并发部署槽位，部署多个应用后仍能创建。"""
    manager = DeployTaskManager(max_concurrent=1)
    monkeypatch.setattr(provisioning_module, "deploy_task_manager", manager)
    monkeypatch.setattr(
        provisioning_module,
        "create_background_task",
        lambda coro: coro.close(),
    )

    # 已有一个真实部署任务占满并发槽位
    deploy_task_id = manager.create_task("app_deploy")

    tracking_id = orchestration_service._schedule_provisioning(
        1,
        "test-stack",
        ["moviepilot"],
        ["task-moviepilot"],
        {"moviepilot": {"username": "admin", "password": "pass"}},
    )

    assert tracking_id is not None
    tracking = manager.get_task(tracking_id)
    assert tracking is not None
    assert tracking["meta"]["operation"] == "provision"
    assert tracking["status"] == "deploying"

    # 跟踪任务进行中时，真实部署任务仍可创建
    manager.complete_task(deploy_task_id)
    another = manager.create_task("app_deploy")
    assert another


@pytest.mark.asyncio
async def test_provision_group_retries_failed_app_while_container_running(
    monkeypatch,
):
    """首次配置失败且容器仍在运行时自动重试，成功后标记任务完成。"""
    manager = DeployTaskManager()
    monkeypatch.setattr(provisioning_module, "deploy_task_manager", manager)
    _patch_group(
        monkeypatch,
        {"moviepilot": {"url": "http://host:3000"}},
    )
    monkeypatch.setattr(
        orchestration_service,
        "_wait_task_terminal",
        AsyncMock(return_value="completed"),
    )
    provision = AsyncMock(
        side_effect=[
            ProvisionResult(ok=False, message="服务未就绪"),
            ProvisionResult(ok=True, message="账号已配置并验证通过"),
        ]
    )
    monkeypatch.setattr(orchestration_service, "_provision_app", provision)
    monkeypatch.setattr(orchestration_service, "_project_running", lambda name: True)
    monkeypatch.setattr(orchestration_service, "PROVISION_RETRY_DELAYS", (0,))
    monkeypatch.setattr(
        orchestration_service, "_wire_group", AsyncMock(return_value=None)
    )

    task_id = manager.create_task(
        "orchestration_provision",
        meta={"operation": "provision", "instance_name": "test-stack"},
        enforce_limit=False,
    )

    await orchestration_service._provision_group(1, _items()[:1], task_id)

    assert provision.await_count == 2
    task = manager.get_task(task_id)
    assert task is not None
    assert task["status"] == "completed"
    assert task["progress"]["message"] == "账号与集成已自动配置完成"


@pytest.mark.asyncio
async def test_provision_group_skips_retry_when_container_stopped(monkeypatch):
    """容器已退出时不做无意义的重试。"""
    manager = DeployTaskManager()
    monkeypatch.setattr(provisioning_module, "deploy_task_manager", manager)
    _patch_group(
        monkeypatch,
        {"moviepilot": {"url": "http://host:3000"}},
    )
    monkeypatch.setattr(
        orchestration_service,
        "_wait_task_terminal",
        AsyncMock(return_value="completed"),
    )
    provision = AsyncMock(
        return_value=ProvisionResult(ok=False, message="服务未就绪")
    )
    monkeypatch.setattr(orchestration_service, "_provision_app", provision)
    monkeypatch.setattr(orchestration_service, "_project_running", lambda name: False)
    monkeypatch.setattr(orchestration_service, "PROVISION_RETRY_DELAYS", (0,))
    monkeypatch.setattr(
        orchestration_service, "_wire_group", AsyncMock(return_value=None)
    )

    task_id = manager.create_task(
        "orchestration_provision",
        meta={"operation": "provision", "instance_name": "test-stack"},
        enforce_limit=False,
    )

    await orchestration_service._provision_group(1, _items()[:1], task_id)

    assert provision.await_count == 1


@pytest.mark.asyncio
async def test_cancel_provisioning_cancels_background_task(monkeypatch):
    """停止装配要取消实际等待协程，并推送不再恢复的已停止终态。"""
    manager = DeployTaskManager()
    monkeypatch.setattr(provisioning_module, "deploy_task_manager", manager)
    task_id = manager.create_task(
        "orchestration_provision",
        meta={"operation": "provision", "instance_name": "test-stack"},
        enforce_limit=False,
    )
    manager.update_progress(task_id, "provisioning", 60, "等待 moviepilot 服务就绪")
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

    await orchestration_service.cancel_provisioning(task_id)

    assert background.cancelled()
    task = manager.get_task(task_id)
    assert task is not None
    assert task["status"] == "cancelled"
    assert task["stage"] == "cancelled"
    assert task["progress"]["percentage"] == 60
    assert "应用容器保持运行" in task["progress"]["message"]
    assert manager.get_task_history(task_id)[-1]["_task_status"] == "cancelled"
    assert task_id not in manager.list_active_task_ids()


@pytest.mark.asyncio
async def test_cancel_provisioning_rejects_regular_deploy_task(monkeypatch):
    """普通部署任务不能走装配取消入口。"""
    manager = DeployTaskManager()
    monkeypatch.setattr(provisioning_module, "deploy_task_manager", manager)
    task_id = manager.create_task("app_deploy")

    with pytest.raises(APIException) as exc_info:
        await orchestration_service.cancel_provisioning(task_id)

    assert exc_info.value.status_code == 400
    assert manager.get_task(task_id)["status"] == "deploying"
