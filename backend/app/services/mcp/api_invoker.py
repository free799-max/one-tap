"""OneTap OpenAPI 浏览与受限进程内调用。"""

import asyncio
import json
import re
from datetime import timedelta
from typing import Any
from urllib.parse import quote

import httpx
from fastmcp.exceptions import ToolError
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from app.core.security import create_access_token
from app.database import async_session
from app.services.mcp.audit import sanitize_error
from app.services.mcp.auth import McpActor, current_mcp_actor
from app.services.mcp.config import mcp_config_service
from app.services.mcp.openapi_catalog import get_api_operation, list_api_operations


API_TIMEOUT_SECONDS = 30
MAX_BODY_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 64 * 1024
MAX_RESPONSE_READ_BYTES = 4 * MAX_RESPONSE_BYTES
_SECRET_KEYS = {
    "password",
    "hashedpassword",
    "token",
    "accesstoken",
    "refreshtoken",
    "secret",
    "secretkey",
    "apikey",
    "authorization",
    "cookie",
    "setcookie",
    "subscriptionurl",
    "licensekey",
    "privatekey",
    "passkey",
}


def _normalized_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


def _is_secret_key(key: str) -> bool:
    normalized = _normalized_key(key)
    return normalized in _SECRET_KEYS or normalized.endswith(
        ("password", "token", "secret", "apikey", "subscriptionurl", "privatekey")
    )


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if _is_secret_key(str(key)) else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return sanitize_error(value, limit=len(value))
    return value


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _validator(schema: dict[str, Any], components: dict[str, Any]):
    document = {
        "$schema": Draft202012Validator.META_SCHEMA["$id"],
        "$defs": {"operation": schema},
        "$ref": "#/$defs/operation",
        "components": components,
    }
    registry = Registry().with_resource("", Resource.from_contents(document))
    return Draft202012Validator(document, registry=registry)


def _validate_schema(
    value: Any, schema: dict[str, Any], components: dict[str, Any], label: str
) -> None:
    try:
        errors = list(_validator(schema, components).iter_errors(value))
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(f"{label}的接口 Schema 不受支持") from exc
    if errors:
        location = ".".join(str(part) for part in errors[0].absolute_path)
        suffix = f"（字段 {location}）" if location else ""
        raise ToolError(f"{label}不符合接口参数约束{suffix}")


def _allows_none(schema: dict[str, Any]) -> bool:
    if schema.get("nullable") is True or schema.get("type") == "null":
        return True
    types = schema.get("type")
    if isinstance(types, list) and "null" in types:
        return True
    return any(
        isinstance(option, dict) and _allows_none(option)
        for key in ("anyOf", "oneOf")
        for option in schema.get(key, [])
    )


