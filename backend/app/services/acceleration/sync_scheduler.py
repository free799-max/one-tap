"""本机 Mihomo 订阅自动同步。

只负责把管理端下发的订阅拉取到本机：内容没有变化时不重启容器，
失败只记录状态，不影响其它功能。
"""

from __future__ import annotations

import asyncio
import logging

from app.database import async_session
from app.services.acceleration import managed
from app.services.acceleration import mihomo
from app.services.system.config_service import system_config_service

logger = logging.getLogger(__name__)

DEFAULT_SYNC_INTERVAL_MINUTES = 30


class AccelSyncService:
    """订阅自动同步调度器。"""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop: asyncio.Event | None = None
        self._wake: asyncio.Event | None = None

    def start_scheduler(self) -> None:
        """启动后台循环（幂等）。"""
        if self._task is not None and not self._task.done():
            return
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        self._task = asyncio.create_task(self._loop())
        logger.info("网络加速订阅同步调度器已启动")

    async def stop_scheduler(self) -> None:
        if self._stop is not None:
            self._stop.set()
        if self._wake is not None:
            self._wake.set()
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def wake(self) -> None:
        if self._wake is not None:
            self._wake.set()

    async def _loop(self) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=60)
            return
        except asyncio.TimeoutError:
            pass
        while self._stop is not None and not self._stop.is_set():
            if self._wake is not None:
                self._wake.clear()
            interval = DEFAULT_SYNC_INTERVAL_MINUTES
            try:
                interval = await self.tick()
            except Exception as exc:  # noqa: BLE001 - 后台任务不能因单次失败退出
                logger.warning("网络加速订阅同步失败：%s", exc)
            if self._stop is not None and self._stop.is_set():
                return
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=interval * 60)
            except asyncio.TimeoutError:
                continue

    async def tick(self) -> int:
        """按数据库配置同步一次，并返回下一次检查间隔。"""
        async with async_session() as db:
            config = await system_config_service.get_or_create(db)
            interval = int(config.acceleration_sync_interval_minutes)
            if not config.acceleration_auto_sync_enabled:
                return interval
            if not config.acceleration_subscription_url:
                return interval
            status = await mihomo.status_async(db)
            if not status.get("installed"):
                return interval
            result = await managed.sync(db, force=False, trigger="auto")
            await db.commit()
            if result["detail"].get("changed"):
                logger.info("已自动同步网络加速订阅")
            return interval


accel_sync_service = AccelSyncService()
