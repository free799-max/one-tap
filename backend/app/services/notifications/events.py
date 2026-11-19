"""通知事件与资源订阅台账。"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import (
    Notification,
    NotificationChannel,
    NotificationDelivery,
    NotificationSetting,
    ResourceSubscription,
)
from app.services.notifications.assets import cover_path


EVENT_LABELS = {
    "subscription.added": "添加订阅",
    "subscription.replaced": "替换订阅",
    "subscription.removed": "取消订阅",
    "subscription.updated": "订阅更新",
    "subscription.completed": "订阅完成",
    "download.completed": "下载完成",
    "organize.completed": "整理完成",
    "action.failed": "处理失败",
}

DEFAULT_EVENT_TYPES = list(EVENT_LABELS)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def get_setting(db: AsyncSession) -> NotificationSetting:
    record = (
        await db.execute(sa.select(NotificationSetting).limit(1))
    ).scalar_one_or_none()
    if record is None:
        record = NotificationSetting()
        db.add(record)
        await db.flush()
    return record


async def emit(
    db: AsyncSession,
    *,
    event_type: str,
    title: str,
    markdown: str,
    text: str | None = None,
    domain: str | None = None,
    subject_key: str | None = None,
    subscription: ResourceSubscription | None = None,
    image_url: str | None = None,
    link: str | None = None,
    payload: dict[str, Any] | None = None,
    severity: str = "normal",
    actor_user_id: int | None = None,
    target_user_id: int | None = None,
    group_key: str | None = None,
    dedupe_key: str | None = None,
) -> Notification | None:
    """写入一条通知及渠道投递记录；重复事件返回 None。

    调用方负责提交事务，这样业务状态与通知可以一次落库。
    """
    key = dedupe_key or f"{event_type}:{subject_key or title}"
    exists = (
        await db.execute(
            sa.select(Notification.id).where(Notification.dedupe_key == key)
        )
    ).scalar_one_or_none()
    if exists is not None:
        return None

    setting = await get_setting(db)
    available_at = now_utc()
    proxy_path = cover_path(image_url) if image_url and image_url.startswith("http") else None
    notification = Notification(
        event_id=str(uuid.uuid4()),
        type=event_type,
        severity=severity,
        domain=domain,
        subject_key=subject_key,
        resource_subscription_id=subscription.id if subscription else None,
        dedupe_key=key,
        title=title[:500],
        markdown=markdown,
        image_url=image_url,
        image_proxy_path=proxy_path,
        link=link,
        payload=payload or {},
        target_user_id=target_user_id,
        actor_user_id=actor_user_id,
        group_key=group_key,
        available_at=available_at,
    )
    db.add(notification)
    await db.flush()

    if subscription is not None and not subscription.notify_enabled:
        return notification

    allowed = list(setting.event_types or [])
    if allowed and event_type not in allowed:
        return notification

    # AI 通知方式选了「对话内回复」：只留站内收件箱，不再外发到渠道，
    # 避免 Agent 的回复和 OneTap 推送重复
    if setting.notify_mode == "chat":
        return notification

    channels = (
        await db.execute(
            sa.select(NotificationChannel).where(NotificationChannel.enabled.is_(True))
        )
    ).scalars()
    for channel in channels:
        db.add(
            NotificationDelivery(
                notification_id=notification.id,
                channel_id=channel.id,
                status="pending",
                next_attempt_at=available_at,
            )
        )
    return notification


async def upsert_subscription(
    db: AsyncSession,
    *,
    domain: str,
    subject_key: str,
    title: str,
    kind: str = "subscription",
    source_of_truth: str = "upstream",
    upstream_ref: dict[str, Any] | None = None,
    year: str | None = None,
    poster: str | None = None,
    last_seen: dict[str, Any] | None = None,
    origin: str = "mcp",
    actor_user_id: int | None = None,
    new_attempt: bool = False,
) -> ResourceSubscription:
    """新增或更新台账；``new_attempt`` 时递增 revision 用于事件去重。"""
    record = (
        await db.execute(
            sa.select(ResourceSubscription).where(
                ResourceSubscription.domain == domain,
                ResourceSubscription.subject_key == subject_key,
            )
        )
    ).scalar_one_or_none()

    if record is None:
        record = ResourceSubscription(
            domain=domain,
            subject_key=subject_key,
            kind=kind,
            source_of_truth=source_of_truth,
            upstream_ref=upstream_ref or {},
            title=title[:500],
            year=year,
            poster=poster,
            status="active",
            revision=1,
            last_seen=last_seen or {},
            created_by=actor_user_id,
            origin=origin,
            last_synced_at=now_utc(),
        )
        db.add(record)
        await db.flush()
        return record

    if new_attempt:
        record.revision = (record.revision or 1) + 1
    record.kind = kind
    record.source_of_truth = source_of_truth
    if upstream_ref:
        record.upstream_ref = upstream_ref
    if title:
        record.title = title[:500]
    if year:
        record.year = year
    if poster:
        record.poster = poster
    if last_seen is not None:
        record.last_seen = last_seen
    record.status = "active"
    record.last_synced_at = now_utc()
    if actor_user_id is not None:
        record.created_by = actor_user_id
        record.origin = origin
    return record


async def find_subscription(
    db: AsyncSession,
    *,
    domain: str,
    upstream_field: str,
    upstream_value: Any,
) -> ResourceSubscription | None:
    rows = (
        await db.execute(
            sa.select(ResourceSubscription).where(
                ResourceSubscription.domain == domain
            )
        )
    ).scalars()
    value = str(upstream_value)
    for row in rows:
        ref = dict(row.upstream_ref or {})
        if str(ref.get(upstream_field) or "") == value:
            return row
    return None


async def mark_removed(
    db: AsyncSession, record: ResourceSubscription
) -> ResourceSubscription:
    record.status = "removed"
    record.updated_at = now_utc()
    return record


def markdown_to_text(markdown: str) -> str:
    """把简单的 Markdown 转成纯文本，供不支持富文本的渠道使用。"""
    value = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", markdown)
    value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    value = re.sub(r"[*_`#>]+", "", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()
