"""OneTap 通知方式工具：技能安装/更新时先问用户怎么发通知。"""

from typing import Any

from app.database import async_session
from app.services.notifications.preferences import read_preference, set_preference


def register_notify_tools(server: Any) -> None:
    @server.tool(
        name="onetap_notifications",
        description=(
            "读取通知方式偏好：mode=chat 表示由 Agent 在对话里回复、OneTap 不再外发；"
            "mode=push 表示订阅/下载/整理等事件外发到飞书、微信等已启用渠道。"
            "同时返回已启用渠道、事件白名单和技能版本。"
            "needs_onboarding=true 表示还没问过用户、或技能更新过，必须先问用户再用 onetap_set_notify_mode 保存。"
        ),
    )
    async def onetap_notifications() -> dict:
        async with async_session() as db:
            return await read_preference(db)

    @server.tool(
        name="onetap_set_notify_mode",
        description=(
            "保存用户选择的 AI 通知方式，必须先得到用户明确选择。"
            "mode=chat：不再外发到通知渠道，异步进度由 Agent 在对话里汇报；"
            "mode=push：订阅、下载完成、整理入库、失败等事件由 OneTap 推到已启用渠道。"
            "保存后返回完整偏好，后续按返回的 mode 行事。"
        ),
    )
    async def onetap_set_notify_mode(mode: str) -> dict:
        async with async_session() as db:
            return await set_preference(db, mode)