def _prepare_request(
    operation: dict[str, Any],
    path_params: dict[str, Any],
    query_params: dict[str, Any],
    body: Any,
) -> tuple[str, str, list[tuple[str, Any]], bytes | None]:
    components = operation["components"]
    declared_path: set[str] = set()
    declared_query: set[str] = set()
    path_values: dict[str, Any] = {}
    query_values: list[tuple[str, Any]] = []

    for parameter in operation["parameters"]:
        location = parameter.get("in")
        name = parameter.get("name", "")
        if location not in {"path", "query"}:
            if parameter.get("required"):
                raise ToolError("该接口包含不支持的请求头或 Cookie 参数")
            continue
        schema = parameter.get("schema") or {}
        target = path_params if location == "path" else query_params
        if location == "path":
            declared_path.add(name)
        else:
            declared_query.add(name)
        if name not in target:
            if parameter.get("required"):
                raise ToolError(f"缺少必填参数：{name}")
            continue
        value = target[name]
        if value is None:
            raise ToolError(f"参数 {name} 不能使用 null")
        _validate_schema(value, schema, components, f"参数 {name}")
        if location == "path":
            if isinstance(value, (dict, list)):
                raise ToolError(f"路径参数 {name} 必须是标量")
            path_values[name] = value
        elif isinstance(value, list):
            query_values.extend((name, item) for item in value)
        elif isinstance(value, dict):
            raise ToolError(f"查询参数 {name} 不支持对象值")
        else:
            query_values.append((name, value))

    if set(path_params) != declared_path:
        raise ToolError("包含未声明的路径参数")
    if set(query_params) - declared_query:
        raise ToolError("包含未声明的查询参数")

    path = operation["path"]
    for name, value in path_values.items():
        scalar = str(value).lower() if isinstance(value, bool) else str(value)
        path = path.replace("{" + name + "}", quote(scalar, safe=""))
    if "{" in path or "}" in path:
        raise ToolError("路径参数不完整")

    request_body = operation.get("request_body") or {}
    if not request_body and body is not None:
        raise ToolError("该接口不接收 JSON 请求体")
    content = request_body.get("content") or {}
    media = content.get("application/json")
    if media is None:
        media = next(
            (value for key, value in content.items() if key.endswith("+json")), None
        )
    if content and media is None:
        raise ToolError("该接口请求体不是 JSON，无法通过通用工具调用")
    has_body = body is not None or (
        request_body.get("required") and _allows_none((media or {}).get("schema") or {})
    )
    if request_body.get("required") and not has_body:
        raise ToolError("缺少必填 JSON 请求体")
    if not has_body:
        return operation["method"], path, query_values, None

    schema = (media or {}).get("schema") or {}
    if schema:
        _validate_schema(body, schema, components, "JSON 请求体")
    try:
        encoded = _json_bytes(body)
    except (TypeError, ValueError) as exc:
        raise ToolError("请求体必须是可序列化的 JSON") from exc
    if len(encoded) > MAX_BODY_BYTES:
        raise ToolError("JSON 请求体超过 64 KiB 限制")
    return operation["method"], path, query_values, encoded


def _shrunk(value: Any, depth: int = 0) -> Any:
    if depth > 8:
        return "[内容层级过深]"
    if isinstance(value, dict):
        return {
            key: _shrunk(item, depth + 1)
            for key, item in list(value.items())[:50]
        }
    if isinstance(value, list):
        return [_shrunk(item, depth + 1) for item in value[:20]]
    if isinstance(value, str) and len(value) > 2000:
        return value[:2000] + "...[截断]"
    return value


def _limited_response(status_code: int, data: Any, message: str) -> dict[str, Any]:
    result = {
        "status_code": status_code,
        "data": data,
        "message": message,
        "truncated": False,
    }
    if len(_json_bytes(result)) <= MAX_RESPONSE_BYTES:
        return result
    result["data"] = _shrunk(data)
    result["truncated"] = True
    if len(_json_bytes(result)) <= MAX_RESPONSE_BYTES:
        return result
    result["data"] = None
    result["message"] = "响应过大，已停止返回正文；请缩小查询范围或增加筛选条件"
    return result


def _response_message(payload: Any, fallback: str) -> str:
    if isinstance(payload, dict) and isinstance(payload.get("message"), str):
        return sanitize_error(payload["message"], limit=300)
    return fallback


