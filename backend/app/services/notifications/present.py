"""候选列表推送：把搜索结果按图文卡片投递到已启用的通知渠道。

飞书渲染成带封面的 interactive 卡片（每项一张小封面），
其余渠道退化成 Markdown 编号列表。
"""

from __future__ import annotations

import asyncio
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import APIException
from app.models.notification import NotificationChannel
from app.services.notifications.channels import (
    ChannelMessage,
    build_channel,
)
from app.services.notifications.events import markdown_to_text
from app.services.notifications.presentation import (
    DOMAIN_LABELS,
    PresentItem,
    build_items,
)

MAX_ITEMS = 20
_DEFAULT_NOTES = {
    "music": "回复序号选择一首",
    "video": "回复序号选择一部",
    "comic": "回复序号选择一部",
    "novel": "回复序号选择一本",
}


def _headline(domain: str, keyword: str | None, title: str | None) -> str:
    if title and title.strip():
        return title.strip()
    name = (keyword or "").strip()
    if name:
        return f"《{name}》"
    return f"{DOMAIN_LABELS.get(domain, domain)}候选"


def _message(
    *,
    domain: str,
    headline: str,
    items: list[PresentItem],
    note: str,
    link: str | None,
) -> ChannelMessage:
    label = DOMAIN_LABELS.get(domain, domain)
    summary = f"{label} · {len(items)} 条候选"
    lines = [f"**{headline}**", "", summary, ""]
    lines.extend(item.plain_line(index) for index, item in enumerate(items, 1))
    if note:
        lines.extend(["", note])
    markdown = "\n".join(lines)
    payload = {
        "presentation": {
            "header": headline,
            "subtitle": summary,
            "items": [item.as_payload() for item in items],
            "note": note,
        }
    }
    return ChannelMessage(
        title=headline,
        markdown=markdown,
        text=markdown_to_text(markdown),
        link=link,
        payload=payload,
        domain=domain,
    )


async def _channels(
    db: AsyncSession, names: list[str] | None
) -> list[NotificationChannel]:
    rows = list(
        (
            await db.execute(
                sa.select(NotificationChannel)
                .where(NotificationChannel.enabled.is_(True))
                .order_by(NotificationChannel.id)
            )
        ).scalars()
    )
    if not names:
        return rows
    wanted = {name.strip() for name in names if name and name.strip()}
    selected = [channel for channel in rows if channel.name in wanted]
    missing = wanted - {channel.name for channel in selected}
    if missing:
        available = "、".join(channel.name for channel in rows) or "无"
        raise APIException(
            f"找不到通知渠道：{'、'.join(sorted(missing))}；已启用：{available}", 400
        )
    return selected


async def _deliver(channel: NotificationChannel, message: ChannelMessage) -> dict[str, Any]:
    try:
        impl = build_channel(channel)
        message_id = await impl.send(message)
    except Exception as exc:  # noqa: BLE001 - 单个渠道失败不影响其它渠道
        return {
            "channel": channel.name,
            "type": channel.type,
            "ok": False,
            "error": str(exc)[:500],
        }
    return {
        "channel": channel.name,
        "type": channel.type,
        "ok": True,
        "message_id": message_id or "",
    }


async def push_candidates(
    db: AsyncSession,
    *,
    domain: str,
    items: list[dict[str, Any]],
    keyword: str | None = None,
    title: str | None = None,
    note: str | None = None,
    link: str | None = None,
    channel_names: list[str] | None = None,
    limit: int = MAX_ITEMS,
) -> dict[str, Any]:
    if domain not in DOMAIN_LABELS:
        raise APIException("domain 只能是 video / comic / music / novel", 400)
    raw_items = [item for item in items or [] if isinstance(item, dict)]
    if not raw_items:
        raise APIException("items 为空，先调用 onetap_search 拿到候选", 400)
    count = max(1, min(int(limit or MAX_ITEMS), MAX_ITEMS))
    present_items = build_items(domain, raw_items[:count])
    channels = await _channels(db, channel_names)
    if not channels:
        raise APIException("没有启用的通知渠道，先在 OneTap 通知设置里添加渠道", 400)

    headline = _headline(domain, keyword, title)
    text = (note or "").strip() or _DEFAULT_NOTES.get(domain, "回复序号选择")
    message = _message(
        domain=domain,
        headline=headline,
        items=present_items,
        note=text,
        link=link,
    )
    results = await asyncio.gather(
        *(_deliver(channel, message) for channel in channels)
    )
    pushed = [result for result in results if result["ok"]]
    failed = [result for result in results if not result["ok"]]
    return {
        "domain": domain,
        "items": len(present_items),
        "pushed": pushed,
        "failed": failed,
    }
