"""自动化组合模板部署相关测试。"""

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.database import Base
from app.models.app_store import App
from app.models.orchestration import (
    AppInstance,
    AppOrchestration,
    AppOrchestrationInstance,
)
from app.services.app_store import app_service
from app.services.orchestration import orchestration_service
from app.services.orchestration.shared_config import merge_shared_config


@pytest_asyncio.fixture(name="db")
async def _db():
    """提供内存 SQLite 会话。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    SessionLocal = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with SessionLocal() as session:
        yield session

    await engine.dispose()


@pytest_asyncio.fixture
async def media_orchestration(db):
    """创建一个影视组合模板。"""
    orchestration = AppOrchestration(
        name="media-stack",
        display_name="影视媒体栈",
        category="media",
        app_composition=[
            {"app_name": "moviepilot", "relation": "required"},
            {"app_name": "qbittorrent", "relation": "required"},
            {"app_name": "jellyfin", "relation": "optional", "group": "player"},
            {"app_name": "emby", "relation": "optional", "group": "player"},
        ],
        shared_config_schema={
            "type": "object",
            "properties": {
                "volumes": {
                    "type": "array",
                    "default": [
                        {"mode": "rw", "host_path": "media", "container_path": "/media"},
                        {"mode": "rw", "host_path": "downloads", "container_path": "/downloads"},
                    ],
                    "items": {
                        "type": "object",
                        "properties": {
                            "mode": {"type": "string", "enum": ["rw", "ro"], "default": "rw"},
                            "host_path": {"type": "string", "format": "directory"},
                            "container_path": {"type": "string"},
                        },
                        "required": ["host_path", "container_path", "mode"],
                    },
                },
                "env": {
                    "type": "array",
                    "default": [
                        {"key": "TZ", "value": "Asia/Shanghai"},
                    ],
                    "items": {
                        "type": "object",
                        "properties": {
                            "key": {"type": "string"},
                            "value": {"type": "string"},
                        },
                        "required": ["key", "value"],
                    },
                },
            },
            "required": ["volumes", "env"],
            "containers": [
                {
                    "name": "shared",
                    "title": "公共配置",
                    "description": "所有影视自动化应用共享的存储空间和环境变量",
                    "settings": [
                        {"type": "volumes", "title": "存储空间设置", "fields": ["volumes"]},
                        {"type": "env", "title": "环境变量", "fields": ["env"]},
                    ],
                }
            ],
        },
    )
    db.add(orchestration)
    await db.commit()
    await db.refresh(orchestration)
    return orchestration


@pytest_asyncio.fixture
async def media_apps(db):
    """创建影视相关应用。"""
    apps = [
        App(
            name="moviepilot",
            display_name="MoviePilot",
            yaml_template="services:\n  app:\n    image: moviepilot:latest",
        ),
        App(
            name="qbittorrent",
            display_name="qBittorrent",
            yaml_template="services:\n  app:\n    image: qbittorrent:latest",
        ),
        App(
            name="jellyfin",
            display_name="Jellyfin",
            yaml_template="services:\n  app:\n    image: jellyfin:latest",
        ),
        App(
            name="emby",
            display_name="Emby",
            yaml_template="services:\n  app:\n    image: emby:latest",
        ),
    ]
    for app in apps:
        db.add(app)
    await db.commit()
    return apps


@pytest.mark.asyncio
async def test_deploy_composition_missing_required_raises(db, media_apps, media_orchestration):
    """必选应用未选时应抛出异常。"""
    with pytest.raises(Exception) as exc_info:
        await orchestration_service.deploy(
            db,
            orchestration_name="media-stack",
            instance_name="test-stack",
            selected_apps=["jellyfin"],
            app_configs={},
            shared_config={},
        )
    assert "必选应用" in str(exc_info.value)


@pytest.mark.asyncio
async def test_deploy_composition_conflicting_raises(db, media_apps, media_orchestration):
    """互斥应用同时选时应抛出异常。"""
    # 将 jellyfin 和 emby 设为互斥
    media_orchestration.app_composition = [
        {"app_name": "moviepilot", "relation": "required"},
        {"app_name": "qbittorrent", "relation": "required"},
        {"app_name": "jellyfin", "relation": "optional", "conflict_with": ["emby"]},
        {"app_name": "emby", "relation": "optional", "conflict_with": ["jellyfin"]},
    ]
    await db.commit()

    with pytest.raises(Exception) as exc_info:
        await orchestration_service.deploy(
            db,
            orchestration_name="media-stack",
            instance_name="test-stack",
            selected_apps=["moviepilot", "qbittorrent", "jellyfin", "emby"],
            app_configs={},
            shared_config={},
        )
    assert "互斥" in str(exc_info.value)


@pytest.mark.asyncio
async def test_deploy_composition_insufficient_slots_leaves_no_group(
    db, media_apps, media_orchestration, monkeypatch
):
    """并发槽位不足时应在创建实例组前报错，避免留下空实例组。"""
    from sqlalchemy import func

    from app.services.orchestration.deploy_task_service import deploy_task_manager

    monkeypatch.setattr(deploy_task_manager, "available_slots", lambda: 2)

    with pytest.raises(Exception) as exc_info:
        await orchestration_service.deploy(
            db,
            orchestration_name="media-stack",
            instance_name="test-stack",
            selected_apps=["moviepilot", "qbittorrent", "jellyfin"],
            app_configs={},
            shared_config={},
        )
    assert "并发槽位" in str(exc_info.value)

    result = await db.execute(
        select(func.count()).select_from(AppOrchestrationInstance)
    )
    assert result.scalar_one() == 0


@pytest.mark.asyncio
async def test_deploy_composition_existing_project_leaves_no_group(
    db, media_apps, media_orchestration
):
    """同名 Compose 项目已存在时应在创建实例组前报错。"""
    from sqlalchemy import func

    from app.models.docker import DockerComposeProject

    db.add(
        DockerComposeProject(
            project_name="test-stack-moviepilot",
            is_active=True,
        )
    )
    await db.commit()

    with pytest.raises(Exception) as exc_info:
        await orchestration_service.deploy(
            db,
            orchestration_name="media-stack",
            instance_name="test-stack",
            selected_apps=["moviepilot", "qbittorrent"],
            app_configs={},
            shared_config={},
        )
    assert "已存在" in str(exc_info.value)

    result = await db.execute(
        select(func.count()).select_from(AppOrchestrationInstance)
    )
    assert result.scalar_one() == 0


@pytest.mark.asyncio
async def test_deploy_composition_preflight_rejects_invalid_app_before_group(
    db, media_apps, media_orchestration, monkeypatch
):
    """应用配置不合法时应在创建实例组、部署任何应用之前报错。"""
    from sqlalchemy import func

    qbittorrent = next(app for app in media_apps if app.name == "qbittorrent")
    qbittorrent.config_schema = {
        "type": "object",
        "properties": {
            "volumes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "host_path": {"type": "string"},
                        "container_path": {"type": "string"},
                        "mode": {"type": "string"},
                    },
                    "required": ["host_path", "container_path", "mode"],
                },
            }
        },
    }
    await db.commit()

    deployed: list[str] = []

    async def fake_deploy(db, app_name, instance_name, config, user_id=None):
        deployed.append(app_name)
        raise AssertionError("配置校验失败时不应部署任何应用")

    monkeypatch.setattr(app_service, "deploy", fake_deploy)

    with pytest.raises(Exception) as exc_info:
        await orchestration_service.deploy(
            db,
            orchestration_name="media-stack",
            instance_name="test-stack",
            selected_apps=["moviepilot", "qbittorrent"],
            app_configs={
                "qbittorrent": {
                    "volumes": [{"mode": "rw", "host_path": "downloads"}]
                }
            },
            shared_config={},
        )

    message = str(exc_info.value)
    assert "qBittorrent" in message
    assert "容器路径" in message
    assert deployed == []

    result = await db.execute(
        select(func.count()).select_from(AppOrchestrationInstance)
    )
    assert result.scalar_one() == 0


@pytest.mark.asyncio
async def test_deploy_composition_missing_shared_container_path_rejected(
    db, media_apps, media_orchestration, monkeypatch
):
    """共享存储缺少容器路径时应明确报错，不能自动猜测挂载点。"""
    from sqlalchemy import func

    qbittorrent = next(app for app in media_apps if app.name == "qbittorrent")
    qbittorrent.config_schema = {
        "type": "object",
        "properties": {
            "volumes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "host_path": {"type": "string"},
                        "container_path": {"type": "string"},
                        "mode": {"type": "string"},
                    },
                    "required": ["host_path", "container_path", "mode"],
                },
            }
        },
        "containers": [
            {
                "name": "qbittorrent",
                "settings": [{"type": "volumes", "fields": ["volumes"]}],
            }
        ],
    }
    await db.commit()

    deployed: list[str] = []

    async def fake_deploy(db, app_name, instance_name, config, user_id=None):
        deployed.append(app_name)
        raise AssertionError("共享配置不完整时不应部署任何应用")

    monkeypatch.setattr(app_service, "deploy", fake_deploy)

    with pytest.raises(Exception) as exc_info:
        await orchestration_service.deploy(
            db,
            orchestration_name="media-stack",
            instance_name="test-stack",
            selected_apps=["moviepilot", "qbittorrent"],
            app_configs={},
            shared_config={
                "volumes": [
                    {"mode": "rw", "host_path": "/opt/data/nasdack/data1/movie"}
                ]
            },
        )

    message = str(exc_info.value)
    assert "qBittorrent" in message
    assert "容器路径" in message
    assert deployed == []

    result = await db.execute(
        select(func.count()).select_from(AppOrchestrationInstance)
    )
    assert result.scalar_one() == 0


@pytest.mark.asyncio
async def test_deploy_composition_success(db, media_apps, media_orchestration, monkeypatch):
    """正常选择时应创建组合部署记录并返回任务 ID。"""

    async def fake_deploy(db, app_name, instance_name, config, user_id=None):
        # 创建应用实例记录以模拟 AppService.deploy 行为
        app_result = await db.execute(select(App).where(App.name == app_name))
        app = app_result.scalar_one()
        instance = AppInstance(
            app_id=app.id,
            instance_name=instance_name,
            config=config,
            status="running",
        )
        db.add(instance)
        await db.commit()
        await db.refresh(instance)
        return instance, f"task-{app_name}"

    monkeypatch.setattr(app_service, "deploy", fake_deploy)

    group, returned_task_ids = await orchestration_service.deploy(
        db,
        orchestration_name="media-stack",
        instance_name="test-stack",
        selected_apps=["moviepilot", "qbittorrent", "jellyfin"],
        app_configs={
            "moviepilot": {"port": 3000},
            "qbittorrent": {"port": 8080},
            "jellyfin": {"port": 8096},
        },
        shared_config={"media_root": "media", "downloads_root": "downloads"},
        user_id=1,
    )

    assert group is not None
    assert group.instance_name == "test-stack"
    # 三个应用部署任务 + 一个自动配置/装配进度跟踪任务
    assert returned_task_ids[:3] == [
        "task-moviepilot",
        "task-qbittorrent",
        "task-jellyfin",
    ]
    assert len(returned_task_ids) == 4

    # 验证组合部署记录已创建
    result = await db.execute(
        select(AppOrchestrationInstance).where(
            AppOrchestrationInstance.id == group.id
        )
    )
    stored_group = result.scalar_one()
    assert stored_group.status == "running"

    # 验证关联实例已创建
    result = await db.execute(
        select(AppInstance).where(
            AppInstance.orchestration_group_id == group.id
        )
    )
    instances = result.scalars().all()
    assert len(instances) == 3
    instance_names = {i.instance_name for i in instances}
    assert instance_names == {
        "test-stack-moviepilot",
        "test-stack-qbittorrent",
        "test-stack-jellyfin",
    }


@pytest.mark.asyncio
async def test_deploy_composition_records_orchestration_version(
    db, media_apps, media_orchestration, monkeypatch
):
    """部署后实例组记录模板版本快照，供列表判断是否可升级。"""

    async def fake_deploy(db, app_name, instance_name, config, user_id=None):
        app = (
            await db.execute(select(App).where(App.name == app_name))
        ).scalar_one()
        instance = AppInstance(
            app_id=app.id,
            instance_name=instance_name,
            config=config,
            status="running",
        )
        db.add(instance)
        await db.commit()
        await db.refresh(instance)
        return instance, f"task-{app_name}"

    monkeypatch.setattr(app_service, "deploy", fake_deploy)

    group, _ = await orchestration_service.deploy(
        db,
        orchestration_name="media-stack",
        instance_name="test-stack",
        selected_apps=["moviepilot", "qbittorrent"],
        app_configs={},
        shared_config={},
    )

    assert group.orchestration_version == media_orchestration.version


# ---------- 共享配置合并 ----------


JELLYFIN_SCHEMA = {
    "containers": [
        {
            "name": "jellyfin",
            "settings": [
                {"type": "volumes", "fields": ["volumes"]},
                {"type": "env", "fields": ["jellyfin_env"]},
            ],
        }
    ]
}

SHARED_SCHEMA = {
    "containers": [
        {
            "name": "shared",
            "settings": [
                {"type": "volumes", "fields": ["volumes"]},
                {"type": "env", "fields": ["env"]},
            ],
        }
    ]
}

SHARED_CONFIG = {
    "volumes": [
        {"mode": "rw", "host_path": "media", "container_path": "/media"},
        {"mode": "rw", "host_path": "downloads", "container_path": "/downloads"},
    ],
    "env": [{"key": "TZ", "value": "Asia/Shanghai"}],
}


def test_merge_shared_config_appends_volumes_and_env():
    """共享 volumes/env 应追加到应用主容器对应字段，而不是被覆盖。"""
    app_config = {
        "volumes": [
            {"mode": "rw", "host_path": "jellyfin/config", "container_path": "/config"},
        ],
        "jellyfin_env": [{"key": "JELLYFIN_PublishedServerUrl", "value": "nas"}],
    }
    merged = merge_shared_config(
        SHARED_CONFIG, app_config, SHARED_SCHEMA, JELLYFIN_SCHEMA, "jellyfin"
    )

    container_paths = [v["container_path"] for v in merged["volumes"]]
    assert container_paths == ["/config", "/media", "/downloads"]
    # 应用自身项不打标，共享项打 shared 标记（模板据此跳过 project_name 前缀）
    assert "shared" not in merged["volumes"][0]
    assert merged["volumes"][1]["shared"] is True
    assert merged["volumes"][2]["shared"] is True

    env_keys = [e["key"] for e in merged["jellyfin_env"]]
    assert env_keys == ["JELLYFIN_PublishedServerUrl", "TZ"]


def test_merge_shared_config_app_config_takes_priority():
    """同一 container_path / key 冲突时应用自身配置优先。"""
    app_config = {
        "volumes": [
            {"mode": "ro", "host_path": "custom", "container_path": "/media"},
        ],
        "jellyfin_env": [{"key": "TZ", "value": "UTC"}],
    }
    merged = merge_shared_config(
        SHARED_CONFIG, app_config, SHARED_SCHEMA, JELLYFIN_SCHEMA, "jellyfin"
    )

    assert merged["volumes"] == [
        {"mode": "ro", "host_path": "custom", "container_path": "/media"},
        {"mode": "rw", "host_path": "downloads", "container_path": "/downloads", "shared": True},
    ]
    assert merged["jellyfin_env"] == [{"key": "TZ", "value": "UTC"}]


def test_merge_shared_config_only_merges_main_container():
    """多容器应用（如 moviepilot）只合并主容器的字段，不影响 redis 等附属容器。"""
    app_schema = {
        "containers": [
            {
                "name": "moviepilot",
                "settings": [
                    {"type": "volumes", "fields": ["volumes"]},
                    {"type": "env", "fields": ["moviepilot_env"]},
                ],
            },
            {
                "name": "redis",
                "settings": [
                    {"type": "volumes", "fields": ["redis_volumes"]},
                    {"type": "env", "fields": ["redis_env"]},
                ],
            },
        ]
    }
    app_config = {
        "volumes": [],
        "moviepilot_env": [],
        "redis_volumes": [
            {"mode": "rw", "host_path": "redis/data", "container_path": "/data"},
        ],
        "redis_env": [{"key": "REDIS_PASSWORD", "value": "secret"}],
    }
    merged = merge_shared_config(
        SHARED_CONFIG, app_config, SHARED_SCHEMA, app_schema, "moviepilot"
    )

    assert [v["container_path"] for v in merged["volumes"]] == ["/media", "/downloads"]
    assert merged["moviepilot_env"] == [{"key": "TZ", "value": "Asia/Shanghai"}]
    assert merged["redis_volumes"] == app_config["redis_volumes"]
    assert merged["redis_env"] == app_config["redis_env"]


def test_merge_shared_config_without_shared_config():
    """无共享配置时保持浅合并语义。"""
    app_config = {"volumes": [{"mode": "rw", "host_path": "a", "container_path": "/a"}]}
    merged = merge_shared_config({}, app_config, SHARED_SCHEMA, JELLYFIN_SCHEMA, "jellyfin")
    assert merged == app_config


def test_shared_volume_render_skips_project_prefix():
    """渲染时共享 volumes 的相对路径直接相对于 Docker 挂载目录，不加项目名前缀。"""
    from app.services.app_store.app_service import _render_yaml_template
    from app.services.system import StoragePathResolver

    template = (
        "services:\n"
        "  app:\n"
        "    image: test/app\n"
        "    volumes:\n"
        "{%- for vol in volumes %}\n"
        "      - {{ to_host_path(vol.host_path if (vol.host_path.startswith('/') or vol.shared) else project_name ~ '/' ~ vol.host_path) }}:{{ vol.container_path }}:{{ vol.mode }}\n"
        "{%- endfor %}\n"
    )
    config = {
        "volumes": [
            {"mode": "rw", "host_path": "emby/config", "container_path": "/config"},
            {"mode": "rw", "host_path": "media", "container_path": "/media", "shared": True},
        ],
    }
    resolver = StoragePathResolver(app_data=("/opt/docker", "/docker"))
    rendered = _render_yaml_template(
        template, {}, config, "movie-auto-emby", "emby", resolver
    )
    # 应用自身卷仍按项目目录隔离
    assert "/opt/docker/movie-auto-emby/emby/config:/config:rw" in rendered
    # 共享卷直接挂到 Docker 挂载目录下
    assert "/opt/docker/media:/media:rw" in rendered


@pytest.mark.asyncio
async def test_deploy_composition_merges_shared_config(
    db, media_apps, media_orchestration, monkeypatch
):
    """组合部署时共享 volumes/env 应合并进各应用配置后传给应用部署。"""
    captured_configs: dict[str, dict] = {}

    async def fake_deploy(db, app_name, instance_name, config, user_id=None):
        captured_configs[app_name] = config
        app_result = await db.execute(select(App).where(App.name == app_name))
        app = app_result.scalar_one()
        instance = AppInstance(
            app_id=app.id,
            instance_name=instance_name,
            config=config,
            status="running",
        )
        db.add(instance)
        await db.commit()
        await db.refresh(instance)
        return instance, f"task-{app_name}"

    monkeypatch.setattr(app_service, "deploy", fake_deploy)

    # 为 jellyfin 补充带 containers 定义的 config_schema
    result = await db.execute(select(App).where(App.name == "jellyfin"))
    jellyfin = result.scalar_one()
    jellyfin.config_schema = JELLYFIN_SCHEMA
    await db.commit()

    await orchestration_service.deploy(
        db,
        orchestration_name="media-stack",
        instance_name="test-stack",
        selected_apps=["moviepilot", "qbittorrent", "jellyfin"],
        app_configs={
            "jellyfin": {
                "volumes": [
                    {"mode": "rw", "host_path": "jellyfin/config", "container_path": "/config"},
                ],
            },
        },
        shared_config=SHARED_CONFIG,
    )

    jellyfin_config = captured_configs["jellyfin"]
    container_paths = [v["container_path"] for v in jellyfin_config["volumes"]]
    assert container_paths == ["/config", "/media", "/downloads"]
    assert jellyfin_config["jellyfin_env"] == [{"key": "TZ", "value": "Asia/Shanghai"}]


# ---------- 组合条目级 image/tag 覆盖 ----------


@pytest_asyncio.fixture
async def komga_app(db):
    """创建带默认镜像的 komga 应用，模板使用 {{ image }} 渲染。"""
    app = App(
        name="komga",
        display_name="Komga",
        image="gotson/komga:latest",
        yaml_template="services:\n  app:\n    image: {{ image }}",
    )
    db.add(app)
    await db.commit()
    await db.refresh(app)
    return app


def _render_komga(komga_app, config):
    """按部署路径渲染 komga 的 Compose YAML。"""
    from app.services.app_store.app_service import AppService
    from app.services.system import StoragePathResolver

    return AppService()._render_app(
        db_app=komga_app,
        config=config,
        project_name="test-reader-komga",
        resolver=StoragePathResolver(app_data=("/opt/docker", "/docker")),
    )


async def _deploy_and_capture_config(db, monkeypatch, komga_app, composition):
    """部署单个 komga 应用，返回传给 app_service.deploy 的 config。"""
    orchestration = AppOrchestration(
        name="reader-stack",
        display_name="阅读栈",
        category="reader",
        app_composition=composition,
    )
    db.add(orchestration)
    await db.commit()

    captured: dict[str, dict] = {}

    async def fake_deploy(db, app_name, instance_name, config, user_id=None):
        captured[app_name] = config
        instance = AppInstance(
            app_id=komga_app.id,
            instance_name=instance_name,
            config=config,
            status="running",
        )
        db.add(instance)
        await db.commit()
        await db.refresh(instance)
        return instance, f"task-{app_name}"

    monkeypatch.setattr(app_service, "deploy", fake_deploy)

    await orchestration_service.deploy(
        db,
        orchestration_name="reader-stack",
        instance_name="test-reader",
        selected_apps=["komga"],
        app_configs={},
        shared_config={},
    )
    return captured["komga"]


@pytest.mark.asyncio
async def test_composition_image_and_tag_override_rendered(
    db, komga_app, monkeypatch
):
    """条目带 image+tag 时拼成 image:tag，渲染出的 compose 使用覆盖值。"""
    config = await _deploy_and_capture_config(
        db,
        monkeypatch,
        komga_app,
        [
            {
                "app_name": "komga",
                "relation": "optional",
                "group": "reader",
                "image": "registry.example.com/gotson/komga",
                "tag": "1.16.0",
            }
        ],
    )

    assert config["image"] == "registry.example.com/gotson/komga:1.16.0"
    rendered = _render_komga(komga_app, config)
    assert "image: registry.example.com/gotson/komga:1.16.0" in rendered


@pytest.mark.asyncio
async def test_composition_tag_without_image_falls_back_to_app_image(
    db, komga_app, monkeypatch
):
    """条目只有 tag 而 image 为空时不注入，tag 无处拼接，渲染用 App.image 原样。"""
    config = await _deploy_and_capture_config(
        db,
        monkeypatch,
        komga_app,
        [
            {
                "app_name": "komga",
                "relation": "optional",
                "group": "reader",
                "tag": "1.16.0",
            }
        ],
    )

    assert "image" not in config
    rendered = _render_komga(komga_app, config)
    assert "image: gotson/komga:latest" in rendered


@pytest.mark.asyncio
async def test_composition_image_with_own_tag_ignores_tag_field(
    db, komga_app, monkeypatch
):
    """条目 image 自带 :tag 时忽略 tag 字段，原样使用 image。"""
    config = await _deploy_and_capture_config(
        db,
        monkeypatch,
        komga_app,
        [
            {
                "app_name": "komga",
                "relation": "optional",
                "group": "reader",
                "image": "gotson/komga:2.0.0",
                "tag": "1.16.0",
            }
        ],
    )

    assert config["image"] == "gotson/komga:2.0.0"
    rendered = _render_komga(komga_app, config)
    assert "image: gotson/komga:2.0.0" in rendered


@pytest.mark.asyncio
async def test_composition_without_image_tag_behaves_as_before(
    db, komga_app, monkeypatch
):
    """条目无 image/tag 时不注入 image，行为与现状一致（渲染用 App.image）。"""
    config = await _deploy_and_capture_config(
        db,
        monkeypatch,
        komga_app,
        [{"app_name": "komga", "relation": "optional", "group": "reader"}],
    )

    assert "image" not in config
    rendered = _render_komga(komga_app, config)
    assert "image: gotson/komga:latest" in rendered


# ---------- 单应用部署渲染的 image 注入语义 ----------


def _make_renderable_app(image="gotson/komga:latest"):
    return App(
        name="komga",
        display_name="Komga",
        image=image,
        yaml_template="services:\n  app:\n    image: {{ image }}",
    )


def test_render_app_without_image_in_config_uses_app_image():
    """单应用部署 config 不含 image 键时，渲染仍使用 App 默认镜像（行为不变）。"""
    rendered = _render_komga(_make_renderable_app(), {"port": 8080})
    assert "image: gotson/komga:latest" in rendered


def test_render_app_empty_image_in_config_uses_app_image():
    """config 里 image 为空字符串时视为未给，回落到 App 默认镜像。"""
    rendered = _render_komga(_make_renderable_app(), {"image": ""})
    assert "image: gotson/komga:latest" in rendered


def test_render_app_explicit_image_in_config_wins():
    """config 里显式给了非空 image 时优先于 App 默认镜像。"""
    rendered = _render_komga(
        _make_renderable_app(), {"image": "registry.example.com/gotson/komga:1.16.0"}
    )
    assert "image: registry.example.com/gotson/komga:1.16.0" in rendered


def test_refresh_instance_status_uses_project_when_container_ref_missing(monkeypatch):
    """配置未记录容器 ID 时，仍按 Compose 项目获取实时重启状态。"""
    from unittest.mock import MagicMock

    from app.models.docker import DockerComposeProject
    from app.services.docker import docker_manager

    instance = AppInstance(
        instance_name="movie-cloud-openlist",
        config={},
        status="running",
        project=DockerComposeProject(project_name="movie-cloud-openlist"),
    )
    container = MagicMock()
    container.status = "restarting"
    container.attrs = {"State": {"Status": "restarting"}}
    get_project_container = MagicMock(return_value=container)
    monkeypatch.setattr(
        orchestration_service, "_get_project_container", get_project_container
    )
    monkeypatch.setattr(docker_manager, "_client_obj", MagicMock())

    orchestration_service._refresh_instance_status(instance)

    get_project_container.assert_called_once_with("movie-cloud-openlist")
    container.reload.assert_called_once_with()
    assert instance.status == "restarting"


@pytest.mark.asyncio
async def test_list_instances_refreshes_project_container_status(
    db, media_orchestration, monkeypatch
):
    """编排列表可刷新仅有项目关联的实例，不触发异步懒加载。"""
    from unittest.mock import MagicMock

    from app.models.docker import DockerComposeProject
    from app.services.docker import docker_manager

    project = DockerComposeProject(project_name="movie-cloud-openlist")
    db.add(project)
    await db.flush()
    group = AppOrchestrationInstance(
        orchestration_id=media_orchestration.id,
        instance_name="movie-cloud",
        status="running",
    )
    db.add(group)
    await db.flush()
    db.add(
        AppInstance(
            orchestration_group_id=group.id,
            project_id=project.id,
            instance_name="movie-cloud-openlist",
            config={},
            status="running",
        )
    )
    await db.commit()

    container = MagicMock()
    container.status = "restarting"
    container.attrs = {"State": {"Status": "restarting"}}
    client = MagicMock()
    client.containers.get.return_value = container
    monkeypatch.setattr(docker_manager, "_client_obj", client)

    groups = await orchestration_service.list_instances(db)

    client.containers.get.assert_called_once_with("movie-cloud-openlist")
    assert groups[0].instances[0].status == "restarting"
