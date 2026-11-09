"""音乐自动整理服务。

在 OneTap 内直接调用 Music Tag Web 的网页端接口完成「自动刮削 + 整理」，
不再依赖改造后的镜像：目录与运行参数保存在实例组
``app_configs["music-tag-web"]["auto_organize"]`` 中，由本服务定时调度，
也可在自动化页面手动触发。
"""

from __future__ import annotations

import asyncio
import logging
import posixpath
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from app.core.background import create_background_task
from app.core.exceptions import APIException
from app.database import async_session
from app.models.orchestration import (
    AppInstance,
    AppOrchestrationInstance,
    MusicAutoOrganizeFile,
)
from app.services.apps import AppClientError, get_client
from app.services.apps.music_tag_web.client import (
    AUDIT_ACTION_SCRAPE,
    AUDIT_ACTION_TIDY,
    DEFAULT_DIR_LIST,
    DEFAULT_MODIFY_FIELDS,
    DEFAULT_SOURCES,
    scrape_succeeded,
    tidy_succeeded,
)

logger = logging.getLogger(__name__)

APP_NAME = "music-tag-web"

# 允许写入 app_configs 的字段；last_run_at/last_result 由服务自己维护
CONFIG_FIELDS = (
    "enabled",
    "interval_minutes",
    "watch_dir",
    "library_dir",
    "scrape_enabled",
    "tidy_enabled",
    "source_list",
    "modify_list",
    "dir_list",
    "min_age_seconds",
    "max_files_per_run",
    "scrape_timeout_seconds",
    "tidy_timeout_seconds",
    "navidrome_scan",
    "select_mode",
    "skip_tag",
    "folder_album",
)

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,
    "interval_minutes": 5,
    "watch_dir": "/app/media/downloads",
    "library_dir": "/app/media/music",
    "scrape_enabled": True,
    "tidy_enabled": True,
    "source_list": list(DEFAULT_SOURCES),
    "modify_list": list(DEFAULT_MODIFY_FIELDS),
    "dir_list": [dict(item) for item in DEFAULT_DIR_LIST],
    "min_age_seconds": 60,
    "max_files_per_run": 20,
    "scrape_timeout_seconds": 180,
    "tidy_timeout_seconds": 180,
    "navidrome_scan": True,
    "select_mode": "hard",
    "skip_tag": False,
    "folder_album": False,
}

# 失败重试：最多 5 次，间隔 5/10/20/40 分钟
MAX_ATTEMPTS = 5

# 这些字段变化会改变文件处理结果，需要把已完成状态重置为待处理。
# 例如先只开刮削、之后补开整理时，仍留在下载目录的文件要重新走一轮。
PROCESSING_FIELDS = (
    "enabled",
    "watch_dir",
    "library_dir",
    "scrape_enabled",
    "tidy_enabled",
    "source_list",
    "modify_list",
    "dir_list",
    "select_mode",
    "skip_tag",
    "folder_album",
)


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off"}
    return default


def _as_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def _as_str_list(value: Any, default: list[str]) -> list[str]:
    if isinstance(value, str):
        items = [part.strip() for part in value.split(",")]
    elif isinstance(value, (list, tuple)):
        items = [str(part).strip() for part in value]
    else:
        return list(default)
    result = [item for item in items if item]
    return list(dict.fromkeys(result)) or list(default)


def _as_dir_list(value: Any) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    if isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, dict):
                key = str(item.get("value") or "").strip()
                if key:
                    items.append(
                        {
                            "value": key,
                            "value2": str(item.get("value2") or "").strip(),
                        }
                    )
            elif isinstance(item, str) and item.strip():
                items.append({"value": item.strip(), "value2": ""})
    return items or [dict(item) for item in DEFAULT_DIR_LIST]


def _path_norm(value: str) -> str:
    path = posixpath.normpath(str(value or "").strip())
    return "" if path == "." else path


