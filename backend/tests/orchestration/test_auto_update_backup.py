"""自动升级前应用数据目录快照的测试。"""

import json

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.database import Base
from app.services.orchestration.backup import (
    AppDataEntry,
    snapshot_app_data_dirs,
)
from app.services.system.storage_directory_service import storage_directory_service


@pytest_asyncio.fixture(name="db")
async def _db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    SessionLocal = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with SessionLocal() as session:
        yield session

    await engine.dispose()


def _set_app_data_root(monkeypatch, root):
    async def fake_pair(db):
        return (str(root), str(root))

    monkeypatch.setattr(storage_directory_service, "get_app_data_pair", fake_pair)


def _entry(root):
    instance_dir = root / "test-stack"
    return AppDataEntry(
        instance_name="test-stack-moviepilot",
        app_name="moviepilot",
        project_name="test-stack-moviepilot",
        config={"volumes": [{"host_path": str(instance_dir / "moviepilot")}]},
    )


@pytest.mark.asyncio
async def test_snapshot_copies_without_moving_data(db, monkeypatch, tmp_path):
    """快照是复制：原目录保留，清单标记为自动升级备份。"""
    _set_app_data_root(monkeypatch, tmp_path)
    data_file = tmp_path / "test-stack" / "moviepilot" / "data" / "db.sqlite"
    data_file.parent.mkdir(parents=True)
    data_file.write_text("v1")

    snapshot = await snapshot_app_data_dirs(
        db, [_entry(tmp_path)], "test-stack", keep=2
    )

    assert snapshot is not None
    assert snapshot.parent.name == "auto-update"
    assert data_file.read_text() == "v1"
    copied = snapshot / "test-stack" / "moviepilot" / "data" / "db.sqlite"
    assert copied.read_text() == "v1"

    manifest = json.loads((snapshot / "backup.json").read_text())
    assert manifest["auto"] is True
    assert manifest["instance_name"] == "test-stack"
    assert "orchestration" not in manifest
    assert manifest["apps"][0]["path"] == "test-stack"


@pytest.mark.asyncio
async def test_snapshot_keeps_only_requested_count(db, monkeypatch, tmp_path):
    """超过保留份数的旧快照会被清理。"""
    _set_app_data_root(monkeypatch, tmp_path)
    data_file = tmp_path / "test-stack" / "moviepilot" / "db.sqlite"
    data_file.parent.mkdir(parents=True)
    data_file.write_text("v1")

    for _ in range(3):
        await snapshot_app_data_dirs(db, [_entry(tmp_path)], "test-stack", keep=2)

    backups = tmp_path / "_backups" / "auto-update"
    assert len([item for item in backups.iterdir() if item.is_dir()]) == 2
    assert data_file.exists()


@pytest.mark.asyncio
async def test_snapshot_skips_when_no_app_data_dir(monkeypatch, tmp_path):
    """未配置应用数据目录时不备份，也不报错。"""

    async def no_pair(db):
        return None

    monkeypatch.setattr(storage_directory_service, "get_app_data_pair", no_pair)
    assert await snapshot_app_data_dirs(None, [], "test-stack") is None
