"""AI 通知方式偏好。

技能安装/更新时 Agent 必须先问用户：异步通知交给 OneTap 外发，
还是由 Agent 自己在对话里回复。选择记在通知设置里，技能版本变化后重新询问。
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import APIException
from app.models.notification import NotificationChannel
from app.services.mcp.skills import build_skills_bundle
from app.services.notifications.events import get_setting

# chat：Agent 在对话里回复，OneTap 不再外发；push：外发到已启用的通知渠道
NOTIFY_MODES = {
    "chat": "对话内回复",
    "push": "OneTap 推送",
}
DEFAULT_MODE = "push"


def current_skill_version() -> str:
    return str(build_skills_bundle().get("version") or "")


async def read_preference(db: AsyncSession) -> dict[str, Any]:
    setting = await get_setting(db)
    channels = list(
        (
            await db.execute(
                sa.select(NotificationChannel)
                .where(NotificationChannel.enabled.is_(True))
                .order_by(NotificationChannel.id)
            )
        ).scalars()
    )
    mode = setting.notify_mode if setting.notify_mode in NOTIFY_MODES else None
    version = current_skill_version()
    return {
        "mode": mode or DEFAULT_MODE,
        "configured": mode is not None,
        "mode_label": NOTIFY_MODES[mode or DEFAULT_MODE],
        "skill_version": version,
        "confirmed_skill_version": setting.notify_skill_version or "",
        # 没问过、或技能更新过都要重新问一次
        "needs_onboarding": mode is None or setting.notify_skill_version != version,
        "channels": [
            {"name": channel.name, "type": channel.type} for channel in channels
        ],
        "event_types": list(setting.event_types or []),
    }


async def set_preference(db: AsyncSession, mode: str) -> dict[str, Any]:
    value = (mode or "").strip().lower()
    if value not in NOTIFY_MODES:
        raise APIException("mode 只能是 chat / push", 400)
    setting = await get_setting(db)
    setting.notify_mode = value
    setting.notify_skill_version = current_skill_version()
    await db.commit()
    return await read_preference(db)
