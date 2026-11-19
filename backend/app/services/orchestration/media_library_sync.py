"""按 MoviePilot 已创建的分类目录自动补齐 Jellyfin/Emby 媒体库。"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.database import async_session
from app.models.orchestration import AppOrchestrationInstance
from app.services.apps import get_client
from app.services.apps.media_libraries import (
    create_media_library,
    list_media_libraries,
    path_is_covered,
)
from app.services.orchestration.media_library_targets import build_library_targets

logger = logging.getLogger(__name__)

MEDIA_SERVER_APPS = ("jellyfin", "emby")
SYNC_INTERVAL_SECONDS = 60.0
AUTO_CONFIG_KEY = "media_library_sync"
DEFAULT_INTERVAL_MINUTES = 24 * 60


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off"}
    return default


def _as_interval(value: Any, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(1440, number))


def normalize_auto_config(value: Any) -> dict[str, Any]:
    """归一化自动建库配置，并保留最近运行状态。"""
    source = value if isinstance(value, dict) else {}
    config: dict[str, Any] = {
        "enabled": _as_bool(source.get("enabled"), True),
        "interval_minutes": _as_interval(
            source.get("interval_minutes"), DEFAULT_INTERVAL_MINUTES
        ),
        "last_run_at": None,
        "last_result": None,
    }
    last_run_at = str(source.get("last_run_at") or "").strip()
    if last_run_at:
        config["last_run_at"] = last_run_at
    if isinstance(source.get("last_result"), dict):
        config["last_result"] = dict(source["last_result"])
    return config


def auto_config_from_app_configs(app_configs: dict | None) -> dict[str, Any]:
    """从实例组配置读取自动建库配置。"""
    moviepilot = (app_configs or {}).get("moviepilot")
    raw = moviepilot.get(AUTO_CONFIG_KEY) if isinstance(moviepilot, dict) else None
    return normalize_auto_config(raw)


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_due(config: dict[str, Any], now: datetime) -> bool:
    last_run = _parse_time(config.get("last_run_at"))
    if last_run is None:
        return True
    return now - last_run >= timedelta(minutes=config["interval_minutes"])


class MediaLibrarySyncService:
    """定时检测 MoviePilot 目录并创建缺失的媒体库。"""

    def __init__(self) -> None:
        self._scheduler_task: asyncio.Task | None = None
        self._scheduler_stop: asyncio.Event | None = None
        self._tick_lock = asyncio.Lock()

    def start_scheduler(self, interval_seconds: float = SYNC_INTERVAL_SECONDS) -> None:
        """启动后台循环；首轮等待一个间隔，避免与启动装配并发。"""
        if self._scheduler_task is not None and not self._scheduler_task.done():
            return
        self._scheduler_stop = asyncio.Event()
        self._scheduler_task = asyncio.create_task(
            self._scheduler_loop(interval_seconds)
        )
        logger.info("媒体库自动同步调度器已启动（检查间隔 %.0f 秒）", interval_seconds)

    async def stop_scheduler(self) -> None:
        """停止后台循环。"""
        if self._scheduler_stop is not None:
            self._scheduler_stop.set()
        task = self._scheduler_task
        self._scheduler_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def scheduler_status(self) -> dict[str, Any]:
        """返回调度器运行状态，供排查使用。"""
        return {
            "running": bool(
                self._scheduler_task is not None
                and not self._scheduler_task.done()
            ),
            "syncing": self._tick_lock.locked(),
        }

    async def _scheduler_loop(self, interval_seconds: float) -> None:
        while True:
            stop = self._scheduler_stop
            try:
                if stop is not None:
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
                        return
                    except asyncio.TimeoutError:
                        pass
                else:
                    await asyncio.sleep(interval_seconds)
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("媒体库自动同步检查失败")

    async def tick(self) -> None:
        """扫描所有实例组；上一轮未结束时跳过本轮。"""
        if self._tick_lock.locked():
            return
        async with self._tick_lock:
            async with async_session() as db:
                groups = list(
                    (await db.execute(select(AppOrchestrationInstance))).scalars()
                )
            now = datetime.now(timezone.utc)
            for group in groups:
                config = auto_config_from_app_configs(group.app_configs)
                if not config["enabled"] or not _is_due(config, now):
                    continue
                try:
                    await self.sync_group(group.id)
                except Exception:
                    logger.exception("实例组 %s 自动创建媒体库失败", group.id)

    async def sync_group(self, group_id: int) -> int:
        """同步一个实例组，返回本轮新建的媒体库数量。"""
        async with async_session() as db:
            group = await db.get(AppOrchestrationInstance, group_id)
            if group is None:
                return 0
            app_configs = dict(group.app_configs or {})

        moviepilot = app_configs.get("moviepilot")
        if not isinstance(moviepilot, dict):
            return 0
        config = normalize_auto_config(moviepilot.get(AUTO_CONFIG_KEY))
        if not config["enabled"]:
            return 0
        mp_url = str(moviepilot.get("url") or "").rstrip("/")
        mp_key = str(moviepilot.get("api_key") or "")
        if not mp_url or not mp_key:
            return 0

        servers = [
            (name, server_config)
            for name in MEDIA_SERVER_APPS
            if isinstance((server_config := app_configs.get(name)), dict)
            and str(server_config.get("url") or "").strip()
            and str(server_config.get("api_key") or "").strip()
        ]
        if not servers:
            await self._save_run_status(
                group_id,
                message="实例组中没有可用的 Jellyfin 或 Emby",
            )
            return 0

        client = get_client("moviepilot")()
        ok, directories, err = await client.get_setting(
            mp_url, mp_key, "Directories"
        )
        if not ok:
            logger.warning("实例组 %s 读取 MoviePilot 目录失败: %s", group_id, err)
            await self._save_run_status(
                group_id,
                message=f"读取 MoviePilot 目录失败: {err}",
            )
            return 0
        ok, policy, err = await client.get_classification_policy(mp_url, mp_key)
        if not ok or policy is None:
            logger.warning("实例组 %s 读取分类策略失败: %s", group_id, err)
            await self._save_run_status(
                group_id,
                message=f"读取分类策略失败: {err}",
            )
            return 0

        targets = build_library_targets(
            directories if isinstance(directories, list) else [],
            policy,
        )
        if not targets:
            await self._save_run_status(group_id, message="没有可用的分类目录目标")
            return 0

        directory_cache: dict[str, tuple[bool, list[dict], str | None]] = {}
        present_targets = []
        for target in targets:
            ok, exists, err = await client.storage_dir_exists(
                mp_url,
                mp_key,
                target.path,
                cache=directory_cache,
            )
            if not ok:
                logger.warning(
                    "实例组 %s 检测 MoviePilot 目录 %s 失败: %s",
                    group_id,
                    target.path,
                    err,
                )
                break
            if exists:
                present_targets.append(target)

        server_results: list[dict[str, Any]] = []
        created_count = 0

        for server_name, server_config in servers:
            server_url = str(server_config.get("url") or "").rstrip("/")
            server_key = str(server_config.get("api_key") or "")
            server_created = 0
            server_error: str | None = None
            ok, libraries, err = await list_media_libraries(server_url, server_key)
            if not ok:
                logger.warning(
                    "实例组 %s 读取 %s 媒体库失败: %s",
                    group_id,
                    server_name,
                    err,
                )
                server_error = err
            else:
                existing_paths = [
                    path for library in libraries for path in library.paths
                ]
                for target in present_targets:
                    if path_is_covered(target.path, existing_paths):
                        continue
                    ok, err = await create_media_library(
                        server_url,
                        server_key,
                        name=target.name,
                        collection_type=target.collection_type,
                        path=target.path,
                    )
                    if ok:
                        created_count += 1
                        server_created += 1
                        logger.info(
                            "实例组 %s 已为 %s 创建媒体库「%s」: %s",
                            group_id,
                            server_name,
                            target.name,
                            target.path,
                        )
                        existing_paths.append(target.path)
                    else:
                        logger.warning(
                            "实例组 %s 为 %s 创建媒体库「%s」失败: %s",
                            group_id,
                            server_name,
                            target.name,
                            err,
                        )
                        server_error = err
            server_results.append(
                {
                    "name": server_name,
                    "created": server_created,
                    "error": server_error,
                }
            )

        if present_targets:
            message = (
                f"检查 {len(targets)} 个分类目录，"
                f"发现 {len(present_targets)} 个已创建目录，"
                f"新建 {created_count} 个媒体库"
            )
        else:
            message = (
                f"检查 {len(targets)} 个分类目录，"
                "MoviePilot 尚未创建任何分类目录"
            )
        await self._save_run_status(
            group_id,
            targets=len(targets),
            present_directories=len(present_targets),
            created=created_count,
            servers=server_results,
            message=message,
        )
        return created_count

    async def _save_run_status(
        self,
        group_id: int,
        *,
        targets: int = 0,
        present_directories: int = 0,
        created: int = 0,
        servers: list[dict[str, Any]] | None = None,
        message: str,
    ) -> None:
        """把最近一次检测结果写回实例组配置，供管理端展示。"""
        now = datetime.now(timezone.utc).isoformat()
        async with async_session() as db:
            group = await db.get(AppOrchestrationInstance, group_id)
            if group is None:
                return
            app_configs = dict(group.app_configs or {})
            moviepilot = dict(app_configs.get("moviepilot") or {})
            config = normalize_auto_config(moviepilot.get(AUTO_CONFIG_KEY))
            config["last_run_at"] = now
            config["last_result"] = {
                "targets": targets,
                "present_directories": present_directories,
                "created": created,
                "servers": servers or [],
                "message": message,
            }
            moviepilot[AUTO_CONFIG_KEY] = config
            app_configs["moviepilot"] = moviepilot
            group.app_configs = app_configs
            await db.commit()


media_library_sync_service = MediaLibrarySyncService()
