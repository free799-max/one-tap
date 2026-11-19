"""MoviePilot 资源适配器：把 OneTap 的语义工具映射到 MoviePilot 原生 MCP。

只使用 MoviePilot 公开的 operation_id 与对应参数合同，不猜测 REST 路径。
"""

import json
from typing import Any

from fastmcp.exceptions import ToolError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import APIException
from app.services.apps.moviepilot.integration import MoviePilotIntegrationMixin
from app.services.mcp.auth import current_mcp_actor
from app.services.mcp.config import UpstreamState, mcp_config_service
from app.services.mcp.upstream import build_upstream_client

# MoviePilot 未显式配置 MediaSources 时的默认来源，与上游一致
_DEFAULT_MEDIA_SOURCES = ["themoviedb", "douban"]


def _host() -> str:
    actor = current_mcp_actor()
    return actor.request_host if actor is not None else "127.0.0.1"


async def resolve_state(db: AsyncSession, instance_id: int | None = None) -> UpstreamState:
    states = [
        state
        for state in await mcp_config_service.list_upstreams(
            db, _host(), app_names={"moviepilot"}
        )
        if state.available
    ]
    if instance_id is not None:
        states = [state for state in states if state.app.instance_id == instance_id]
    if not states:
        raise APIException("没有可用的 MoviePilot 实例，请先部署并启动", 400)
    return states[0]


async def _call(state: UpstreamState, tool: str, arguments: dict[str, Any]) -> Any:
    try:
        async with build_upstream_client(state) as client:
            result = await client.call_tool(tool, arguments)
    except ToolError as exc:
        raise APIException(f"MoviePilot 调用失败：{exc}", 502) from exc
    text = "\n".join(
        block.text for block in result.content if getattr(block, "text", None)
    )
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}
    if isinstance(payload, dict) and payload.get("success") is False:
        message = payload.get("message") or payload.get("error") or "MoviePilot 返回失败"
        raise APIException(str(message), 502)
    return payload


async def _api(
    state: UpstreamState,
    operation_id: str,
    *,
    path_params: dict[str, Any] | None = None,
    query: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
) -> Any:
    arguments: dict[str, Any] = {"operation_id": operation_id}
    if path_params:
        arguments["path_params"] = path_params
    if query:
        arguments["query"] = query
    if body is not None:
        arguments["body"] = body
    payload = await _call(state, "moviepilot_api", arguments)
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


def _clean(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item is not None}


async def _media_sources(state: UpstreamState) -> list[str]:
    """读取 MoviePilot 启用的媒体来源；未配置时回退到上游默认值。"""
    ok, value, _ = await MoviePilotIntegrationMixin().get_setting(
        state.app.url or "", state.app.api_key or "", "MediaSources"
    )
    if ok and isinstance(value, list):
        sources = [str(item) for item in value if item]
        if sources:
            return sources
    return list(_DEFAULT_MEDIA_SOURCES)


async def capabilities(db: AsyncSession) -> dict[str, Any]:
    states = [
        state
        for state in await mcp_config_service.list_upstreams(
            db, _host(), app_names={"moviepilot"}
        )
        if state.available
    ]
    return {
        "domains": {
            "video": {
                "available": bool(states),
                "instances": [
                    {
                        "instance_id": state.app.instance_id,
                        "name": state.app.instance_name,
                        "app": state.app.app_name,
                    }
                    for state in states
                ],
                "operations": [
                    "search",
                    "detail",
                    "subscriptions",
                    "subscribe",
                    "unsubscribe",
                    "downloads",
                    "replace",
                ],
            },
            "comic": {"available": False, "reason": "漫画适配器尚未接入"},
            "music": {"available": False, "reason": "音乐适配器尚未接入"},
            "novel": {"available": False, "reason": "小说适配器尚未接入"},
        }
    }


async def search(
    db: AsyncSession,
    *,
    title: str,
    instance_id: int | None = None,
    media_source: list[str] | None = None,
    page: int = 1,
    count: int = 20,
) -> dict[str, Any]:
    state = await resolve_state(db, instance_id)
    query: dict[str, Any] = {
        "title": title,
        "type": "media",
        "page": page,
        "count": count,
        "media_source": media_source or await _media_sources(state),
    }
    data = await _api(state, "media.search", query=query)
    items = data if isinstance(data, list) else []
    return {
        "instance_id": state.app.instance_id,
        "items": [
            _clean(
                {
                    "title": item.get("title"),
                    "year": item.get("year"),
                    "type": item.get("type"),
                    "media_source": item.get("media_source"),
                    "media_id": item.get("media_id"),
                    # MoviePilot 搜索返回的是 TMDB 字段名，通知卡片和候选推送都要海报与评分
                    "vote": item.get("vote_average") or item.get("vote"),
                    "overview": (item.get("overview") or "")[:300] or None,
                    "poster": item.get("poster_path") or item.get("poster"),
                }
            )
            for item in items
            if isinstance(item, dict)
        ],
    }


async def detail(
    db: AsyncSession,
    *,
    media_id: str,
    media_source: str,
    type_name: str,
    instance_id: int | None = None,
) -> dict[str, Any]:
    state = await resolve_state(db, instance_id)
    data = await _api(
        state,
        "media.detail",
        path_params={"media_id": media_id},
        query={"media_source": media_source, "type_name": type_name},
    )
    return {"instance_id": state.app.instance_id, "detail": data}


