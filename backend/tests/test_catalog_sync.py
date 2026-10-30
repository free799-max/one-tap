"""应用与编排方案目录同步服务（catalog_sync）测试。"""

from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — 注册所有模型到 Base.metadata
from app.config import settings
from app.database import Base
from app.models.app_store import App
from app.models.orchestration import (
    AppInstance,
    AppOrchestration,
    OrchestrationUpdateEvent,
)
from app.services import catalog_sync
from app.services.manage_client import ManageRequestError


def _make_app_item(slug: str, **overrides) -> dict:
    """构造一条管理端应用市场数据。"""
    item = {
        "id": 1,
        "slug": slug,
        "name": f"{slug} 应用",
        "description": "应用描述",
        "category": "media",
        "icon": f"{slug}-icon",
        "homepage": "https://example.com",
        "repo_url": "https://github.com/example/app",
        "image": "example/image",
        "registry": "docker.io",
        "tags": ["标签"],
        "architectures": ["amd64"],
        "default_ports": [{"port": 8080, "protocol": "tcp"}],
        "config_schema": {
            "type": "object",
            "properties": {"port": {"type": "integer", "default": 8080}},
        },
        "compose_template": (
            "services:\n"
            "  app:\n"
            "    image: {{ image }}\n"
            "    container_name: {{ project_name }}\n"
        ),
        "readme": "# Readme",
        "type": "compose",
        "changelog": "首个版本",
        "backup_paths": ["/config"],
        "version_source": "manual",
        "version_source_ref": None,
        "auto_update": False,
        "is_published": True,
        "sort_order": 0,
        "latest_version": "2.0.0",
    }
    item.update(overrides)
    return item


def _make_item(name: str, **overrides) -> dict:
    """构造一条管理端方案数据。"""
    item = {
        "name": name,
        "display_name": f"{name} 显示名",
        "description": "描述",
        "category": "media",
        "icon": f"/icons/orchestrations/{name}.svg",
        "website": "https://example.com",
        "source_url": None,
        "version": "1.0.0",
        "app_composition": [{"app_name": "app-a", "relation": "required"}],
        "shared_config_schema": {"type": "object", "properties": {}},
    }
    item.update(overrides)
    return item


class _FakeResponse:
    """模拟 httpx 响应。"""

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeClient:
    """模拟 httpx.AsyncClient，按配置返回响应或抛异常。"""

    def __init__(self, *args, payload=None, exc=None, **kwargs):
        self._payload = payload
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url):
        if self._exc is not None:
            raise self._exc
        return _FakeResponse(self._payload)


def _patch_http(monkeypatch, payload=None, exc=None):
    """替换 catalog_sync 模块内的 httpx 为假实现。"""
    monkeypatch.setattr(
        catalog_sync,
        "httpx",
        SimpleNamespace(
            AsyncClient=lambda *a, **kw: _FakeClient(*a, payload=payload, exc=exc, **kw)
        ),
    )


@pytest.fixture(autouse=True)
def _reset_sync_state():
    """每个用例前后重置模块级同步状态，避免相互污染。"""
    catalog_sync.last_sync_at = None
    catalog_sync.last_error = None
    catalog_sync.last_result = None
    yield
    catalog_sync.last_sync_at = None
    catalog_sync.last_error = None
    catalog_sync.last_result = None


