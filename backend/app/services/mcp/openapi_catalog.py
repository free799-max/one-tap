"""运行时 OpenAPI 目录、权限角色与传输策略。"""

from functools import lru_cache
from typing import Any

from fastapi.routing import APIRoute
from starlette.routing import WebSocketRoute

from app.core import security as security_module


_HTTP_METHODS = {"get", "post", "put", "delete", "patch", "options", "head", "trace"}
_SECRET_PATHS = {
    "/api/settings/secret",
    "/api/settings/hosts-optimizer/secret",
    "/api/integrations/vaultwarden/secret",
    "/api/integrations/network/secret",
    "/api/license/secret",
}
_SIDE_EFFECT_READS = {("GET", "/api/docker/compose"), ("GET", "/api/settings")}
_DESTRUCTIVE_PATH_PARTS = (
    "/restore",
    "/delete",
    "/prune",
    "/reset",
    "/remove",
)


def _app():
    from app.main import app

    return app


def _dependency_calls(dependant: Any):
    yield dependant.call
    for dependency in dependant.dependencies:
        yield from _dependency_calls(dependency)


def _required_role(dependant: Any) -> str:
    calls = set(_dependency_calls(dependant))
    if security_module.require_admin in calls:
        return "admin"
    if calls.intersection(
        {security_module.get_current_user, security_module.get_current_user_sse}
    ):
        return "user"
    return "public"


def _route_roles() -> dict[tuple[str, str], str]:
    roles: dict[tuple[str, str], str] = {}
    for route in _app().routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods:
            roles[(method.upper(), route.path)] = _required_role(route.dependant)
    return roles


def _domain(path: str) -> str:
    parts = path.strip("/").split("/")
    if len(parts) < 2 or parts[0] != "api":
        return "system"
    if parts[1] == "settings" and len(parts) > 2 and parts[2] == "mcp":
        return "mcp"
    return parts[1]


def _transport(path: str, operation: dict[str, Any]) -> str:
    if path.endswith("/logs/stream") or path.endswith("/events"):
        return "sse"
    media_types = {
        media_type
        for response in operation.get("responses", {}).values()
        for media_type in response.get("content", {})
    }
    if "text/event-stream" in media_types:
        return "sse"
    if path.endswith("/icon"):
        return "file"
    json_types = {
        media_type
        for media_type in media_types
        if media_type == "application/json" or media_type.endswith("+json")
    }
    if media_types and not json_types:
        return "file"
    return "http"


def _blocked_reason(path: str, transport: str) -> str:
    if path.startswith("/api/auth/"):
        return "认证与登录接口不通过 MCP 通用工具调用"
    if path in _SECRET_PATHS:
        return "秘密查看接口不通过 MCP 调用"
    if path.startswith("/api/settings/mcp"):
        return "MCP 控制面仅允许管理员在设置页操作"
    if path == "/api/docker/containers/{container_id}/exec":
        return "宿主机命令执行不通过 MCP 通用工具调用"
    if transport == "sse":
        return "流式接口请改用对应的任务状态轮询接口"
    if transport == "file":
        return "二进制文件接口不通过 JSON 通用工具调用"
    if transport == "websocket":
        return "交互式终端不通过 MCP 通用工具调用"
    return ""


def _risk(method: str, path: str, transport: str) -> str:
    if path in _SECRET_PATHS:
        return "secret"
    if path.startswith("/api/settings/mcp") or path.startswith("/api/auth/"):
        return "control_plane"
    if transport != "http":
        return "unsupported"
    if method == "DELETE" or any(part in path for part in _DESTRUCTIVE_PATH_PARTS):
        return "destructive"
    if (method, path) in _SIDE_EFFECT_READS:
        return "write"
    if method in {"GET", "HEAD", "OPTIONS"}:
        return "read"
    return "write"


def _summary(
    *,
    operation_id: str,
    method: str,
    path: str,
    operation: dict[str, Any],
    required_role: str,
) -> dict[str, Any]:
    transport = _transport(path, operation)
    blocked_reason = _blocked_reason(path, transport)
    if not blocked_reason:
        for parameter in operation.get("parameters", []):
            if parameter.get("in") in {"header", "cookie"} and parameter.get("required"):
                blocked_reason = "包含自定义请求头或 Cookie 的接口不通过通用工具调用"
                break
    if not blocked_reason:
        content = (operation.get("requestBody") or {}).get("content") or {}
        if content and not any(
            media_type == "application/json" or media_type.endswith("+json")
            for media_type in content
        ):
            blocked_reason = "非 JSON 请求体接口不通过通用工具调用"
    return {
        "kind": "onetap_api",
        "entry_id": f"api:{operation_id}",
        "source": "onetap",
        "source_name": "OneTap API",
        "domain": _domain(path),
        "name": operation_id,
        "title": operation.get("summary") or operation_id,
        "description": operation.get("description") or "",
        "method": method,
        "path": path,
        "public_name": operation_id,
        "tags": list(operation.get("tags") or []),
        "risk": _risk(method, path, transport),
        "transport": transport,
        "required_role": required_role,
        "invocable": not blocked_reason,
        "blocked_reason": blocked_reason,
    }


@lru_cache(maxsize=1)
def _operation_index() -> dict[str, dict[str, Any]]:
    document = _app().openapi()
    roles = _route_roles()
    result: dict[str, dict[str, Any]] = {}
    for path, path_item in document.get("paths", {}).items():
        for method, operation in path_item.items():
            if method not in _HTTP_METHODS:
                continue
            operation_id = operation.get("operationId")
            if not operation_id or operation_id in result:
                raise RuntimeError(f"OpenAPI operationId 缺失或重复：{operation_id}")
            upper_method = method.upper()
            summary = _summary(
                operation_id=operation_id,
                method=upper_method,
                path=path,
                operation=operation,
                required_role=roles.get((upper_method, path), "public"),
            )
            result[operation_id] = {
                "summary": summary,
                "operation": operation,
                "components": document.get("components", {}),
            }
    return result


def clear_openapi_catalog_cache() -> None:
    _operation_index.cache_clear()


def list_api_operations() -> list[dict[str, Any]]:
    operations = [
        dict(item["summary"])
        for item in sorted(
            _operation_index().values(),
            key=lambda item: (
                item["summary"]["domain"],
                item["summary"]["path"],
                item["summary"]["method"],
            ),
        )
    ]
    for route in _app().routes:
        if not isinstance(route, WebSocketRoute):
            continue
        operation_id = f"websocket:{route.path}"
        summary = _summary(
            operation_id=operation_id,
            method="WS",
            path=route.path,
            operation={
                "summary": "交互式 WebSocket 终端",
                "description": "该通道不属于 OpenAPI HTTP 操作，仅用于目录完整性展示。",
            },
            required_role="user",
        )
        blocked_reason = _blocked_reason(route.path, "websocket")
        summary.update(
            transport="websocket",
            risk="unsupported",
            invocable=not blocked_reason,
            blocked_reason=blocked_reason,
        )
        operations.append(summary)
    return operations


def get_api_operation(operation_id: str) -> dict[str, Any] | None:
    item = _operation_index().get(operation_id)
    if item is None:
        return None
    operation = item["operation"]
    return {
        **item["summary"],
        "parameters": operation.get("parameters") or [],
        "request_body": operation.get("requestBody"),
        "responses": operation.get("responses") or {},
        "components": item["components"],
        "security": operation.get("security"),
    }


def api_operation_ids() -> set[str]:
    return set(_operation_index())
