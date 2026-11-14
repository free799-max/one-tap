import json

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from fastmcp.exceptions import ToolError
from starlette.routing import WebSocketRoute

from app.main import app
from app.services.mcp.api_invoker import (
    MAX_BODY_BYTES,
    MAX_RESPONSE_BYTES,
    _limited_response,
    _prepare_request,
    _redact,
    browse_api_catalog,
    invoke_api,
)
from app.services.mcp.auth import McpActor
from app.services.mcp.config import (
    DEFAULT_LOCAL_TOOLS,
    LOCAL_TOOL_DEFINITIONS,
    mcp_config_service,
)
from app.services.mcp.openapi_catalog import get_api_operation, list_api_operations


def _operation_id(path: str, method: str = "GET") -> str:
    return next(
        item["name"]
        for item in list_api_operations()
        if item["path"] == path and item["method"] == method
    )


def _actor(mcp_env) -> McpActor:
    return McpActor(
        user_id=mcp_env["admin"].id,
        token_id=1,
        username=mcp_env["admin"].username,
        request_host="test",
    )


async def _enable_invocation(mcp_env, operation_ids: list[str]) -> None:
    async with mcp_env["session"]() as db:
        await mcp_config_service.set_enabled(db, True)
        await mcp_config_service.update_local_tools(
            db,
            list(dict.fromkeys([*DEFAULT_LOCAL_TOOLS, "onetap_api_request"])),
        )
        await mcp_config_service.update_api_operations(db, operation_ids)


def _http_operations() -> list[tuple[str, str, str]]:
    return [
        (method.upper(), path, operation["operationId"])
        for path, path_item in app.openapi()["paths"].items()
        for method, operation in path_item.items()
        if method in {"get", "post", "put", "delete", "patch", "options", "head"}
    ]


def test_catalog_covers_every_openapi_operation_and_websocket():
    expected = _http_operations()
    entries = list_api_operations()
    actual = [
        (item["method"], item["path"], item["name"])
        for item in entries
        if item["transport"] != "websocket"
    ]
    websocket_count = sum(isinstance(route, WebSocketRoute) for route in app.routes)

    assert sorted(actual) == sorted(expected)
    assert len(entries) == len(expected) + websocket_count
    assert len({item["entry_id"] for item in entries}) == len(entries)
    assert all(item["required_role"] in {"public", "user", "admin"} for item in entries)

    by_path = {(item["method"], item["path"]): item for item in entries}
    assert by_path[("GET", "/api/health")]["required_role"] == "public"
    assert by_path[("GET", "/api/apps")]["required_role"] == "user"
    assert by_path[("POST", "/api/apps/{name}/deploy")]["required_role"] == "admin"
    assert by_path[("POST", "/api/auth/login")]["invocable"] is False
    assert by_path[("POST", "/api/settings/secret")]["risk"] == "secret"
    assert by_path[("POST", "/api/settings/secret")]["invocable"] is False
    assert by_path[("GET", "/api/apps/{name}/icon")]["transport"] == "file"
    assert by_path[("POST", "/api/docker/containers/{container_id}/exec")][
        "invocable"
    ] is False
    assert all(
        not item["invocable"]
        for item in entries
        if item["path"].startswith("/api/settings/mcp")
        or item["transport"] in {"sse", "file", "websocket"}
    )


@pytest.mark.asyncio
async def test_management_catalog_is_complete_and_tracks_effective_state(mcp_client):
    response = await mcp_client.get("/api/settings/mcp/catalog")
    assert response.status_code == 200
    catalog = response.json()["data"]
    expected_count = (
        len(_http_operations())
        + sum(isinstance(route, WebSocketRoute) for route in app.routes)
        + len(LOCAL_TOOL_DEFINITIONS)
    )
    assert len(catalog["entries"]) == expected_count
    assert {item["name"] for item in catalog["entries"]} >= {
        "onetap_api_catalog",
        "onetap_api_request",
        "media_targets_list",
    }

    health_id = _operation_id("/api/health")
    response = await mcp_client.get(
        "/api/settings/mcp/catalog/detail", params={"entry_id": f"api:{health_id}"}
    )
    assert response.status_code == 200
    detail = response.json()["data"]
    assert detail["path"] == "/api/health"
    assert detail["components"]["schemas"]

    response = await mcp_client.put(
        "/api/settings/mcp/api-operations", json={"operation_ids": [health_id]}
    )
    assert response.json()["data"] == [health_id]
    await mcp_client.put(
        "/api/settings/mcp/local-tools",
        json={"enabled_tools": [*DEFAULT_LOCAL_TOOLS, "onetap_api_request"]},
    )
    await mcp_client.put("/api/settings/mcp", json={"enabled": True})

    response = await mcp_client.get("/api/settings/mcp/catalog")
    health = next(
        item
        for item in response.json()["data"]["entries"]
        if item["entry_id"] == f"api:{health_id}"
    )
    assert health["enabled"] is True
    assert health["effective"] is True

    login_id = _operation_id("/api/auth/login", "POST")
    response = await mcp_client.put(
        "/api/settings/mcp/api-operations", json={"operation_ids": [login_id]}
    )
    assert response.status_code == 400


