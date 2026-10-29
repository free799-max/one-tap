"""Reader 书架自动同步服务。

把原先集成在 ``free5104/reader`` 镜像里的 Book Bridge 逻辑搬到 OneTap：

1. 读取 Reader 书架，补缓存缺失章节；
2. 导出 EPUB、修复 XML 后写入二毛书库目录；
3. 触发二毛书库扫描并等待导入任务结束。

目录与账号保存在实例组 ``app_configs["reader"]["auto_sync"]`` 中，
由后台调度器按间隔执行，也可在自动化页面手动触发。运行状态写入
``reader_auto_sync_books`` 表，用于判断是否需要重新导出。
"""

from __future__ import annotations

import asyncio
import logging
import os
import posixpath
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.background import create_background_task
from app.core.exceptions import APIException
from app.database import async_session
from app.models.orchestration import (
    AppInstance,
    AppOrchestrationInstance,
    ReaderAutoSyncBook,
)
from app.services.apps.reader import (
    ErmaoClient,
    ErmaoClientError,
    ReaderClientError,
    ReaderSession,
    fix_epub,
)
from app.services.docker import docker_manager
from app.services.system import StoragePathResolver, storage_directory_service

logger = logging.getLogger(__name__)

APP_NAME = "reader"
TARGET_APP = "ermao-library"

DEFAULT_EMAIL = "admin@example.com"
DEFAULT_READER_LIBRARY = "/storage/localStore/ermao"
DEFAULT_ERMAO_LIBRARY = "/storage/localStore/ermao"

# 失败重试：最多 5 次，间隔 10/20/40/80/160 分钟
MAX_ATTEMPTS = 5

CONFIG_FIELDS = (
    "enabled",
    "interval_minutes",
    "reader_url",
    "reader_username",
    "reader_password",
    "reader_token",
    "library_dir",
    "library_host_dir",
    "ermao_library_dir",
    "cache_missing_chapters",
    "max_seconds",
    "scan_enabled",
    "scan_wait_seconds",
    "auto_setup",
    "ermao_url",
    "ermao_email",
    "ermao_password",
    "ermao_lib_id",
)

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,
    "interval_minutes": 10,
    "reader_url": "",
    "reader_username": "",
    "reader_password": "",
    "reader_token": "",
    "library_dir": "",
    "library_host_dir": "",
    "ermao_library_dir": "",
    "cache_missing_chapters": True,
    "max_seconds": 2700,
    "scan_enabled": True,
    "scan_wait_seconds": 180,
    "auto_setup": True,
    "ermao_url": "",
    "ermao_email": DEFAULT_EMAIL,
    "ermao_password": "",
    "ermao_lib_id": "",
}

RUNNING_TASK_STATES = {"PENDING", "QUEUED", "RUNNING", "WAITING"}
UNSAFE_NAME_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


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


def _path(value: Any) -> str:
    """规范化容器/宿主机路径；空值保持为空。"""
    text = str(value or "").strip()
    if not text:
        return ""
    path = posixpath.normpath(text)
    return "" if path == "." else path


def normalize_config(raw: Any) -> dict[str, Any]:
    """把数据库中的配置补齐/校正为可运行的结构。"""
    source = raw if isinstance(raw, dict) else {}
    config = {
        "enabled": _as_bool(source.get("enabled"), DEFAULT_CONFIG["enabled"]),
        "interval_minutes": _as_int(
            source.get("interval_minutes"),
            DEFAULT_CONFIG["interval_minutes"],
            1,
            1440,
        ),
        "reader_url": str(source.get("reader_url") or "").strip().rstrip("/"),
        "reader_username": str(source.get("reader_username") or "").strip(),
        "reader_password": str(source.get("reader_password") or ""),
        "reader_token": str(source.get("reader_token") or "").strip(),
        "library_dir": _path(source.get("library_dir")),
        "library_host_dir": _path(source.get("library_host_dir")),
        "ermao_library_dir": _path(source.get("ermao_library_dir")),
        "cache_missing_chapters": _as_bool(
            source.get("cache_missing_chapters"),
            DEFAULT_CONFIG["cache_missing_chapters"],
        ),
        "max_seconds": _as_int(
            source.get("max_seconds"), DEFAULT_CONFIG["max_seconds"], 60, 21600
        ),
        "scan_enabled": _as_bool(
            source.get("scan_enabled"), DEFAULT_CONFIG["scan_enabled"]
        ),
        "scan_wait_seconds": _as_int(
            source.get("scan_wait_seconds"),
            DEFAULT_CONFIG["scan_wait_seconds"],
            10,
            3600,
        ),
        "auto_setup": _as_bool(
            source.get("auto_setup"), DEFAULT_CONFIG["auto_setup"]
        ),
        "ermao_url": str(source.get("ermao_url") or "").strip().rstrip("/"),
        "ermao_email": str(source.get("ermao_email") or "").strip(),
        "ermao_password": str(source.get("ermao_password") or ""),
        "ermao_lib_id": str(source.get("ermao_lib_id") or "").strip(),
    }
    # 运行结果字段原样保留，供前端展示
    for key in ("last_run_at", "last_result"):
        if key in source:
            config[key] = source[key]
    return config


