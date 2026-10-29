"""OneTap 内音乐自动整理（Music Tag Web）测试。"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — 注册所有模型到 Base.metadata
from app.core.exceptions import APIException
from app.database import Base
from app.models.app_store import App
from app.models.orchestration import (
    AppInstance,
    AppOrchestration,
    AppOrchestrationInstance,
    MusicAutoOrganizeFile,
)
from app.services.apps.music_tag_web.client import (
    AUDIT_ACTION_SCRAPE,
    MusicTagWebSession,
    scrape_succeeded,
    tidy_succeeded,
)
from app.services.orchestration.music_auto import (
    MusicAutoOrganizeService,
    normalize_config,
    validate_config,
)


@pytest_asyncio.fixture(name="session_local")
async def _session_local(monkeypatch):
    """内存 SQLite 会话工厂，接管 music_auto 模块的 async_session。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    monkeypatch.setattr(
        "app.services.orchestration.music_auto.async_session", SessionLocal
    )
    yield SessionLocal
    await engine.dispose()


async def _seed_group(session_local, app_configs: dict | None = None) -> int:
    async with session_local() as db:
        orch = AppOrchestration(
            name="music-stack",
            display_name="音乐自动化",
            category="music",
            app_composition=[],
            shared_config_schema={},
        )
        db.add(orch)
        await db.flush()
        group = AppOrchestrationInstance(
            orchestration_id=orch.id,
            instance_name="music-auto",
            status="running",
            app_configs=app_configs or {},
        )
        db.add(group)
        await db.flush()

        app = App(
            name="music-tag-web",
            display_name="Music Tag Web",
            yaml_template="services: {}",
        )
        db.add(app)
        await db.flush()
        db.add(
            AppInstance(
                app_id=app.id,
                orchestration_group_id=group.id,
                instance_name="music-auto-music-tag-web",
                config={
                    "volumes": [
                        {"container_path": "/app/data"},
                        {"container_path": "/app/media"},
                        {"container_path": "/mnt/music"},
                        {"container_path": "/app/media/downloads"},
                        {"container_path": "/app/media/music"},
                    ]
                },
                status="running",
            )
        )
        await db.commit()
        return group.id


def test_normalize_config_fills_defaults_and_filters_empty_lists():
    config = normalize_config(
        {
            "enabled": "true",
            "interval_minutes": 0,
            "watch_dir": "/app/downloads/",
            "library_dir": "/app/library",
            "source_list": [],
            "dir_list": [{"value": "artist"}],
        }
    )

    assert config["enabled"] is True
    assert config["interval_minutes"] == 1
    assert config["watch_dir"] == "/app/downloads"
    assert config["library_dir"] == "/app/library"
    assert config["source_list"]
    assert config["dir_list"] == [{"value": "artist", "value2": ""}]


def test_validate_config_rejects_nested_or_disabled_work():
    with pytest.raises(APIException):
        validate_config(
            normalize_config(
                {"watch_dir": "/app/media", "library_dir": "/app/media/music"}
            )
        )
    with pytest.raises(APIException):
        validate_config(
            normalize_config(
                {
                    "scrape_enabled": False,
                    "tidy_enabled": False,
                }
            )
        )
    with pytest.raises(APIException):
        validate_config(
            normalize_config(
                {"watch_dir": "downloads", "library_dir": "/app/music"}
            )
        )


def test_audit_result_parsing():
    ok, detail = scrape_succeeded(
        {
            "status": "success",
            "extra": "刮削完成,成功:1,匹配:1,待候选:0,失败:0",
        }
    )
    assert ok is True
    assert "成功:1" in detail

    failed, _ = scrape_succeeded(
        {
            "status": "success",
            "extra": "刮削完成,成功:0,匹配:0,待候选:0,失败:1",
        }
    )
    assert failed is False

    assert tidy_succeeded({"status": "success", "extra": "整理文件夹完成"})[0]


