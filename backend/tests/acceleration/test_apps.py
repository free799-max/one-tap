"""网络加速按应用接入（Mihomo 代理）测试。"""

import json

import pytest
import pytest_asyncio
import yaml
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.exceptions import APIException
from app.database import Base
from app.models.app_store import App
from app.models.docker import DockerComposeProject
from app.models.orchestration import AppInstance
from app.services.acceleration import apps as accel_apps
from app.services.acceleration import mihomo
from app.services.compose import compose_manager


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        yield session
    await engine.dispose()


async def _instance(db: AsyncSession, tmp_path, app_name: str = "moviepilot"):
    compose_file = tmp_path / f"{app_name}-compose.yml"
    compose_file.write_text(
        "services:\n"
        "  app:\n"
        "    image: demo:latest\n"
        "    environment:\n"
        "      - TZ=Asia/Shanghai\n",
        encoding="utf-8",
    )
    app = App(
        name=app_name,
        display_name=app_name.title(),
        yaml_template="services: {}",
    )
    db.add(app)
    await db.flush()
    project = DockerComposeProject(
        project_name=f"{app_name}-project",
        config_files=json.dumps([str(compose_file)]),
        working_dir=str(tmp_path),
    )
    db.add(project)
    await db.flush()
    instance = AppInstance(
        app=app,
        project_id=project.id,
        instance_name=app_name,
        status="running",
    )
    db.add(instance)
    await db.flush()
    return instance, compose_file


@pytest.mark.asyncio
async def test_list_apps_only_returns_supported(db_session, tmp_path):
    await _instance(db_session, tmp_path, "moviepilot")
    await _instance(db_session, tmp_path, "qbittorrent")
    await _instance(db_session, tmp_path, "nginx")

    items = await accel_apps.list_apps(db_session)
    names = {item["app_name"] for item in items}
    assert names == {"moviepilot", "qbittorrent"}
    assert all(item["proxy_enabled"] is False for item in items)
    assert all(item["can_enable"] for item in items)


@pytest.mark.asyncio
async def test_set_proxy_injects_and_removes_env(db_session, tmp_path, monkeypatch):
    instance, compose_file = await _instance(db_session, tmp_path)

    async def running(db):
        return {"installed": True, "running": True}

    async def proxy(db):
        return "http://192.168.1.17:7890"

    monkeypatch.setattr(mihomo, "status_async", running)
    monkeypatch.setattr(mihomo, "proxy_url", proxy)
    actions = []

    async def fake_action(db, project, action, progress_callback=None):
        actions.append(action)
        return {"returncode": 0}

    monkeypatch.setattr(compose_manager, "action_async", fake_action)

    enabled = await accel_apps.set_proxy(db_session, instance.id, True)
    assert enabled["enabled"] is True
    assert enabled["proxy"] == "http://192.168.1.17:7890"
    assert enabled["services"] == ["app"]

    content = yaml.safe_load(compose_file.read_text(encoding="utf-8"))
    env = content["services"]["app"]["environment"]
    assert env == {
        "TZ": "Asia/Shanghai",
        "PROXY_HOST": "http://192.168.1.17:7890",
        "BANGUMI_PROXY_ENABLE": "true",
        "MUSIC_COVER_PROXY": "/api/v1/system/img/1?imgurl=https://coverartarchive.org",
    }
    assert instance.proxy_enabled is True

    await accel_apps.set_proxy(db_session, instance.id, False)
    content = yaml.safe_load(compose_file.read_text(encoding="utf-8"))
    env = content["services"]["app"]["environment"]
    assert env == {"TZ": "Asia/Shanghai"}
    assert instance.proxy_enabled is False
    assert actions == ["up", "up"]


@pytest.mark.asyncio
async def test_set_proxy_only_changes_moviepilot_main_service(
    db_session, tmp_path, monkeypatch
):
    instance, compose_file = await _instance(db_session, tmp_path)
    proxy_url = "http://192.168.1.17:7890"
    instance.app.config_schema = {
        "containers": [
            {"name": "moviepilot"},
            {"name": "redis"},
            {"name": "postgresql"},
        ]
    }
    compose_file.write_text(
        yaml.safe_dump({
            "services": {
                "moviepilot": {
                    "image": "moviepilot:latest",
                    "environment": {
                        "TZ": "Asia/Shanghai",
                        "PROXY_HOST": proxy_url,
                        "HTTP_PROXY": proxy_url,
                        "HTTPS_PROXY": proxy_url,
                        "http_proxy": proxy_url,
                        "https_proxy": proxy_url,
                        "NO_PROXY": "localhost,127.0.0.1,::1",
                        "no_proxy": "localhost,127.0.0.1,::1",
                    },
                },
                "redis": {
                    "image": "redis:latest",
                    "environment": {
                        "TZ": "Asia/Shanghai",
                        "HTTP_PROXY": proxy_url,
                        "NO_PROXY": "localhost,127.0.0.1,::1",
                    },
                },
                "postgresql": {
                    "image": "postgres:latest",
                    "environment": {
                        "POSTGRES_DB": "moviepilot",
                        "HTTPS_PROXY": proxy_url,
                        "no_proxy": "localhost,127.0.0.1,::1",
                    },
                },
            }
        }),
        encoding="utf-8",
    )

    async def running(db):
        return {"installed": True, "running": True}

    async def proxy(db):
        return proxy_url

    async def fake_action(db, project, action, progress_callback=None):
        return {"returncode": 0}

    monkeypatch.setattr(mihomo, "status_async", running)
    monkeypatch.setattr(mihomo, "proxy_url", proxy)
    monkeypatch.setattr(compose_manager, "action_async", fake_action)

    enabled = await accel_apps.set_proxy(db_session, instance.id, True)

    assert enabled["services"] == ["moviepilot"]
    content = yaml.safe_load(compose_file.read_text(encoding="utf-8"))
    assert content["services"]["moviepilot"]["environment"] == {
        "TZ": "Asia/Shanghai",
        "PROXY_HOST": proxy_url,
        "BANGUMI_PROXY_ENABLE": "true",
        "MUSIC_COVER_PROXY": "/api/v1/system/img/1?imgurl=https://coverartarchive.org",
    }
    assert content["services"]["redis"]["environment"] == {"TZ": "Asia/Shanghai"}
    assert content["services"]["postgresql"]["environment"] == {
        "POSTGRES_DB": "moviepilot"
    }


