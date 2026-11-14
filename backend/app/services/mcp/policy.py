"""MCP 工具列表和调用的服务端权限策略。"""

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Any

import mcp.types as mt
from fastmcp.exceptions import PromptError, ResourceError, ToolError
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.tools.tool import Tool, ToolResult

from app.services.mcp.audit import record_tool_call
from app.services.mcp.config import split_public_tool_name


if TYPE_CHECKING:
    from app.services.mcp.server import McpAggregator


class McpPolicyMiddleware(Middleware):
    def __init__(self, aggregator: "McpAggregator"):
        self.aggregator = aggregator

    async def _allowed_names(self) -> set[str]:
        return await self.aggregator.allowed_tool_names()

    async def on_list_tools(
        self,
        context: MiddlewareContext[mt.ListToolsRequest],
        call_next: Callable[
            [MiddlewareContext[mt.ListToolsRequest]], Awaitable[Sequence[Tool]]
        ],
    ) -> Sequence[Tool]:
        await self.aggregator.ensure_upstreams()
        tools = await call_next(context)
        allowed = await self._allowed_names()
        return [tool for tool in tools if tool.key in allowed]

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: Callable[
            [MiddlewareContext[mt.CallToolRequestParams]], Awaitable[ToolResult]
        ],
    ) -> ToolResult:
        name = context.message.name
        audit_name = name
        started_at = time.monotonic()
        source = "local"
        remote = split_public_tool_name(name)
        instance_id: int | None = None
        if name == "onetap_api_request":
            operation_id = (context.message.arguments or {}).get("operation_id")
            if (
                isinstance(operation_id, str)
                and len(operation_id) <= 128
                and operation_id.isidentifier()
            ):
                source = "api"
                audit_name = f"api:{operation_id}"
        elif remote is not None:
            source = "remote"
            instance_id = int(remote[0])
        try:
            await self.aggregator.ensure_upstreams()
            if name not in await self._allowed_names():
                raise ToolError("工具未启用或不在白名单中")
            result = await call_next(context)
        except asyncio.CancelledError:
            await asyncio.shield(
                record_tool_call(
                    tool_name=audit_name,
                    source=source,
                    upstream_instance_id=instance_id,
                    success=False,
                    error="调用已取消",
                    started_at=started_at,
                )
            )
            raise
        except Exception as exc:
            await record_tool_call(
                tool_name=audit_name,
                source=source,
                upstream_instance_id=instance_id,
                success=False,
                error=exc,
                started_at=started_at,
            )
            raise
        await record_tool_call(
            tool_name=audit_name,
            source=source,
            upstream_instance_id=instance_id,
            success=True,
            started_at=started_at,
        )
        return result

    async def on_list_resources(self, context: Any, call_next: Any) -> list:
        return []

    async def on_list_resource_templates(self, context: Any, call_next: Any) -> list:
        return []

    async def on_list_prompts(self, context: Any, call_next: Any) -> list:
        return []

    async def on_read_resource(self, context: Any, call_next: Any) -> list:
        raise ResourceError("MCP 资源未开放")

    async def on_get_prompt(self, context: Any, call_next: Any) -> Any:
        raise PromptError("MCP Prompt 未开放")