@pytest.mark.asyncio
async def test_session_walk_and_payloads(monkeypatch):
    session = MusicTagWebSession("http://mtw", "token")
    calls: list[tuple[str, str, dict | None]] = []

    async def fake_json(method, path, payload=None):
        calls.append((method, path, payload))
        return {"result": True}

    tree = {
        "/app/media/downloads": [
            {
                "name": "Artist",
                "icon": "icon-folder",
                "children_count": 1,
            },
            {"name": "cover.jpg", "icon": "icon-image", "size": 10},
        ],
        "/app/media/downloads/Artist": [
            {
                "name": "song.mp3",
                "icon": "icon-script-file",
                "size": 120,
                "update_time": "2026-10-24 10:00:00",
            }
        ],
    }

    async def fake_list(directory):
        return tree.get(directory, [])

    monkeypatch.setattr(session, "_json", fake_json)
    monkeypatch.setattr(session, "list_children", fake_list)

    files = await session.walk_audio_files("/app/media/downloads")
    assert files == [
        {
            "path": "/app/media/downloads/Artist/song.mp3",
            "name": "song.mp3",
            "directory": "/app/media/downloads/Artist",
            "size": 120,
            "mtime": "2026-10-24 10:00:00",
        }
    ]

    await session.scrape_file(
        directory="/app/media/downloads/Artist",
        filename="song.mp3",
        sources=("netease",),
        modify_fields=("title", "artist"),
    )
    method, path, payload = calls[-1]
    assert (method, path) == ("POST", "/apimt/batch_auto_update_id3/")
    assert payload["select_data"][0]["name"] == "song.mp3"
    assert payload["music_info"]["source_list"] == ["netease"]

    await session.tidy_file(
        directory="/app/media/downloads/Artist",
        filename="song.mp3",
        root_path="/app/media/music",
        dir_list=[{"value": "artist", "value2": ""}],
    )
    method, path, payload = calls[-1]
    assert (method, path) == ("POST", "/apimt/tidy_folder/")
    assert payload["root_path"] == "/app/media/music"
    await session.aclose()


@pytest.mark.asyncio
async def test_panel_uses_detected_directories(session_local):
    group_id = await _seed_group(session_local)
    service = MusicAutoOrganizeService()

    async with session_local() as db:
        panel = await service.get_panel(db, group_id)

    assert panel["supported"] is True
    assert panel["config"]["watch_dir"] == "/app/media/downloads"
    assert panel["config"]["library_dir"] == "/app/media/music"
    assert panel["detected"] == {
        "watch_dir": "/app/media/downloads",
        "library_dir": "/app/media/music",
    }
    assert panel["config"]["enabled"] is False


@pytest.mark.asyncio
async def test_save_config_persists_and_keeps_last_run(session_local):
    group_id = await _seed_group(
        session_local,
        {
            "music-tag-web": {
                "url": "http://mtw",
                "username": "admin",
                "password": "secret",
                "auto_organize": {
                    "last_run_at": "2026-10-24T10:00:00",
                    "last_result": {"status": "ok", "message": "旧结果"},
                },
            }
        },
    )
    service = MusicAutoOrganizeService()

    async with session_local() as db:
        panel = await service.save_config(
            db,
            group_id,
            {
                "enabled": True,
                "watch_dir": "/app/media/downloads",
                "library_dir": "/app/media/music",
                "interval_minutes": 10,
            },
        )

    assert panel["config"]["enabled"] is True
    assert panel["config"]["interval_minutes"] == 10
    assert panel["config"]["last_result"] == {
        "status": "ok",
        "message": "旧结果",
    }
    async with session_local() as db:
        group = await db.get(AppOrchestrationInstance, group_id)
        entry = group.app_configs["music-tag-web"]
        assert entry["username"] == "admin"
        assert entry["auto_organize"]["enabled"] is True
        # 保存后清掉 last_run_at，让定时调度立即按新配置运行
        assert "last_run_at" not in entry["auto_organize"]


