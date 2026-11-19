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

    async def _available_instance_ids(self) -> set[int]:
        return await self.aggregator.available_instance_ids()

    @staticmethod
    def _is_allowed(name: str, available_ids: set[int]) -> bool:
        if name.startswith("onetap_"):
            return True
        remote = split_public_tool_name(name)
        return remote is not None and int(remote[0]) in available_ids

    async def on_list_tools(
        self,
        context: MiddlewareContext[mt.ListToolsRequest],
        call_next: Callable[
            [MiddlewareContext[mt.ListToolsRequest]], Awaitable[Sequence[Tool]]
        ],
    ) -> Sequence[Tool]:
        await self.aggregator.ensure_upstreams()
        tools = await call_next(context)
        available_ids = await self._available_instance_ids()
        local_enabled = await self.aggregator.mcp_enabled()
        return [
            tool
            for tool in tools
            if self._is_allowed(tool.key, available_ids)
            and (local_enabled or not tool.key.startswith("onetap_"))
        ]

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: Callable[
            [MiddlewareContext[mt.CallToolRequestParams]], Awaitable[ToolResult]
        ],
    ) -> ToolResult:
        name = context.message.name
        started_at = time.monotonic()
        is_local = name.startswith("onetap_")
        source = "local" if is_local else "remote"
        remote = None if is_local else split_public_tool_name(name)
        instance_id: int | None = None
        if remote is not None:
            instance_id = int(remote[0])
        try:
            await self.aggregator.ensure_upstreams()
            if is_local:
                if not await self.aggregator.mcp_enabled():
                    raise ToolError("AI 接入未启用")
            else:
                available_ids = await self._available_instance_ids()
                if instance_id is None or instance_id not in available_ids:
                    raise ToolError("工具来源不可用")
            result = await call_next(context)
        except asyncio.CancelledError:
            await asyncio.shield(
                record_tool_call(
                    tool_name=name,
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
                tool_name=name,
                source=source,
                upstream_instance_id=instance_id,
                success=False,
                error=exc,
                started_at=started_at,
            )
            raise
        await record_tool_call(
            tool_name=name,
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