def normalize_config(raw: Any) -> dict[str, Any]:
    """把数据库中的配置补齐/校正为可运行的结构。"""
    source = raw if isinstance(raw, dict) else {}
    config = {
        "enabled": _as_bool(source.get("enabled"), DEFAULT_CONFIG["enabled"]),
        "interval_minutes": _as_int(
            source.get("interval_minutes"), DEFAULT_CONFIG["interval_minutes"], 1, 1440
        ),
        "watch_dir": _path_norm(
            str(source.get("watch_dir") or DEFAULT_CONFIG["watch_dir"])
        )
        or DEFAULT_CONFIG["watch_dir"],
        "library_dir": _path_norm(
            str(source.get("library_dir") or DEFAULT_CONFIG["library_dir"])
        )
        or DEFAULT_CONFIG["library_dir"],
        "scrape_enabled": _as_bool(
            source.get("scrape_enabled"), DEFAULT_CONFIG["scrape_enabled"]
        ),
        "tidy_enabled": _as_bool(
            source.get("tidy_enabled"), DEFAULT_CONFIG["tidy_enabled"]
        ),
        "source_list": _as_str_list(
            source.get("source_list"), DEFAULT_CONFIG["source_list"]
        ),
        "modify_list": _as_str_list(
            source.get("modify_list"), DEFAULT_CONFIG["modify_list"]
        ),
        "dir_list": _as_dir_list(source.get("dir_list")),
        "min_age_seconds": _as_int(
            source.get("min_age_seconds"),
            DEFAULT_CONFIG["min_age_seconds"],
            0,
            86400,
        ),
        "max_files_per_run": _as_int(
            source.get("max_files_per_run"),
            DEFAULT_CONFIG["max_files_per_run"],
            1,
            200,
        ),
        "scrape_timeout_seconds": _as_int(
            source.get("scrape_timeout_seconds"),
            DEFAULT_CONFIG["scrape_timeout_seconds"],
            10,
            3600,
        ),
        "tidy_timeout_seconds": _as_int(
            source.get("tidy_timeout_seconds"),
            DEFAULT_CONFIG["tidy_timeout_seconds"],
            10,
            3600,
        ),
        "navidrome_scan": _as_bool(
            source.get("navidrome_scan"), DEFAULT_CONFIG["navidrome_scan"]
        ),
        "select_mode": str(source.get("select_mode") or "hard"),
        "skip_tag": _as_bool(source.get("skip_tag"), False),
        "folder_album": _as_bool(source.get("folder_album"), False),
    }
    # 运行结果字段原样保留，供前端展示
    for key in ("last_run_at", "last_result"):
        if key in source:
            config[key] = source[key]
    return config


def validate_config(config: dict[str, Any]) -> None:
    """校验目录与开关组合，不合法时抛 APIException。"""
    watch_dir = config["watch_dir"]
    library_dir = config["library_dir"]
    if not watch_dir.startswith("/"):
        raise APIException("监控目录必须是 Music Tag Web 容器内的绝对路径", 400)
    if not library_dir.startswith("/"):
        raise APIException("整理目录必须是 Music Tag Web 容器内的绝对路径", 400)
    if watch_dir == library_dir:
        raise APIException("监控目录与整理目录不能相同", 400)

    watch = watch_dir.rstrip("/")
    library = library_dir.rstrip("/")
    if library == watch or library.startswith(f"{watch}/"):
        raise APIException("整理目录不能位于监控目录内部，否则会反复整理", 400)
    if watch.startswith(f"{library}/"):
        raise APIException("监控目录不能位于整理目录内部", 400)

    if not config["scrape_enabled"] and not config["tidy_enabled"]:
        raise APIException("自动刮削和自动整理至少开启一项", 400)
    if config["scrape_enabled"] and not config["source_list"]:
        raise APIException("自动刮削至少需要一个音乐源", 400)
    if config["scrape_enabled"] and not config["modify_list"]:
        raise APIException("自动刮削至少需要一个写入字段", 400)