@pytest.mark.asyncio
async def test_plan_files_processes_stable_new_files_and_skips_fresh(session_local):
    group_id = await _seed_group(session_local)
    service = MusicAutoOrganizeService()
    now = datetime.now()
    utc_now = datetime.now(timezone.utc)
    old_mtime = (utc_now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    fresh_mtime = utc_now.strftime("%Y-%m-%d %H:%M:%S")
    files = [
        {
            "path": "/app/media/downloads/Artist/new.mp3",
            "name": "new.mp3",
            "directory": "/app/media/downloads/Artist",
            "size": 100,
            "mtime": old_mtime,
        },
        {
            "path": "/app/media/downloads/Artist/fresh.mp3",
            "name": "fresh.mp3",
            "directory": "/app/media/downloads/Artist",
            "size": 150,
            "mtime": fresh_mtime,
        },
        {
            "path": "/app/media/downloads/Artist/done.mp3",
            "name": "done.mp3",
            "directory": "/app/media/downloads/Artist",
            "size": 200,
            "mtime": old_mtime,
        },
        {
            "path": "/app/media/downloads/Artist/stable.mp3",
            "name": "stable.mp3",
            "directory": "/app/media/downloads/Artist",
            "size": 300,
            "mtime": old_mtime,
        },
    ]
    async with session_local() as db:
        db.add_all(
            [
                MusicAutoOrganizeFile(
                    group_id=group_id,
                    path=files[2]["path"],
                    size=200,
                    mtime=files[2]["mtime"],
                    status="done",
                ),
                MusicAutoOrganizeFile(
                    group_id=group_id,
                    path=files[3]["path"],
                    size=300,
                    mtime=files[3]["mtime"],
                    status="pending",
                    first_seen_at=now - timedelta(minutes=5),
                ),
            ]
        )
        await db.commit()

    config = normalize_config({"min_age_seconds": 60})
    candidates = await service._plan_files(
        group_id, files, config, force=False, tz_name="UTC"
    )

    # 已经稳定的新文件本轮就处理；刚出现/仍在写入的文件先等一轮
    assert [item["name"] for item in candidates] == ["new.mp3", "stable.mp3"]
    async with session_local() as db:
        state = (
            await db.execute(
                MusicAutoOrganizeFile.__table__.select().where(
                    MusicAutoOrganizeFile.path == files[1]["path"]
                )
            )
        ).first()
        assert state.status == "pending"

    # 手动触发时允许立即处理刚发现的文件
    candidates = await service._plan_files(
        group_id, files, config, force=True, tz_name="UTC"
    )
    assert {item["name"] for item in candidates} == {
        "new.mp3",
        "fresh.mp3",
        "stable.mp3",
    }


class _FakeSession:
    """模拟 Music Tag Web 会话，验证自动整理编排流程。"""

    def __init__(self) -> None:
        self.scraped: list[str] = []
        self.tidied: list[str] = []
        self._audit_id = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def walk_audio_files(self, *_args, **_kwargs):
        return [
            {
                "path": "/app/media/downloads/Artist/song.mp3",
                "name": "song.mp3",
                "directory": "/app/media/downloads/Artist",
                "size": 100,
                "mtime": "2026-10-24 10:00:00",
            }
        ]

    async def latest_audit_id(self):
        return self._audit_id

    async def scrape_file(self, **_kwargs):
        self.scraped.append(_kwargs["filename"])
        self._audit_id += 1

    async def tidy_file(self, **_kwargs):
        self.tidied.append(_kwargs["filename"])
        self._audit_id += 1

    async def wait_audit(self, action, after_id, _timeout, **_kwargs):
        if action == AUDIT_ACTION_SCRAPE:
            return {
                "id": after_id + 1,
                "status": "success",
                "extra": "刮削完成,成功:1,匹配:1,待候选:0,失败:0",
            }
        return {
            "id": after_id + 1,
            "status": "success",
            "extra": "整理文件夹完成",
        }


class _FakeClient:
    session = _FakeSession()

    async def open_organize_session(self, *_args, **_kwargs):
        return self.session


@pytest.mark.asyncio
async def test_run_group_scrapes_tidies_and_persists_result(
    session_local, monkeypatch
):
    group_id = await _seed_group(
        session_local,
        {
            "music-tag-web": {
                "url": "http://mtw",
                "username": "admin",
                "password": "secret",
                "auto_organize": {
                    "enabled": True,
                    "watch_dir": "/app/media/downloads",
                    "library_dir": "/app/media/music",
                    "min_age_seconds": 0,
                    "navidrome_scan": False,
                },
            }
        },
    )
    fake = _FakeClient()
    monkeypatch.setattr(
        "app.services.orchestration.music_auto.get_client",
        lambda _name: (lambda: fake),
    )
    service = MusicAutoOrganizeService()

    await service._run_group(group_id, "manual", force=True)

    assert fake.session.scraped == ["song.mp3"]
    assert fake.session.tidied == ["song.mp3"]
    async with session_local() as db:
        state = (
            await db.execute(
                MusicAutoOrganizeFile.__table__.select().where(
                    MusicAutoOrganizeFile.group_id == group_id
                )
            )
        ).first()
        assert state.status == "done"
        group = await db.get(AppOrchestrationInstance, group_id)
        last = group.app_configs["music-tag-web"]["auto_organize"]["last_result"]
        assert last["status"] == "ok"
        assert last["processed"] == 1
        assert "last_run_at" in group.app_configs["music-tag-web"]["auto_organize"]
