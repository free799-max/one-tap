"""MoviePilot 分类目录自动创建媒体库测试。"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — 注册所有模型到 Base.metadata
from app.database import Base
from app.models.orchestration import AppOrchestration, AppOrchestrationInstance
from app.services.apps.media_libraries import MediaLibraryInfo
from app.services.orchestration import media_library_sync
from app.services.orchestration.media_library_sync import normalize_auto_config


POLICY = {
    "categories": [
        {
            "id": "movie.animation",
            "media_type": "电影",
            "name": "动画电影",
            "path": ["动画电影"],
            "enabled": True,
        }
    ]
}

DIRECTORIES = [
    {
        "name": "默认",
        "library_storage": "local",
        "library_path": "/mnt/movie/media",
        "library_type_folder": True,
        "library_category_folder": True,
        "media_type": None,
        "media_category_id": "movie.animation",
    }
]

TARGET_PATH = "/mnt/movie/media/电影/动画电影"


@pytest_asyncio.fixture(name="session_local")
async def _session_local(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    monkeypatch.setattr(media_library_sync, "async_session", session_factory)
    yield session_factory
    await engine.dispose()


async def _seed_group(session_local, app_configs: dict) -> int:
    async with session_local() as db:
        orchestration = AppOrchestration(
            name="movie-auto",
            display_name="影视自动化",
            category="movie",
            app_composition=[],
            shared_config_schema={},
        )
        db.add(orchestration)
        await db.flush()
        group = AppOrchestrationInstance(
            orchestration_id=orchestration.id,
            instance_name="movie-auto",
            status="running",
            app_configs=app_configs,
        )
        db.add(group)
        await db.flush()
        await db.commit()
        return group.id


class _FakeMoviePilot:
    existing_dirs = {TARGET_PATH}

    def __init__(self) -> None:
        self.exists_calls: list[str] = []

    async def get_setting(self, _url, _key, setting):
        assert setting == "Directories"
        return True, list(DIRECTORIES), None

    async def get_classification_policy(self, _url, _key):
        return True, POLICY, None

    async def storage_dir_exists(self, _url, _key, path, cache=None):
        self.exists_calls.append(path)
        return True, path in self.existing_dirs, None


def _base_app_configs() -> dict:
    return {
        "moviepilot": {"url": "http://mp:3000", "api_key": "mp-key"},
        "jellyfin": {"url": "http://jf:8096", "api_key": "jf-key"},
        "emby": {"url": "http://emby:8096", "api_key": "emby-key"},
    }


@pytest.mark.asyncio
async def test_sync_group_creates_missing_libraries_on_both_servers(
    session_local, monkeypatch
):
    group_id = await _seed_group(session_local, _base_app_configs())
    created: list[tuple[str, str]] = []

    monkeypatch.setattr(
        media_library_sync,
        "get_client",
        lambda _name: _FakeMoviePilot,
    )

    async def fake_list(url, _key):
        return True, [], None

    async def fake_create(url, _key, *, name, collection_type, path):
        created.append((url, path))
        assert name == "动画电影"
        assert collection_type == "movies"
        return True, None

    monkeypatch.setattr(media_library_sync, "list_media_libraries", fake_list)
    monkeypatch.setattr(media_library_sync, "create_media_library", fake_create)

    count = await media_library_sync.MediaLibrarySyncService().sync_group(group_id)

    assert count == 2
    assert created == [
        ("http://jf:8096", TARGET_PATH),
        ("http://emby:8096", TARGET_PATH),
    ]


@pytest.mark.asyncio
async def test_sync_group_reuses_existing_or_covering_library(
    session_local, monkeypatch
):
    group_id = await _seed_group(session_local, _base_app_configs())
    created: list[str] = []

    monkeypatch.setattr(
        media_library_sync,
        "get_client",
        lambda _name: _FakeMoviePilot,
    )

    async def fake_list(url, _key):
        if url == "http://jf:8096":
            return (
                True,
                [
                    MediaLibraryInfo(
                        id="1",
                        name="电影",
                        paths=("/mnt/movie/media/电影",),
                        collection_type="movies",
                    )
                ],
                None,
            )
        return True, [], None

    async def fake_create(url, _key, **_kwargs):
        created.append(url)
        return True, None

    monkeypatch.setattr(media_library_sync, "list_media_libraries", fake_list)
    monkeypatch.setattr(media_library_sync, "create_media_library", fake_create)

    count = await media_library_sync.MediaLibrarySyncService().sync_group(group_id)

    assert count == 1
    assert created == ["http://emby:8096"]


@pytest.mark.asyncio
async def test_sync_group_skips_missing_directories(session_local, monkeypatch):
    group_id = await _seed_group(session_local, _base_app_configs())
    created: list[str] = []
    fake_mp = _FakeMoviePilot()
    fake_mp.existing_dirs = set()

    monkeypatch.setattr(
        media_library_sync,
        "get_client",
        lambda _name: lambda: fake_mp,
    )

    async def fake_list(_url, _key):
        return True, [], None

    async def fake_create(url, _key, **_kwargs):
        created.append(url)
        return True, None

    monkeypatch.setattr(media_library_sync, "list_media_libraries", fake_list)
    monkeypatch.setattr(media_library_sync, "create_media_library", fake_create)

    count = await media_library_sync.MediaLibrarySyncService().sync_group(group_id)

    assert count == 0
    assert created == []
    assert len(fake_mp.exists_calls) == 1


@pytest.mark.asyncio
async def test_one_server_failure_does_not_block_other_server(
    session_local, monkeypatch
):
    group_id = await _seed_group(session_local, _base_app_configs())
    created: list[str] = []

    monkeypatch.setattr(
        media_library_sync,
        "get_client",
        lambda _name: _FakeMoviePilot,
    )

    async def fake_list(url, _key):
        if url == "http://jf:8096":
            return False, [], "认证失败"
        return True, [], None

    async def fake_create(url, _key, **_kwargs):
        created.append(url)
        return True, None

    monkeypatch.setattr(media_library_sync, "list_media_libraries", fake_list)
    monkeypatch.setattr(media_library_sync, "create_media_library", fake_create)

    count = await media_library_sync.MediaLibrarySyncService().sync_group(group_id)

    assert count == 1
    assert created == ["http://emby:8096"]


@pytest.mark.asyncio
async def test_scheduler_start_and_stop_is_idempotent():
    service = media_library_sync.MediaLibrarySyncService()

    service.start_scheduler(3600)
    service.start_scheduler(3600)
    assert service.scheduler_status()["running"] is True

    await service.stop_scheduler()
    assert service.scheduler_status()["running"] is False


def test_normalize_auto_config_defaults_and_bounds():
    defaults = normalize_auto_config(None)
    assert defaults["enabled"] is True
    assert defaults["interval_minutes"] == 24 * 60

    config = normalize_auto_config(
        {
            "enabled": "false",
            "interval_minutes": 0,
            "last_run_at": "2026-11-17T00:00:00+00:00",
            "last_result": {"created": 2},
        }
    )
    assert config["enabled"] is False
    assert config["interval_minutes"] == 1
    assert config["last_result"] == {"created": 2}


@pytest.mark.asyncio
async def test_disabled_auto_sync_skips_all_remote_calls(
    session_local, monkeypatch
):
    app_configs = _base_app_configs()
    app_configs["moviepilot"]["media_library_sync"] = {
        "enabled": False,
        "interval_minutes": 1,
    }
    group_id = await _seed_group(session_local, app_configs)
    fake_mp = _FakeMoviePilot()

    monkeypatch.setattr(
        media_library_sync,
        "get_client",
        lambda _name: lambda: fake_mp,
    )

    count = await media_library_sync.MediaLibrarySyncService().sync_group(group_id)

    assert count == 0
    assert fake_mp.exists_calls == []


@pytest.mark.asyncio
async def test_sync_group_persists_last_run_status(session_local, monkeypatch):
    group_id = await _seed_group(session_local, _base_app_configs())

    monkeypatch.setattr(
        media_library_sync,
        "get_client",
        lambda _name: _FakeMoviePilot,
    )

    async def fake_list(_url, _key):
        return True, [], None

    async def fake_create(_url, _key, **_kwargs):
        return True, None

    monkeypatch.setattr(media_library_sync, "list_media_libraries", fake_list)
    monkeypatch.setattr(media_library_sync, "create_media_library", fake_create)

    await media_library_sync.MediaLibrarySyncService().sync_group(group_id)

    async with session_local() as db:
        group = await db.get(AppOrchestrationInstance, group_id)
        config = group.app_configs["moviepilot"]["media_library_sync"]

    assert config["last_run_at"]
    assert config["last_result"]["targets"] == 1
    assert config["last_result"]["present_directories"] == 1
    assert config["last_result"]["created"] == 2
    assert config["last_result"]["servers"] == [
        {"name": "jellyfin", "created": 1, "error": None},
        {"name": "emby", "created": 1, "error": None},
    ]
