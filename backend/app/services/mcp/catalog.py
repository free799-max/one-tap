"""统一管理目录：OneTap API、本地工具与原生 MCP 上游。"""

import asyncio
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.mcp.config import (
    LOCAL_TOOL_DEFINITIONS,
    UpstreamState,
    mcp_config_service,
    public_tool_name,
)
from app.services.mcp.openapi_catalog import get_api_operation, list_api_operations
from app.services.mcp.upstream import discover_upstream_tools


def _local_domain(name: str) -> str:
    if name.startswith("onetap_api_"):
        return "api"
    return name.split("_", 1)[0]


def _remote_risk(annotations: dict[str, Any] | None) -> str:
    values = annotations or {}
    if values.get("destructiveHint") is True:
        return "destructive"
    if values.get("readOnlyHint") is True:
        return "read"
    if values.get("readOnlyHint") is False:
        return "write"
    return "unknown"


def _local_tools(config: dict[str, Any], tools: dict[str, Any]) -> list[dict[str, Any]]:
    enabled_names = set(config["local_tools"])
    entries = []
    for name, definition in LOCAL_TOOL_DEFINITIONS.items():
        tool = tools.get(name)
        enabled = name in enabled_names
        effective = bool(config["enabled"] and enabled and tool is not None)
        if tool is None:
            status_reason = "工具尚未注册"
        elif not enabled:
            status_reason = "未授权"
        elif not config["enabled"]:
            status_reason = "MCP 聚合未启用"
        else:
            status_reason = ""
        annotations = (
            tool.annotations.model_dump(exclude_none=True)
            if tool is not None and tool.annotations is not None
            else None
        )
        entries.append(
            {
                "kind": "local_tool",
                "entry_id": f"local:{name}",
                "source": "local",
                "source_name": "OneTap 内置工具",
                "domain": _local_domain(name),
                "name": name,
                "title": definition["title"],
                "description": definition["description"],
                "method": "MCP",
                "path": "",
                "public_name": name,
                "tags": ["local", _local_domain(name)],
                "risk": "read" if definition["read_only"] else "write",
                "transport": "mcp",
                "required_role": "admin",
                "invocable": tool is not None,
                "blocked_reason": "" if tool is not None else "工具尚未注册",
                "enabled": enabled,
                "effective": effective,
                "status_reason": status_reason,
                "input_schema": dict(tool.parameters) if tool is not None else {},
                "output_schema": tool.output_schema if tool is not None else None,
                "annotations": annotations,
            }
        )
    return entries


