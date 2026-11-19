"""把资源域动作转成订阅台账与通知事件。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import ResourceSubscription
from app.services.mcp.auth import current_mcp_actor
from app.services.notifications import events, messages
from app.services.notifications.presentation import format_size


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _option(options: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = _text(options.get(key))
        if value:
            return value
    return ""


def _poster(options: dict[str, Any]) -> str | None:
    value = _option(options, "poster", "cover", "image", "thumbnailUrl", "poster_path")
    return value or None


_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif")


def _image_url(value: Any) -> str | None:
    """只接受看起来是图片的绝对地址，避免把书籍详情页当封面。"""
    text = _text(value)
    if not text.lower().startswith(("http://", "https://")):
        return None
    path = text.split("?", 1)[0].split("#", 1)[0].lower()
    return text if path.endswith(_IMAGE_SUFFIXES) else None


def _artifact_id(result: dict[str, Any] | None) -> Any:
    if not isinstance(result, dict):
        return None
    for container in (result, result.get("result"), result.get("detail")):
        if not isinstance(container, dict):
            continue
        for key in ("id", "subscribe_id", "subscription_id", "task_id"):
            value = container.get(key)
            if value not in (None, ""):
                return value
    return None


def _result_poster(result: dict[str, Any]) -> str | None:
    for container in (result, result.get("result"), result.get("detail")):
        if isinstance(container, dict):
            value = _poster(container)
            if value:
                return value
    return None


# 从媒体详情补充到订阅台账的字段，供各阶段通知卡片展示
_VIDEO_DETAIL_KEYS = (
    "genres",
    "rating",
    "vote_count",
    "runtime",
    "directors",
    "actors",
    "release_date",
)


async def _video_metadata(
    db: AsyncSession,
    *,
    result: dict[str, Any],
    options: dict[str, Any],
    media_id: str,
    media_source: str,
    instance_id: int | None,
) -> dict[str, Any]:
    """影视订阅没传海报/元数据时，从订阅结果或媒体详情补齐。"""
    metadata: dict[str, Any] = {}
    poster = _result_poster(result)
    if poster:
        metadata["poster"] = poster
    # 网盘方案没有自己的媒体库，元数据来自巡影的 TMDB 详情；
    # 演职员常因代理超时缺失，再借 MoviePilot 的 TMDB 源补齐（同一份数据）
    if _text(result.get("variant")) == "cloud":
        for key in _VIDEO_DETAIL_KEYS:
            value = options.get(key)
            if value not in (None, "", []):
                metadata.setdefault(key, value)
        tmdb_id = _option(options, "media_id") or media_id
        if tmdb_id and not (metadata.get("directors") and metadata.get("actors")):
            from app.services.resources import moviepilot

            fetched = await moviepilot.fetch_metadata(
                db,
                media_id=tmdb_id,
                media_source="themoviedb",
                type_name=_option(options, "type", "type_name"),
                instance_id=None,
            )
            for key, value in fetched.items():
                metadata.setdefault(key, value)
        return metadata
    if not media_id or media_source == "unknown":
        return metadata
    from app.services.resources import moviepilot

    fetched = await moviepilot.fetch_metadata(
        db,
        media_id=media_id,
        media_source=media_source,
        type_name=_option(options, "type", "type_name"),
        instance_id=instance_id,
    )
    for key, value in fetched.items():
        metadata.setdefault(key, value)
    return metadata


def _result_book_url(result: dict[str, Any] | None) -> str:
    """取小说订阅后 Reader 实际保存的书籍地址。

    订阅时可能自动换源，换源后的 bookUrl 与搜索结果里的地址不同；
    台账统一用最终地址，否则同一本书会被同步任务认成两本。
    """
    if not isinstance(result, dict):
        return ""
    selection = result.get("source_selection")
    if isinstance(selection, dict):
        selected = selection.get("selected")
        if isinstance(selected, dict):
            value = _option(selected, "book_url", "bookUrl")
            if value:
                return value
    for container in (result.get("result"), result.get("detail")):
        if isinstance(container, dict):
            value = _option(container, "bookUrl", "book_url")
            if value:
                return value
    return ""


def _actor_id() -> int | None:
    actor = current_mcp_actor()
    return actor.user_id if actor else None


def _music_parts(item_id: str, options: dict[str, Any]) -> tuple[str, str]:
    source = _option(options, "source")
    song_id = _option(options, "id", "song_id")
    if (not source or not song_id) and ":" in item_id:
        left, _, right = item_id.partition(":")
        source = source or left
        song_id = song_id or right
    return source, song_id


async def record_added(
    db: AsyncSession,
    *,
    domain: str,
    item_id: str,
    options: dict[str, Any],
    result: dict[str, Any],
    instance_id: int | None,
) -> ResourceSubscription:
    title = _option(options, "name", "title") or _text(result.get("title"))
    year = _option(options, "year") or None
    poster = _poster(options)
    actor = _actor_id()
    effective_instance = result.get("instance_id") or instance_id
    upstream_ref: dict[str, Any] = {"instance_id": effective_instance}
    last_seen: dict[str, Any] = {}
    novel_meta: dict[str, Any] = {}
    video_meta: dict[str, Any] = {}
    variant: str | None = None
    kind = "subscription"
    source_of_truth = "upstream"

    if domain == "video":
        media_source = _option(options, "media_source") or "unknown"
        media_id = _option(options, "media_id") or item_id or str(_artifact_id(result) or "")
        season = _int(options.get("season"))
        subject_key = f"video:{media_source}:{media_id}:S{season}"
        upstream_ref.update(
            {
                "subscribe_id": _artifact_id(result),
                "media_source": media_source,
                "media_id": media_id,
                "season": season,
            }
        )
        last_seen = {
            "total_episode": _int(options.get("total_episode")),
            "lack_episode": _int(options.get("total_episode")),
            "type": _option(options, "type", "type_name"),
            "media_source": media_source,
            "season": season,
            "resolution": _option(options, "resolution"),
            "quality": _option(options, "quality"),
        }
        metadata = await _video_metadata(
            db,
            result=result,
            options=options,
            media_id=media_id,
            media_source=media_source,
            instance_id=effective_instance,
        )
        poster = poster or metadata.get("poster")
        year = year or metadata.get("year") or None
        last_seen["type"] = last_seen["type"] or _text(metadata.get("type"))
        for key in _VIDEO_DETAIL_KEYS:
            if metadata.get(key) not in (None, "", []):
                last_seen[key] = metadata[key]
                # 评分/时长/导演/主演等详情字段带给通知卡片
                video_meta[key] = metadata[key]
    elif domain == "comic":
        # 本地方案的订阅 ID 与搜索 item_id（漫画链接）不同，优先用结果里的订阅 ID
        manga_id = str(_artifact_id(result) or item_id or "")
        variant = (
            _text(result.get("variant")) or _text(options.get("variant")) or None
        )
        subject_key = f"comic:{effective_instance or 0}:{manga_id}"
        upstream_ref["manga_id"] = manga_id
        if variant:
            upstream_ref["variant"] = variant
        # Comic Auto 订阅后返回的详情字段用于通知卡片与后续同步
        meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
        if meta:
            options = {**meta, **options}
            poster = poster or _text(meta.get("cover")) or None
        title = title or _text(result.get("title"))
        last_seen = {
            "status": "active",
            **{
                key: options[key]
                for key in (
                    "author",
                    "tags",
                    "category",
                    "comic_status",
                    "cover",
                    "latest_chapter",
                    "total_chapters",
                )
                if options.get(key) not in (None, "", 0)
            },
        }
    elif domain == "novel":
        book_url = (
            _result_book_url(result)
            or _option(options, "bookUrl", "book_url")
            or item_id
        )
        subject_key = "novel:" + hashlib.sha1(book_url.encode()).hexdigest()
        upstream_ref["book_url"] = book_url
        book = result.get("result") if isinstance(result.get("result"), dict) else {}
        selection = (
            result.get("source_selection")
            if isinstance(result.get("source_selection"), dict)
            else {}
        )
        selected = (
            selection.get("selected")
            if isinstance(selection.get("selected"), dict)
            else {}
        )
        novel_meta = {
            "author": _option(options, "author") or _text(book.get("author")),
            "origin_name": _option(options, "origin_name", "origin")
            or _text(selected.get("origin_name"))
            or _text(book.get("originName")),
            "total_chapters": _int(options.get("total_chapters"))
            or _int(selected.get("total_chapters"))
            or _int(book.get("totalChapterNum")),
        }
        poster = _image_url(poster) or _image_url(book.get("coverUrl"))
        last_seen = {"status": "active", **novel_meta}
    else:
        source, song_id = _music_parts(item_id, options)
        subject_key = f"music:{source}:{song_id}"
        kind = "request"
        source_of_truth = "local"
        upstream_ref.update(
            {
                "source": source,
                "song_id": song_id,
                "request_id": _text(result.get("request_id")),
            }
        )
        options = dict(options)
        if result.get("size") and not (options.get("file_size") or options.get("size")):
            options["file_size"] = result["size"]
        last_seen = {
            "status": "queued",
            "title": title,
            "artist": _option(options, "artist"),
            "album": _option(options, "album"),
            "audio_format": _option(options, "audio_format", "format"),
            "file_size": options.get("file_size") or options.get("size"),
        }

    record = await events.upsert_subscription(
        db,
        domain=domain,
        subject_key=subject_key,
        title=title,
        kind=kind,
        source_of_truth=source_of_truth,
        upstream_ref=upstream_ref,
        year=year,
        poster=poster,
        last_seen=last_seen,
        origin="mcp",
        actor_user_id=actor,
        new_attempt=True,
    )
    headline, markdown, text = messages.added(
        domain, title or subject_key, year=year, options=options, variant=variant
    )
    payload_options = {**novel_meta, **options} if novel_meta else options
    if video_meta:
        payload_options = {**video_meta, **payload_options}
    await events.emit(
        db,
        event_type="subscription.added",
        title=headline,
        markdown=markdown,
        text=text,
        domain=domain,
        subject_key=subject_key,
        subscription=record,
        image_url=poster,
        payload={"item_id": item_id, "options": payload_options},
        actor_user_id=actor,
        dedupe_key=f"subscription.added:{subject_key}:r{record.revision}",
    )
    return record


async def record_replaced(
    db: AsyncSession,
    *,
    domain: str,
    subscription_id: str,
    options: dict[str, Any],
    result: dict[str, Any],
    instance_id: int | None,
) -> ResourceSubscription | None:
    record = await _find_for_action(db, domain, subscription_id)
    actor = _actor_id()
    if record is None:
        # 订阅可能是在 OneTap 之外创建的；用最小台账承接通知。
        subject_key = f"{domain}:replace:{subscription_id}"
        record = await events.upsert_subscription(
            db,
            domain=domain,
            subject_key=subject_key,
            title=f"订阅 {subscription_id}",
            upstream_ref={"instance_id": instance_id, "subscribe_id": subscription_id},
            last_seen={},
            actor_user_id=actor,
            new_attempt=True,
        )
    else:
        record = await events.upsert_subscription(
            db,
            domain=domain,
            subject_key=record.subject_key,
            title=record.title,
            kind=record.kind,
            source_of_truth=record.source_of_truth,
            upstream_ref={**dict(record.upstream_ref or {}), "instance_id": instance_id},
            year=record.year,
            poster=record.poster,
            last_seen={**dict(record.last_seen or {}), "status": "replacing"},
            actor_user_id=actor,
            new_attempt=True,
        )

    headline, markdown, text = messages.replaced(
        domain, record.title, year=record.year, options=options
    )
    await events.emit(
        db,
        event_type="subscription.replaced",
        title=headline,
        markdown=markdown,
        text=text,
        domain=domain,
        subject_key=record.subject_key,
        subscription=record,
        image_url=record.poster,
        payload={"subscription_id": subscription_id, "options": options},
        actor_user_id=actor,
        dedupe_key=f"subscription.replaced:{record.subject_key}:r{record.revision}",
    )
    return record


async def record_removed(
    db: AsyncSession,
    *,
    domain: str,
    subscription_id: str,
    instance_id: int | None,
) -> ResourceSubscription | None:
    record = await _find_for_action(db, domain, subscription_id)
    actor = _actor_id()
    if record is not None:
        await events.mark_removed(db, record)
        title = record.title
        subject_key = record.subject_key
        poster = record.poster
    else:
        title = f"订阅 {subscription_id}"
        subject_key = f"{domain}:removed:{subscription_id}"
        poster = None

    headline, markdown, text = messages.removed(domain, title)
    await events.emit(
        db,
        event_type="subscription.removed",
        title=headline,
        markdown=markdown,
        text=text,
        domain=domain,
        subject_key=subject_key,
        subscription=record,
        image_url=poster,
        payload={"subscription_id": subscription_id, "instance_id": instance_id},
        actor_user_id=actor,
        dedupe_key=f"subscription.removed:{subject_key}:{subscription_id}",
    )
    return record


async def _find_for_action(
    db: AsyncSession, domain: str, subscription_id: str
) -> ResourceSubscription | None:
    if domain == "comic":
        return await events.find_subscription(
            db, domain=domain, upstream_field="manga_id", upstream_value=subscription_id
        )
    if domain == "novel":
        return await events.find_subscription(
            db, domain=domain, upstream_field="book_url", upstream_value=subscription_id
        )
    if domain == "music":
        found = await events.find_subscription(
            db, domain=domain, upstream_field="task_id", upstream_value=subscription_id
        )
        if found is not None:
            return found
        return await _find_music_by_title(db, subscription_id)
    return await events.find_subscription(
        db, domain=domain, upstream_field="subscribe_id", upstream_value=subscription_id
    )


async def _find_music_by_title(
    db: AsyncSession, value: str
) -> ResourceSubscription | None:
    rows = (
        await db.execute(
            sa.select(ResourceSubscription).where(
                ResourceSubscription.domain == "music"
            )
        )
    ).scalars()
    needle = value.strip().lower()
    for row in rows:
        ref = dict(row.upstream_ref or {})
        last_seen = dict(row.last_seen or {})
        if needle and needle in {
            str(ref.get("task_id") or "").lower(),
            str(last_seen.get("task_id") or "").lower(),
        }:
            return row
        if needle and needle in (row.title or "").lower():
            return row
    return None


def _music_metadata(record: ResourceSubscription) -> dict[str, Any]:
    last_seen = dict(record.last_seen or {})
    return {
        key: last_seen[key]
        for key in ("artist", "album", "audio_format", "file_size")
        if last_seen.get(key) not in (None, "")
    }


async def notify_music_file(
    *,
    path: str,
    status: str,
    previous_status: str | None,
    error: str | None = None,
) -> None:
    """音乐自动整理完成/失败时发事件。"""
    if status == previous_status or status not in {"done", "failed"}:
        return
    from app.database import async_session
    from app.models.orchestration import MusicAutoOrganizeFile

    stem = Path(path).stem
    title = stem
    artist = ""
    # 整理文件名固定为「歌手 - 歌名.后缀」，歌手用于区分同名歌曲
    if " - " in stem:
        artist, title = (part.strip() for part in stem.split(" - ", 1))
    async with async_session() as db:
        record = await _find_music_file_request(db, path, title, artist)
        if record is None:
            # 只通知 OneTap 自己入队的请求，避免把外部整理的文件全部推到收件箱。
            return
        file_state = (
            await db.execute(
                sa.select(MusicAutoOrganizeFile)
                .where(MusicAutoOrganizeFile.path == path)
                .order_by(MusicAutoOrganizeFile.updated_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        metadata = _music_metadata(record)
        metadata["audio_format"] = Path(path).suffix[1:].upper()
        if file_state is not None and file_state.size:
            metadata["file_size"] = file_state.size
        record.last_seen = {
            **dict(record.last_seen or {}),
            **metadata,
            "path": path,
            "status": status,
        }
        if status == "done":
            record.status = "completed"
        else:
            record.status = "failed"
        subject_key = record.subject_key
        display_title = record.title or title
        poster = record.poster
        file_key = hashlib.sha1(path.encode()).hexdigest()[:16]
        payload = {"path": path, "options": metadata}

        if status == "done":
            headline, markdown, text = messages.organize_completed(
                "music",
                display_title,
                detail=" · ".join(
                    part
                    for part in (
                        _text(metadata.get("artist")),
                        _text(metadata.get("audio_format")),
                        format_size(metadata.get("file_size")),
                    )
                    if part
                ),
            )
            await events.emit(
                db,
                event_type="organize.completed",
                title=headline,
                markdown=markdown,
                text=text,
                domain="music",
                subject_key=subject_key,
                subscription=record,
                image_url=poster,
                payload=payload,
                dedupe_key=f"organize.completed:{subject_key}:{file_key}",
            )
        else:
            headline, markdown, text = messages.failed(
                "music", display_title, detail=error or "整理失败"
            )
            await events.emit(
                db,
                event_type="action.failed",
                title=headline,
                markdown=markdown,
                text=text,
                domain="music",
                subject_key=subject_key,
                subscription=record,
                image_url=poster,
                severity="high",
                payload={**payload, "error": error},
                dedupe_key=f"action.failed:{subject_key}:{file_key}",
            )
        await db.commit()


def _record_artist(record: ResourceSubscription) -> str:
    last_seen = dict(record.last_seen or {})
    ref = dict(record.upstream_ref or {})
    options = ref.get("options") if isinstance(ref.get("options"), dict) else {}
    return str(
        last_seen.get("artist") or options.get("artist") or ref.get("artist") or ""
    ).strip().lower()


async def _find_music_file_request(
    db: AsyncSession, path: str, title: str, artist: str = ""
) -> ResourceSubscription | None:
    rows = (
        await db.execute(
            sa.select(ResourceSubscription).where(
                ResourceSubscription.domain == "music",
                ResourceSubscription.status.in_(("active", "downloading", "completed")),
            )
        )
    ).scalars()
    needle = title.strip().lower()
    singer = artist.strip().lower()
    candidates: list[tuple[ResourceSubscription, int]] = []
    for row in rows:
        last_seen = dict(row.last_seen or {})
        if str(last_seen.get("path") or "") == path:
            return row
        row_title = (row.title or "").strip().lower()
        if not needle or not row_title:
            continue
        if row_title == needle:
            score = len(row_title) + 1000
        elif needle in row_title or row_title in needle:
            # 优先匹配更具体的标题，避免「晴天」抢走「晴天 (Live)」的文件。
            score = len(row_title)
        else:
            continue
        candidates.append((row, score))

    if singer:
        same_artist = [
            (row, score) for row, score in candidates if _record_artist(row) == singer
        ]
        if same_artist:
            candidates = same_artist
        else:
            # 同名不同歌手不能互相认领文件；歌手未知的老记录仍可匹配
            candidates = [
                (row, score) for row, score in candidates if not _record_artist(row)
            ]
    if not candidates:
        return None
    best_score = max(score for _, score in candidates)
    best = [row for row, score in candidates if score == best_score]
    # 同分并列说明区分不了（例如同名同歌手两条），宁可不发也不要张冠李戴
    if len(best) != 1:
        return None
    return best[0]


def _novel_options(last_seen: dict[str, Any], **extra: Any) -> dict[str, Any]:
    """合并台账里的小说元数据，供卡片字段展示。"""
    merged: dict[str, Any] = {}
    for key in (
        "author",
        "origin_name",
        "total_chapters",
        "cached_chapters",
        "file_size",
    ):
        value = extra.get(key)
        if value in (None, ""):
            value = last_seen.get(key)
        if value not in (None, ""):
            merged[key] = value
    return merged


async def notify_reader_export(
    *,
    book_url: str,
    title: str,
    total: int,
    previous_exported: int,
    destination: str | None,
    author: str | None = None,
    origin_name: str | None = None,
    cover: str | None = None,
    size: int | None = None,
) -> None:
    """Reader 导出 EPUB 后发事件。

    首次导出只给 OneTap 添加的书发通知，书架里原本就有的书只建基线，
    避免升级后把整个书架的历史导出补发一遍。
    """
    from app.database import async_session

    subject_key = "novel:" + hashlib.sha1(book_url.encode()).hexdigest()
    async with async_session() as db:
        record = (
            await db.execute(
                sa.select(ResourceSubscription).where(
                    ResourceSubscription.domain == "novel",
                    ResourceSubscription.subject_key == subject_key,
                )
            )
        ).scalar_one_or_none()
        if record is None:
            await events.upsert_subscription(
                db,
                domain="novel",
                subject_key=subject_key,
                title=title,
                upstream_ref={"book_url": book_url},
                poster=cover,
                last_seen={
                    "total_chapters": total,
                    "exported_total": total,
                    "author": author,
                    "origin_name": origin_name,
                    "file_size": size,
                },
                origin="sync",
            )
            await db.commit()
            return
        record.last_seen = {
            **dict(record.last_seen or {}),
            "total_chapters": total,
            "exported_total": total,
            "destination": destination,
            "author": author or (record.last_seen or {}).get("author"),
            "origin_name": origin_name or (record.last_seen or {}).get("origin_name"),
            "file_size": size or (record.last_seen or {}).get("file_size"),
        }
        if _image_url(cover):
            record.poster = cover
        record.status = "completed"
        if previous_exported < 0 and record.origin != "mcp":
            await db.commit()
            return
        detail = f"已导出 {total} 章"
        headline, markdown, text = messages.organize_completed(
            "novel", title, detail=detail
        )
        await events.emit(
            db,
            event_type="organize.completed",
            title=headline,
            markdown=markdown,
            text=text,
            domain="novel",
            subject_key=subject_key,
            subscription=record,
            image_url=record.poster,
            payload={
                "book_url": book_url,
                "total": total,
                "destination": destination,
                "options": _novel_options(
                    record.last_seen or {},
                    total_chapters=total,
                    file_size=size,
                ),
            },
            dedupe_key=f"organize.completed:{subject_key}:{total}",
        )
        await db.commit()


async def notify_reader_cached(
    *,
    book_url: str,
    title: str,
    total: int,
    cached: int,
    author: str | None = None,
    origin_name: str | None = None,
    cover: str | None = None,
) -> None:
    """Reader 章节缓存补齐后发事件；只通知 OneTap 添加的书。"""
    from app.database import async_session

    if total <= 0 or cached < total:
        return
    subject_key = "novel:" + hashlib.sha1(book_url.encode()).hexdigest()
    async with async_session() as db:
        record = (
            await db.execute(
                sa.select(ResourceSubscription).where(
                    ResourceSubscription.domain == "novel",
                    ResourceSubscription.subject_key == subject_key,
                )
            )
        ).scalar_one_or_none()
        if record is None or record.origin != "mcp":
            return
        record.last_seen = {
            **dict(record.last_seen or {}),
            "status": "done",
            "total_chapters": total,
            "cached_chapters": cached,
            "author": author or (record.last_seen or {}).get("author"),
            "origin_name": origin_name or (record.last_seen or {}).get("origin_name"),
        }
        if _image_url(cover):
            record.poster = cover
        record.status = "completed"
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
            image_url=record.poster,
            payload={
                "book_url": book_url,
                "total": total,
                "cached": cached,
                "options": _novel_options(
                    record.last_seen or {},
                    total_chapters=total,
                    cached_chapters=cached,
                ),
            },
            dedupe_key=f"download.completed:{subject_key}:{total}",
        )
        await db.commit()


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
