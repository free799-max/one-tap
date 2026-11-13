
"""实例组版本快照、可升级标记与更新记录 API 测试。"""

import pytest
from sqlalchemy import select

from app.models.app_store import App
from app.models.docker import DockerComposeProject, DockerComposeVersion
from app.models.orchestration import (
    AppInstance,
    AppOrchestration,
    OrchestrationUpdateEvent,
    OrchestrationUpgradeBackup,
)
from app.services.app_store import app_service
from app.services.orchestration import orchestration_service
from app.services.orchestration import upgrade as upgrade_module


async def _register_and_login(client, username: str = "upgrader") -> str:
    await client.post(
        "/api/auth/register", json={"username": username, "password": "upgrader123"}
    )
    resp = await client.post(
        "/api/auth/login", json={"username": username, "password": "upgrader123"}
    )
    return resp.json()["data"]["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _test_db(client):
    """取测试库会话生成器（复用接口依赖覆盖中的会话）。"""
    from app.database import get_db

    return client._transport.app.dependency_overrides[get_db]()


async def _bump_template_version(client, name: str, version: str, changelog: str):
    """直接改库模拟管理端下发了新版本模板。"""
    async for db in _test_db(client):
        orchestration = (
            await db.execute(
                select(AppOrchestration).where(AppOrchestration.name == name)
            )
        ).scalar_one()
        orchestration.version = version
        orchestration.changelog = changelog
        await db.commit()
        break


async def _set_template_composition(client, name: str, composition: list[dict]):
    """直接改库模拟管理端调整了模板组合。"""
    async for db in _test_db(client):
        orchestration = (
            await db.execute(
                select(AppOrchestration).where(AppOrchestration.name == name)
            )
        ).scalar_one()
        orchestration.app_composition = composition
        await db.commit()
        break


def _fake_deploy():
    """模拟 app_service.deploy：创建实例并返回任务 ID。"""

    async def fake_deploy(db, app_name, instance_name, config, user_id=None):
        app = (
            await db.execute(select(App).where(App.name == app_name))
        ).scalar_one()
        project = DockerComposeProject(
            project_name=instance_name,
            config_files="[]",
            working_dir="/tmp",
        )
        db.add(project)
        await db.flush()
        db.add(
            DockerComposeVersion(
                project_id=project.id,
                version_number=1,
                content=f"services:\n  app:\n    image: {app_name}:latest\n",
                is_current=True,
            )
        )
        instance = AppInstance(
            app_id=app.id,
            instance_name=instance_name,
            config=config,
            status="running",
            project_id=project.id,
        )
        db.add(instance)
        await db.commit()
        await db.refresh(instance)
        return instance, f"task-{app_name}"

    return fake_deploy


@pytest.mark.asyncio
async def test_instances_report_upgrade_available(client_with_seed, monkeypatch):
    """模板版本高于实例组快照时，列表与详情都标记可升级。"""
    token = await _register_and_login(client_with_seed, "upgrader")
    monkeypatch.setattr(app_service, "deploy", _fake_deploy())

    deploy = await client_with_seed.post(
        "/api/orchestrations/media-stack/deploy",
        json={
            "instance_name": "api-stack",
            "selected_apps": ["moviepilot", "qbittorrent"],
            "app_configs": {},
            "shared_config": {},
        },
        headers=_auth(token),
    )
    assert deploy.status_code == 200
    group_id = deploy.json()["data"]["group_id"]

    listed = await client_with_seed.get(
        "/api/orchestrations/instances", headers=_auth(token)
    )
    item = next(g for g in listed.json()["data"] if g["id"] == group_id)
    assert item["orchestration_version"] == "1.0.0"
    assert item["template_version"] == "1.0.0"
    assert item["upgrade_available"] is False

    # 管理端发布 2.0.0
    await _bump_template_version(
        client_with_seed, "media-stack", "2.0.0", "升级影视组件"
    )

    listed = await client_with_seed.get(
        "/api/orchestrations/instances", headers=_auth(token)
    )
    item = next(g for g in listed.json()["data"] if g["id"] == group_id)
    assert item["orchestration_version"] == "1.0.0"
    assert item["template_version"] == "2.0.0"
    assert item["upgrade_available"] is True
    assert item["template_changelog"] == "升级影视组件"

    detail = await client_with_seed.get(
        f"/api/orchestrations/instances/{group_id}", headers=_auth(token)
    )
    data = detail.json()["data"]
    assert data["orchestration_version"] == "1.0.0"
    assert data["template_version"] == "2.0.0"
    assert data["upgrade_available"] is True
    assert data["template_changelog"] == "升级影视组件"


@pytest.mark.asyncio
async def test_upgrade_endpoint_starts_image_only_upgrade(
    client_with_seed, monkeypatch
):
    """升级接口只启动镜像升级，保留模板移除的应用并先写配置快照。"""
    token = await _register_and_login(client_with_seed, "upgrade-api")
    monkeypatch.setattr(app_service, "deploy", _fake_deploy())

    # 后台升级协程使用独立的全局会话，这里只验证接口同步阶段的行为
    monkeypatch.setattr(
        upgrade_module,
        "create_background_task",
        lambda coro: coro.close(),
    )

    deploy = await client_with_seed.post(
        "/api/orchestrations/media-stack/deploy",
        json={
            "instance_name": "api-stack",
            "selected_apps": ["moviepilot", "qbittorrent"],
            "app_configs": {},
            "shared_config": {},
        },
        headers=_auth(token),
    )
    assert deploy.status_code == 200
    group_id = deploy.json()["data"]["group_id"]

    # 新模板：移除 qbittorrent，新增 jellyfin
    await _set_template_composition(
        client_with_seed,
        "media-stack",
        [
            {"app_name": "moviepilot", "relation": "required"},
            {"app_name": "jellyfin", "relation": "optional"},
        ],
    )
    await _bump_template_version(
        client_with_seed, "media-stack", "2.0.0", "替换下载器为 Jellyfin"
    )

    upgraded = await client_with_seed.post(
        f"/api/orchestrations/instances/{group_id}/upgrade",
        json={
            "instance_name": "ignored",
            "selected_apps": ["moviepilot", "jellyfin"],
            "app_configs": {},
            "shared_config": {},
        },
        headers=_auth(token),
    )
    assert upgraded.status_code == 200
    body = upgraded.json()["data"]
    assert body["group_id"] == group_id
    # 整个实例组只创建一个升级进度任务
    assert len(body["task_ids"]) == 1

    async for db in _test_db(client_with_seed):
        backup = (
            await db.execute(select(OrchestrationUpgradeBackup))
        ).scalar_one()
        assert backup.from_version == "1.0.0"
        assert backup.to_version == "2.0.0"
        payload_apps = {app["app_name"] for app in backup.payload["apps"]}
        assert payload_apps == {"moviepilot", "qbittorrent"}

        # 模板移除的 qbittorrent 在同步阶段不会被删除
        removed = (
            await db.execute(
                select(AppInstance).where(
                    AppInstance.instance_name == "api-stack-qbittorrent"
                )
            )
        ).scalar_one_or_none()
        assert removed is not None
        break

    detail = await client_with_seed.get(
        f"/api/orchestrations/instances/{group_id}", headers=_auth(token)
    )
    data = detail.json()["data"]
    # 后台升级协程未执行，版本仍是旧值，但组内应用没有被删除
    assert data["orchestration_version"] == "1.0.0"
    assert {app["app_name"] for app in data["apps"]} == {
        "moviepilot",
        "qbittorrent",
    }


@pytest.mark.asyncio
async def test_update_events_endpoint_filters_and_orders(client):
    """更新记录接口按时间倒序返回，并支持 scope/名称过滤。"""
    token = await _register_and_login(client, "events-user")

    async for db in _test_db(client):
        db.add_all(
            [
                OrchestrationUpdateEvent(
                    orchestration_name="media-stack",
                    scope="template",
                    from_version="1.0.0",
                    to_version="2.0.0",
                    mode="auto",
                    status="applied",
                    detail={"changelog": "模板更新", "created": False},
                ),
                OrchestrationUpdateEvent(
                    orchestration_name="media-stack",
                    scope="instance",
                    group_id=7,
                    from_version="1.0.0",
                    to_version="2.0.0",
                    mode="manual",
                    status="applied",
                    detail={"selected_apps": ["moviepilot"]},
                ),
            ]
        )
        await db.commit()
        break

    resp = await client.get(
        "/api/orchestrations/update-events", headers=_auth(token)
    )
    assert resp.status_code == 200
    events = resp.json()["data"]
    assert [e["scope"] for e in events] == ["instance", "template"]

    instance_only = await client.get(
        "/api/orchestrations/update-events",
        params={"scope": "instance"},
        headers=_auth(token),
    )
    assert len(instance_only.json()["data"]) == 1
    assert instance_only.json()["data"][0]["group_id"] == 7

    limited = await client.get(
        "/api/orchestrations/update-events",
        params={"orchestration_name": "media-stack", "limit": 1},
        headers=_auth(token),
    )
    assert len(limited.json()["data"]) == 1


@pytest.mark.asyncio
async def test_update_events_requires_auth(client):
    """未认证时无法查询更新记录。"""
    resp = await client.get("/api/orchestrations/update-events")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_cancel_single_upgrade_api(client_with_seed, monkeypatch):
    token = await _register_and_login(client_with_seed, "cancel-upgrader")
    task_id = upgrade_module.deploy_task_manager.create_task(
        "app_upgrade",
        meta={"operation": "upgrade", "app_name": "moviepilot"},
    )

    async def fake_cancel(progress_task_id):
        assert progress_task_id == task_id
        upgrade_module.deploy_task_manager.cancel_task(
            task_id, "已停止升级，镜像下载已中断"
        )

    monkeypatch.setattr(orchestration_service, "cancel_upgrade", fake_cancel)
    resp = await client_with_seed.post(
        f"/api/deploy-tasks/{task_id}/cancel",
        headers=_auth(token),
    )

    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "cancelled"
    assert resp.json()["data"]["progress"]["message"] == "已停止升级，镜像下载已中断"