@pytest_asyncio.fixture
async def sync_db(monkeypatch):
    """提供内存数据库会话工厂，并注入到 catalog_sync 与配置中。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    SessionLocal = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    monkeypatch.setattr(catalog_sync, "async_session", SessionLocal)
    monkeypatch.setattr(settings, "MANAGE_BASE_URL", "http://manage.test")
    yield SessionLocal
    await engine.dispose()


async def _get_by_name(SessionLocal, name: str) -> AppOrchestration | None:
    async with SessionLocal() as session:
        result = await session.execute(
            select(AppOrchestration).where(AppOrchestration.name == name)
        )
        return result.scalar_one_or_none()


async def test_sync_creates_new_orchestrations(sync_db, monkeypatch):
    """远端方案在本地不存在时新建记录。"""
    _patch_http(
        monkeypatch,
        payload={"success": True, "data": [_make_item("media-stack"), _make_item("comics-stack")]},
    )

    stats = await catalog_sync.sync_orchestrations()

    assert stats == {
        "created": 2,
        "updated": 0,
        "removed": 0,
        "total": 2,
        "events": 2,
    }
    orchestration = await _get_by_name(sync_db, "media-stack")
    assert orchestration is not None
    assert orchestration.display_name == "media-stack 显示名"
    assert orchestration.category == "media"
    assert orchestration.icon == "/icons/orchestrations/media-stack.svg"
    assert orchestration.app_composition == [
        {"app_name": "app-a", "relation": "required"}
    ]
    assert orchestration.is_builtin is False
    assert catalog_sync.last_error is None
    assert catalog_sync.last_result == stats
    assert catalog_sync.last_sync_at is not None


async def test_sync_updates_existing_without_touching_is_builtin(
    sync_db, monkeypatch
):
    """已有记录字段被更新，is_builtin 保持不变。"""
    async with sync_db() as session:
        session.add(
            AppOrchestration(
                name="media-stack",
                display_name="旧显示名",
                category="other",
                version="0.1.0",
                is_builtin=True,
                app_composition=[],
                shared_config_schema={},
            )
        )
        await session.commit()

    _patch_http(
        monkeypatch,
        payload={
            "success": True,
            "data": [_make_item("media-stack", display_name="新显示名", version="2.0.0")],
        },
    )

    stats = await catalog_sync.sync_orchestrations()

    assert stats == {"created": 0, "updated": 1, "removed": 0, "total": 1, "events": 1}
    orchestration = await _get_by_name(sync_db, "media-stack")
    assert orchestration.display_name == "新显示名"
    assert orchestration.version == "2.0.0"
    assert orchestration.category == "media"
    assert orchestration.is_builtin is True


async def test_sync_hides_and_relists_local_record_without_deleting(
    sync_db, monkeypatch
):
    """远端缺失的记录只下架不删除，重新下发时可恢复展示。"""
    async with sync_db() as session:
        session.add(
            AppOrchestration(
                name="local-only",
                display_name="本地专属",
                category="other",
                app_composition=[],
                shared_config_schema={},
            )
        )
        await session.commit()

    _patch_http(
        monkeypatch,
        payload={"success": True, "data": [_make_item("media-stack")]},
    )

    await catalog_sync.sync_orchestrations()

    local = await _get_by_name(sync_db, "local-only")
    remote = await _get_by_name(sync_db, "media-stack")
    assert local is not None and local.is_listed is False
    assert remote is not None and remote.is_listed is True

    _patch_http(
        monkeypatch,
        payload={
            "success": True,
            "data": [_make_item("local-only"), _make_item("media-stack")],
        },
    )
    await catalog_sync.sync_orchestrations()

    local = await _get_by_name(sync_db, "local-only")
    assert local is not None and local.is_listed is True


async def test_sync_removes_duplicate_builtin_orchestration(sync_db, monkeypatch):
    """管理端方案与本地内置方案同分类时，清理无实例引用的内置旧记录。"""
    async with sync_db() as session:
        session.add(
            AppOrchestration(
                name="comic",
                display_name="漫画自动化",
                category="comics",
                version="1.2.1",
                is_builtin=False,
                app_composition=[{"app_name": "app-a", "relation": "required"}],
                shared_config_schema={},
            )
        )
        session.add(
            AppOrchestration(
                name="comics-stack",
                display_name="漫画自动化",
                category="comics",
                version="1.0.0",
                is_builtin=True,
                app_composition=[],
                shared_config_schema={},
            )
        )
        await session.commit()

    _patch_http(
        monkeypatch,
        payload={
            "success": True,
            "data": [
                _make_item("comic", display_name="漫画自动化", category="comics"),
            ],
        },
    )

    stats = await catalog_sync.sync_orchestrations()

    assert stats == {
        "created": 0,
        "updated": 1,
        "removed": 1,
        "total": 1,
        "events": 1,
    }
    assert await _get_by_name(sync_db, "comic") is not None
    assert await _get_by_name(sync_db, "comics-stack") is None


async def test_sync_keeps_builtin_orchestration_with_instances(sync_db, monkeypatch):
    """内置旧方案仍被实例引用时保留，避免破坏部署记录。"""
    async with sync_db() as session:
        session.add(
            AppOrchestration(
                name="comic",
                display_name="漫画自动化",
                category="comics",
                version="1.2.1",
                is_builtin=False,
                app_composition=[{"app_name": "app-a", "relation": "required"}],
                shared_config_schema={},
            )
        )
        legacy = AppOrchestration(
            name="comics-stack",
            display_name="漫画自动化",
            category="comics",
            version="1.0.0",
            is_builtin=True,
            app_composition=[],
            shared_config_schema={},
        )
        session.add(legacy)
        await session.flush()
        session.add(
            AppInstance(
                instance_name="comics-stack",
                orchestration_id=legacy.id,
                config={},
            )
        )
        await session.commit()

    _patch_http(
        monkeypatch,
        payload={
            "success": True,
            "data": [
                _make_item("comic", display_name="漫画自动化", category="comics"),
            ],
        },
    )

    await catalog_sync.sync_orchestrations()

    assert await _get_by_name(sync_db, "comics-stack") is not None


async def test_sync_claims_builtin_when_slug_changed(sync_db, monkeypatch):
    """内置方案与远端显示名一致时按改名认领，不新建重复记录。"""
    async with sync_db() as session:
        session.add(
            AppOrchestration(
                name="comics-stack",
                display_name="漫画自动化",
                category="comics",
                version="1.0.0",
                is_builtin=True,
                app_composition=[],
                shared_config_schema={},
            )
        )
        await session.commit()

    _patch_http(
        monkeypatch,
        payload={
            "success": True,
            "data": [
                _make_item(
                    "comic",
                    display_name="漫画自动化",
                    category="comics",
                    version="1.2.1",
                ),
            ],
        },
    )

    stats = await catalog_sync.sync_orchestrations()

    assert stats == {"created": 0, "updated": 1, "removed": 0, "total": 1, "events": 1}
    assert await _get_by_name(sync_db, "comics-stack") is None
    claimed = await _get_by_name(sync_db, "comic")
    assert claimed is not None
    assert claimed.is_builtin is True


async def test_sync_accepts_bare_list_response(sync_db, monkeypatch):
    """防御性解析：响应体直接为列表时也能处理。"""
    _patch_http(monkeypatch, payload=[_make_item("media-stack")])

    stats = await catalog_sync.sync_orchestrations()

    assert stats == {"created": 1, "updated": 0, "removed": 0, "total": 1, "events": 1}


async def test_sync_skipped_when_manage_base_url_empty(sync_db, monkeypatch):
    """MANAGE_BASE_URL 为空时跳过同步。"""
    monkeypatch.setattr(settings, "MANAGE_BASE_URL", "")
    _patch_http(monkeypatch, exc=AssertionError("不应发起 HTTP 请求"))

    result = await catalog_sync.sync_orchestrations()

    assert result == {"skipped": True}


async def test_sync_network_error_records_last_error(sync_db, monkeypatch):
    """网络异常时记录 last_error 并重新抛出。"""
    _patch_http(monkeypatch, exc=httpx.ConnectError("connection refused"))

    with pytest.raises(ManageRequestError):
        await catalog_sync.sync_orchestrations()

    assert catalog_sync.last_error is not None
    assert "connection refused" in catalog_sync.last_error
    assert catalog_sync.last_result is None
    assert catalog_sync.last_sync_at is not None


async def _get_events(SessionLocal) -> list[OrchestrationUpdateEvent]:
    """按写入顺序返回全部更新事件。"""
    async with SessionLocal() as session:
        result = await session.execute(
            select(OrchestrationUpdateEvent).order_by(OrchestrationUpdateEvent.id)
        )
        return list(result.scalars().all())


async def test_sync_updates_manage_order_without_version_events(sync_db, monkeypatch):
    """仅调整管理端顺序时也更新本地缓存，且不产生版本事件。"""
    _patch_http(
        monkeypatch,
        payload={
            "success": True,
            "data": [_make_item("media-stack"), _make_item("comics-stack")],
        },
    )
    await catalog_sync.sync_orchestrations()

    media = await _get_by_name(sync_db, "media-stack")
    comics = await _get_by_name(sync_db, "comics-stack")
    assert media is not None and comics is not None
    assert (media.sort_order, comics.sort_order) == (0, 1)

    _patch_http(
        monkeypatch,
        payload={
            "success": True,
            "data": [_make_item("comics-stack"), _make_item("media-stack")],
        },
    )
    stats = await catalog_sync.sync_orchestrations()

    assert stats == {
        "created": 0,
        "updated": 2,
        "removed": 0,
        "total": 2,
        "events": 0,
    }
    media = await _get_by_name(sync_db, "media-stack")
    comics = await _get_by_name(sync_db, "comics-stack")
    assert media is not None and comics is not None
    assert (media.sort_order, comics.sort_order) == (1, 0)
    assert len(await _get_events(sync_db)) == 2


async def test_sync_updates_chinese_category_without_version_event(
    sync_db, monkeypatch
):
    """管理端分类中文化后，同版本方案也会更新本地分类。"""
    _patch_http(
        monkeypatch,
        payload={"success": True, "data": [_make_item("movie", category="media")]},
    )
    await catalog_sync.sync_orchestrations()

    _patch_http(
        monkeypatch,
        payload={"success": True, "data": [_make_item("movie", category="影视")]},
    )
    stats = await catalog_sync.sync_orchestrations()

    assert stats["updated"] == 1
    assert stats["events"] == 0
    movie = await _get_by_name(sync_db, "movie")
    assert movie is not None and movie.category == "影视"
    assert len(await _get_events(sync_db)) == 1


async def test_sync_version_change_writes_auto_event(sync_db, monkeypatch):
    """版本变化时写一条 scope=template、mode=auto 的 applied 事件。"""
    async with sync_db() as session:
        session.add(
            AppOrchestration(
                name="media-stack",
                display_name="旧显示名",
                category="media",
                version="1.0.0",
                app_composition=[{"app_name": "app-a", "relation": "required"}],
                shared_config_schema={},
            )
        )
        await session.commit()

    _patch_http(
        monkeypatch,
        payload={
            "success": True,
            "data": [
                _make_item(
                    "media-stack",
                    version="2.0.0",
                    changelog="新增漫画支持",
                )
            ],
        },
    )

    stats = await catalog_sync.sync_orchestrations()

    assert stats == {"created": 0, "updated": 1, "removed": 0, "total": 1, "events": 1}
    events = await _get_events(sync_db)
    assert len(events) == 1
    event = events[0]
    assert event.orchestration_name == "media-stack"
    assert event.scope == "template"
    assert event.mode == "auto"
    assert event.status == "applied"
    assert event.from_version == "1.0.0"
    assert event.to_version == "2.0.0"
    assert event.group_id is None
    assert event.detail == {"changelog": "新增漫画支持", "created": False}

    # changelog 落库到模板
    orchestration = await _get_by_name(sync_db, "media-stack")
    assert orchestration.changelog == "新增漫画支持"


async def test_sync_manual_mode_is_recorded(sync_db, monkeypatch):
    """手动同步传入的 mode 会写入事件。"""
    _patch_http(
        monkeypatch,
        payload={"success": True, "data": [_make_item("media-stack")]},
    )

    await catalog_sync.sync_orchestrations(mode="manual")

    events = await _get_events(sync_db)
    assert [(e.scope, e.mode, e.status) for e in events] == [
        ("template", "manual", "applied")
    ]
    # 首次创建：from_version 为空，detail.created 标记新建
    assert events[0].from_version is None
    assert events[0].to_version == "1.0.0"
    assert events[0].detail["created"] is True


async def test_sync_same_version_content_change_writes_no_event(
    sync_db, monkeypatch
):
    """版本相同但组合变化时照常 upsert，不写事件。"""
    async with sync_db() as session:
        session.add(
            AppOrchestration(
                name="media-stack",
                display_name="旧显示名",
                category="media",
                version="1.0.0",
                app_composition=[],
                shared_config_schema={},
            )
        )
        await session.commit()

    _patch_http(
        monkeypatch,
        payload={
            "success": True,
            "data": [_make_item("media-stack", version="1.0.0", changelog="同版本微调")],
        },
    )

    stats = await catalog_sync.sync_orchestrations()

    assert stats == {"created": 0, "updated": 1, "removed": 0, "total": 1, "events": 0}
    assert await _get_events(sync_db) == []
    orchestration = await _get_by_name(sync_db, "media-stack")
    assert orchestration.app_composition == [
        {"app_name": "app-a", "relation": "required"}
    ]
    assert orchestration.changelog == "同版本微调"


async def test_sync_unchanged_version_and_composition_is_skipped(
    sync_db, monkeypatch
):
    """版本与组合都没变时跳过更新，不计入 updated。"""
    async with sync_db() as session:
        session.add(
            AppOrchestration(
                name="media-stack",
                display_name="本地显示名",
                category="media",
                version="1.0.0",
                icon="/icons/orchestrations/media-stack.svg",
                app_composition=[{"app_name": "app-a", "relation": "required"}],
                shared_config_schema={},
            )
        )
        await session.commit()

    _patch_http(
        monkeypatch,
        payload={
            "success": True,
            "data": [_make_item("media-stack", version="1.0.0")],
        },
    )

    stats = await catalog_sync.sync_orchestrations()

    assert stats == {"created": 0, "updated": 0, "removed": 0, "total": 0, "events": 0}
    assert await _get_events(sync_db) == []
    # 未触发更新：本地展示字段保持原值
    orchestration = await _get_by_name(sync_db, "media-stack")
    assert orchestration.display_name == "本地显示名"


async def test_sync_failure_writes_failed_event(sync_db, monkeypatch):
    """同步失败时记录一条 failed 事件后重新抛出。"""
    _patch_http(monkeypatch, exc=httpx.ConnectError("connection refused"))

    with pytest.raises(ManageRequestError):
        await catalog_sync.sync_orchestrations(mode="manual")

    events = await _get_events(sync_db)
    assert len(events) == 1
    event = events[0]
    assert event.scope == "template"
    assert event.mode == "manual"
    assert event.status == "failed"
    assert event.orchestration_name == "sync"
    assert "connection refused" in event.message


async def _get_app(SessionLocal, name: str) -> App | None:
    async with SessionLocal() as session:
        result = await session.execute(select(App).where(App.name == name))
        return result.scalar_one_or_none()


async def test_sync_apps_creates_new_app(sync_db, monkeypatch):
    """远端应用在本地不存在时新建记录，并完成字段映射。"""
    _patch_http(monkeypatch, payload={"success": True, "data": [_make_app_item("jellyfin")]})

    stats = await catalog_sync.sync_apps()

    assert stats == {"created": 1, "updated": 0, "removed": 0, "skipped": 0, "total": 1}
    app = await _get_app(sync_db, "jellyfin")
    assert app is not None
    assert app.display_name == "jellyfin 应用"
    assert app.description == "应用描述"
    assert app.category == "media"
    assert app.tags == ["标签"]
    assert app.website == "https://example.com"
    assert app.source_url == "https://github.com/example/app"
    assert app.image == "example/image"
    assert app.architectures == ["amd64"]
    assert app.default_ports == [{"port": 8080, "protocol": "tcp"}]
    assert app.version == "2.0.0"
    assert app.yaml_template.startswith("services:")
    assert app.type == "compose"
    assert app.changelog == "首个版本"
    assert app.is_builtin is False
    assert app.backup_paths == ["/config"]
    assert app.is_paid is False


async def test_sync_apps_updates_existing_and_keeps_is_builtin(
    sync_db, monkeypatch
):
    """已有应用被更新，is_builtin 等本地字段保持不变。"""
    async with sync_db() as session:
        session.add(
            App(
                name="jellyfin",
                display_name="旧名称",
                category="other",
                description="旧描述",
                image="old/image",
                yaml_template="services:\n  app:\n    image: old/image\n",
                is_builtin=True,
                backup_paths=["/data/config"],
            )
        )
        await session.commit()

    _patch_http(monkeypatch, payload={"success": True, "data": [_make_app_item("jellyfin")]})

    stats = await catalog_sync.sync_apps()

    assert stats == {"created": 0, "updated": 1, "removed": 0, "skipped": 0, "total": 1}
    app = await _get_app(sync_db, "jellyfin")
    assert app.display_name == "jellyfin 应用"
    assert app.description == "应用描述"
    assert app.image == "example/image"
    assert app.is_builtin is True
    assert app.backup_paths == ["/config"]
    assert app.changelog == "首个版本"


async def test_sync_apps_preserves_backup_paths_for_legacy_payload(
    sync_db, monkeypatch
):
    """旧版管理端不返回 backup_paths 时，保留本地已有的备份路径。"""
    async with sync_db() as session:
        session.add(
            App(
                name="jellyfin",
                display_name="旧名称",
                category="other",
                image="old/image",
                yaml_template="services:\n  app:\n    image: old/image\n",
                backup_paths=["/data/config"],
            )
        )
        await session.commit()

    item = _make_app_item("jellyfin")
    item.pop("backup_paths")
    _patch_http(monkeypatch, payload={"success": True, "data": [item]})

    stats = await catalog_sync.sync_apps()

    assert stats == {"created": 0, "updated": 1, "removed": 0, "skipped": 0, "total": 1}
    app = await _get_app(sync_db, "jellyfin")
    assert app.display_name == "jellyfin 应用"
    assert app.backup_paths == ["/data/config"]


async def test_sync_apps_maps_is_paid(sync_db, monkeypatch):
    """管理端返回 is_paid 时同步到本地应用。"""
    _patch_http(
        monkeypatch,
        payload={"success": True, "data": [_make_app_item("mt-photos", is_paid=True)]},
    )

    stats = await catalog_sync.sync_apps()

    assert stats == {"created": 1, "updated": 0, "removed": 0, "skipped": 0, "total": 1}
    app = await _get_app(sync_db, "mt-photos")
    assert app.is_paid is True


async def test_sync_apps_preserves_is_paid_for_legacy_payload(sync_db, monkeypatch):
    """旧版管理端不返回 is_paid 时，保留本地已有的付费标识。"""
    async with sync_db() as session:
        session.add(
            App(
                name="mt-photos",
                display_name="旧名称",
                category="other",
                image="old/image",
                yaml_template="services:\n  app:\n    image: old/image\n",
                is_paid=True,
            )
        )
        await session.commit()

    item = _make_app_item("mt-photos")
    _patch_http(monkeypatch, payload={"success": True, "data": [item]})

    stats = await catalog_sync.sync_apps()

    assert stats == {"created": 0, "updated": 1, "removed": 0, "skipped": 0, "total": 1}
    app = await _get_app(sync_db, "mt-photos")
    assert app.is_paid is True


async def test_sync_apps_skips_unchanged_app(sync_db, monkeypatch):
    """远端内容与本地一致时不计数、不写库。"""
    async with sync_db() as session:
        session.add(
            App(
                name="jellyfin",
                display_name="jellyfin 应用",
                category="media",
                description="应用描述",
                tags=["标签"],
                icon="jellyfin-icon",
                website="https://example.com",
                source_url="https://github.com/example/app",
                architectures=["amd64"],
                image="example/image",
                default_ports=[{"port": 8080, "protocol": "tcp"}],
                config_schema={
                    "type": "object",
                    "properties": {"port": {"type": "integer", "default": 8080}},
                },
                yaml_template=(
                    "services:\n"
                    "  app:\n"
                    "    image: {{ image }}\n"
                    "    container_name: {{ project_name }}\n"
                ),
                readme="# Readme",
                type="compose",
                changelog="首个版本",
                backup_paths=["/config"],
                version="2.0.0",
            )
        )
        await session.commit()

    _patch_http(monkeypatch, payload={"success": True, "data": [_make_app_item("jellyfin")]})

    stats = await catalog_sync.sync_apps()

    assert stats == {"created": 0, "updated": 0, "removed": 0, "skipped": 0, "total": 0}


async def test_sync_apps_skips_item_without_template(sync_db, monkeypatch):
    """远端缺少 Compose 模板时跳过，且不覆盖本地已有模板。"""
    async with sync_db() as session:
        session.add(
            App(
                name="jellyfin",
                display_name="本地名称",
                category="media",
                image="local/image",
                yaml_template="services:\n  app:\n    image: local/image\n",
            )
        )
        await session.commit()

    _patch_http(
        monkeypatch,
        payload={
            "success": True,
            "data": [_make_app_item("jellyfin", compose_template=None)],
        },
    )

    stats = await catalog_sync.sync_apps()

    assert stats == {"created": 0, "updated": 0, "removed": 0, "skipped": 1, "total": 0}
    app = await _get_app(sync_db, "jellyfin")
    assert app.display_name == "本地名称"
    assert app.yaml_template == "services:\n  app:\n    image: local/image\n"


async def test_sync_apps_skips_invalid_template(sync_db, monkeypatch):
    """模板渲染或 Schema 校验失败时跳过该应用。"""
    _patch_http(
        monkeypatch,
        payload={
            "success": True,
            "data": [
                _make_app_item("bad-app", compose_template="services: [{{ broken"),
                _make_app_item("good-app"),
            ],
        },
    )

    stats = await catalog_sync.sync_apps()

    assert stats == {"created": 1, "updated": 0, "removed": 0, "skipped": 1, "total": 1}
    assert await _get_app(sync_db, "bad-app") is None
    assert await _get_app(sync_db, "good-app") is not None


async def test_sync_apps_removes_apps_missing_from_remote(sync_db, monkeypatch):
    """管理端已下架（远端不再返回）的非内置应用会从本地缓存删除。"""
    async with sync_db() as session:
        session.add(
            App(
                name="unpublished",
                display_name="已下架",
                category="other",
                yaml_template="services:\n  app:\n    image: local/image\n",
            )
        )
        await session.commit()

    _patch_http(monkeypatch, payload={"success": True, "data": [_make_app_item("jellyfin")]})

    stats = await catalog_sync.sync_apps()

    assert stats == {"created": 1, "updated": 0, "removed": 1, "skipped": 0, "total": 1}
    assert await _get_app(sync_db, "unpublished") is None
    assert await _get_app(sync_db, "jellyfin") is not None


async def test_sync_apps_keeps_builtin_and_deployed_apps_missing_from_remote(
    sync_db, monkeypatch
):
    """内置应用与仍有部署实例的应用不会因远端缺失被删除。"""
    async with sync_db() as session:
        builtin = App(
            name="moviepilot",
            display_name="MoviePilot",
            category="movie",
            yaml_template="services:\n  app:\n    image: local/moviepilot\n",
            is_builtin=True,
        )
        deployed = App(
            name="jellyfin",
            display_name="Jellyfin",
            category="movie",
            yaml_template="services:\n  app:\n    image: local/jellyfin\n",
        )
        session.add_all([builtin, deployed])
        await session.flush()
        session.add(AppInstance(instance_name="jellyfin", app_id=deployed.id))
        await session.commit()

    _patch_http(monkeypatch, payload={"success": True, "data": [_make_app_item("komga")]})

    stats = await catalog_sync.sync_apps()

    assert stats == {"created": 1, "updated": 0, "removed": 0, "skipped": 0, "total": 1}
    assert await _get_app(sync_db, "moviepilot") is not None
    assert await _get_app(sync_db, "jellyfin") is not None
    assert await _get_app(sync_db, "komga") is not None


async def test_sync_apps_skips_removal_when_remote_list_empty(sync_db, monkeypatch):
    """远端返回空列表时视为异常，不清理本地缓存。"""
    async with sync_db() as session:
        session.add(
            App(
                name="jellyfin",
                display_name="Jellyfin",
                category="movie",
                yaml_template="services:\n  app:\n    image: local/jellyfin\n",
            )
        )
        await session.commit()

    _patch_http(monkeypatch, payload={"success": True, "data": []})

    stats = await catalog_sync.sync_apps()

    assert stats == {"created": 0, "updated": 0, "removed": 0, "skipped": 0, "total": 0}
    assert await _get_app(sync_db, "jellyfin") is not None


async def test_sync_apps_accepts_bare_list_response(sync_db, monkeypatch):
    """防御性解析：响应体直接为列表时也能处理。"""
    _patch_http(monkeypatch, payload=[_make_app_item("jellyfin")])

    stats = await catalog_sync.sync_apps()

    assert stats == {"created": 1, "updated": 0, "removed": 0, "skipped": 0, "total": 1}


async def test_sync_apps_skipped_when_manage_base_url_empty(sync_db, monkeypatch):
    """MANAGE_BASE_URL 为空时跳过同步。"""
    monkeypatch.setattr(settings, "MANAGE_BASE_URL", "")
    _patch_http(monkeypatch, exc=AssertionError("不应发起 HTTP 请求"))

    result = await catalog_sync.sync_apps()

    assert result == {"skipped": True}


async def test_sync_apps_resolves_relative_icon_to_manage_url(
    sync_db, monkeypatch
):
    """管理端上传的图标是相对路径时，补全为可访问的绝对 URL。"""
    _patch_http(
        monkeypatch,
        payload={
            "success": True,
            "data": [_make_app_item("jellyfin", icon="/api/public/apps/1/icon")],
        },
    )

    await catalog_sync.sync_apps()

    app = await _get_app(sync_db, "jellyfin")
    assert app.icon == "http://manage.test/api/public/apps/1/icon"




async def test_sync_claims_local_record_when_slug_renamed(sync_db, monkeypatch):
    """管理端改 slug 时按显示名认领旧记录，不产生重复方案。"""
    async with sync_db() as session:
        session.add(
            AppOrchestration(
                name="music-stack",
                display_name="音乐自动化",
                category="music",
                version="1.1.0",
                is_builtin=False,
                app_composition=[{"app_name": "app-a", "relation": "required"}],
                shared_config_schema={},
            )
        )
        await session.commit()

    _patch_http(
        monkeypatch,
        payload={
            "success": True,
            "data": [
                _make_item(
                    "music-auto",
                    display_name="音乐自动化",
                    category="music",
                    version="1.1.0",
                )
            ],
        },
    )

    stats = await catalog_sync.sync_orchestrations()

    assert stats == {"created": 0, "updated": 1, "removed": 0, "total": 1, "events": 0}
    assert await _get_by_name(sync_db, "music-stack") is None
    renamed = await _get_by_name(sync_db, "music-auto")
    assert renamed is not None
    assert renamed.display_name == "音乐自动化"
    assert renamed.category == "music"


async def test_sync_does_not_steal_remote_duplicate_name(sync_db, monkeypatch):
    """旧 slug 仍在远端返回时不能认领，避免把另一个方案误改名。"""
    async with sync_db() as session:
        session.add(
            AppOrchestration(
                name="old-a",
                display_name="同名方案",
                category="music",
                version="1.0.0",
                is_builtin=False,
                app_composition=[],
                shared_config_schema={},
            )
        )
        await session.commit()

    _patch_http(
        monkeypatch,
        payload={
            "success": True,
            "data": [
                _make_item("old-a", display_name="同名方案", category="music"),
                _make_item("new-b", display_name="同名方案", category="music"),
            ],
        },
    )

    await catalog_sync.sync_orchestrations()

    assert await _get_by_name(sync_db, "old-a") is not None
    assert await _get_by_name(sync_db, "new-b") is not None


async def test_sync_all_runs_each_section_in_order(sync_db, monkeypatch):
    """整体同步依次执行分类、应用市场与编排方案，并汇总结果。"""
    calls: list[object] = []

    async def fake_categories():
        calls.append("categories")
        return {"total": 3}

    async def fake_apps():
        calls.append("apps")
        return {"created": 1, "updated": 0, "removed": 0, "skipped": 0, "total": 1}

    async def fake_orchestrations(mode: str = "auto"):
        calls.append(("orchestrations", mode))
        return {"created": 2, "updated": 0, "removed": 0, "total": 2, "events": 2}

    monkeypatch.setattr(catalog_sync, "sync_categories", fake_categories)
    monkeypatch.setattr(catalog_sync, "sync_apps", fake_apps)
    monkeypatch.setattr(catalog_sync, "sync_orchestrations", fake_orchestrations)

    result = await catalog_sync.sync_all(mode="manual")

    assert calls == ["categories", "apps", ("orchestrations", "manual")]
    assert result["categories"] == {"total": 3}
    assert result["apps"]["total"] == 1
    assert result["orchestrations"]["total"] == 2
    assert result["errors"] == []


async def test_sync_all_keeps_other_sections_when_one_fails(sync_db, monkeypatch):
    """单类同步失败不影响其余部分，失败信息收集到 errors。"""

    async def failing_categories():
        raise RuntimeError("分类接口异常")

    async def fake_apps():
        return {"created": 0, "updated": 1, "removed": 0, "skipped": 0, "total": 1}

    async def fake_orchestrations(mode: str = "auto"):
        return {"created": 0, "updated": 1, "removed": 0, "total": 1, "events": 0}

    monkeypatch.setattr(catalog_sync, "sync_categories", failing_categories)
    monkeypatch.setattr(catalog_sync, "sync_apps", fake_apps)
    monkeypatch.setattr(catalog_sync, "sync_orchestrations", fake_orchestrations)

    result = await catalog_sync.sync_all()

    assert result["categories"] == {"error": "分类接口异常"}
    assert result["apps"]["updated"] == 1
    assert result["orchestrations"]["updated"] == 1
    assert len(result["errors"]) == 1
    assert "应用分类" in result["errors"][0]


async def test_sync_all_skipped_when_manage_base_url_empty(sync_db, monkeypatch):
    """MANAGE_BASE_URL 为空时整体跳过，不执行任何同步。"""
    monkeypatch.setattr(settings, "MANAGE_BASE_URL", "")

    async def fail():  # pragma: no cover - 不应被调用
        raise AssertionError("未配置管理端时不应执行同步")

    monkeypatch.setattr(catalog_sync, "sync_categories", fail)
    monkeypatch.setattr(catalog_sync, "sync_apps", fail)
    monkeypatch.setattr(catalog_sync, "sync_orchestrations", fail)

    result = await catalog_sync.sync_all()

    assert result == {"skipped": True}