def validate_config(config: dict[str, Any]) -> None:
    """校验地址与目录，不合法时抛 APIException。"""
    reader_url = config["reader_url"]
    if not reader_url:
        raise APIException("未检测到 Reader 地址，请填写访问地址", 400)
    if not reader_url.startswith(("http://", "https://")):
        raise APIException("Reader 地址必须以 http:// 或 https:// 开头", 400)

    ermao_url = config["ermao_url"]
    if ermao_url and not ermao_url.startswith(("http://", "https://")):
        raise APIException("二毛地址必须以 http:// 或 https:// 开头", 400)

    for key, label in (
        ("library_dir", "Reader 容器内书库目录"),
        ("library_host_dir", "后端宿主机书库目录"),
        ("ermao_library_dir", "二毛容器内书库目录"),
    ):
        value = config[key]
        if value and not value.startswith("/"):
            raise APIException(f"{label}必须是绝对路径", 400)

    if not config["library_dir"]:
        raise APIException("未检测到 Reader 书库目录，请手动填写", 400)
    if not config["library_host_dir"]:
        raise APIException("未检测到后端可写的书库目录，请手动填写", 400)
    if config["library_host_dir"] == "/":
        raise APIException("书库目录不能是根目录", 400)

    if config["scan_enabled"]:
        if not config["ermao_url"]:
            raise APIException("开启二毛扫描前需要填写二毛地址", 400)
        if not config["ermao_email"]:
            raise APIException("开启二毛扫描前需要填写登录邮箱", 400)
        if not config["ermao_password"]:
            raise APIException("开启二毛扫描前需要填写登录密码", 400)


def safe_name(name: str | None) -> str:
    """把书名转换为安全的文件名。"""
    cleaned = UNSAFE_NAME_RE.sub("_", name or "").strip()
    if not cleaned:
        cleaned = "untitled"
    return cleaned[:120]


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def _now() -> datetime:
    return datetime.now()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _chown(path: Path) -> None:
    """尽量把文件属主改成 1000:1000，与常见的二毛运行用户一致。"""
    try:
        os.chown(path, 1000, 1000)
    except OSError:
        pass


def _same_path(left: str, right: str) -> bool:
    return _path(left).rstrip("/") == _path(right).rstrip("/")


