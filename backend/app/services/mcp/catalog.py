"""OneTap MCP 工具目录。

只列资源网关自己的语义工具（``onetap_*``），不再展示 MoviePilot 的透传工具。
"""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.mcp.config import mcp_config_service

LOCAL_SOURCE_ID = "local"

_LOCAL_RISK = {
    "onetap_capabilities": "read",
    "onetap_search": "read",
    "onetap_detail": "read",
    "onetap_subscriptions": "read",
    "onetap_downloads": "read",
    "onetap_present": "write",
    "onetap_notifications": "read",
    "onetap_set_notify_mode": "write",
    "onetap_subscribe": "write",
    "onetap_unsubscribe": "destructive",
    "onetap_download": "destructive",
    "onetap_replace": "destructive",
}

_LOCAL_CATEGORY = {
    "onetap_capabilities": "系统",
    "onetap_search": "查询",
    "onetap_detail": "查询",
    "onetap_subscriptions": "查询",
    "onetap_downloads": "查询",
    "onetap_present": "通知",
    "onetap_notifications": "通知",
    "onetap_set_notify_mode": "通知",
    "onetap_subscribe": "订阅",
    "onetap_unsubscribe": "订阅",
    "onetap_download": "下载",
    "onetap_replace": "替换",
}


def _annotations(tool: Any) -> dict[str, Any] | None:
    value = getattr(tool, "annotations", None)
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    return value if isinstance(value, dict) else None


def _local_entry(tool: Any, available: bool) -> dict[str, Any]:
    name = str(tool.name)
    return {
        "kind": "local_tool",
        "entry_id": f"local:{name}",
        "source": LOCAL_SOURCE_ID,
        "source_name": "OneTap",
        "domain": _LOCAL_CATEGORY.get(name, "资源网关"),
        "name": name,
        "title": tool.title or name,
        "description": tool.description or "",
        "method": "MCP",
        "path": "",
        "public_name": name,
        "tags": ["onetap"],
        "risk": _LOCAL_RISK.get(name, "write"),
        "transport": "mcp",
        "required_role": "admin",
        "invocable": available,
        "blocked_reason": "" if available else "AI 接入未启用",
        "upstream_instance_id": None,
        "input_schema": tool.parameters or {},
        "output_schema": getattr(tool, "output_schema", None),
        "annotations": _annotations(tool),
    }


async def build_mcp_catalog(db: AsyncSession, host: str) -> dict[str, Any]:
    from app.services.mcp.server import mcp_aggregator

    config = await mcp_config_service.get_config(db)
    enabled = bool(config["enabled"])
    tools = await mcp_aggregator.server._tool_manager.get_tools()
    entries = [
        _local_entry(tool, enabled)
        for name, tool in sorted(tools.items())
        if name.startswith("onetap_")
    ]
    source = {
        "id": LOCAL_SOURCE_ID,
        "instance_id": 0,
        "kind": "local_tool",
        "name": "OneTap",
        "available": enabled,
        "error": "" if enabled else "AI 接入未启用",
        "count": len(entries),
    }
    return {
        "entries": entries,
        "sources": [source],
        "generated_at": datetime.now(timezone.utc),
    }


async def get_mcp_catalog_entry(
    db: AsyncSession, host: str, entry_id: str
) -> dict[str, Any] | None:
    catalog = await build_mcp_catalog(db, host)
    for entry in catalog["entries"]:
        if entry["entry_id"] == entry_id:
            return entry
    return None