async def invoke_api(
    *,
    operation_id: str,
    path_params: dict[str, Any],
    query_params: dict[str, Any],
    body: Any,
    actor: McpActor | None = None,
) -> dict[str, Any]:
    operation = get_api_operation(operation_id)
    if operation is None:
        raise ToolError("未知的 OneTap API operationId")
    if not operation["invocable"]:
        raise ToolError(operation["blocked_reason"] or "该接口禁止通过 MCP 调用")

    current = actor or current_mcp_actor()
    if current is None:
        raise ToolError("缺少有效的 MCP 调用身份")
    async with async_session() as db:
        config = await mcp_config_service.get_config(db)
    if not config["enabled"] or "onetap_api_request" not in set(config["local_tools"]):
        raise ToolError("统一 API 调用工具未启用")
    if operation_id not in set(config["api_operation_ids"]):
        raise ToolError("该 API operationId 未授权")

    method, path, query, encoded_body = _prepare_request(
        operation, path_params, query_params, body
    )
    internal_token = create_access_token(
        {"sub": str(current.user_id)}, expires_delta=timedelta(seconds=60)
    )
    headers = {
        "Authorization": f"Bearer {internal_token}",
        "Accept": "application/json",
    }
    if encoded_body is not None:
        headers["Content-Type"] = "application/json"

    from app.main import app

    transport = httpx.ASGITransport(app=app)
    try:
        async with asyncio.timeout(API_TIMEOUT_SECONDS):
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://onetap.internal",
                follow_redirects=False,
            ) as client:
                async with client.stream(
                    method,
                    path,
                    params=query,
                    content=encoded_body,
                    headers=headers,
                ) as response:
                    chunks: list[bytes] = []
                    size = 0
                    oversized = False
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > MAX_RESPONSE_READ_BYTES:
                            oversized = True
                            break
                        chunks.append(chunk)
                    status_code = response.status_code
    except TimeoutError as exc:
        raise ToolError("OneTap API 调用超时") from exc

    if oversized:
        if not 200 <= status_code < 300:
            raise ToolError(f"OneTap API 请求失败（HTTP {status_code}）")
        return _limited_response(status_code, None, "响应过大，已截断") | {
            "truncated": True
        }

    raw = b"".join(chunks)
    if status_code == 204 or not raw:
        payload: Any = None
    else:
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = raw.decode("utf-8", errors="replace")

    message = _response_message(payload, "ok" if 200 <= status_code < 300 else "请求失败")
    if not 200 <= status_code < 300:
        raise ToolError(f"OneTap API 请求失败（HTTP {status_code}）：{message}")
    if isinstance(payload, dict) and payload.get("success") is False:
        raise ToolError(f"OneTap API 请求失败：{message}")
    if isinstance(payload, dict) and {
        "success",
        "data",
        "message",
    }.issubset(payload):
        payload = payload["data"]
    return _limited_response(status_code, _redact(payload), message)


def _referenced_components(operation: dict[str, Any]) -> dict[str, Any]:
    source = {
        "parameters": operation["parameters"],
        "request_body": operation["request_body"],
        "responses": operation["responses"],
    }
    schemas = operation["components"].get("schemas", {})
    retained: dict[str, Any] = {}

    def refs(value: Any):
        if isinstance(value, dict):
            reference = value.get("$ref")
            if isinstance(reference, str):
                yield reference
            for item in value.values():
                yield from refs(item)
        elif isinstance(value, list):
            for item in value:
                yield from refs(item)

    queue = list(refs(source))
    while queue:
        reference = queue.pop()
        prefix = "#/components/schemas/"
        if not reference.startswith(prefix):
            continue
        name = reference[len(prefix) :]
        if name in retained or name not in schemas:
            continue
        retained[name] = schemas[name]
        queue.extend(refs(retained[name]))
    return {"schemas": retained}


def browse_api_catalog(
    *,
    query: str = "",
    domain: str = "",
    method: str = "",
    risk: str = "",
    operation_id: str = "",
    page: int = 1,
    page_size: int = 50,
    authorized_ids: set[str] | None = None,
) -> dict[str, Any]:
    authorized = authorized_ids or set()
    if operation_id:
        operation = get_api_operation(operation_id)
        if operation is None:
            operation = next(
                (
                    item
                    for item in list_api_operations()
                    if item["name"] == operation_id
                ),
                None,
            )
            if operation is None:
                raise ToolError("未知的 OneTap API operationId")
            operation |= {
                "parameters": [],
                "request_body": None,
                "responses": {},
                "components": {},
            }
        operation["components"] = _referenced_components(operation)
        operation["authorized"] = operation_id in authorized
        return {"total": 1, "items": [operation]}

    normalized = query.strip().lower()
    methods = method.strip().upper()
    items = []
    for item in list_api_operations():
        if domain and item["domain"] != domain:
            continue
        if methods and item["method"] != methods:
            continue
        if risk and item["risk"] != risk:
            continue
        haystack = " ".join(
            str(item.get(key) or "")
            for key in ("name", "title", "description", "method", "path", "public_name")
        ).lower()
        if normalized and normalized not in haystack:
            continue
        items.append(item | {"authorized": item["name"] in authorized})

    page = max(1, page)
    page_size = min(100, max(1, page_size))
    start = (page - 1) * page_size
    return {
        "total": len(items),
        "page": page,
        "page_size": page_size,
        "items": items[start : start + page_size],
    }
