"""安装上报：启动与定时向管理端登记本机安装，用于统计安装与使用情况。

每台机器按 installation_id 去重，一台机器只算一个安装；上报不依赖授权状态，
免费版与授权版都会上报，授权版由管理端根据激活记录自动识别。
"""

from __future__ import annotations

import asyncio
import logging
import platform
import socket

import httpx

from app.config import settings
from app.database import async_session
from app.services.licensing import licensing_service
from app.services.manage_client import (
    ManageRequestError,
    manage_base_urls,
    with_manage_fallback,
)

logger = logging.getLogger(__name__)

REPORT_INTERVAL_SECONDS = 24 * 3600
"""安装上报间隔：每天一次。"""


def _trim(value: str | None, limit: int) -> str | None:
    if not value:
        return None
    return value[:limit]


def _device_platform() -> str | None:
    name = platform.system() or ""
    release = platform.release() or ""
    return _trim(" ".join(part for part in (name, release) if part), 100)


class InstallationReporter:
    """安装上报调度器：启动时上报一次，之后每天上报一次。"""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop: asyncio.Event | None = None

    async def report_once(self) -> bool:
        """向管理端上报一次本机安装，成功返回 True，未配置或失败返回 False。"""
        if not manage_base_urls():
            return False
        async with async_session() as db:
            state = await licensing_service.get_state(db)
            await db.commit()
            payload = {
                "installation_id": state.installation_id,
                "device_name": _trim(
                    state.device_name or socket.gethostname(), 100
                ),
                "hostname": _trim(socket.gethostname(), 255),
                "platform": _device_platform(),
                "app_version": _trim(settings.APP_VERSION, 50),
            }

        async def fetch(base_url: str) -> httpx.Response:
            async with httpx.AsyncClient(
                timeout=settings.LICENSE_HTTP_TIMEOUT_SECONDS
            ) as client:
                return await client.post(
                    f"{base_url}/api/public/installations/report", json=payload
                )

        try:
            response, base_url = await with_manage_fallback(fetch)
        except ManageRequestError as exc:
            logger.warning("安装上报失败：%s", exc)
            return False
        if response.status_code >= 400:
            logger.warning(
                "安装上报被 %s 拒绝：HTTP %s", base_url, response.status_code
            )
            return False
        logger.info("安装上报成功：%s", base_url)
        return True

    def start_scheduler(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._scheduler_loop())

    async def stop_scheduler(self) -> None:
        if self._stop is not None:
            self._stop.set()
        task = self._task
        self._task = None
        self._stop = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _scheduler_loop(self) -> None:
        while True:
            await self._report_tick()
            stop = self._stop
            if stop is None:
                return
            try:
                await asyncio.wait_for(stop.wait(), timeout=REPORT_INTERVAL_SECONDS)
                return
            except asyncio.TimeoutError:
                pass

    async def _report_tick(self) -> None:
        try:
            await self.report_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 上报失败不影响主流程
            logger.warning("安装上报异常：%s", exc)


installation_reporter = InstallationReporter()