def _parse_datetime(value: Any) -> datetime | None:
    """解析 ISO 时间，返回无时区的本地时间用于比较。"""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def _app_timezone(group: AppOrchestrationInstance) -> str | None:
    """取 Music Tag Web 容器的 TZ，用于解释它返回的本地时间。"""
    for instance in group.instances:
        if not instance.app or instance.app.name != APP_NAME:
            continue
        for item in (instance.config or {}).get("env") or []:
            if isinstance(item, dict) and item.get("key") == "TZ":
                value = str(item.get("value") or "").strip()
                if value:
                    return value
    for item in (group.shared_config or {}).get("env") or []:
        if isinstance(item, dict) and item.get("key") == "TZ":
            value = str(item.get("value") or "").strip()
            if value:
                return value
    return None


def _mtime_age_seconds(value: Any, tz_name: str | None) -> float | None:
    """按应用时区解析文件的 mtime，返回距现在的秒数。

    Music Tag Web 的 ``update_time`` 是不带时区的本地时间（如
    ``2026-10-24 16:03:46``）。解析失败时返回 None，调用方按保守策略
    再等一轮。
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        try:
            parsed = parsed.replace(tzinfo=ZoneInfo(tz_name) if tz_name else None)
        except (ZoneInfoNotFoundError, ValueError):
            parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()


def _now() -> datetime:
    return datetime.now()


def _now_iso() -> str:
    """带时区的 ISO 时间，便于前端按浏览器时区展示。"""
    return datetime.now(timezone.utc).isoformat()


def _detect_dirs(group: AppOrchestrationInstance) -> dict[str, str]:
    """从应用实际挂载中推断下载目录与曲库目录，作为配置默认值。"""
    paths: list[str] = []
    for instance in group.instances:
        if not instance.app or instance.app.name != APP_NAME:
            continue
        config = instance.config or {}
        for volume in config.get("volumes") or []:
            if isinstance(volume, dict):
                path = str(volume.get("container_path") or "").strip()
                if path:
                    paths.append(path.rstrip("/"))

    def pick(paths_list: list[str], names: set[str], keyword: str) -> str | None:
        # 优先 Music Tag Web 容器内的 /app 目录，避免选中共享根目录
        for prefix in ("/app/", ""):
            for path in paths_list:
                if prefix and not path.startswith(prefix):
                    continue
                if posixpath.basename(path).lower() in names:
                    return path
        return next(
            (path for path in paths_list if keyword in path.lower()), None
        )

    watch = pick(paths, {"downloads", "download"}, "download")
    library = pick(paths, {"music", "library"}, "library")
    if library is None:
        library = pick(paths, {"music"}, "music")

    return {
        "watch_dir": watch or DEFAULT_CONFIG["watch_dir"],
        "library_dir": library or DEFAULT_CONFIG["library_dir"],
    }


class MusicAutoOrganizeService:
    """音乐自动整理的配置、手动触发与定时调度。"""

    def __init__(self) -> None:
        self._runs: dict[int, dict[str, Any]] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._scheduler_task: asyncio.Task | None = None
        self._scheduler_stop: asyncio.Event | None = None

    # ------------------------------------------------------------------
    # 配置查询与保存
    # ------------------------------------------------------------------
    async def _load_group(self, db, group_id: int) -> AppOrchestrationInstance:
        result = await db.execute(
            select(AppOrchestrationInstance)
            .where(AppOrchestrationInstance.id == group_id)
            .options(
                selectinload(AppOrchestrationInstance.instances).selectinload(
                    AppInstance.app
                )
            )
        )
        group = result.scalar_one_or_none()
        if group is None:
            raise APIException("组合部署记录不存在", 404)
        return group

    @staticmethod
    def _app_instance(
        group: AppOrchestrationInstance, app_name: str
    ) -> AppInstance | None:
        for instance in group.instances:
            if instance.app and instance.app.name == app_name:
                return instance
        return None

    @staticmethod
    def _entry(group: AppOrchestrationInstance, app_name: str) -> dict:
        value = (group.app_configs or {}).get(app_name)
        return value if isinstance(value, dict) else {}

    async def get_panel(
        self, db, group_id: int, app_name: str = APP_NAME
    ) -> dict[str, Any]:
        """返回配置面板所需的配置、检测目录与运行状态。"""
        group = await self._load_group(db, group_id)
        if self._app_instance(group, app_name) is None:
            raise APIException("应用不在该实例组中", 404)
        if app_name != APP_NAME:
            return {"supported": False, "config": {}, "stats": {}}

        entry = self._entry(group, app_name)
        stored = entry.get("auto_organize")
        detected = _detect_dirs(group)
        config = normalize_config(stored)
        stored_dict = stored if isinstance(stored, dict) else {}
        if not stored_dict.get("watch_dir"):
            config["watch_dir"] = detected["watch_dir"]
        if not stored_dict.get("library_dir"):
            config["library_dir"] = detected["library_dir"]

        stats = await self._stats(db, group_id)
        run = self._runs.get(group_id) or {}
        return {
            "supported": True,
            "config": config,
            "detected": detected,
            "running": bool(run.get("running")),
            "progress": run or None,
            "last_run": config.get("last_result"),
            "stats": stats,
        }

    async def save_config(
        self,
        db,
        group_id: int,
        config: dict[str, Any],
        app_name: str = APP_NAME,
    ) -> dict[str, Any]:
        """保存自动整理配置到实例组 app_configs。"""
        if app_name != APP_NAME:
            raise APIException("该应用不支持自动整理", 400)
        group = await self._load_group(db, group_id)
        if self._app_instance(group, app_name) is None:
            raise APIException("应用不在该实例组中", 404)

        normalized = normalize_config(config)
        validate_config(normalized)

        app_configs = dict(group.app_configs or {})
        entry = dict(app_configs.get(app_name) or {})
        auto = dict(entry.get("auto_organize") or {})
        previous = normalize_config(auto)
        processing_changed = any(
            previous[key] != normalized[key] for key in PROCESSING_FIELDS
        )

        for key in CONFIG_FIELDS:
            auto[key] = normalized[key]
        # 让下次定时任务立即按新配置运行
        auto.pop("last_run_at", None)
        entry["auto_organize"] = auto
        app_configs[app_name] = entry
        group.app_configs = app_configs

        if processing_changed:
            # 处理方式变化后，仍留在下载目录的已完成文件需要重新处理
            await db.execute(
                update(MusicAutoOrganizeFile)
                .where(
                    MusicAutoOrganizeFile.group_id == group_id,
                    MusicAutoOrganizeFile.status == "done",
                )
                .values(
                    status="pending",
                    attempts=0,
                    first_seen_at=_now(),
                    last_error=None,
                )
            )

        await db.commit()
        return await self.get_panel(db, group_id, app_name)

    async def _stats(self, db, group_id: int) -> dict[str, int]:
        rows = (
            await db.execute(
                select(MusicAutoOrganizeFile).where(
                    MusicAutoOrganizeFile.group_id == group_id
                )
            )
        ).scalars()
        counts = {"pending": 0, "done": 0, "failed": 0}
        for row in rows:
            counts[row.status] = counts.get(row.status, 0) + 1
        return counts

    # ------------------------------------------------------------------
    # 手动触发与调度
    # ------------------------------------------------------------------
    async def start_run(
        self,
        db,
        group_id: int,
        *,
        trigger: str = "manual",
        force: bool = True,
        app_name: str = APP_NAME,
    ) -> dict[str, Any]:
        """后台启动一轮自动整理，立即返回当前进度。"""
        if app_name != APP_NAME:
            raise APIException("该应用不支持自动整理", 400)
        group = await self._load_group(db, group_id)
        if self._app_instance(group, app_name) is None:
            raise APIException("应用不在该实例组中", 404)
        config = normalize_config(self._entry(group, app_name).get("auto_organize"))
        validate_config(config)

        current = self._runs.get(group_id) or {}
        if current.get("running"):
            return current

        self._runs[group_id] = {
            "running": True,
            "stage": "queued",
            "message": "已加入执行队列",
            "processed": 0,
            "failed": 0,
            "total": 0,
            "trigger": trigger,
            "started_at": _now_iso(),
        }
        create_background_task(self._run_group(group_id, trigger, force))
        return self._runs[group_id]

    def scheduler_status(self) -> dict[str, Any]:
        """定时调度器是否运行（供排查）。"""
        return {
            "running": bool(
                self._scheduler_task is not None
                and not self._scheduler_task.done()
            ),
            "active_groups": [
                group_id
                for group_id, info in self._runs.items()
                if info.get("running")
            ],
        }

    def start_scheduler(self, interval_seconds: float = 30.0) -> None:
        """启动后台定时调度循环（幂等）。"""
        if self._scheduler_task is not None and not self._scheduler_task.done():
            return
        self._scheduler_stop = asyncio.Event()
        self._scheduler_task = asyncio.create_task(
            self._scheduler_loop(interval_seconds)
        )
        logger.info("音乐自动整理调度器已启动（检查间隔 %.0f 秒）", interval_seconds)

    async def stop_scheduler(self) -> None:
        """停止后台定时调度循环。"""
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
                logger.exception("音乐自动整理调度检查失败")

    async def tick(self) -> None:
        """检查所有启用自动整理的实例组，到点则后台运行。"""
        async with async_session() as db:
            groups = list(
                (
                    await db.execute(select(AppOrchestrationInstance))
                ).scalars()
            )
        for group in groups:
            entry = (group.app_configs or {}).get(APP_NAME)
            if not isinstance(entry, dict):
                continue
            config = normalize_config(entry.get("auto_organize"))
            if not config["enabled"]:
                continue
            if (self._runs.get(group.id) or {}).get("running"):
                continue
            if not self._due(config):
                continue
            try:
                async with async_session() as db:
                    await self.start_run(
                        db,
                        group.id,
                        trigger="auto",
                        force=False,
                    )
            except APIException as exc:
                logger.warning(
                    "实例组 %s 自动整理配置无效，已跳过：%s", group.id, exc.message
                )
            except Exception:
                logger.exception("实例组 %s 自动整理启动失败", group.id)

    @staticmethod
    def _due(config: dict[str, Any]) -> bool:
        last = _parse_datetime(config.get("last_run_at"))
        if last is None:
            return True
        return _now() - last >= timedelta(minutes=config["interval_minutes"])

    # ------------------------------------------------------------------
    # 单轮执行
    # ------------------------------------------------------------------
    async def _run_group(self, group_id: int, trigger: str, force: bool) -> None:
        lock = self._locks.setdefault(group_id, asyncio.Lock())
        async with lock:
            result: dict[str, Any] = {
                "status": "ok",
                "trigger": trigger,
                "processed": 0,
                "failed": 0,
                "total": 0,
                "message": "",
                "started_at": _now_iso(),
                "finished_at": None,
            }
            try:
                async with async_session() as db:
                    group = await self._load_group(db, group_id)
                    entry = self._entry(group, APP_NAME)
                    config = normalize_config(entry.get("auto_organize"))
                validate_config(config)

                client = get_client(APP_NAME)()
                session = await client.open_organize_session(
                    entry.get("url") or "",
                    entry.get("username") or "",
                    entry.get("password") or "",
                    timeout=max(30.0, config["scrape_timeout_seconds"]),
                )

                processed = 0
                failed = 0
                moved = 0
                async with session:
                    self._update_progress(
                        group_id,
                        stage="scanning",
                        message=f"扫描监控目录 {config['watch_dir']}",
                    )
                    files = await session.walk_audio_files(
                        config["watch_dir"],
                        max_files=max(
                            1000, config["max_files_per_run"] * 100
                        ),
                        max_dirs=2000,
                    )
                    candidates = await self._plan_files(
                        group_id,
                        files,
                        config,
                        force=force,
                        tz_name=_app_timezone(group),
                    )
                    total = len(candidates)
                    self._update_progress(
                        group_id,
                        stage="processing",
                        total=total,
                        processed=0,
                        failed=0,
                        message=(
                            f"发现 {len(files)} 个音频文件，"
                            f"本轮处理 {total} 个"
                        ),
                    )

                    for index, item in enumerate(candidates, start=1):
                        path = item["path"]
                        name = item["name"]
                        directory = item["directory"]
                        self._update_progress(
                            group_id,
                            message=f"处理 {name}（{index}/{total}）",
                            processed=index - 1,
                        )
                        try:
                            if config["scrape_enabled"]:
                                before = await session.latest_audit_id()
                                await session.scrape_file(
                                    directory=directory,
                                    filename=name,
                                    sources=config["source_list"],
                                    modify_fields=config["modify_list"],
                                    select_mode=config["select_mode"],
                                    skip_tag=config["skip_tag"],
                                    folder_album=config["folder_album"],
                                )
                                audit = await session.wait_audit(
                                    AUDIT_ACTION_SCRAPE,
                                    before,
                                    config["scrape_timeout_seconds"],
                                )
                                ok, detail = scrape_succeeded(audit)
                                if not ok:
                                    raise AppClientError(f"刮削未成功: {detail}")

                            if config["tidy_enabled"]:
                                before = await session.latest_audit_id()
                                await session.tidy_file(
                                    directory=directory,
                                    filename=name,
                                    root_path=config["library_dir"],
                                    dir_list=config["dir_list"],
                                )
                                audit = await session.wait_audit(
                                    AUDIT_ACTION_TIDY,
                                    before,
                                    config["tidy_timeout_seconds"],
                                )
                                ok, detail = tidy_succeeded(audit)
                                if not ok:
                                    raise AppClientError(f"整理未完成: {detail}")
                                moved += 1

                            await self._mark_state(
                                group_id, path, "done", processed=True
                            )
                            processed += 1
                        except Exception as exc:
                            failed += 1
                            logger.warning(
                                "实例组 %s 自动整理失败 %s: %s",
                                group_id,
                                path,
                                exc,
                            )
                            await self._mark_state(
                                group_id,
                                path,
                                "failed",
                                error=str(exc),
                                increment_attempt=True,
                            )
                        self._update_progress(
                            group_id,
                            processed=processed,
                            failed=failed,
                        )

                result.update(
                    processed=processed,
                    failed=failed,
                    total=total,
                    message=(
                        f"处理完成：成功 {processed} 个，失败 {failed} 个"
                        if total
                        else "没有需要处理的文件"
                    ),
                    status=(
                        "ok"
                        if failed == 0
                        else ("partial" if processed else "error")
                    ),
                )

                if moved and config["navidrome_scan"]:
                    scan_ok, scan_message = await self._scan_navidrome(group_id)
                    result["navidrome"] = {
                        "ok": scan_ok,
                        "message": scan_message,
                    }
            except Exception as exc:
                logger.exception("实例组 %s 自动整理执行失败", group_id)
                result.update(
                    status="error",
                    message=f"执行失败: {exc}",
                )
            finally:
                result["finished_at"] = _now_iso()
                self._update_progress(
                    group_id,
                    running=False,
                    stage="finished",
                    message=result["message"],
                )
                try:
                    await self._persist_result(group_id, result)
                except Exception:
                    logger.exception("实例组 %s 自动整理结果落库失败", group_id)

    def _update_progress(self, group_id: int, **fields: Any) -> None:
        info = self._runs.setdefault(group_id, {})
        info.update(fields)

    async def _plan_files(
        self,
        group_id: int,
        files: list[dict],
        config: dict[str, Any],
        *,
        force: bool,
        tz_name: str | None = None,
    ) -> list[dict]:
        """按处理状态与文件稳定性筛选本轮要处理的文件。

        ``tz_name`` 为 Music Tag Web 容器的时区，用于判断新文件的
        mtime 是否已经稳定；已经在下载目录里放够 ``min_age_seconds``
        的文件会在本轮直接处理，不必再等一个检查间隔。
        """
        async with async_session() as db:
            rows = list(
                (
                    await db.execute(
                        select(MusicAutoOrganizeFile).where(
                            MusicAutoOrganizeFile.group_id == group_id
                        )
                    )
                ).scalars()
            )
            states = {row.path: row for row in rows}
            now = _now()
            candidates: list[dict] = []

            for item in files:
                path = item["path"]
                state = states.get(path)
                same = (
                    state is not None
                    and state.size == item["size"]
                    and (state.mtime or "") == item["mtime"]
                )

                if state is not None and state.status == "done" and same:
                    continue

                if not same:
                    if state is None:
                        state = MusicAutoOrganizeFile(
                            group_id=group_id,
                            path=path,
                            size=item["size"],
                            mtime=item["mtime"],
                            status="pending",
                            attempts=0,
                            first_seen_at=now,
                        )
                        db.add(state)
                        states[path] = state
                        if not force:
                            age = _mtime_age_seconds(
                                item.get("mtime"), tz_name
                            )
                            if age is None or age < config["min_age_seconds"]:
                                # 文件刚出现或 mtime 太新，先等一轮，
                                # 避免处理仍在写入的下载文件
                                continue
                            # 文件在下载目录里已经稳定，本轮直接处理
                            state.first_seen_at = now - timedelta(
                                seconds=config["min_age_seconds"]
                            )
                    elif state.status == "failed":
                        # 刮削会改写文件标签，失败重试时 size/mtime 变化
                        # 不代表是新文件，保留重试计数与退避
                        state.size = item["size"]
                        state.mtime = item["mtime"]
                    else:
                        state.size = item["size"]
                        state.mtime = item["mtime"]
                        state.status = "pending"
                        state.attempts = 0
                        state.first_seen_at = now
                        state.last_error = None
                        if not force:
                            continue

                if not force:
                    if state.status == "failed":
                        if state.attempts >= MAX_ATTEMPTS:
                            continue
                        delay = timedelta(
                            minutes=min(
                                5 * 2 ** max(state.attempts - 1, 0), 60
                            )
                        )
                        updated = state.updated_at or state.first_seen_at
                        if updated and now - updated < delay:
                            continue
                    else:
                        first_seen = state.first_seen_at or now
                        elapsed = (now - first_seen).total_seconds()
                        if elapsed < config["min_age_seconds"]:
                            continue

                candidates.append(
                    {
                        "path": path,
                        "name": item["name"],
                        "directory": item["directory"],
                    }
                )
                if len(candidates) >= config["max_files_per_run"]:
                    break

            await db.commit()
            return candidates

    async def _mark_state(
        self,
        group_id: int,
        path: str,
        status: str,
        *,
        error: str | None = None,
        increment_attempt: bool = False,
        processed: bool = False,
    ) -> None:
        async with async_session() as db:
            state = (
                await db.execute(
                    select(MusicAutoOrganizeFile).where(
                        MusicAutoOrganizeFile.group_id == group_id,
                        MusicAutoOrganizeFile.path == path,
                    )
                )
            ).scalar_one_or_none()
            if state is None:
                state = MusicAutoOrganizeFile(
                    group_id=group_id, path=path, status=status
                )
                db.add(state)
            state.status = status
            state.last_error = error
            state.updated_at = _now()
            if processed:
                state.processed_at = _now()
            if increment_attempt:
                state.attempts = (state.attempts or 0) + 1
            await db.commit()

    async def _scan_navidrome(self, group_id: int) -> tuple[bool, str]:
        """整理移动文件后触发 Navidrome 扫描。"""
        async with async_session() as db:
            group = await self._load_group(db, group_id)
            navidrome = self._entry(group, "navidrome")
        if not navidrome.get("url"):
            return False, "实例组中没有 Navidrome，已跳过扫描"
        try:
            client = get_client("navidrome")()
            return await client.scan_library(
                navidrome.get("url") or "",
                navidrome.get("username") or "",
                navidrome.get("password") or "",
            )
        except Exception as exc:
            return False, f"触发 Navidrome 扫描失败: {exc}"

    async def _persist_result(self, group_id: int, result: dict[str, Any]) -> None:
        async with async_session() as db:
            group = await self._load_group(db, group_id)
            app_configs = dict(group.app_configs or {})
            entry = dict(app_configs.get(APP_NAME) or {})
            auto = dict(entry.get("auto_organize") or {})
            auto["last_run_at"] = result.get("finished_at") or _now_iso()
            auto["last_result"] = result
            entry["auto_organize"] = auto
            app_configs[APP_NAME] = entry
            group.app_configs = app_configs
            await db.commit()


music_auto_service = MusicAutoOrganizeService()