class ReaderAutoSyncService:
    """Reader 自动导出的配置、手动触发与定时调度。"""

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

    @classmethod
    def _apply_provisioned_credentials(
        cls, group: AppOrchestrationInstance, config: dict[str, Any]
    ) -> None:
        """账号自动配置成功后，以 app_configs 中的凭据为准。

        自动同步面板里可能还留着首次运行前生成的密码；应用一旦完成
        自动配置，就改用真实生效的账号与 accessToken，避免反复登录失败。
        """
        reader = cls._entry(group, APP_NAME)
        if reader.get("provisioned"):
            for key, field in (
                ("reader_username", "username"),
                ("reader_password", "password"),
            ):
                value = str(reader.get(field) or "").strip()
                if value:
                    config[key] = value

        ermao = cls._entry(group, TARGET_APP)
        if ermao.get("provisioned"):
            for key, field in (
                ("ermao_email", "username"),
                ("ermao_password", "password"),
            ):
                value = str(ermao.get(field) or "").strip()
                if value:
                    config[key] = value

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
        stored = entry.get("auto_sync")
        detected = await self._detect(group, db)
        config = normalize_config(stored)
        # 组内没有二毛时默认关闭扫描，避免用户只想导出却保存失败
        if self._app_instance(group, TARGET_APP) is None:
            if not isinstance(stored, dict) or "scan_enabled" not in stored:
                config["scan_enabled"] = False

        # 用户没有显式填写时，用应用实际部署信息与同组二毛配置补齐
        defaults = {
            "reader_url": detected.get("reader_url", ""),
            "reader_username": detected.get("reader_username", ""),
            "reader_password": detected.get("reader_password", ""),
            "reader_token": detected.get("reader_token", ""),
            "library_dir": detected.get("library_dir", ""),
            "library_host_dir": detected.get("library_host_dir", ""),
            "ermao_library_dir": detected.get("ermao_library_dir", ""),
            "ermao_url": detected.get("ermao_url", ""),
            "ermao_email": detected.get("ermao_email") or DEFAULT_EMAIL,
            "ermao_password": detected.get("ermao_password", ""),
        }
        for key, value in defaults.items():
            if not config.get(key):
                config[key] = value
        # 已完成自动配置时以真实生效的账号为准
        self._apply_provisioned_credentials(group, config)

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
        """保存自动同步配置到实例组 app_configs。"""
        if app_name != APP_NAME:
            raise APIException("该应用不支持自动同步", 400)
        group = await self._load_group(db, group_id)
        if self._app_instance(group, app_name) is None:
            raise APIException("应用不在该实例组中", 404)

        detected = await self._detect(group, db)
        normalized = normalize_config(config)
        for key in (
            "reader_url",
            "reader_username",
            "reader_password",
            "reader_token",
            "library_dir",
            "library_host_dir",
        ):
            if not normalized[key]:
                normalized[key] = detected.get(key, "")
        for key in ("ermao_library_dir", "ermao_url"):
            if not normalized[key]:
                normalized[key] = detected.get(key, "")
        if not normalized["ermao_email"]:
            normalized["ermao_email"] = (
                detected.get("ermao_email") or DEFAULT_EMAIL
            )
        if not normalized["ermao_password"]:
            normalized["ermao_password"] = detected.get("ermao_password", "")
        self._apply_provisioned_credentials(group, normalized)
        # 开启扫描但还没有密码时生成一个，首次运行会自动初始化二毛账号
        if normalized["scan_enabled"] and not normalized["ermao_password"]:
            normalized["ermao_password"] = secrets.token_urlsafe(12)

        validate_config(normalized)

        app_configs = dict(group.app_configs or {})
        entry = dict(app_configs.get(app_name) or {})
        auto = dict(entry.get("auto_sync") or {})
        for key in CONFIG_FIELDS:
            auto[key] = normalized[key]
        # 让下次定时任务立即按新配置运行
        auto.pop("last_run_at", None)
        entry["auto_sync"] = auto
        app_configs[app_name] = entry
        group.app_configs = app_configs
        await db.commit()
        return await self.get_panel(db, group_id, app_name)

    async def _stats(self, db, group_id: int) -> dict[str, int]:
        rows = (
            await db.execute(
                select(ReaderAutoSyncBook).where(
                    ReaderAutoSyncBook.group_id == group_id
                )
            )
        ).scalars()
        counts = {"pending": 0, "done": 0, "failed": 0}
        for row in rows:
            counts[row.status] = counts.get(row.status, 0) + 1
        return counts

    # ------------------------------------------------------------------
    # 应用信息检测
    # ------------------------------------------------------------------
    async def _container_mounts(self, instance_name: str | None) -> list[dict]:
        """读取运行中容器的真实挂载；Docker 不可用时返回空列表。"""
        if not instance_name:
            return []
        try:
            detail = await docker_manager.run_sync(
                docker_manager.get_container_detail, instance_name
            )
        except Exception:  # noqa: BLE001 - Docker 不可用属于正常降级
            logger.debug("读取容器 %s 挂载失败", instance_name, exc_info=True)
            return []
        if not detail:
            return []
        mounts = []
        for mount in detail.get("mounts") or []:
            destination = str(mount.get("destination") or "").rstrip("/")
            source = str(mount.get("source") or "").rstrip("/")
            if destination:
                mounts.append(
                    {
                        "source": source,
                        "destination": destination,
                        "rw": bool(mount.get("rw", True)),
                    }
                )
        return mounts

    async def _config_mounts(self, instance: AppInstance | None, db) -> list[dict]:
        """从数据库中的实例配置还原挂载，作为 Docker 不可用时的兜底。"""
        if instance is None:
            return []
        resolver = StoragePathResolver(
            app_data=await storage_directory_service.get_app_data_pair(db),
            data_directories=await storage_directory_service.list_enabled_pairs(db),
        )
        mounts = []
        for volume in (instance.config or {}).get("volumes") or []:
            if not isinstance(volume, dict):
                continue
            destination = str(volume.get("container_path") or "").rstrip("/")
            if not destination:
                continue
            host = str(volume.get("host_path") or "").strip()
            source = resolver.to_host_path(host) if host else ""
            if source and not source.startswith("/"):
                source = ""
            mounts.append(
                {
                    "source": source.rstrip("/"),
                    "destination": destination,
                    "rw": str(volume.get("mode") or "rw") != "ro",
                }
            )
        return mounts

    @staticmethod
    def _pick_reader_library(reader_mounts: list[dict]) -> dict | None:
        """从 Reader 挂载中挑出书库目录，避开 /storage 与 /logs。"""
        candidates = [
            mount
            for mount in reader_mounts
            if mount["destination"] not in ("/storage", "/logs")
        ] or reader_mounts
        if not candidates:
            return None

        def score(mount: dict) -> int:
            destination = mount["destination"]
            value = 0
            if destination == DEFAULT_READER_LIBRARY:
                value += 100
            if "ermao" in destination.lower():
                value += 40
            if destination == "/libraries/uploads":
                value += 30
            if "book" in destination.lower() or "library" in destination.lower():
                value += 10
            return value

        return max(candidates, key=score)

    @staticmethod
    def _pick_ermao_library_dir(
        ermao_mounts: list[dict], source: str
    ) -> str:
        """找到二毛容器内与 Reader 书库同一个宿主机目录的挂载点。"""
        shared = [
            mount
            for mount in ermao_mounts
            if source and mount.get("source") == source
        ]
        candidates = shared or [
            mount
            for mount in ermao_mounts
            if any(
                keyword in mount["destination"].lower()
                for keyword in ("ermao", "librar", "book")
            )
        ]
        if not candidates:
            return ""
        for preferred in (DEFAULT_ERMAO_LIBRARY, "/libraries/uploads"):
            for mount in candidates:
                if mount["destination"] == preferred:
                    return preferred
        return candidates[0]["destination"]

    async def _detect(self, group: AppOrchestrationInstance, db) -> dict[str, str]:
        """从应用实际部署信息中推断地址、目录与账号默认值。"""
        reader_instance = self._app_instance(group, APP_NAME)
        ermao_instance = self._app_instance(group, TARGET_APP)
        reader_entry = self._entry(group, APP_NAME)
        ermao_entry = self._entry(group, TARGET_APP)

        reader_mounts = await self._container_mounts(
            reader_instance.instance_name if reader_instance else None
        )
        ermao_mounts = await self._container_mounts(
            ermao_instance.instance_name if ermao_instance else None
        )
        source_kind = "docker"
        if not reader_mounts:
            source_kind = "config"
            reader_mounts = await self._config_mounts(reader_instance, db)
        if not ermao_mounts:
            ermao_mounts = await self._config_mounts(ermao_instance, db)

        library = self._pick_reader_library(reader_mounts)
        library_dir = library["destination"] if library else ""
        library_host_dir = library["source"] if library else ""
        ermao_library_dir = self._pick_ermao_library_dir(
            ermao_mounts, library_host_dir
        )
        if not ermao_library_dir and library_dir:
            # 两个容器挂载结构一致时可以直接沿用 Reader 的路径
            if any(
                mount["destination"] == library_dir for mount in ermao_mounts
            ):
                ermao_library_dir = library_dir

        email = str(
            ermao_entry.get("email")
            or ermao_entry.get("username")
            or ""
        ).strip()
        if not email:
            for row in ermao_entry.get("env") or []:
                if isinstance(row, dict) and str(row.get("key") or "").upper() in {
                    "EMAIL",
                    "ADMIN_EMAIL",
                    "ERMAO_EMAIL",
                }:
                    email = str(row.get("value") or "").strip()
                    break

        return {
            "reader_url": str(reader_entry.get("url") or "").strip().rstrip("/"),
            "reader_username": str(reader_entry.get("username") or "").strip(),
            "reader_password": str(reader_entry.get("password") or ""),
            "reader_token": (
                str(reader_entry.get("api_key") or "").strip()
                if reader_entry.get("auth_type") in ("api_key", "apikey")
                else ""
            ),
            "ermao_url": str(ermao_entry.get("url") or "").strip().rstrip("/"),
            "library_dir": library_dir,
            "library_host_dir": library_host_dir,
            "ermao_library_dir": ermao_library_dir,
            "ermao_email": email or DEFAULT_EMAIL,
            "ermao_password": str(ermao_entry.get("password") or ""),
            "source": source_kind,
        }

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
        """后台启动一轮自动同步，立即返回当前进度。"""
        if app_name != APP_NAME:
            raise APIException("该应用不支持自动同步", 400)
        group = await self._load_group(db, group_id)
        if self._app_instance(group, app_name) is None:
            raise APIException("应用不在该实例组中", 404)

        entry = self._entry(group, app_name)
        config = normalize_config(entry.get("auto_sync"))
        detected = await self._detect(group, db)
        for key in ("reader_url", "library_dir", "library_host_dir"):
            if not config[key]:
                config[key] = detected.get(key, "")
        for key in (
            "reader_username",
            "reader_password",
            "reader_token",
            "ermao_password",
        ):
            if not config[key]:
                config[key] = detected.get(key, "")
        for key in ("ermao_library_dir", "ermao_url"):
            if not config[key]:
                config[key] = detected.get(key, "")
        self._apply_provisioned_credentials(group, config)
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
        logger.info("Reader 自动同步调度器已启动（检查间隔 %.0f 秒）", interval_seconds)

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
                logger.exception("Reader 自动同步调度检查失败")

    async def tick(self) -> None:
        """检查所有启用自动同步的实例组，到点则后台运行。"""
        from app.services.licensing import licensing_service

        async with async_session() as db:
            try:
                await licensing_service.require_valid(db)
            except APIException:
                logger.info("授权无效，跳过 Reader 自动同步调度")
                return
            groups = list(
                (await db.execute(select(AppOrchestrationInstance))).scalars()
            )
        for group in groups:
            entry = (group.app_configs or {}).get(APP_NAME)
            if not isinstance(entry, dict):
                continue
            config = normalize_config(entry.get("auto_sync"))
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
                    "实例组 %s 自动同步配置无效，已跳过：%s", group.id, exc.message
                )
            except Exception:
                logger.exception("实例组 %s 自动同步启动失败", group.id)

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
                "exported": 0,
                "cached": 0,
                "message": "",
                "started_at": _now_iso(),
                "finished_at": None,
            }
            try:
                async with async_session() as db:
                    group = await self._load_group(db, group_id)
                    entry = self._entry(group, APP_NAME)
                    config = normalize_config(entry.get("auto_sync"))
                    detected = await self._detect(group, db)
                for key in ("reader_url", "library_dir", "library_host_dir"):
                    if not config[key]:
                        config[key] = detected.get(key, "")
                for key in (
                    "reader_username",
                    "reader_password",
                    "reader_token",
                    "ermao_password",
                ):
                    if not config[key]:
                        config[key] = detected.get(key, "")
                for key in ("ermao_library_dir", "ermao_url"):
                    if not config[key]:
                        config[key] = detected.get(key, "")
                if not config["ermao_email"]:
                    config["ermao_email"] = (
                        detected.get("ermao_email") or DEFAULT_EMAIL
                    )
                self._apply_provisioned_credentials(group, config)
                validate_config(config)

                library_root = Path(config["library_host_dir"])
                library_root.mkdir(parents=True, exist_ok=True)
                if not os.access(library_root, os.W_OK):
                    raise APIException(
                        f"书库目录不可写: {library_root}", 400
                    )

                started = time.monotonic()
                stats = await self._run_shelf(
                    group_id, config, library_root, started, force, result
                )
                result.update(stats)
                result["message"] = self._result_message(result)

                scanned = False
                if stats["exported"] > 0 and config["scan_enabled"]:
                    scan_ok, scan_message, lib_id = await self._scan_ermao(config)
                    result["ermao"] = {
                        "ok": scan_ok,
                        "message": scan_message,
                    }
                    scanned = scan_ok
                    if lib_id:
                        await self._persist_updates(
                            group_id, {"ermao_lib_id": lib_id}
                        )
                result["status"] = (
                    "ok"
                    if result["failed"] == 0
                    else ("partial" if result["exported"] else "error")
                )
                result["scanned"] = scanned
            except Exception as exc:
                logger.exception("实例组 %s 自动同步执行失败", group_id)
                result.update(status="error", message=f"执行失败: {exc}")
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
                    logger.exception("实例组 %s 自动同步结果落库失败", group_id)

    async def _run_shelf(
        self,
        group_id: int,
        config: dict[str, Any],
        library_root: Path,
        started: float,
        force: bool,
        result: dict[str, Any],
    ) -> dict[str, int]:
        """遍历 Reader 书架，缓存章节并导出 EPUB。"""
        states = await self._load_states(group_id)
        examined = 0
        failed = 0
        exported = 0
        cached_total = 0
        total_books = 0
        deadline = started + max(config["max_seconds"], 60)

        self._update_progress(
            group_id, stage="scanning", message="读取 Reader 书架"
        )
        async with ReaderSession(
            config["reader_url"],
            config["reader_token"],
            username=config["reader_username"],
            password=config["reader_password"],
        ) as session:
            shelf = await session.shelf()
            total_books = len(shelf)
            self._update_progress(
                group_id,
                total=total_books,
                processed=0,
                failed=0,
                message=f"书架共 {total_books} 本",
            )

            for item in shelf:
                if time.monotonic() > deadline:
                    self._update_progress(
                        group_id, message="达到单轮时间上限，剩余书籍下轮继续"
                    )
                    break

                name = str(item.get("name") or "").strip()
                book_url = str(item.get("bookUrl") or "").strip()
                if not book_url:
                    continue
                if book_url.startswith("storage/"):
                    logger.info("%s: 本地书，跳过", name)
                    examined += 1
                    self._update_progress(
                        group_id, processed=examined, failed=failed
                    )
                    continue

                state = states.get(book_url)
                if state is None:
                    state = ReaderAutoSyncBook(
                        group_id=group_id,
                        book_url=book_url,
                        name=name,
                        status="pending",
                        attempts=0,
                        exported_total=-1,
                        total_chapters=0,
                    )
                    states[book_url] = state

                examined += 1
                try:
                    total = await self._chapter_total(session, book_url, item)
                    if total <= 0:
                        logger.info("%s: 目录为空，跳过", name)
                        self._update_progress(
                            group_id, processed=examined, failed=failed
                        )
                        continue

                    cached = int(item.get("cachedChapterCount") or 0)
                    destination = library_root / f"{safe_name(name)}.epub"
                    if not force and self._should_skip(state, total, destination):
                        self._update_progress(
                            group_id, processed=examined, failed=failed
                        )
                        continue

                    self._update_progress(
                        group_id,
                        stage="caching",
                        message=(
                            f"{name}: 目录 {total} 章，已缓存 {cached} 章"
                        ),
                    )
                    added, timed_out = await self._cache_missing(
                        group_id,
                        session,
                        book_url,
                        name,
                        cached,
                        total,
                        config,
                        deadline,
                    )
                    cached_total += added

                    need_export = (
                        not destination.is_file()
                        or total != self._exported_total(state)
                        or added > 0
                    )
                    if need_export and not timed_out:
                        self._update_progress(
                            group_id,
                            stage="exporting",
                            message=f"导出 {name}",
                        )
                        await self._export_book(
                            session, book_url, name, destination
                        )
                        state.status = "done"
                        state.destination = str(destination)
                        state.exported_total = total
                        state.total_chapters = total
                        state.attempts = 0
                        state.last_error = None
                        state.exported_at = _now()
                        await self._save_state(state)
                        exported += 1
                    else:
                        state.total_chapters = total
                        if state.status != "done":
                            state.status = "pending"
                        await self._save_state(state)
                except Exception as exc:
                    failed += 1
                    logger.warning("实例组 %s 同步 %s 失败: %s", group_id, name, exc)
                    state.status = "failed"
                    state.attempts = (state.attempts or 0) + 1
                    state.last_error = str(exc)
                    if (state.total_chapters or 0) <= 0:
                        state.total_chapters = int(
                            item.get("totalChapterNum") or 0
                        )
                    await self._save_state(state)

                self._update_progress(
                    group_id, processed=examined, failed=failed
                )

        return {
            "processed": examined - failed,
            "failed": failed,
            "exported": exported,
            "cached": cached_total,
            "total": total_books,
        }

    async def _chapter_total(
        self,
        session: ReaderSession,
        book_url: str,
        item: dict[str, Any],
    ) -> int:
        """读取实时目录章数，失败时退回书架记录。"""
        try:
            return len(await session.chapter_list(book_url))
        except ReaderClientError as exc:
            logger.info("%s: 目录接口失败，改用书架章数: %s", item.get("name"), exc)
            try:
                return int(item.get("totalChapterNum") or 0)
            except (TypeError, ValueError):
                return 0

    @staticmethod
    def _exported_total(state: ReaderAutoSyncBook) -> int:
        value = state.exported_total
        return value if value is not None else -1

    def _should_skip(
        self,
        state: ReaderAutoSyncBook,
        total: int,
        destination: Path,
    ) -> bool:
        """已完成且文件仍在、章数未变化时跳过；失败书籍按退避跳过。"""
        if (
            state.status == "done"
            and destination.is_file()
            and total == self._exported_total(state)
        ):
            return True
        if state.status != "failed":
            return False
        if total != (state.total_chapters or 0):
            # 书籍更新了，重新给满重试次数
            state.attempts = 0
            return False
        attempts = state.attempts or 0
        if attempts >= MAX_ATTEMPTS:
            return True
        if attempts <= 0:
            return False
        delay = timedelta(minutes=min(10 * 2 ** (attempts - 1), 360))
        updated = state.updated_at or state.created_at
        if updated and _now() - updated < delay:
            return True
        return False

    async def _cache_missing(
        self,
        group_id: int,
        session: ReaderSession,
        book_url: str,
        name: str,
        cached: int,
        total: int,
        config: dict[str, Any],
        deadline: float,
    ) -> tuple[int, bool]:
        """补齐缺失章节，返回（新增章数, 是否因超时中断）。"""
        if not config["cache_missing_chapters"] or cached >= total:
            return 0, False

        added = 0
        for index in range(cached, total):
            if time.monotonic() > deadline:
                return added, True
            success = False
            for _attempt in range(3):
                try:
                    success = await session.cache_chapter(book_url, index)
                except ReaderClientError as exc:
                    logger.info("%s: 缓存第 %d 章失败: %s", name, index + 1, exc)
                if success:
                    break
                await asyncio.sleep(1.0)
            if success:
                added += 1
            if (index + 1) % 50 == 0 or index + 1 == total:
                self._update_progress(
                    group_id,
                    message=f"{name} 缓存进度 {index + 1}/{total}",
                )
        return added, False

    async def _export_book(
        self,
        session: ReaderSession,
        book_url: str,
        name: str,
        destination: Path,
    ) -> None:
        """下载 EPUB、修复 XML 并原子替换目标文件。"""
        part = destination.with_name(f".{destination.name}.part")
        fixed = destination.with_name(f".{destination.name}.fixed")
        try:
            await session.export_epub(book_url, part)
            try:
                outcome = await asyncio.to_thread(fix_epub, part, fixed)
            except Exception as exc:
                part.replace(destination)
                _chown(destination)
                raise ReaderClientError(
                    f"EPUB 规范化失败（已保留原始文件）: {exc}"
                ) from exc
            fixed.replace(destination)
            _chown(destination)
            logger.info(
                "%s: 已导出 %s（%d 条目，修复前 XML 失败 %d，修复后失败 %d）",
                name,
                destination,
                outcome.entries,
                len(outcome.failures_before),
                len(outcome.failures_after),
            )
        finally:
            part.unlink(missing_ok=True)
            fixed.unlink(missing_ok=True)

    async def _scan_ermao(
        self, config: dict[str, Any]
    ) -> tuple[bool, str, str | None]:
        """登录二毛、必要时初始化，然后触发书库扫描并等待任务结束。"""
        if not config["ermao_url"]:
            return False, "实例组中没有二毛读书，已跳过扫描", None
        note = ""
        try:
            async with ErmaoClient(config["ermao_url"]) as client:
                initialized = await client.setup_status()
                if not initialized:
                    if not config["auto_setup"]:
                        return False, "二毛尚未初始化，自动初始化已关闭", None
                    if not config["ermao_password"]:
                        return False, "二毛尚未初始化，且没有可用密码", None
                    if len(config["ermao_password"]) < 10:
                        return (
                            False,
                            "二毛尚未初始化，自动创建账号的密码至少需要 10 位",
                            None,
                        )
                    await client.setup(
                        config["ermao_email"], config["ermao_password"]
                    )
                    note = "已自动初始化二毛账号；"
                else:
                    try:
                        await client.login(
                            config["ermao_email"], config["ermao_password"]
                        )
                    except ErmaoClientError as exc:
                        return (
                            False,
                            f"二毛登录失败: {exc}（如已手动初始化，请填写原账号密码）",
                            None,
                        )

                libraries = await client.libraries()
                library = self._match_library(libraries, config)
                if library is None and config["auto_setup"]:
                    root = config["ermao_library_dir"] or config["library_dir"]
                    if root:
                        library = await client.create_library(
                            root, "Reader 书库"
                        )
                if library is None:
                    return False, note + "二毛中没有匹配的书库", None

                library_id = str(library.get("id") or "")
                before = {
                    str(task.get("id"))
                    for task in await client.import_tasks(library_id)
                }
                await client.scan(library_id)

                deadline = time.monotonic() + config["scan_wait_seconds"]
                tasks: list[dict[str, Any]] = []
                while time.monotonic() < deadline:
                    await asyncio.sleep(2.0)
                    tasks = await client.import_tasks(library_id)
                    if not any(
                        str(task.get("state")) in RUNNING_TASK_STATES
                        for task in tasks
                    ):
                        break

                summary = self._task_summary(tasks, before)
                return True, f"{note}扫描书库: {summary}", library_id
        except (ErmaoClientError, ReaderClientError) as exc:
            return False, f"二毛扫描失败: {exc}", None
        except Exception as exc:  # noqa: BLE001 - 扫描失败不应推翻导出结果
            logger.exception("二毛扫描出现未预期错误")
            return False, f"二毛扫描失败: {exc}", None

    @staticmethod
    def _match_library(
        libraries: list[dict[str, Any]], config: dict[str, Any]
    ) -> dict[str, Any] | None:
        """按配置的书库 ID 或目录匹配现有书库。"""
        library_id = config["ermao_lib_id"]
        for library in libraries:
            if library_id and str(library.get("id")) == library_id:
                return library
        for root in (config["ermao_library_dir"], config["library_dir"]):
            if not root:
                continue
            for library in libraries:
                if _same_path(str(library.get("rootPath") or ""), root):
                    return library
        if len(libraries) == 1:
            return libraries[0]
        return None

    @staticmethod
    def _task_summary(
        tasks: list[dict[str, Any]], before: set[str]
    ) -> str:
        """汇总本轮新增的导入任务。"""
        labels: list[str] = []
        for task in tasks:
            if str(task.get("id")) in before:
                continue
            label = (
                str(task.get("sourceName") or task.get("bookTitle") or "").strip()
                or str(task.get("id") or "?")
            )
            state = str(task.get("state") or "?")
            error = str(task.get("errorSummary") or "").strip()
            labels.append(f"{label}={state}" + (f"({error})" if error else ""))
            if len(labels) >= 10:
                break
        return ", ".join(labels) if labels else "无新任务"

    @staticmethod
    def _result_message(result: dict[str, Any]) -> str:
        if not result["total"]:
            return "书架为空，没有需要同步的书籍"
        message = (
            f"同步完成：检查 {result['processed'] + result['failed']} 本，"
            f"导出 {result['exported']} 本，缓存 {result['cached']} 章"
        )
        if result["failed"]:
            message += f"，失败 {result['failed']} 本"
        return message

    # ------------------------------------------------------------------
    # 状态读写
    # ------------------------------------------------------------------
    async def _load_states(self, group_id: int) -> dict[str, ReaderAutoSyncBook]:
        async with async_session() as db:
            rows = list(
                (
                    await db.execute(
                        select(ReaderAutoSyncBook).where(
                            ReaderAutoSyncBook.group_id == group_id
                        )
                    )
                ).scalars()
            )
            for row in rows:
                db.expunge(row)
            return {row.book_url: row for row in rows}

    async def _save_state(self, state: ReaderAutoSyncBook) -> None:
        async with async_session() as db:
            await db.merge(state)
            await db.commit()

    async def _persist_updates(self, group_id: int, updates: dict[str, Any]) -> None:
        """把首次运行解析出的书库 ID 等写回配置。"""
        async with async_session() as db:
            group = await self._load_group(db, group_id)
            app_configs = dict(group.app_configs or {})
            entry = dict(app_configs.get(APP_NAME) or {})
            auto = dict(entry.get("auto_sync") or {})
            for key, value in updates.items():
                if value:
                    auto[key] = value
            entry["auto_sync"] = auto
            app_configs[APP_NAME] = entry
            group.app_configs = app_configs
            await db.commit()

    async def _persist_result(self, group_id: int, result: dict[str, Any]) -> None:
        async with async_session() as db:
            group = await self._load_group(db, group_id)
            app_configs = dict(group.app_configs or {})
            entry = dict(app_configs.get(APP_NAME) or {})
            auto = dict(entry.get("auto_sync") or {})
            auto["last_run_at"] = result.get("finished_at") or _now_iso()
            auto["last_result"] = result
            entry["auto_sync"] = auto
            app_configs[APP_NAME] = entry
            group.app_configs = app_configs
            await db.commit()

    def _update_progress(self, group_id: int, **fields: Any) -> None:
        info = self._runs.setdefault(group_id, {})
        info.update(fields)


reader_auto_service = ReaderAutoSyncService()
