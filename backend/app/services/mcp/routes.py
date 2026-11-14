"""无重定向的 MCP 精确 ASGI 路由。"""

from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from app.services.mcp.server import mcp_asgi_app


class McpEndpoint:
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        child_scope = dict(scope)
        child_scope["root_path"] = f"{scope.get('root_path', '')}/mcp"
        child_scope["path"] = "/"
        await mcp_asgi_app(child_scope, receive, send)


mcp_routes = [
    Route(
        "/mcp",
        endpoint=McpEndpoint(),
        methods=["GET", "POST", "DELETE"],
    )
]
