"""OneTap MCP 聚合 Server 与 ASGI 应用。"""

import asyncio
import logging
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import mcp.types as mt
from fastmcp import FastMCP
from fastmcp.exceptions import NotFoundError
from fastmcp.server.middleware import MiddlewareContext
from fastmcp.server.server import MountedServer
from fastmcp.tools.tool import Tool, ToolResult

from app.core.exceptions import APIException
from app.database import async_session
from app.services.mcp.auth import McpAuthMiddleware, current_mcp_actor
from app.services.mcp.config import (
    LOCAL_TOOL_DEFINITIONS,
    mcp_config_service,
    public_tool_name,
)
from app.services.mcp.local_tools import register_local_tools
from app.services.mcp.policy import McpPolicyMiddleware
from app.services.mcp.upstream import UpstreamFastMCPProxy, build_upstream_proxy


logger = logging.getLogger(__name__)
PUBLIC_TOOL_NAME = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


class AggregatedFastMCP(FastMCP):
    """本地调用优先，远程工具目录并发加载，固定 FastMCP 2.14.7。"""

    async def get_tool(self, key: str) -> Tool:
        try:
            return await self._tool_manager.get_tool(key)
        except NotFoundError:
            tools = await self.get_tools()
            if key not in tools:
                raise NotFoundError(f"Unknown tool: {key}") from None
            return tools[key]

    async def _call_tool(
        self, context: MiddlewareContext[mt.CallToolRequestParams]
    ) -> ToolResult:
        name = context.message.name
        try:
            tool = await self._tool_manager.get_tool(name)
            if self._should_enable_component(tool):
                return await self._tool_manager.call_tool(
                    key=name, arguments=context.message.arguments or {}
                )
        except NotFoundError:
            pass
        return await super()._call_tool(context)

    async def get_tools(self) -> dict[str, Tool]:
        all_tools = dict(await self._tool_manager.get_tools())
        mounted_servers = list(self._mounted_servers)

        async def load(mounted: MountedServer) -> tuple[MountedServer, dict[str, Tool]]:
            try:
                return mounted, await mounted.server.get_tools()
            except Exception:
                logger.warning("原生 MCP 上游 %s 工具目录加载失败", mounted.prefix)
                return mounted, {}

        for mounted, tools in await asyncio.gather(
            *(load(mounted) for mounted in mounted_servers)
        ):
            for key, tool in tools.items():
                if mounted.tool_names and key in mounted.tool_names:
                    new_key = mounted.tool_names[key]
                elif mounted.prefix:
                    new_key = f"{mounted.prefix}_{key}"
                else:
                    new_key = key
                if PUBLIC_TOOL_NAME.fullmatch(new_key):
                    if new_key in all_tools:
                        logger.warning("忽略重复的 MCP 公开工具名：%s", new_key[:160])
                        continue
                    all_tools[new_key] = tool.model_copy(key=new_key)
                else:
                    logger.warning("忽略非法 MCP 公开工具名：%s", new_key[:160])
        return all_tools


@dataclass
class MountedUpstream:
    prefix: str
    proxy: UpstreamFastMCPProxy
    fingerprint: tuple[Any, ...]


class McpAggregator:
    def __init__(self):
        self.server = AggregatedFastMCP("OneTap MCP")
        register_local_tools(self.server)
        self.server.add_middleware(McpPolicyMiddleware(self))
        self._mounted: dict[int, MountedUpstream] = {}
        self._mount_lock = asyncio.Lock()
        self.http_app = self.server.http_app(path="/", stateless_http=True)
        self.asgi_app = McpAuthMiddleware(self.http_app)

    def _host(self) -> str:
        actor = current_mcp_actor()
        return actor.request_host if actor is not None else "127.0.0.1"

    async def ensure_upstreams(self) -> None:
        async with self._mount_lock:
            async with async_session() as db:
                states = await mcp_config_service.list_upstreams(db, self._host())
                await mcp_config_service.prune_upstreams(
                    db, {state.app.instance_id for state in states}
                )
            current_prefixes = {state.prefix for state in states}
            self.server._mounted_servers = [
                mounted
                for mounted in self.server._mounted_servers
                if mounted.prefix in current_prefixes
            ]
            self._mounted = {
                instance_id: mounted
                for instance_id, mounted in self._mounted.items()
                if mounted.prefix in current_prefixes
            }

            for state in states:
                instance_id = state.app.instance_id
                fingerprint = (
                    state.prefix,
                    state.app.url,
                    state.app.api_key,
                    state.enabled,
                    state.available,
                )
                current = self._mounted.get(instance_id)
                if current is not None:
                    if current.fingerprint != fingerprint:
                        current.proxy.invalidate()
                        current.fingerprint = fingerprint
                    continue

                async def loader(instance_id: int = instance_id):
                    async with async_session() as db:
                        try:
                            return await mcp_config_service.get_upstream(
                                db, instance_id, self._host()
                            )
                        except APIException:
                            return None

                proxy = build_upstream_proxy(loader)
                self.server.mount(proxy, prefix=state.prefix)
                self._mounted[instance_id] = MountedUpstream(
                    prefix=state.prefix,
                    proxy=proxy,
                    fingerprint=fingerprint,
                )

    async def allowed_tool_names(self) -> set[str]:
        async with async_session() as db:
            config = await mcp_config_service.get_config(db)
            if not config["enabled"]:
                return set()
            names = {
                name
                for name in LOCAL_TOOL_DEFINITIONS
                if name in set(config["local_tools"])
            }
            for state in await mcp_config_service.list_upstreams(db, self._host()):
                if not state.enabled or not state.available:
                    continue
                names.update(
                    public_tool_name(state.prefix, native_name)
                    for native_name in state.allowed_tools
                    if PUBLIC_TOOL_NAME.fullmatch(
                        public_tool_name(state.prefix, native_name)
                    )
                )
            return names

    @asynccontextmanager
    async def lifespan(self):
        async with self.http_app.lifespan(self.http_app):
            yield


mcp_aggregator = McpAggregator()
mcp_asgi_app = mcp_aggregator.asgi_app
mcp_lifespan = mcp_aggregator.lifespan