def test_browse_catalog_filters_and_returns_referenced_schemas_only():
    health_id = _operation_id("/api/health")
    result = browse_api_catalog(domain="health", method="get", query="健康")
    assert result["total"] == 1
    assert result["items"][0]["name"] == health_id

    detail = browse_api_catalog(operation_id=health_id, authorized_ids={health_id})
    assert detail["items"][0]["authorized"] is True
    assert detail["items"][0]["components"] == {"schemas": {}}

    body_operation = next(
        item
        for item in list_api_operations()
        if (get_api_operation(item["name"]) or {}).get("request_body")
    )
    full = get_api_operation(body_operation["name"])
    compact = browse_api_catalog(operation_id=body_operation["name"])["items"][0]
    assert compact["components"]["schemas"]
    assert len(compact["components"]["schemas"]) < len(full["components"]["schemas"])


@pytest.mark.asyncio
async def test_invoke_api_enforces_allowlist_and_real_route_auth(mcp_client, mcp_env):
    health_id = _operation_id("/api/health")
    apps_id = _operation_id("/api/apps")
    await _enable_invocation(mcp_env, [health_id])
    actor = _actor(mcp_env)

    with pytest.raises(ToolError, match="未授权"):
        await invoke_api(
            operation_id=apps_id, path_params={}, query_params={}, body=None, actor=actor
        )

    health = await invoke_api(
        operation_id=health_id, path_params={}, query_params={}, body=None, actor=actor
    )
    assert health["status_code"] == 200
    assert health["data"]["status"] == "ok"

    async with mcp_env["session"]() as db:
        await mcp_config_service.update_api_operations(db, [health_id, apps_id])
    apps = await invoke_api(
        operation_id=apps_id,
        path_params={},
        query_params={"category": "movie"},
        body=None,
        actor=actor,
    )
    assert apps["status_code"] == 200
    assert apps["data"] == []


@pytest.mark.asyncio
async def test_permanently_blocked_operations_cannot_be_called(mcp_env):
    login_id = _operation_id("/api/auth/login", "POST")
    await _enable_invocation(mcp_env, [])
    with pytest.raises(ToolError, match="不通过 MCP"):
        await invoke_api(
            operation_id=login_id,
            path_params={},
            query_params={},
            body={"username": "admin", "password": "must-not-leak"},
            actor=_actor(mcp_env),
        )


def _schema_operation() -> dict:
    return {
        "method": "POST",
        "path": "/api/items/{item_id}",
        "parameters": [
            {
                "in": "path",
                "name": "item_id",
                "required": True,
                "schema": {"type": "string"},
            },
            {
                "in": "query",
                "name": "limit",
                "required": True,
                "schema": {"type": "integer", "minimum": 1},
            },
        ],
        "request_body": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/ItemBody"}
                }
            },
        },
        "components": {
            "schemas": {
                "ItemBody": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name"],
                    "properties": {
                        "name": {"type": "string"},
                        "mode": {
                            "anyOf": [{"type": "string"}, {"type": "null"}]
                        },
                    },
                }
            }
        },
    }


def test_request_schema_refs_anyof_path_encoding_and_size_limit():
    operation = _schema_operation()
    method, path, query, body = _prepare_request(
        operation,
        {"item_id": "a/b ?"},
        {"limit": 2},
        {"name": "demo", "mode": None},
    )
    assert method == "POST"
    assert path == "/api/items/a%2Fb%20%3F"
    assert query == [("limit", 2)]
    assert json.loads(body) == {"name": "demo", "mode": None}

    with pytest.raises(ToolError, match="未声明"):
        _prepare_request(operation, {"item_id": "x"}, {"limit": 1, "extra": 1}, {"name": "x"})
    with pytest.raises(ToolError, match="约束"):
        _prepare_request(operation, {"item_id": "x"}, {"limit": 1}, {"name": 3})
    with pytest.raises(ToolError, match="64 KiB"):
        _prepare_request(
            operation,
            {"item_id": "x"},
            {"limit": 1},
            {"name": "x" * MAX_BODY_BYTES},
        )