def _remote_entry(
    state: UpstreamState,
    tool: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    name = tool["name"]
    enabled = name in set(state.allowed_tools)
    available = state.available
    effective = bool(config["enabled"] and state.enabled and available and enabled)
    if not enabled:
        status_reason = "未授权"
    elif not state.enabled:
        status_reason = "上游来源未启用"
    elif not available:
        status_reason = state.unavailable_reason
    elif not config["enabled"]:
        status_reason = "MCP 聚合未启用"
    else:
        status_reason = ""
    return {
        "kind": "remote_tool",
        "entry_id": f"remote:{state.app.instance_id}:{name}",
        "source": f"upstream:{state.app.instance_id}",
        "source_name": state.app.instance_name,
        "domain": name.split(".", 1)[0],
        "name": name,
        "title": tool.get("title") or name,
        "description": tool.get("description") or "",
        "method": "MCP",
        "path": "",
        "public_name": public_tool_name(state.prefix, name),
        "tags": [state.app.app_name, name.split(".", 1)[0]],
        "risk": _remote_risk(tool.get("annotations")),
        "transport": "mcp",
        "required_role": "admin",
        "invocable": available,
        "blocked_reason": "" if available else state.unavailable_reason,
        "enabled": enabled,
        "effective": effective,
        "status_reason": status_reason,
        "upstream_instance_id": state.app.instance_id,
        "input_schema": tool.get("input_schema") or {},
        "output_schema": tool.get("output_schema"),
        "annotations": tool.get("annotations"),
    }


def _missing_remote_entry(
    state: UpstreamState, name: str, config: dict[str, Any], error: str
) -> dict[str, Any]:
    reason = error or "本次上游目录未发现该工具"
    return {
        "kind": "remote_tool",
        "entry_id": f"remote:{state.app.instance_id}:{name}",
        "source": f"upstream:{state.app.instance_id}",
        "source_name": state.app.instance_name,
        "domain": name.split(".", 1)[0],
        "name": name,
        "title": name,
        "description": "已配置权限，但本次未能从上游读取工具详情。",
        "method": "MCP",
        "path": "",
        "public_name": public_tool_name(state.prefix, name),
        "tags": [state.app.app_name, "missing"],
        "risk": "unknown",
        "transport": "mcp",
        "required_role": "admin",
        "invocable": False,
        "blocked_reason": reason,
        "enabled": True,
        "effective": False,
        "status_reason": reason,
        "upstream_instance_id": state.app.instance_id,
        "input_schema": {},
        "output_schema": None,
        "annotations": None,
    }


async def _remote_source(
    state: UpstreamState, config: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    source = {
        "id": f"upstream:{state.app.instance_id}",
        "kind": "remote_tool",
        "name": state.app.instance_name,
        "available": state.available,
        "enabled": state.enabled,
        "error": "" if state.available else state.unavailable_reason,
        "count": 0,
    }
    if not state.available:
        entries = [
            _missing_remote_entry(state, name, config, source["error"])
            for name in state.allowed_tools
        ]
        source["count"] = len(entries)
        return source, entries

    try:
        tools = await discover_upstream_tools(state)
    except Exception as exc:
        source["available"] = False
        source["error"] = str(exc) or type(exc).__name__
        entries = [
            _missing_remote_entry(state, name, config, source["error"])
            for name in state.allowed_tools
        ]
        source["count"] = len(entries)
        return source, entries

    entries = [_remote_entry(state, tool, config) for tool in tools]
    discovered = {tool["name"] for tool in tools}
    entries.extend(
        _missing_remote_entry(state, name, config, "")
        for name in state.allowed_tools
        if name not in discovered
    )
    source["count"] = len(entries)
    return source, entries


def _api_entries(config: dict[str, Any]) -> list[dict[str, Any]]:
    enabled_names = set(config["api_operation_ids"])
    request_enabled = "onetap_api_request" in set(config["local_tools"])
    entries = []
    for operation in list_api_operations():
        name = operation["name"]
        enabled = name in enabled_names
        effective = bool(
            config["enabled"]
            and request_enabled
            and enabled
            and operation["invocable"]
        )
        if not operation["invocable"]:
            status_reason = operation["blocked_reason"]
        elif not enabled:
            status_reason = "未授权"
        elif not request_enabled:
            status_reason = "统一 API 调用工具未启用"
        elif not config["enabled"]:
            status_reason = "MCP 聚合未启用"
        else:
            status_reason = ""
        entries.append(
            operation
            | {
                "enabled": enabled,
                "effective": effective,
                "status_reason": status_reason,
            }
        )
    return entries


async def build_mcp_catalog(db: AsyncSession, host: str) -> dict[str, Any]:
    from app.services.mcp.server import mcp_aggregator

    config = await mcp_config_service.get_config(db)
    states = await mcp_config_service.list_upstreams(db, host)
    registered_tools, remote_results = await asyncio.gather(
        mcp_aggregator.server._tool_manager.get_tools(),
        asyncio.gather(*(_remote_source(state, config) for state in states)),
    )
    api_entries = _api_entries(config)
    local_entries = _local_tools(config, registered_tools)
    entries = api_entries + local_entries
    sources = [
        {
            "id": "onetap",
            "kind": "onetap_api",
            "name": "OneTap API",
            "available": True,
            "enabled": True,
            "error": "",
            "count": len(api_entries),
        },
        {
            "id": "local",
            "kind": "local_tool",
            "name": "OneTap 内置工具",
            "available": True,
            "enabled": True,
            "error": "",
            "count": len(local_entries),
        },
    ]
    for source, remote_entries in remote_results:
        sources.append(source)
        entries.extend(remote_entries)
    return {
        "entries": entries,
        "sources": sources,
        "generated_at": datetime.now(timezone.utc),
    }


async def get_mcp_catalog_entry(
    db: AsyncSession, host: str, entry_id: str
) -> dict[str, Any] | None:
    if entry_id.startswith("api:"):
        operation = get_api_operation(entry_id[4:])
        if operation is not None:
            config = await mcp_config_service.get_config(db)
            current = next(
                item for item in _api_entries(config) if item["entry_id"] == entry_id
            )
            return current | {
                key: value
                for key, value in operation.items()
                if key in {"parameters", "request_body", "responses", "components", "security"}
            }

    catalog = await build_mcp_catalog(db, host)
    for entry in catalog["entries"]:
        if entry["entry_id"] == entry_id:
            if entry["kind"] == "onetap_api":
                return entry | {
                    "parameters": [],
                    "request_body": None,
                    "responses": {},
                    "components": {},
                    "security": None,
                }
            return entry
    return None
