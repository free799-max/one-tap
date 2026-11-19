"""OneTap 消息推送工具：把候选列表推到通知渠道。"""

from typing import Any

from app.database import async_session
from app.services.notifications.present import push_candidates


def register_present_tools(server: Any) -> None:
    @server.tool(
        name="onetap_present",
        description=(
            "把候选列表按图文卡片推送到 OneTap 已启用的通知渠道：飞书渲染成带封面的 interactive 卡片，"
            "其他渠道退化成文本列表。items 直接传 onetap_search 返回的 items，保持原顺序，最多 20 条。"
            "通知方式为 push（见 onetap_notifications）时，每次搜索后自动推一次；为 chat 时只在用户明确要求"
            "「推给我 / 发飞书 / 要带图的列表」时推一次。推送后仍要在对话里按列表规范回复候选，"
            "不要用它代替正常回复，也不要对同一批候选重复推送。"
        ),
    )
    async def onetap_present(
        domain: str,
        items: list[dict],
        keyword: str | None = None,
        title: str | None = None,
        note: str | None = None,
        link: str | None = None,
        channel_names: list[str] | None = None,
        limit: int = 20,
    ) -> dict:
        async with async_session() as db:
            return await push_candidates(
                db,
                domain=domain,
                items=items,
                keyword=keyword,
                title=title,
                note=note,
                link=link,
                channel_names=channel_names,
                limit=limit,
            )