def test_response_redaction_and_structured_truncation():
    redacted = _redact(
        {
            "reader_password": "secret-value",
            "nested": {"apiKey": "key-value", "token_id": 7},
            "subscription_url": "https://example.test/private",
        }
    )
    assert redacted["reader_password"] == "[REDACTED]"
    assert redacted["nested"] == {"apiKey": "[REDACTED]", "token_id": 7}
    assert redacted["subscription_url"] == "[REDACTED]"

    result = _limited_response(
        200,
        {"items": ["x" * 1000 for _ in range(200)], "password": "removed"},
        "ok",
    )
    encoded = json.dumps(result, ensure_ascii=False).encode()
    assert result["truncated"] is True
    assert len(encoded) <= MAX_RESPONSE_BYTES
    assert json.loads(encoded)["truncated"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [201, 202, 204])
async def test_invoke_api_supports_success_statuses(
    mcp_env, monkeypatch, status_code
):
    health_id = _operation_id("/api/health")
    await _enable_invocation(mcp_env, [health_id])
    operation = get_api_operation(health_id)
    monkeypatch.setattr(
        "app.services.mcp.api_invoker.get_api_operation", lambda _: operation
    )

    fake_app = FastAPI()

    @fake_app.get("/api/health")
    async def fake_health():
        if status_code == 204:
            return Response(status_code=204)
        return JSONResponse(
            {"success": True, "data": {"status": "ok"}, "message": "ok"},
            status_code=status_code,
        )

    import sys

    monkeypatch.setattr(sys.modules["app.main"], "app", fake_app)
    result = await invoke_api(
        operation_id=health_id,
        path_params={},
        query_params={},
        body=None,
        actor=_actor(mcp_env),
    )
    assert result["status_code"] == status_code
    assert result["data"] == (None if status_code == 204 else {"status": "ok"})


@pytest.mark.asyncio
async def test_unified_catalog_preserves_remote_tools_and_failures(
    mcp_client, moviepilot, monkeypatch
):
    async def fake_tools(state):
        return [
            {
                "name": "media.search",
                "title": "搜索媒体",
                "description": "按关键词搜索",
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
                "output_schema": None,
                "annotations": {"readOnlyHint": True},
            },
            {
                "name": "subscription.add",
                "title": "添加订阅",
                "description": "创建订阅",
                "input_schema": {"type": "object", "properties": {}},
                "output_schema": None,
                "annotations": {"readOnlyHint": False, "destructiveHint": False},
            },
        ]

    monkeypatch.setattr("app.services.mcp.catalog.discover_upstream_tools", fake_tools)
    response = await mcp_client.put(
        f"/api/settings/mcp/upstreams/{moviepilot.id}",
        json={
            "enabled": True,
            "allowed_tools": ["media.search", "subscription.add", "stale.tool"],
        },
    )
    assert response.status_code == 200
    await mcp_client.put("/api/settings/mcp", json={"enabled": True})

    response = await mcp_client.get("/api/settings/mcp/catalog")
    entries = response.json()["data"]["entries"]
    remote = [item for item in entries if item["kind"] == "remote_tool"]
    assert {item["name"] for item in remote} == {
        "media.search",
        "subscription.add",
        "stale.tool",
    }
    assert all(item["public_name"].startswith(f"moviepilot_{moviepilot.id}_") for item in remote)
    assert next(item for item in remote if item["name"] == "media.search")["effective"] is True
    assert next(item for item in remote if item["name"] == "subscription.add")["risk"] == "write"
    assert next(item for item in remote if item["name"] == "stale.tool")["invocable"] is False

    response = await mcp_client.get(
        "/api/settings/mcp/catalog/detail",
        params={"entry_id": f"remote:{moviepilot.id}:media.search"},
    )
    assert response.status_code == 200
    assert response.json()["data"]["input_schema"]["required"] == ["query"]

    response = await mcp_client.get(
        "/api/settings/mcp/catalog/detail",
        params={"entry_id": "local:music_organize_status"},
    )
    assert response.json()["data"]["input_schema"]["required"] == ["group_id"]

    async def failed_discovery(state):
        raise RuntimeError("上游离线")

    monkeypatch.setattr(
        "app.services.mcp.catalog.discover_upstream_tools", failed_discovery
    )
    response = await mcp_client.get("/api/settings/mcp/catalog")
    catalog = response.json()["data"]
    source = next(item for item in catalog["sources"] if item["id"].startswith("upstream:"))
    assert source["available"] is False
    assert source["error"] == "上游离线"
    retained = [item for item in catalog["entries"] if item["kind"] == "remote_tool"]
    assert len(retained) == 3
    assert all(item["enabled"] for item in retained)
    assert all(not item["invocable"] for item in retained)
