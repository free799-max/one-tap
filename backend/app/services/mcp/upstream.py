"""原生 MCP 上游客户端与远程代理构造。"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastmcp import Client, FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError
from fastmcp.server.proxy import FastMCPProxy, ProxyClient
from fastmcp.tools.tool import Tool, ToolResult

from app.core.exceptions import APIException
from app.services.mcp.config import UpstreamState


UPSTREAM_TIMEOUT_SECONDS = 30
UPSTREAM_DISCOVERY_TIMEOUT_SECONDS = 5
UPSTREAM_CACHE_SECONDS = 15
UPSTREAM_FAILURE_BACKOFF_SECONDS = 5


def upstream_url(state: UpstreamState) -> str:
    if not state.available:
        raise APIException(
            f"{state.app.display_name} 不可连接：{state.unavailable_reason}", 400
        )
    assert state.app.url is not None
    parsed = urlsplit(state.app.url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise APIException("实例访问地址格式不正确", 400)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise APIException("实例访问地址不能包含凭据、查询参数或片段", 400)
    return f"{state.app.url.rstrip('/')}/api/v1/mcp"


def _http_client_factory(api_key: str) -> Callable[..., httpx.AsyncClient]:
    def create(
        *,
        headers: dict[str, str] | None = None,
        auth: httpx.Auth | None = None,
        follow_redirects: bool = False,
        timeout: httpx.Timeout | None = None,
        **_: Any,
    ) -> httpx.AsyncClient:
        # FastMCP 2.x 会合并入站请求头；这里只保留服务端显式配置的上游凭据。
        return httpx.AsyncClient(
            headers={"X-API-KEY": api_key},
            follow_redirects=False,
            timeout=timeout or httpx.Timeout(10.0, read=UPSTREAM_TIMEOUT_SECONDS),
        )

    return create


def build_upstream_client(state: UpstreamState) -> ProxyClient:
    if not state.app.api_key:
        raise APIException("实例缺少 API Key", 400)
    transport = StreamableHttpTransport(
        upstream_url(state),
        headers={"X-API-KEY": state.app.api_key},
        httpx_client_factory=_http_client_factory(state.app.api_key),
    )
    return ProxyClient(transport)


class UpstreamFastMCPProxy(FastMCPProxy):
    """带发现超时、短缓存和失败退避的远程 MCP 代理。"""

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._cached_tools: dict[str, Tool] | None = None
        self._cached_at = 0.0
        self._failed_at = 0.0

    def invalidate(self) -> None:
        self._cached_tools = None
        self._cached_at = 0.0
        self._failed_at = 0.0

    async def get_tools(self) -> dict[str, Tool]:
        now = time.monotonic()
        if self._cached_tools is not None and now - self._cached_at < UPSTREAM_CACHE_SECONDS:
            return self._cached_tools
        if self._failed_at and now - self._failed_at < UPSTREAM_FAILURE_BACKOFF_SECONDS:
            raise ToolError("原生 MCP 上游暂时不可用，请稍后重试")
        try:
            async with asyncio.timeout(UPSTREAM_DISCOVERY_TIMEOUT_SECONDS):
                tools = await super().get_tools()
        except Exception as exc:
            self._failed_at = time.monotonic()
            self._cached_tools = None
            raise ToolError("原生 MCP 上游暂时不可用") from exc
        self._cached_tools = tools
        self._cached_at = time.monotonic()
        self._failed_at = 0.0
        return tools

    async def _call_tool_middleware(
        self, key: str, arguments: dict[str, Any]
    ) -> ToolResult:
        try:
            async with asyncio.timeout(UPSTREAM_TIMEOUT_SECONDS):
                return await super()._call_tool_middleware(key, arguments)
        except TimeoutError as exc:
            raise ToolError("原生 MCP 工具调用超时") from exc


def build_upstream_proxy(
    loader: Callable[[], Awaitable[UpstreamState | None]],
) -> UpstreamFastMCPProxy:
    async def client_factory() -> Client:
        state = await loader()
        if state is None or not state.enabled or not state.available:
            return ProxyClient(FastMCP("disabled-upstream"))
        return build_upstream_client(state)

    return UpstreamFastMCPProxy(client_factory=client_factory)


async def discover_upstream_tools(state: UpstreamState) -> list[dict[str, Any]]:
    try:
        async with asyncio.timeout(UPSTREAM_DISCOVERY_TIMEOUT_SECONDS):
            async with build_upstream_client(state) as client:
                tools = await client.list_tools()
    except APIException:
        raise
    except Exception as exc:
        raise APIException(
            f"连接原生 MCP 失败：{type(exc).__name__}，请检查实例地址和 API Key", 502
        ) from exc

    return [
        {
            "name": tool.name,
            "title": tool.title,
            "description": tool.description or "",
            "input_schema": tool.inputSchema,
            "output_schema": tool.outputSchema,
            "annotations": (
                tool.annotations.model_dump(exclude_none=True)
                if tool.annotations is not None
                else None
            ),
        }
        for tool in tools
    ]
