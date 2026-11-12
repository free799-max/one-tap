"""网络加速订阅自动同步调度测试。"""

from types import SimpleNamespace

import pytest

from app.services.acceleration import sync_scheduler


class _Session:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_tick_uses_saved_interval_and_auto_trigger(monkeypatch):
    config = SimpleNamespace(
        acceleration_sync_interval_minutes=45,
        acceleration_auto_sync_enabled=True,
        acceleration_subscription_url="https://manage.example.com/sub/abc",
    )
    db = SimpleNamespace(commit=lambda: None)
    commits = []

    async def commit():
        commits.append(True)

    db.commit = commit

    async def get_config(session):
        assert session is db
        return config

    async def sync(session, *, force, trigger):
        assert session is db
        assert force is False
        assert trigger == "auto"
        return {"detail": {"changed": False}}

    async def status(db):
        return {"installed": True}

    monkeypatch.setattr(sync_scheduler, "async_session", lambda: _Session(db))
    monkeypatch.setattr(
        sync_scheduler.system_config_service, "get_or_create", get_config
    )
    monkeypatch.setattr(sync_scheduler.managed, "sync", sync)
    monkeypatch.setattr(sync_scheduler.mihomo, "status_async", status)

    interval = await sync_scheduler.AccelSyncService().tick()

    assert interval == 45
    assert commits == [True]


@pytest.mark.asyncio
async def test_tick_skips_when_auto_sync_disabled(monkeypatch):
    config = SimpleNamespace(
        acceleration_sync_interval_minutes=15,
        acceleration_auto_sync_enabled=False,
        acceleration_subscription_url="https://manage.example.com/sub/abc",
    )
    db = SimpleNamespace()

    async def get_config(session):
        return config

    async def status(db):  # pragma: no cover - 关闭时不应检查本机状态
        raise AssertionError("关闭自动同步后不应执行同步")

    monkeypatch.setattr(sync_scheduler, "async_session", lambda: _Session(db))
    monkeypatch.setattr(
        sync_scheduler.system_config_service, "get_or_create", get_config
    )
    monkeypatch.setattr(sync_scheduler.mihomo, "status_async", status)

    assert await sync_scheduler.AccelSyncService().tick() == 15
