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
async def test_tick_clears_rules_when_license_invalid(monkeypatch):
    """授权无效时停止同步，并清空订阅与已下发的加速规则。"""
    config = SimpleNamespace(
        acceleration_sync_interval_minutes=30,
        acceleration_auto_sync_enabled=True,
        acceleration_subscription_url="https://manage.example.com/sub/abc",
        acceleration_managed_rules=[{"kind": "DOMAIN", "value": "api.github.com"}],
        acceleration_rules_synced_at=None,
    )
    db = SimpleNamespace()
    commits: list[bool] = []
    cleared: list[bool] = []

    async def commit():
        commits.append(True)

    db.commit = commit

    async def get_config(session):
        return config

    async def allowed(session):
        return False

    async def stop_and_clear(session):
        cleared.append(True)
        config.acceleration_subscription_url = ""
        config.acceleration_managed_rules = []
        config.acceleration_rules_synced_at = None
        return True

    async def sync(session, *, force, trigger):  # pragma: no cover - 不应触发
        raise AssertionError("授权无效时不应同步订阅")

    monkeypatch.setattr(sync_scheduler, "async_session", lambda: _Session(db))
    monkeypatch.setattr(
        sync_scheduler.system_config_service, "get_or_create", get_config
    )
    monkeypatch.setattr(
        sync_scheduler.licensing_service, "acceleration_allowed", allowed
    )
    monkeypatch.setattr(sync_scheduler.managed, "stop_and_clear", stop_and_clear)
    monkeypatch.setattr(sync_scheduler.managed, "sync", sync)

    assert await sync_scheduler.AccelSyncService().tick() == 30
    assert cleared == [True]
    assert commits == [True]
    assert config.acceleration_subscription_url == ""
    assert config.acceleration_managed_rules == []


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
