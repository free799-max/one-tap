"""订阅与下载状态同步。

后台按间隔读取 MoviePilot / Suwayomi / Reader / music-dl 的状态，
把变化写入本地台账并生成通知事件。首次同步只建立基线，不补发历史通知。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Any

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session
from app.models.notification import (
    NotificationSyncState,
    ResourceSubscription,
)
from app.services.mcp.config import mcp_config_service
from app.services.notifications import events, messages
from app.services.notifications.presentation import format_size
from app.services.resources import comic_auto, mediary_scout, musicdl, reader, suwayomi
from app.services.resources import moviepilot
from app.services.resources.base import candidate_apps

logger = logging.getLogger(__name__)

_VIDEO_HISTORY_LIMIT = 50
_SYNC_PAGE_SIZE = 100
_SYNC_CLOUD_HISTORY_LIMIT = 300

# 巡影工作流通知 kind -> OneTap 事件类型；每日巡检汇总与「已是最新」不单独推送
_CLOUD_EVENT_TYPES = {
    "movie_init": "organize.completed",
    "series_initialized": "organize.completed",
    "tracking_initialized": "organize.completed",
    "episodes_restored": "subscription.updated",
    "tracking_completed": "subscription.completed",
    "no_coverage": "action.failed",
    "transfer_failed": "action.failed",
    "replacement_done": "subscription.replaced",
}

# 通知正文第一行是「📺 片名」，其余行带 emoji 前缀（✅ 状态 / 🎞 体积 / 🔍 原因）
_LEAD_EMOJI = re.compile(r"^[^\w\u4e00-\u9fff（(]+", re.UNICODE)
# 集号/话号列表的分隔符：E01、E02 或 1，2
_LIST_SEPARATORS = re.compile(r"[、,，]")
_LIST_ITEM = re.compile(r"([A-Za-z]*)(\d+)")


def _compact_list_line(line: str) -> str:
    """把「本次新增：E01、E02、…、E35」收成「本次新增：E01–E35」。"""
    label, sep, rest = line.partition("：")
    if not sep:
        return line
    items = [
        item.strip()
        for item in _LIST_SEPARATORS.split(rest)
        if item.strip()
    ]
    if len(items) < 4:
        return line
    matches = [_LIST_ITEM.fullmatch(item) for item in items]
    if any(match is None for match in matches):
        return line
    prefix = matches[0].group(1)
    width = len(matches[0].group(2))
    if any(
        match.group(1) != prefix or len(match.group(2)) != width
        for match in matches
    ):
        return line
    numbers = [int(match.group(2)) for match in matches]
    if numbers != list(range(numbers[0], numbers[0] + len(numbers))):
        return line
    return f"{label}：{items[0]}–{items[-1]}"


_SEASON_SUFFIX = re.compile(r"\s*第\s*[0-9一二三四五六七八九十百]+\s*季\s*$")


def _cloud_title_key(value: str) -> str:
    """「天下第一 第 1 季」和库里的「天下第一」归一成同一个 key。"""
    return _SEASON_SUFFIX.sub("", str(value or "").strip())


def _notification_detail(body: str, title: str | None = None) -> str | None:
    """从巡影通知正文里提取可放在 OneTap 通知详情里的短文本。"""
    lines = [line.strip() for line in (body or "").splitlines() if line.strip()]
    parts: list[str] = []
    for line in lines[1:]:
        cleaned = _LEAD_EMOJI.sub("", line).strip()
        if not cleaned or cleaned == "已获取入库":
            continue
        # 正文里偶尔会再写一遍片名，标题已经展示过
        if title and cleaned.startswith(title) and len(cleaned) <= len(title) + 8:
            continue
        parts.append(_compact_list_line(cleaned))
    detail = " · ".join(parts)
    return detail[:300] or None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _progress(value: Any) -> float:
    """下载进度归一成 0~1；部分下载器给的是百分比。"""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return 0.0
    if amount > 1:
        amount /= 100
    return max(0.0, min(amount, 1.0))


def _download_hash(item: dict[str, Any]) -> str:
    return str(item.get("download_hash") or item.get("hash") or "").strip().lower()


def _poster(item: dict[str, Any]) -> str | None:
    for key in ("poster", "image", "cover", "thumbnailUrl", "pic"):
        value = item.get(key)
        if value:
            return str(value)
    return None


def _history_season(item: dict[str, Any]) -> int:
    match = re.search(r"S(\d+)", str(item.get("seasons") or ""))
    if match:
        return int(match.group(1))
    return _int(item.get("season"), 0)


_VIDEO_META_KEYS = ("type", "media_source", "season", "resolution", "quality")
_VIDEO_DETAIL_KEYS = (
    "genres",
    "rating",
    "vote_count",
    "runtime",
    "directors",
    "actors",
    "release_date",
)
# 老订阅没有详情元数据时补抓一次；失败后按天重试，避免每个同步周期都打上游
_VIDEO_META_RETRY_SECONDS = 86400
# 每次同步重建 last_seen 时要保留的字段（详情元数据只补抓一次，不能被冲掉）
_VIDEO_PRESERVED_KEYS = (
    *_VIDEO_META_KEYS,
    *_VIDEO_DETAIL_KEYS,
    "meta_fetched_at",
)


def _video_options(record: ResourceSubscription, **extra: Any) -> dict[str, Any]:
    """影视卡片字段：类型/年份/媒体源/季/画质/详情元数据，来自订阅台账。"""
    last_seen = dict(record.last_seen or {})
    ref = dict(record.upstream_ref or {})
    options: dict[str, Any] = {
        "type": last_seen.get("type"),
        "media_source": last_seen.get("media_source") or ref.get("media_source"),
        "season": last_seen.get("season", ref.get("season")),
        "resolution": last_seen.get("resolution"),
        "quality": last_seen.get("quality"),
        "year": record.year,
    }
    for key in _VIDEO_DETAIL_KEYS:
        options[key] = last_seen.get(key)
    options.update(extra)
    return {key: value for key, value in options.items() if value not in (None, "", [])}


async def _sync_state(db: AsyncSession, key: str) -> dict:
    record = (
        await db.execute(
            sa.select(NotificationSyncState).where(NotificationSyncState.key == key)
        )
    ).scalar_one_or_none()
    return dict(record.value or {}) if record else {}


async def _save_sync_state(db: AsyncSession, key: str, value: dict) -> None:
    record = (
        await db.execute(
            sa.select(NotificationSyncState).where(NotificationSyncState.key == key)
        )
    ).scalar_one_or_none()
    if record is None:
        db.add(NotificationSyncState(key=key, value=value))
    else:
        record.value = value


class NotificationSyncService:
    _FAST_SYNC_INTERVAL_SECONDS = 180
    _FAST_SYNC_NAMES = {"_sync_novel", "_sync_music"}
    _SCHEDULER_CHECK_SECONDS = 30

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._last_run: dict[str, float] = {}

    def start_scheduler(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(
            self._loop(settings.NOTIFICATION_SYNC_INTERVAL_SECONDS)
        )

    async def stop_scheduler(self) -> None:
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def _syncs(self):
        return (
            self._sync_video,
            self._sync_comic,
            self._sync_novel,
            self._sync_music,
        )

    def _due_syncs(self, interval_seconds: float, now: float) -> set[str]:
        due = set()
        for sync in self._syncs():
            interval = (
                self._FAST_SYNC_INTERVAL_SECONDS
                if sync.__name__ in self._FAST_SYNC_NAMES
                else interval_seconds
            )
            last_run = self._last_run.get(sync.__name__)
            if last_run is None or now - last_run >= interval:
                due.add(sync.__name__)
        return due

    async def _loop(self, interval_seconds: float) -> None:
        while True:
            now = asyncio.get_running_loop().time()
            due = self._due_syncs(interval_seconds, now)
            try:
                if due:
                    await self.tick(due)
            except Exception as exc:  # noqa: BLE001 - 后台循环不能退出
                logger.warning("通知同步失败：%s", exc)
            for name in due:
                self._last_run[name] = now
            await asyncio.sleep(self._SCHEDULER_CHECK_SECONDS)

    async def tick(self, sync_names: set[str] | None = None) -> None:
        async with async_session() as db:
            for sync in self._syncs():
                if sync_names is not None and sync.__name__ not in sync_names:
                    continue
                try:
                    await sync(db)
                    await db.commit()
                except Exception as exc:  # noqa: BLE001 - 单域失败不影响其他域
                    await db.rollback()
                    logger.warning("同步 %s 失败：%s", sync.__name__, exc)

    async def _sync_video(self, db: AsyncSession) -> None:
        states = await mcp_config_service.list_upstreams(
            db, settings.NOTIFICATION_SYNC_HOST, app_names={"moviepilot"}
        )
        for state in states:
            if not state.available:
                continue
            data = await moviepilot.subscriptions(
                db, instance_id=state.app.instance_id, page=1, count=_SYNC_PAGE_SIZE
            )
            items = data.get("items") or []
            if isinstance(items, dict):
                items = items.get("items") or []
            for item in items:
                if isinstance(item, dict):
                    await self._sync_video_item(db, state.app.instance_id, item)
            await self._sync_video_history(db, state)
        await self._sync_cloud_video(db)

    async def _sync_cloud_video(self, db: AsyncSession) -> None:
        """巡影网盘方案：把它自己的获取/入库/失败通知转成 OneTap 通知。"""
        apps = await candidate_apps(db, ("mediary-scout",))
        for app in apps:
            if not (app.api_key or (app.env_secrets or {}).get("api_key")):
                continue
            await self._sync_cloud_instance(db, app)

    async def _sync_cloud_instance(self, db: AsyncSession, app) -> None:
        try:
            activity = await mediary_scout.downloads(
                db, instance_id=app.instance_id, limit=_SYNC_PAGE_SIZE
            )
        except Exception as exc:  # noqa: BLE001 - 上游不可用不阻塞其他域
            logger.warning("读取巡影任务失败（%s）：%s", app.instance_id, exc)
            return

        recent = [
            item
            for item in activity.get("recent") or []
            if isinstance(item, dict) and item.get("id")
        ]
        state_key = f"mediary_scout:{app.instance_id}"
        state = await _sync_state(db, state_key)
        if not state.get("initialized"):
            # 首次同步只建立基线，不补发历史通知
            await _save_sync_state(
                db,
                state_key,
                {
                    "initialized": True,
                    "seen": [
                        str(item["id"]) for item in recent
                    ][-_SYNC_CLOUD_HISTORY_LIMIT:],
                },
            )
            return

        seen = [str(item) for item in state.get("seen") or []]
        seen_set = set(seen)
        pending = [item for item in recent if str(item["id"]) not in seen_set]
        pending.sort(key=lambda item: str(item.get("createdAt") or ""))
        library = await self._cloud_library(db, app) if pending else {}
        for item in pending:
            await self._emit_cloud_notification(db, app, item, library)
            seen.append(str(item["id"]))
        await _save_sync_state(
            db,
            state_key,
            {
                "initialized": True,
                "seen": seen[-_SYNC_CLOUD_HISTORY_LIMIT:],
            },
        )

    async def _cloud_library(self, db: AsyncSession, app) -> dict[str, dict]:
        """巡影媒体库按标题索引，用来补 tmdbId/类型/季号。"""
        try:
            rows = await mediary_scout.library_raw(
                db, instance_id=app.instance_id
            )
        except Exception as exc:  # noqa: BLE001 - 拿不到库只影响卡片丰富度
            logger.warning("读取巡影媒体库失败（%s）：%s", app.instance_id, exc)
            return {}
        library: dict[str, dict] = {}
        for item in rows:
            key = _cloud_title_key(str(item.get("title") or ""))
            if key:
                library[key] = item
        return library

    async def _cloud_metadata(
        self,
        db: AsyncSession,
        record: ResourceSubscription | None,
        library_item: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """巡影通知需要的详情：优先复用台账，其次问 TMDB。"""
        last_seen = dict(record.last_seen or {}) if record else {}
        library_item = library_item or {}
        tmdb_id = library_item.get("tmdbId")
        if record is not None:
            tmdb_id = tmdb_id or (record.upstream_ref or {}).get("media_id")
        media_type = str(
            library_item.get("type") or last_seen.get("type") or ""
        ).strip()
        if not tmdb_id:
            return {"tmdb_id": None, "media_type": media_type, "detail": {}}
        detail = await mediary_scout.tmdb_metadata(tmdb_id, media_type)
        return {"tmdb_id": tmdb_id, "media_type": media_type, "detail": detail}

    async def _emit_cloud_notification(
        self,
        db: AsyncSession,
        app,
        item: dict[str, Any],
        library: dict[str, dict] | None = None,
    ) -> None:
        kind = str(item.get("kind") or "").lower()
        event_type = _CLOUD_EVENT_TYPES.get(kind)
        if event_type is None:
            return
        raw_title = str(item.get("title") or "").strip()
        record = await self._find_cloud_subscription(
            db, app.instance_id, raw_title
        )
        library_item = (library or {}).get(_cloud_title_key(raw_title))
        if library_item is None:
            library_item = (library or {}).get(raw_title)
        last_seen = dict(record.last_seen or {}) if record is not None else {}
        needs_meta = (
            record is None
            or not record.poster
            or not last_seen.get("genres")
            or not last_seen.get("directors")
        )
        # 巡影里直接订阅的条目 OneTap 没有台账，这里补一条并带上卡片元数据
        if needs_meta:
            meta = await self._cloud_metadata(db, record, library_item)
        else:
            meta = {"tmdb_id": None, "media_type": "", "detail": {}}
        detail = meta["detail"]
        if record is None and library_item:
            record = await self._create_cloud_subscription(
                db, app, library_item, detail, raw_title
            )
        elif record is not None and detail:
            self._merge_cloud_metadata(record, detail)
        title = record.title if record is not None else raw_title
        detail_text = _notification_detail(str(item.get("body") or ""), title)
        if event_type == "organize.completed":
            headline, markdown, text = messages.organize_completed(
                "video", title, detail=detail_text, variant="cloud"
            )
        elif event_type == "subscription.updated":
            headline, markdown, text = messages.updated(
                "video", title, detail=detail_text or "新增集数已入库"
            )
        elif event_type == "subscription.completed":
            headline, markdown, text = messages.completed(
                "video", title, detail=detail_text or "已补齐全部已播集数"
            )
        elif event_type == "subscription.replaced":
            headline, markdown, text = messages.replaced(
                "video", title, variant="cloud"
            )
        else:
            headline, markdown, text = messages.failed(
                "video", title, detail=detail_text
            )
        options = (
            _video_options(record)
            if record is not None
            else {"type": "电影" if meta["media_type"] == "movie" else "电视剧"}
        )
        options = {**options, "variant": "cloud"}
        await events.emit(
            db,
            event_type=event_type,
            title=headline,
            markdown=markdown,
            text=text,
            domain="video",
            subject_key=record.subject_key if record is not None else None,
            subscription=record,
            image_url=(record.poster if record is not None else None)
            or detail.get("poster"),
            payload={
                "variant": "cloud",
                "instance_id": app.instance_id,
                "kind": kind,
                "notification_id": item.get("id"),
                "body": item.get("body"),
                "options": options,
            },
            dedupe_key=f"mediary_scout:{app.instance_id}:{item.get('id')}",
        )
        if record is not None and event_type in {
            "organize.completed",
            "subscription.completed",
        }:
            record.status = "completed"

    async def _create_cloud_subscription(
        self,
        db: AsyncSession,
        app,
        library_item: dict[str, Any],
        detail: dict[str, Any],
        fallback_title: str,
    ) -> ResourceSubscription:
        tmdb_id = str(library_item.get("tmdbId") or "").strip()
        type_name = "电影" if str(library_item.get("type")) == "movie" else "电视剧"
        season = _int(library_item.get("seasonNumber"), 1) or 1
        poster = str(detail.get("poster") or "")
        last_seen: dict[str, Any] = {
            "type": type_name,
            "media_source": "mediary-scout",
            "season": season,
            "total_episode": _int(library_item.get("totalEpisodes")),
            "lack_episode": max(
                _int(library_item.get("totalEpisodes"))
                - _int(library_item.get("obtainedCount")),
                0,
            ),
        }
        for key in _VIDEO_DETAIL_KEYS:
            if detail.get(key) not in (None, "", []):
                last_seen[key] = detail[key]
        return await events.upsert_subscription(
            db,
            domain="video",
            subject_key=f"video:mediary-scout:{tmdb_id}:S{season}",
            title=str(library_item.get("title") or fallback_title),
            year=str(library_item.get("year") or detail.get("year") or "") or None,
            poster=poster or None,
            upstream_ref={
                "instance_id": app.instance_id,
                "media_source": "mediary-scout",
                "media_id": tmdb_id or None,
                "season": season,
            },
            last_seen=last_seen,
            origin="sync",
        )

    @staticmethod
    def _merge_cloud_metadata(
        record: ResourceSubscription, detail: dict[str, Any]
    ) -> None:
        last_seen = dict(record.last_seen or {})
        for key in _VIDEO_DETAIL_KEYS:
            if detail.get(key) not in (None, "", []) and not last_seen.get(key):
                last_seen[key] = detail[key]
        record.last_seen = last_seen
        if not record.poster and detail.get("poster"):
            record.poster = detail["poster"]
        if not record.year and detail.get("year"):
            record.year = str(detail["year"])

    async def _find_cloud_subscription(
        self, db: AsyncSession, instance_id: int, title: str
    ) -> ResourceSubscription | None:
        """按标题把巡影通知挂回订阅台账（剧集通知会带季号）。"""
        if not title:
            return None
        records = (
            await db.execute(
                sa.select(ResourceSubscription).where(
                    ResourceSubscription.domain == "video"
                )
            )
        ).scalars()
        best: ResourceSubscription | None = None
        for record in records:
            ref = record.upstream_ref or {}
            if ref.get("media_source") != "mediary-scout":
                continue
            if _int(ref.get("instance_id")) not in (0, instance_id):
                continue
            name = str(record.title or "")
            if not name:
                continue
            if not (
                title == name or title.startswith(name) or name.startswith(title)
            ):
                continue
            if best is None or len(name) > len(str(best.title or "")):
                best = record
        return best

    async def _enrich_video_meta(
        self,
        db: AsyncSession,
        instance_id: int,
        record: ResourceSubscription,
        item: dict[str, Any],
    ) -> None:
        """给缺少详情元数据的影视订阅补一次（类型/评分/时长/演职员/海报）。"""
        last_seen = dict(record.last_seen or {})
        if last_seen.get("genres"):
            return
        fetched_at = str(last_seen.get("meta_fetched_at") or "")
        if fetched_at:
            try:
                elapsed = (_now() - datetime.fromisoformat(fetched_at)).total_seconds()
                if elapsed < _VIDEO_META_RETRY_SECONDS:
                    return
            except ValueError:
                pass
        ref = dict(record.upstream_ref or {})
        media_id = str(ref.get("media_id") or item.get("media_id") or "")
        if not media_id:
            return
        meta = await moviepilot.fetch_metadata(
            db,
            media_id=media_id,
            media_source=str(ref.get("media_source") or ""),
            type_name=str(last_seen.get("type") or item.get("type") or ""),
            instance_id=instance_id,
        )
        last_seen["meta_fetched_at"] = _now().isoformat()
        for key in _VIDEO_DETAIL_KEYS:
            if meta.get(key) not in (None, "", []):
                last_seen[key] = meta[key]
        if not last_seen.get("type") and meta.get("type"):
            last_seen["type"] = meta["type"]
        record.last_seen = last_seen
        if not record.poster and meta.get("poster"):
            record.poster = meta["poster"]
        if not record.year and meta.get("year"):
            record.year = meta["year"]
        await db.flush()

    async def _sync_video_item(
        self, db: AsyncSession, instance_id: int, item: dict[str, Any]
    ) -> None:
        media_source = str(item.get("media_source") or "unknown")
        media_id = str(item.get("media_id") or item.get("id") or "")
        season = _int(item.get("season"), 0)
        subject_key = f"video:{media_source}:{media_id}:S{season}"
        total = _int(item.get("total_episode"))
        lack = _int(item.get("lack_episode"))
        title = str(item.get("name") or item.get("title") or "")
        year = str(item.get("year") or "") or None
        poster = _poster(item)
        last_seen = {
            # 订阅时写入的类型/媒体源/画质等元数据要保留，供后续事件卡片展示
            "type": str(item.get("type") or ""),
            "media_source": media_source,
            "season": season,
            "subscribe_id": item.get("id"),
            "total_episode": total,
            "lack_episode": lack,
            "state": item.get("state"),
            "last_update": item.get("last_update"),
        }

        existing = (
            await db.execute(
                sa.select(ResourceSubscription).where(
                    ResourceSubscription.domain == "video",
                    ResourceSubscription.subject_key == subject_key,
                )
            )
        ).scalar_one_or_none()
        if existing is None and item.get("id") is not None:
            existing = await events.find_subscription(
                db,
                domain="video",
                upstream_field="subscribe_id",
                upstream_value=item.get("id"),
            )
            if existing is not None:
                existing.subject_key = subject_key
                await db.flush()
        previous = dict(existing.last_seen or {}) if existing else {}
        for key in _VIDEO_PRESERVED_KEYS:
            if not last_seen.get(key) and previous.get(key) not in (None, ""):
                last_seen[key] = previous[key]
        record = await events.upsert_subscription(
            db,
            domain="video",
            subject_key=subject_key,
            title=title,
            upstream_ref={
                "instance_id": instance_id,
                "subscribe_id": item.get("id"),
                "media_source": media_source,
                "media_id": media_id,
                "season": season,
            },
            year=year,
            poster=poster,
            last_seen=last_seen,
        )
        await self._enrich_video_meta(db, instance_id, record, item)
        poster = poster or record.poster
        if existing is None:
            return

        previous_total = _int(previous.get("total_episode"))
        previous_lack = _int(previous.get("lack_episode"))
        if total > previous_total and previous_total > 0:
            headline, markdown, text = messages.updated(
                "video",
                title,
                count=total - previous_total,
                year=year,
            )
            await events.emit(
                db,
                event_type="subscription.updated",
                title=headline,
                markdown=markdown,
                text=text,
                domain="video",
                subject_key=subject_key,
                subscription=record,
                image_url=poster,
                payload={
                    "total_episode": total,
                    "previous": previous_total,
                    "options": _video_options(
                        record,
                        total_episode=total,
                        detail=f"新增 {total - previous_total} 集",
                    ),
                },
                dedupe_key=f"subscription.updated:{subject_key}:{total}",
            )
        if total > 0 and lack == 0 and previous_lack > 0:
            headline, markdown, text = messages.completed(
                "video", title, detail=f"共 {total} 集", year=year
            )
            await events.emit(
                db,
                event_type="subscription.completed",
                title=headline,
                markdown=markdown,
                text=text,
                domain="video",
                subject_key=subject_key,
                subscription=record,
                image_url=poster,
                payload={
                    "total_episode": total,
                    "options": _video_options(
                        record, total_episode=total, detail=f"共 {total} 集"
                    ),
                },
                dedupe_key=f"subscription.completed:{subject_key}:{total}",
            )
        if total > 0 and lack == 0:
            record.status = "completed"
        elif lack > 0:
            record.status = "active"

    async def _sync_video_history(self, db: AsyncSession, state) -> None:
        if not state.app.url or not state.app.api_key:
            return
        for kind, event_type, label in (
            ("download", "download.completed", "下载完成"),
            ("transfer", "organize.completed", "整理入库"),
        ):
            items = await self._moviepilot_history(state, kind)
            if items is None:
                continue
            key = f"moviepilot:{state.app.instance_id}:{kind}"
            sync_state = await _sync_state(db, key)
            last_id = _int(sync_state.get("last_id"), 0)
            ids = [_int(item.get("id")) for item in items if item.get("id") is not None]
            if not sync_state.get("initialized"):
                await _save_sync_state(
                    db,
                    key,
                    {"last_id": max(ids, default=0), "initialized": True},
                )
                continue
            groups: dict[tuple[str, str], dict[str, Any]] = {}
            tasks: dict[str, dict[str, Any]] | None = None
            processed_max = last_id
            for item in sorted(items, key=lambda row: _int(row.get("id"))):
                history_id = _int(item.get("id"))
                if history_id <= last_id:
                    continue
                subscription = await self._match_video_history(db, item)
                if subscription is None:
                    processed_max = history_id
                    continue
                if event_type == "download.completed":
                    download_hash = _download_hash(item)
                    if download_hash:
                        if tasks is None:
                            tasks = await self._downloader_tasks(state)
                            if tasks is None:
                                # 下载器状态读不到时先不判断，避免把还在下载的任务报成完成
                                break
                        task = tasks.get(download_hash)
                        if task is not None and _progress(task.get("progress")) < 0.999:
                            # 任务还在下载：停在这里，下一轮从同一条历史继续判断
                            break
                processed_max = history_id
                if event_type == "organize.completed":
                    status = item.get("status")
                    if status is not None and status not in (True, "true", "success", 1):
                        continue
                group_key = (subscription.subject_key, event_type)
                bucket = groups.setdefault(
                    group_key,
                    {
                        "subscription": subscription,
                        "items": [],
                        "title": "",
                        "year": subscription.year,
                        "poster": subscription.poster,
                    },
                )
                bucket["items"].append(item)
                if not bucket["title"]:
                    bucket["title"] = str(
                        item.get("title")
                        or item.get("torrent_name")
                        or subscription.title
                        or "资源"
                    )
                if not bucket["year"]:
                    bucket["year"] = str(item.get("year") or "") or subscription.year
                if not bucket["poster"]:
                    bucket["poster"] = _poster(item) or subscription.poster

            for (subject_key, grouped_event), bucket in groups.items():
                group_items = bucket["items"]
                subscription = bucket["subscription"]
                title = bucket["title"]
                year = bucket["year"]
                poster = bucket["poster"]
                latest_id = max(_int(item.get("id")) for item in group_items)
                episodes = sorted(
                    {
                        str(item.get("episodes"))
                        for item in group_items
                        if item.get("episodes")
                    }
                )
                if len(episodes) == 1:
                    detail = f"{label} · {episodes[0]}"
                elif episodes:
                    detail = f"{label} · {len(episodes)} 个文件（{episodes[0]} 等）"
                else:
                    detail = f"{label} · {len(group_items)} 个文件"
                headline, markdown, text = (
                    messages.download_completed(
                        "video", title, detail=detail, year=year
                    )
                    if grouped_event == "download.completed"
                    else messages.organize_completed(
                        "video", title, detail=detail, year=year
                    )
                )
                await events.emit(
                    db,
                    event_type=grouped_event,
                    title=headline,
                    markdown=markdown,
                    text=text,
                    domain="video",
                    subject_key=subject_key,
                    subscription=subscription,
                    image_url=poster,
                    payload={
                        "history_ids": [
                            _int(item.get("id")) for item in group_items
                        ],
                        "kind": kind,
                        "options": _video_options(subscription, detail=detail),
                    },
                    dedupe_key=f"{grouped_event}:mp:{state.app.instance_id}:{latest_id}",
                )
            if ids:
                await _save_sync_state(
                    db,
                    key,
                    {**sync_state, "last_id": processed_max, "initialized": True},
                )

    async def _match_video_history(
        self, db: AsyncSession, item: dict[str, Any]
    ) -> ResourceSubscription | None:
        """只把历史记录关联到 OneTap 已跟踪的影视订阅。"""
        rows = list(
            (
                await db.execute(
                    sa.select(ResourceSubscription).where(
                        ResourceSubscription.domain == "video"
                    )
                )
            ).scalars()
        )
        media_id = str(item.get("media_id") or "")
        season = _history_season(item)
        if media_id:
            for row in rows:
                ref = dict(row.upstream_ref or {})
                if str(ref.get("media_id") or "") == media_id and _int(
                    ref.get("season")
                ) == season:
                    return row
        title = str(item.get("title") or "")
        year = str(item.get("year") or "")
        if title:
            matches = [
                row
                for row in rows
                if (row.title or "") == title and (not year or (row.year or "") == year)
            ]
            if len(matches) == 1:
                return matches[0]
        return None

    async def _downloader_tasks(self, state) -> dict[str, dict[str, Any]] | None:
        """读取下载器任务；失败返回 None，由调用方下一轮再判断。"""
        try:
            async with asyncio.timeout(20):
                tasks = await moviepilot.task_states(state)
        except Exception as exc:  # noqa: BLE001 - 下载器异常不能中断通知同步
            logger.info("读取 MoviePilot 下载任务失败：%s", exc)
            return None
        return {str(key).lower(): value for key, value in tasks.items()}

    async def _moviepilot_history(self, state, kind: str) -> list[dict] | None:
        base = str(state.app.url or "").rstrip("/")
        if not base:
            return None
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.get(
                    f"{base}/api/v1/history/{kind}",
                    params={"page": 1, "count": _VIDEO_HISTORY_LIMIT},
                    headers={"X-API-KEY": state.app.api_key or ""},
                )
        except httpx.HTTPError as exc:
            logger.info("读取 MoviePilot %s 历史失败：%s", kind, exc)
            return None
        if response.status_code >= 400:
            logger.info(
                "读取 MoviePilot %s 历史返回 HTTP %s", kind, response.status_code
            )
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        data = payload.get("data", payload) if isinstance(payload, dict) else {}
        if isinstance(data, dict):
            items = data.get("items") or data.get("list") or []
        elif isinstance(data, list):
            items = data
        else:
            items = []
        return [item for item in items if isinstance(item, dict)]

    async def _sync_comic(self, db: AsyncSession) -> None:
        apps = await candidate_apps(db, ("suwayomi", "comic-auto"))
        for app in apps:
            is_local = app.app_name == "comic-auto"
            try:
                if is_local:
                    # 进度要逐条查章节统计，只轮询订阅中的漫画
                    data = await comic_auto.downloads(
                        db,
                        instance_id=app.instance_id,
                        limit=_SYNC_PAGE_SIZE,
                        active_only=True,
                    )
                else:
                    data = await suwayomi.downloads(
                        db, instance_id=app.instance_id, limit=_SYNC_PAGE_SIZE
                    )
            except Exception as exc:  # noqa: BLE001 - 单实例失败不影响另一套方案
                logger.warning(
                    "同步漫画实例 %s(%s) 失败：%s",
                    app.app_name,
                    app.instance_id,
                    exc,
                )
                continue
            for item in data.get("items") or []:
                await self._sync_comic_item(
                    db,
                    app.instance_id,
                    item,
                    variant="local" if is_local else "online",
                )

    async def _sync_comic_item(
        self,
        db: AsyncSession,
        instance_id: int,
        item: dict[str, Any],
        *,
        variant: str = "online",
    ) -> None:
        manga_id = str(item.get("task_id") or "")
        if not manga_id:
            return
        subject_key = f"comic:{instance_id}:{manga_id}"
        title = str(item.get("title") or "")
        total = _int(item.get("total_chapters"))
        downloaded = _int(item.get("downloaded_chapters"))
        status = str(item.get("status") or "")
        meta = {
            key: item.get(key)
            for key in (
                "author",
                "tags",
                "category",
                "comic_status",
                "cover",
                "latest_chapter",
            )
            if item.get(key)
        }
        last_seen = {
            "total_chapters": total,
            "downloaded_chapters": downloaded,
            "status": status,
            **meta,
        }
        existing = (
            await db.execute(
                sa.select(ResourceSubscription).where(
                    ResourceSubscription.domain == "comic",
                    ResourceSubscription.subject_key == subject_key,
                )
            )
        ).scalar_one_or_none()
        previous = dict(existing.last_seen or {}) if existing else {}
        # 同步数据可能缺元数据（如本地漫画），沿用台账里已存的作者/封面等
        options = {
            key: previous.get(key) or meta.get(key)
            for key in (
                "author",
                "tags",
                "category",
                "comic_status",
                "cover",
                "latest_chapter",
            )
            if previous.get(key) or meta.get(key)
        }
        record = await events.upsert_subscription(
            db,
            domain="comic",
            subject_key=subject_key,
            title=title,
            upstream_ref={
                "instance_id": instance_id,
                "manga_id": manga_id,
                "variant": variant,
            },
            last_seen=last_seen,
            poster=options.get("cover"),
        )
        if existing is None:
            return

        previous_total = _int(previous.get("total_chapters"))
        previous_downloaded = _int(previous.get("downloaded_chapters"))
        if total > previous_total and previous_total > 0:
            headline, markdown, text = messages.updated(
                "comic", title, count=total - previous_total
            )
            await events.emit(
                db,
                event_type="subscription.updated",
                title=headline,
                markdown=markdown,
                text=text,
                domain="comic",
                subject_key=subject_key,
                subscription=record,
                image_url=record.poster,
                payload={
                    "total_chapters": total,
                    "downloaded_chapters": downloaded,
                    "previous": previous_total,
                    "options": options,
                    "variant": variant,
                },
                dedupe_key=f"subscription.updated:{subject_key}:{total}",
            )
        if (
            total > 0
            and previous_total > 0
            and downloaded >= total
            and previous_downloaded < total
        ):
            if variant == "local":
                # 本地漫画下载完成后由 Comic Auto 刮削入库
                headline, markdown, text = messages.organize_completed(
                    "comic", title, detail=f"共 {total} 章", variant=variant
                )
                event_type = "organize.completed"
            else:
                # 在线漫画只是缓存章节，没有整理入库环节
                headline, markdown, text = messages.download_completed(
                    "comic", title, detail=f"共 {total} 章", variant=variant
                )
                event_type = "download.completed"
            await events.emit(
                db,
                event_type=event_type,
                title=headline,
                markdown=markdown,
                text=text,
                domain="comic",
                subject_key=subject_key,
                subscription=record,
                image_url=record.poster,
                payload={
                    "total_chapters": total,
                    "downloaded_chapters": downloaded,
                    "options": options,
                    "variant": variant,
                },
                dedupe_key=f"{event_type}:{subject_key}:{total}",
            )
        if total > 0 and downloaded >= total:
            record.status = "completed"
        elif downloaded > 0:
            record.status = "downloading"

    async def _sync_novel(self, db: AsyncSession) -> None:
        apps = await candidate_apps(db, ("reader",))
        for app in apps:
            data = await reader.downloads(db, instance_id=app.instance_id, limit=_SYNC_PAGE_SIZE)
            for item in data.get("items") or []:
                await self._sync_novel_item(db, app.instance_id, item)

    async def _sync_novel_item(
        self, db: AsyncSession, instance_id: int, item: dict[str, Any]
    ) -> None:
        book_url = str(item.get("task_id") or "")
        if not book_url:
            return
        subject_key = "novel:" + hashlib.sha1(book_url.encode()).hexdigest()
        title = str(item.get("title") or "")
        author = str(item.get("author") or "")
        origin_name = str(item.get("origin_name") or "")
        cover = str(item.get("cover") or "") or None
        total = _int(item.get("total_chapters"))
        cached = _int(item.get("cached_chapters"))
        status = str(item.get("status") or "")
        existing = (
            await db.execute(
                sa.select(ResourceSubscription).where(
                    ResourceSubscription.domain == "novel",
                    ResourceSubscription.subject_key == subject_key,
                )
            )
        ).scalar_one_or_none()
        previous = dict(existing.last_seen or {}) if existing else {}
        author = author or str(previous.get("author") or "")
        origin_name = origin_name or str(previous.get("origin_name") or "")
        cover = cover or (existing.poster if existing else None)
        last_seen = {
            "book_url": book_url,
            "author": author,
            "origin_name": origin_name,
            "total_chapters": total,
            "cached_chapters": cached,
            "status": status,
        }
        record = await events.upsert_subscription(
            db,
            domain="novel",
            subject_key=subject_key,
            title=title,
            upstream_ref={"instance_id": instance_id, "book_url": book_url},
            poster=cover,
            last_seen=last_seen,
            origin="sync",
        )
        if existing is None:
            return

        options = {
            "author": author,
            "origin_name": origin_name,
            "total_chapters": total,
            "cached_chapters": cached,
        }
        previous_total = _int(previous.get("total_chapters"))
        previous_status = str(previous.get("status") or "")
        if total > previous_total and previous_total > 0:
            headline, markdown, text = messages.updated(
                "novel", title, count=total - previous_total
            )
            await events.emit(
                db,
                event_type="subscription.updated",
                title=headline,
                markdown=markdown,
                text=text,
                domain="novel",
                subject_key=subject_key,
                subscription=record,
                image_url=cover,
                payload={
                    "total_chapters": total,
                    "previous": previous_total,
                    "options": options,
                },
                dedupe_key=f"subscription.updated:{subject_key}:{total}",
            )
        if (
            status == "done"
            and previous_total > 0
            and previous_status != "done"
            and total > 0
        ):
            headline, markdown, text = messages.download_completed(
                "novel", title, detail=f"已缓存 {cached}/{total} 章"
            )
            await events.emit(
                db,
                event_type="download.completed",
                title=headline,
                markdown=markdown,
                text=text,
                domain="novel",
                subject_key=subject_key,
                subscription=record,
                image_url=cover,
                payload={
                    "total_chapters": total,
                    "cached_chapters": cached,
                    "options": options,
                },
                dedupe_key=f"download.completed:{subject_key}:{total}",
            )
        if status == "done":
            record.status = "completed"

    async def _sync_music(self, db: AsyncSession) -> None:
        requests = list(
            (
                await db.execute(
                    sa.select(ResourceSubscription).where(
                        ResourceSubscription.domain == "music",
                        ResourceSubscription.status.in_(("active", "downloading")),
                    )
                )
            ).scalars()
        )
        if not requests:
            return
        apps = await candidate_apps(db, ("music-dl",))
        if not apps:
            return
        data = await musicdl.downloads(
            db, instance_id=apps[0].instance_id, limit=_SYNC_PAGE_SIZE
        )
        tasks = [item for item in data.get("items") or [] if item.get("task_id")]
        for record in requests:
            match = self._match_music_task(record, tasks)
            if match is None:
                continue
            status = str(match.get("status") or "").lower()
            previous = str((record.last_seen or {}).get("status") or "").lower()
            record.last_seen = {
                **(record.last_seen or {}),
                "task_id": match.get("task_id"),
                "status": status,
                "progress": match.get("progress"),
                "path": match.get("path"),
                "file_size": match.get("size")
                or (record.last_seen or {}).get("file_size"),
            }
            metadata = {
                key: record.last_seen[key]
                for key in ("artist", "album", "audio_format", "file_size")
                if record.last_seen.get(key) not in (None, "")
            }
            detail = " · ".join(
                part
                for part in (
                    str(metadata.get("artist") or "").strip(),
                    str(metadata.get("audio_format") or "").strip(),
                    format_size(metadata.get("file_size")),
                )
                if part
            )
            payload = {"path": match.get("path"), "options": metadata}
            if status in {"completed", "done", "finished", "success"} and previous not in {
                "completed",
                "done",
                "finished",
                "success",
            }:
                headline, markdown, text = messages.download_completed(
                    "music", record.title, detail=detail
                )
                await events.emit(
                    db,
                    event_type="download.completed",
                    title=headline,
                    markdown=markdown,
                    text=text,
                    domain="music",
                    subject_key=record.subject_key,
                    subscription=record,
                    image_url=record.poster,
                    payload=payload,
                    dedupe_key=f"download.completed:{record.subject_key}:{record.revision}",
                )
                record.status = "completed"
            elif status in {"error", "failed"}:
                headline, markdown, text = messages.failed(
                    "music", record.title, detail=str(match.get("error") or "下载失败")
                )
                await events.emit(
                    db,
                    event_type="action.failed",
                    title=headline,
                    markdown=markdown,
                    text=text,
                    domain="music",
                    subject_key=record.subject_key,
                    subscription=record,
                    image_url=record.poster,
                    severity="high",
                    payload=payload,
                    dedupe_key=f"action.failed:{record.subject_key}:{record.revision}",
                )
                record.status = "failed"

    @staticmethod
    def _match_music_task(
        record: ResourceSubscription, tasks: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        ref = dict(record.upstream_ref or {})
        task_id = str(ref.get("task_id") or "")
        if task_id:
            for task in tasks:
                if str(task.get("task_id")) == task_id:
                    return task
        title = (record.title or "").strip().lower()
        source = str(ref.get("source") or "").lower()
        artist = str((record.last_seen or {}).get("artist") or "").strip().lower()
        matches: list[dict[str, Any]] = []
        for task in tasks:
            task_title = str(task.get("title") or "").strip().lower()
            if not title or not task_title or not (title in task_title or task_title in title):
                continue
            if source and str(task.get("source") or "").lower() != source:
                continue
            # 同名不同歌手要按歌手再筛一次，否则会把另一首歌的任务当成自己的
            if artist:
                task_artist = str(task.get("artist") or "").strip().lower()
                if task_artist and task_artist != artist:
                    continue
            matches.append(task)
        if len(matches) == 1:
            return matches[0]
        return None


notification_sync_service = NotificationSyncService()
