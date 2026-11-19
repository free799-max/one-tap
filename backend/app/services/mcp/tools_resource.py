"""OneTap 资源网关：语义化 MCP 工具。

工具按资源域（video / comic / music / novel）统一分发，
AI 不需要直接面对各上游应用的原始工具或 operation 名。
"""

from typing import Any

from app.core.exceptions import APIException
from app.database import async_session
from app.services.resources import registry


def register_resource_tools(server: Any) -> None:
    @server.tool(
        name="onetap_capabilities",
        description="查看 OneTap 当前支持的资源域（video 影视、comic 漫画、music 音乐、novel 小说）及可用实例。video 有 local（MoviePilot + qBittorrent，订阅后下载并整理入库）与 cloud（巡影，订阅即转存网盘、回读校验并持续追更）两种变体；comic 有 online（Suwayomi，订阅即加入书库）与 local（Comic Auto + Komga/Kavita，订阅后下载并整理入库）两种变体。同一域同时可用时会返回 requires_instance_id=true，必须先让用户选方案再把 instance_id 贯穿本次所有调用。",
    )
    async def onetap_capabilities() -> dict:
        async with async_session() as db:
            return await registry.capabilities(db)

    @server.tool(
        name="onetap_search",
        description="搜索资源。domain 取 video / comic / music / novel；默认返回 20 条候选，包含 item_id，不要替用户挑选。music 默认 sort=quality，按码率从高到低排序，并返回 bitrate、size；sort=default 使用原始顺序。video 的 cloud 变体（巡影）走 TMDB 只读搜索，不会开始转存，用户选中后再调 onetap_subscribe（带 tmdb_id）；video/comic 同时部署了两套方案时会报错要求传 instance_id，不要自己猜。",
    )
    async def onetap_search(
        domain: str,
        keyword: str,
        instance_id: int | None = None,
        page: int = 1,
        count: int = 20,
        sources: list[str] | None = None,
        source_id: str | None = None,
        sort: str = "quality",
    ) -> dict:
        async with async_session() as db:
            return await registry.search(
                db,
                domain=domain,
                keyword=keyword,
                instance_id=instance_id,
                page=page,
                count=count,
                sources=sources,
                source_id=source_id,
                sort=sort,
            )

    @server.tool(
        name="onetap_detail",
        description="读取单个资源详情。item_id 必须来自同一条 onetap_search 结果；影视需要 options 里的 media_source 与 type_name，音乐需要 options 里的 extra，本地漫画需要 options 里的 crawling_type / station_url / comic_href / category / name。",
    )
    async def onetap_detail(
        domain: str,
        item_id: str,
        options: dict | None = None,
        instance_id: int | None = None,
    ) -> dict:
        async with async_session() as db:
            return await registry.detail(
                db,
                domain=domain,
                item_id=item_id,
                options=options,
                instance_id=instance_id,
            )

    @server.tool(
        name="onetap_subscriptions",
        description="列出订阅。domain 取 video / comic / music / novel；每项含 subscription_id，供退订和替换使用。",
    )
    async def onetap_subscriptions(
        domain: str,
        instance_id: int | None = None,
        page: int = 1,
        count: int = 30,
    ) -> dict:
        async with async_session() as db:
            return await registry.subscriptions(
                db,
                domain=domain,
                instance_id=instance_id,
                page=page,
                count=count,
            )

    @server.tool(
        name="onetap_subscribe",
        description="添加订阅。video 的 local（MoviePilot）options 需要 name/type（可选 media_source、media_id、year、season、resolution、quality）；video 的 cloud（巡影）先 onetap_search 让用户选，再用返回的 tmdb_id 订阅（带 tmdb_id 时 type 必填，否则巡影按剧集处理；也可传 name 片名），可选 year、season、storage_id，命中多个候选时会返回 status=ambiguous 与 candidates，必须让用户选一个后用 options.tmdb_id 重发，不要自己拍板；comic 在线（Suwayomi）传漫画 item_id 即可，本地（Comic Auto）传 name/category 即可（Comic Auto 会自己搜索全部站点并匹配最优源），也可以把搜索结果里的 crawling_type/station_url/comic_href 放进 options 指定源，授权库条目用 license_subscription_id/license_comic_id；music 会把歌曲加入下载队列，options 需要 source/name 等歌曲字段；novel 的 options 需要搜索结果里的 name/bookUrl 等信息，首次订阅会自动比较同书名、同作者的候选书源并选用章节最多的源，可能耗时数十秒。把搜索结果里的 poster 或 cover 一并放进 options，通知才能带封面。调用前先让用户确认。调用成功即提交完成，立即回复用户，不要等待或轮询下载、整理、入库状态。",
    )
    async def onetap_subscribe(
        domain: str,
        item_id: str = "",
        options: dict | None = None,
        instance_id: int | None = None,
    ) -> dict:
        async with async_session() as db:
            return await registry.subscribe(
                db,
                domain=domain,
                item_id=item_id,
                options=options,
                instance_id=instance_id,
            )

    @server.tool(
        name="onetap_unsubscribe",
        description="删除订阅（破坏性）。domain 取 video / comic / music / novel；必须先向用户复述对象并得到明确同意。",
    )
    async def onetap_unsubscribe(
        domain: str,
        subscription_id: str,
        instance_id: int | None = None,
    ) -> dict:
        async with async_session() as db:
            return await registry.unsubscribe(
                db,
                domain=domain,
                subscription_id=subscription_id,
                instance_id=instance_id,
            )

    @server.tool(
        name="onetap_downloads",
        description="查询下载或缓存进度。domain 取 video / comic / music / novel；每项含 task_id。video 的 cloud（巡影）返回正在获取的任务和最近完成记录；comic 在线返回已缓存章节数，本地返回已下载/总章节数。仅在用户主动询问进度时查询一次，不要在订阅、替换或启动下载后轮询等待。",
    )
    async def onetap_downloads(
        domain: str,
        instance_id: int | None = None,
        limit: int = 20,
    ) -> dict:
        async with async_session() as db:
            return await registry.downloads(
                db,
                domain=domain,
                instance_id=instance_id,
                limit=limit,
            )

    @server.tool(
        name="onetap_download",
        description="控制下载任务：start 开始或恢复、stop 暂停、delete 删除。delete 属于破坏性操作，必须传 confirm=true 并且先得到用户同意。comic 本地（Comic Auto）只支持 start（触发补下载与入库），不支持 stop/delete；video 的 cloud（巡影）不支持任何单任务控制。",
    )
    async def onetap_download(
        domain: str,
        task_id: str,
        action: str,
        confirm: bool = False,
        options: dict | None = None,
        instance_id: int | None = None,
    ) -> dict:
        if action not in {"start", "stop", "delete"}:
            raise APIException("action 只能是 start / stop / delete", 400)
        async with async_session() as db:
            return await registry.download(
                db,
                domain=domain,
                task_id=task_id,
                action=action,
                confirm=confirm,
                options=options,
                instance_id=instance_id,
            )

    @server.tool(
        name="onetap_replace",
        description="替换下载不正确的资源：影视按洗版语义重新搜索（旧文件在新版本入库前保留）；漫画/音乐/小说重新补齐或重新下载，不主动删除已有文件。必须先确认并传 confirm=true。",
    )
    async def onetap_replace(
        domain: str,
        subscription_id: str,
        options: dict | None = None,
        confirm: bool = False,
        instance_id: int | None = None,
    ) -> dict:
        if not confirm:
            raise APIException("替换资源前需要用户确认，请传 confirm=true", 400)
        async with async_session() as db:
            return await registry.replace(
                db,
                domain=domain,
                subscription_id=subscription_id,
                options=options,
                confirm=confirm,
                instance_id=instance_id,
            )