@pytest.mark.asyncio
async def test_mediary_scout_only_proxies_web_service(db_session, tmp_path):
    instance, compose_file = await _instance(db_session, tmp_path, "mediary-scout")
    instance.app.config_schema = {
        "containers": [{"name": "web"}, {"name": "postgres"}, {"name": "pansou"}]
    }
    compose_file.write_text(
        yaml.safe_dump({
            "services": {
                "web": {
                    "image": "mediary-scout:latest",
                    "environment": {"TZ": "Asia/Shanghai"},
                },
                "postgres": {
                    "image": "postgres:16-alpine",
                    "environment": {"POSTGRES_DB": "mediatrack"},
                },
                "pansou": {
                    "image": "pansou-web:latest",
                    "environment": {"ENABLED_PLUGINS": "true"},
                },
            }
        }),
        encoding="utf-8",
    )

    items = await accel_apps.list_apps(db_session)
    assert [item["app_name"] for item in items] == ["mediary-scout"]
    assert items[0]["services"] == ["web"]

    proxy_url = "http://192.168.1.17:7890"
    content, services = accel_apps._apply_proxy(
        compose_file.read_text(encoding="utf-8"), proxy_url, True, instance
    )

    assert services == ["web"]
    data = yaml.safe_load(content)
    assert data["services"]["web"]["environment"] == {
        "TZ": "Asia/Shanghai",
        "HTTPS_PROXY": proxy_url,
    }
    assert data["services"]["postgres"]["environment"] == {
        "POSTGRES_DB": "mediatrack"
    }
    assert data["services"]["pansou"]["environment"] == {"ENABLED_PLUGINS": "true"}

    content, _ = accel_apps._apply_proxy(content, proxy_url, False, instance)
    data = yaml.safe_load(content)
    assert data["services"]["web"]["environment"] == {"TZ": "Asia/Shanghai"}


@pytest.mark.asyncio
async def test_other_supported_app_uses_only_https_proxy(db_session, tmp_path):
    instance, compose_file = await _instance(db_session, tmp_path, "emby")

    content, services = accel_apps._apply_proxy(
        compose_file.read_text(encoding="utf-8"),
        "http://192.168.1.17:7890",
        True,
        instance,
    )

    assert services == ["app"]
    env = yaml.safe_load(content)["services"]["app"]["environment"]
    assert env == {
        "TZ": "Asia/Shanghai",
        "HTTPS_PROXY": "http://192.168.1.17:7890",
    }


@pytest.mark.asyncio
async def test_set_proxy_requires_running_mihomo(db_session, tmp_path, monkeypatch):
    instance, _ = await _instance(db_session, tmp_path)

    async def stopped(db):
        return {"installed": False, "running": False}

    monkeypatch.setattr(mihomo, "status_async", stopped)
    with pytest.raises(APIException, match="Mihomo 未运行"):
        await accel_apps.set_proxy(db_session, instance.id, True)


@pytest.mark.asyncio
async def test_set_proxy_rejects_unsupported_app(db_session, tmp_path):
    instance, _ = await _instance(db_session, tmp_path, "nginx")
    with pytest.raises(APIException, match="暂不支持"):
        await accel_apps.set_proxy(db_session, instance.id, True)


@pytest.mark.asyncio
async def test_disable_all_restores_app_network(db_session, tmp_path, monkeypatch):
    instance, compose_file = await _instance(db_session, tmp_path)

    async def running(db):
        return {"installed": True, "running": True}

    async def proxy(db):
        return "http://192.168.1.17:7890"

    monkeypatch.setattr(mihomo, "status_async", running)
    monkeypatch.setattr(mihomo, "proxy_url", proxy)

    async def fake_action(db, project, action, progress_callback=None):
        return {"returncode": 0}

    monkeypatch.setattr(compose_manager, "action_async", fake_action)
    await accel_apps.set_proxy(db_session, instance.id, True)

    disabled = await accel_apps.disable_all(db_session)

    assert disabled == [instance.instance_name]
    assert instance.proxy_enabled is False
    content = yaml.safe_load(compose_file.read_text(encoding="utf-8"))
    env = content["services"]["app"]["environment"]
    assert "PROXY_HOST" not in env
    assert "HTTP_PROXY" not in env
    assert "BANGUMI_PROXY_ENABLE" not in env
    assert env["TZ"] == "Asia/Shanghai"