def metadata(detail: dict[str, Any]) -> dict[str, Any]:
    """从媒体详情提取通知卡片用的元数据（海报、类型、评分、时长、演职员）。"""

    def names(key: str, limit: int) -> list[str]:
        result = []
        for item in detail.get(key) or []:
            name = str(item.get("name") if isinstance(item, dict) else item or "").strip()
            # MoviePilot 的演职员表可能重复（不同 credit），去重后再截断
            if name and name not in result:
                result.append(name)
            if len(result) >= limit:
                break
        return result

    runtime = detail.get("runtime")
    if not runtime:
        episode_run_time = detail.get("episode_run_time") or []
        runtime = episode_run_time[0] if episode_run_time else None
    values = {
        "poster": detail.get("poster_path") or "",
        "type": detail.get("type") or "",
        "year": str(detail.get("year") or ""),
        "genres": names("genres", 4),
        "rating": detail.get("vote_average"),
        "vote_count": detail.get("vote_count"),
        "runtime": runtime,
        "directors": names("directors", 2),
        "actors": names("actors", 3),
        "release_date": detail.get("release_date") or detail.get("first_air_date") or "",
    }
    return {key: value for key, value in values.items() if value not in (None, "", [])}


async def fetch_metadata(
    db: AsyncSession,
    *,
    media_id: str,
    media_source: str,
    type_name: str,
    instance_id: int | None = None,
) -> dict[str, Any]:
    """读取媒体元数据；失败返回空字典，不影响订阅与通知主流程。"""
    try:
        result = await detail(
            db,
            media_id=media_id,
            media_source=media_source,
            type_name=type_name,
            instance_id=instance_id,
        )
    except Exception:
        return {}
    return metadata(result.get("detail") or {})


async def subscriptions(
    db: AsyncSession,
    *,
    instance_id: int | None = None,
    page: int = 1,
    count: int = 30,
) -> dict[str, Any]:
    state = await resolve_state(db, instance_id)
    data = await _api(state, "subscription.list", query={"page": page, "count": count})
    return {"instance_id": state.app.instance_id, "items": data}


async def subscribe(
    db: AsyncSession,
    *,
    name: str,
    type: str,
    year: str | None = None,
    media_source: str | None = None,
    media_id: str | None = None,
    season: int | None = None,
    resolution: str | None = None,
    quality: str | None = None,
    best_version: bool | None = None,
    instance_id: int | None = None,
) -> dict[str, Any]:
    state = await resolve_state(db, instance_id)
    body = _clean(
        {
            "name": name,
            "type": type,
            "year": year,
            "media_source": media_source,
            "media_id": media_id,
            "season": season,
            "resolution": resolution,
            "quality": quality,
            "best_version": 1 if best_version else None,
        }
    )
    data = await _api(state, "subscription.add", body=body)
    return {"instance_id": state.app.instance_id, "result": data}


async def unsubscribe(
    db: AsyncSession, *, subscribe_id: int, instance_id: int | None = None
) -> dict[str, Any]:
    state = await resolve_state(db, instance_id)
    data = await _api(
        state, "subscription.delete", path_params={"subscribe_id": subscribe_id}
    )
    return {"instance_id": state.app.instance_id, "result": data}


async def replace(
    db: AsyncSession,
    *,
    subscribe_id: int,
    resolution: str | None = None,
    quality: str | None = None,
    instance_id: int | None = None,
) -> dict[str, Any]:
    """按洗版方式替换：开启 best_version 并立即搜索，旧文件在新版本入库前保留。"""
    state = await resolve_state(db, instance_id)
    body = _clean(
        {
            "id": subscribe_id,
            "best_version": 1,
            "resolution": resolution,
            "quality": quality,
        }
    )
    await _api(state, "subscription.update", body=body)
    data = await _api(
        state, "subscription.search", path_params={"subscribe_id": subscribe_id}
    )
    return {"instance_id": state.app.instance_id, "result": data}


async def downloads(
    db: AsyncSession,
    *,
    status: str | None = None,
    limit: int = 20,
    instance_id: int | None = None,
) -> dict[str, Any]:
    state = await resolve_state(db, instance_id)
    arguments: dict[str, Any] = {"limit": limit}
    if status:
        arguments["status"] = status
    payload = await _call(
        state,
        "downloader_operation",
        {"action": "tasks.list", "arguments": arguments},
    )
    data = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload
    return {"instance_id": state.app.instance_id, "items": data}


async def task_states(state: UpstreamState) -> dict[str, dict[str, Any]]:
    """按 hash 索引下载器任务；通知同步用它确认资源是否真的下载完成。

    MoviePilot 在「添加下载」时就写下载历史，历史记录本身不能证明已下完。
    """
    payload = await _call(
        state,
        "downloader_operation",
        {"action": "tasks.list", "arguments": {"limit": 200}},
    )
    data = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload
    items = data.get("items") if isinstance(data, dict) else data
    result: dict[str, dict[str, Any]] = {}
    for item in items or []:
        if isinstance(item, dict) and item.get("hash"):
            result[str(item["hash"]).lower()] = item
    return result


async def download_action(
    db: AsyncSession,
    *,
    task_id: str,
    action: str,
    delete_files: bool = False,
    instance_id: int | None = None,
) -> dict[str, Any]:
    state = await resolve_state(db, instance_id)
    action_map = {"start": "tasks.start", "stop": "tasks.stop", "delete": "tasks.delete"}
    remote_action = action_map.get(action)
    if remote_action is None:
        raise APIException("action 只能是 start / stop / delete", 400)
    arguments: dict[str, Any] = {"task_id": task_id}
    if action == "delete":
        arguments["delete_files"] = delete_files
    payload = await _call(
        state, "downloader_operation", {"action": remote_action, "arguments": arguments}
    )
    data = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload
    return {"instance_id": state.app.instance_id, "result": data}
