"""OneTap 本地 MCP 工具，复用已有媒体自动化服务。"""

from typing import Any

from fastmcp import FastMCP

from app.database import async_session
from app.services.integrations.discovery import list_deployed_apps
from app.services.mcp.api_invoker import browse_api_catalog, invoke_api
from app.services.mcp.auth import current_mcp_actor
from app.services.mcp.config import (
    MEDIA_APP_NAMES,
    NATIVE_MCP_APPS,
    mcp_config_service,
    upstream_prefix,
)
from app.services.orchestration.music_auto import music_auto_service
from app.services.orchestration.reader_auto import reader_auto_service


def _request_host() -> str:
    actor = current_mcp_actor()
    return actor.request_host if actor is not None else "127.0.0.1"


def _status_result(group_id: int, panel: dict[str, Any]) -> dict[str, Any]:
    return {
        "group_id": group_id,
        "running": bool(panel.get("running")),
        "progress": panel.get("progress"),
        "stats": panel.get("stats") or {},
    }


def register_local_tools(server: FastMCP) -> None:
    @server.tool(
        name="media_targets_list",
        annotations={"readOnlyHint": True, "destructiveHint": False},
    )
    async def media_targets_list() -> list[dict[str, Any]]:
        """列出 OneTap 中运行中的媒体自动化应用及其原生 MCP 支持情况。"""
        async with async_session() as db:
            apps = await list_deployed_apps(db, _request_host())
        return [
            {
                "instance_id": app.instance_id,
                "app_name": app.app_name,
                "display_name": app.display_name,
                "instance_name": app.instance_name,
                "status": app.status,
                "native_mcp": app.app_name in NATIVE_MCP_APPS,
                "mcp_prefix": (
                    upstream_prefix(app) if app.app_name in NATIVE_MCP_APPS else None
                ),
            }
            for app in apps
            if app.app_name in MEDIA_APP_NAMES and app.status == "running"
        ]

    @server.tool(
        name="music_organize_status",
        annotations={"readOnlyHint": True, "destructiveHint": False},
    )
    async def music_organize_status(group_id: int) -> dict[str, Any]:
        """查询指定实例组的音乐自动整理进度与文件统计。"""
        async with async_session() as db:
            panel = await music_auto_service.get_panel(db, group_id)
        return _status_result(group_id, panel)

    @server.tool(
        name="reader_sync_status",
        annotations={"readOnlyHint": True, "destructiveHint": False},
    )
    async def reader_sync_status(group_id: int) -> dict[str, Any]:
        """查询指定实例组的 Reader 自动同步进度与书籍统计。"""
        async with async_session() as db:
            panel = await reader_auto_service.get_panel(db, group_id)
        return _status_result(group_id, panel)

    @server.tool(
        name="music_organize_run",
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def music_organize_run(group_id: int) -> dict[str, Any]:
        """立即启动一轮音乐刮削整理，并返回当前任务进度。"""
        async with async_session() as db:
            status = await music_auto_service.start_run(
                db, group_id, trigger="mcp", force=True
            )
        return {"group_id": group_id, "started": True, "progress": status}

    @server.tool(
        name="reader_sync_run",
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def reader_sync_run(group_id: int) -> dict[str, Any]:
        """立即启动一轮 Reader 章节缓存、EPUB 导出和书库同步。"""
        async with async_session() as db:
            status = await reader_auto_service.start_run(
                db, group_id, trigger="mcp", force=True
            )
        return {"group_id": group_id, "started": True, "progress": status}

    @server.tool(
        name="onetap_api_catalog",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "openWorldHint": False,
        },
    )
    async def onetap_api_catalog(
        query: str = "",
        domain: str = "",
        method: str = "",
        risk: str = "",
        operation_id: str = "",
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        """查询完整 OneTap API 目录；传 operation_id 时返回该接口的详细 Schema。"""
        async with async_session() as db:
            config = await mcp_config_service.get_config(db)
        return browse_api_catalog(
            query=query,
            domain=domain,
            method=method,
            risk=risk,
            operation_id=operation_id,
            page=page,
            page_size=page_size,
            authorized_ids=set(config["api_operation_ids"]),
        )

    @server.tool(
        name="onetap_api_request",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "openWorldHint": False,
        },
    )
    async def onetap_api_request(
        operation_id: str,
        path_params: dict[str, Any] | None = None,
        query_params: dict[str, Any] | None = None,
        body: Any = None,
    ) -> dict[str, Any]:
        """调用已授权的 OneTap API，只允许目录中的 operationId 和声明参数。"""
        return await invoke_api(
            operation_id=operation_id,
            path_params=path_params or {},
            query_params=query_params or {},
            body=body,
        )
