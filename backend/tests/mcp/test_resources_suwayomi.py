"""Suwayomi 漫画适配器测试：用 httpx.MockTransport 模拟 GraphQL 服务，不联网。"""

import json

import httpx
import pytest

from app.core.exceptions import APIException
from app.services.integrations.discovery import DeployedApp
from app.services.resources import suwayomi as adapter


def _state(**overrides) -> DeployedApp:
    values = {
        "instance_id": 7,
        "app_name": "suwayomi",
        "display_name": "Suwayomi",
        "instance_name": "comic",
        "status": "running",
        "url": "http://suwayomi:4567",
    }
    values.update(overrides)
    return DeployedApp(**values)


@pytest.fixture(autouse=True)
def _patch_state(monkeypatch):
    async def fake_resolve_app(db, app_names, instance_id=None):
        return _state(instance_id=instance_id or 7)

    monkeypatch.setattr(adapter, "resolve_app", fake_resolve_app)


def _mock_httpx(monkeypatch, handler):
    """用 httpx.MockTransport 替换 AsyncClient，拦截客户端的 HTTP 请求。"""
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )


def _gql_handler(responses):
    """按 query 中的关键字返回预设响应，并记录每个请求体。"""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/graphql"
        body = json.loads(request.content)
        calls.append(body)
        for marker, response in responses:
            if marker in body["query"]:
                return httpx.Response(200, json=response)
        return httpx.Response(500, json={"errors": [{"message": "未匹配的请求"}]})

    return handler, calls


@pytest.mark.asyncio
async def test_search_without_online_source_reports_clear_error(monkeypatch):
    """只有本地图源时搜索给出明确中文提示，而不是返回假数据。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {
                    "sources": {
                        "nodes": [
                            {
                                "id": "0",
                                "name": "Local source",
                                "lang": "localsourcelang",
                            }
                        ]
                    }
                }
            },
        )

    _mock_httpx(monkeypatch, handler)

    with pytest.raises(APIException) as exc:
        await adapter.search(None, keyword="海贼王")

    assert exc.value.status_code == 400
    assert "在线图源" in exc.value.message


@pytest.mark.asyncio
async def test_search_passes_source_query_page_and_type(monkeypatch):
    """fetchSourceManga 的变量应包含 source/query/page/type，并归一化结果。"""
    responses = [
        (
            "sources(",
            {
                "data": {
                    "sources": {
                        "nodes": [
                            {
                                "id": "0",
                                "name": "Local source",
                                "lang": "localsourcelang",
                            },
                            {"id": "123", "name": "在线图源", "lang": "zh"},
                        ]
                    }
                }
            },
        ),
        (
            "fetchSourceManga",
            {
                "data": {
                    "fetchSourceManga": {
                        "hasNextPage": False,
                        "mangas": [
                            {
                                "id": 42,
                                "title": "海贼王",
                                "author": "尾田荣一郎",
                                "artist": "尾田荣一郎",
                                "genre": ["冒险", "热血"],
                                "status": "ONGOING",
                                "thumbnailUrl": "http://img/42.jpg",
                                "sourceId": "123",
                                "user": {"inLibrary": False},
                            }
                        ],
                    }
                }
            },
        ),
    ]
    handler, calls = _gql_handler(responses)
    _mock_httpx(monkeypatch, handler)

    result = await adapter.search(None, keyword="海贼王", page=2, count=1)

    assert calls[1]["variables"] == {
        "source": "123",
        "query": "海贼王",
        "page": 2,
        "type": "SEARCH",
    }
    assert result["instance_id"] == 7
    assert result["source_id"] == "123"
    assert result["items"] == [
        {
            "variant": "online",
            "item_id": "42",
            "title": "海贼王",
            "author": "尾田荣一郎",
            "artist": "尾田荣一郎",
            "genre": ["冒险", "热血"],
            "status": "ONGOING",
            "cover": "http://img/42.jpg",
            "in_library": False,
            "source_id": "123",
        }
    ]


@pytest.mark.asyncio
async def test_subscriptions_normalizes_library(monkeypatch):
    """书库连接归一化成 subscription_id/title/author/cover/initialized。"""
    handler, calls = _gql_handler(
        [
            (
                "mangas(",
                {
                    "data": {
                        "mangas": {
                            "totalCount": 2,
                            "nodes": [
                                {
                                    "id": 9,
                                    "title": "钢之炼金术师",
                                    "author": "荒川弘",
                                    "thumbnailUrl": "http://img/9.jpg",
                                    "initialized": True,
                                    "sourceId": "123",
                                },
                                {
                                    "id": 10,
                                    "title": "进击的巨人",
                                    "author": "谏山创",
                                    "thumbnailUrl": None,
                                    "initialized": False,
                                    "sourceId": "123",
                                },
                            ],
                        }
                    }
                },
            )
        ]
    )
    _mock_httpx(monkeypatch, handler)

    result = await adapter.subscriptions(None, page=2, count=10)

    assert calls[0]["variables"] == {"first": 10, "offset": 10}
    assert result["total"] == 2
    assert result["items"] == [
        {
            "subscription_id": "9",
            "title": "钢之炼金术师",
            "author": "荒川弘",
            "cover": "http://img/9.jpg",
            "initialized": True,
        },
        {
            "subscription_id": "10",
            "title": "进击的巨人",
            "author": "谏山创",
            "cover": None,
            "initialized": False,
        },
    ]


@pytest.mark.asyncio
async def test_subscribe_updates_library_then_fetches_chapters(monkeypatch):
    """订阅先 updateManga 置 inLibrary=true，再 fetchMangaAndChapters。"""
    handler, calls = _gql_handler(
        [
            (
                "updateManga",
                {
                    "data": {
                        "updateManga": {
                            "manga": {
                                "id": 42,
                                "title": "海贼王",
                                "user": {"inLibrary": True},
                            }
                        }
                    }
                },
            ),
            (
                "fetchMangaAndChapters",
                {"data": {"fetchMangaAndChapters": {"manga": {"id": 42}}}},
            ),
            (
                "chapters",
                {
                    "data": {
                        "manga": {
                            "author": "尾田荣一郎",
                            "genre": ["冒险", "热血"],
                            "status": "ONGOING",
                            "thumbnailUrl": "http://img/42.jpg",
                            "chapters": {"totalCount": 1100},
                        }
                    }
                },
            ),
        ]
    )
    _mock_httpx(monkeypatch, handler)

    result = await adapter.subscribe(None, item_id="42")

    assert calls[0]["variables"] == {"id": 42, "inLibrary": True}
    assert calls[1]["variables"] == {"id": 42}
    assert result["subscription_id"] == "42"
    assert result["title"] == "海贼王"
    assert result["in_library"] is True
    assert result["chapters_fetched"] is True
    assert result["warning"] is None
    # 详情字段用于通知卡片：作者/标签/状态/话数
    assert result["meta"] == {
        "author": "尾田荣一郎",
        "tags": ["冒险", "热血"],
        "comic_status": "ONGOING",
        "cover": "http://img/42.jpg",
        "total_chapters": 1100,
    }


@pytest.mark.asyncio
async def test_subscribe_keeps_subscription_when_chapter_fetch_fails(monkeypatch):
    """章节拉取失败不影响订阅结果，但返回 chapters_fetched=false 与 warning。"""
    handler, _calls = _gql_handler(
        [
            (
                "updateManga",
                {"data": {"updateManga": {"manga": {"id": 42, "title": "海贼王"}}}},
            ),
            (
                "fetchMangaAndChapters",
                {"errors": [{"message": "没有在线图源"}]},
            ),
        ]
    )
    _mock_httpx(monkeypatch, handler)

    result = await adapter.subscribe(None, item_id="42")

    assert result["chapters_fetched"] is False
    assert result["warning"] is not None
    assert "拉取章节失败" in result["warning"]


@pytest.mark.asyncio
async def test_downloads_reports_download_phase(monkeypatch):
    """按 downloadCount 与章节总数计算 done/partial/idle。"""
    handler, calls = _gql_handler(
        [
            (
                "downloadCount",
                {
                    "data": {
                        "mangas": {
                            "totalCount": 3,
                            "nodes": [
                                {
                                    "id": 1,
                                    "title": "已下完",
                                    "user": {"downloadCount": 5},
                                    "chapters": {"totalCount": 5},
                                },
                                {
                                    "id": 2,
                                    "title": "部分下载",
                                    "user": {"downloadCount": 2},
                                    "chapters": {"totalCount": 5},
                                },
                                {
                                    "id": 3,
                                    "title": "未下载",
                                    "user": {"downloadCount": 0},
                                    "chapters": {"totalCount": 3},
                                },
                            ],
                        }
                    }
                },
            )
        ]
    )
    _mock_httpx(monkeypatch, handler)

    result = await adapter.downloads(None, limit=20)

    assert calls[0]["variables"] == {"first": 20, "offset": 0}
    assert result["total"] == 3
    assert [(item["task_id"], item["status"]) for item in result["items"]] == [
        ("1", "done"),
        ("2", "partial"),
        ("3", "idle"),
    ]
    assert result["items"][0]["downloaded_chapters"] == 5
    assert result["items"][1]["total_chapters"] == 5


@pytest.mark.asyncio
async def test_download_start_filters_downloaded_and_enqueues(monkeypatch):
    """start 只入队未下载章节，然后启动下载器。"""
    handler, calls = _gql_handler(
        [
            (
                "fetchMangaAndChapters",
                {"data": {"fetchMangaAndChapters": {"manga": {"id": 42}}}},
            ),
            (
                "enqueueChapterDownloads",
                {
                    "data": {
                        "enqueueChapterDownloads": {
                            "downloadStatus": {"state": "STARTED", "queueSize": 2}
                        }
                    }
                },
            ),
            (
                "startDownloader",
                {
                    "data": {
                        "startDownloader": {
                            "downloadStatus": {"state": "STARTED", "queueSize": 2}
                        }
                    }
                },
            ),
            (
                "chapters(filter",
                {
                    "data": {
                        "chapters": {
                            "totalCount": 3,
                            "nodes": [
                                {
                                    "id": 1,
                                    "name": "第 1 话",
                                    "user": {"isDownloaded": True},
                                },
                                {
                                    "id": 2,
                                    "name": "第 2 话",
                                    "user": {"isDownloaded": False},
                                },
                                {
                                    "id": 3,
                                    "name": "第 3 话",
                                    "user": {"isDownloaded": False},
                                },
                            ],
                        }
                    }
                },
            ),
        ]
    )
    _mock_httpx(monkeypatch, handler)

    result = await adapter.download(None, task_id="42", action="start")

    enqueue = next(call for call in calls if "enqueueChapterDownloads" in call["query"])
    assert enqueue["variables"] == {"ids": [2, 3]}
    assert calls[0]["variables"] == {"id": 42}
    assert result["queued"] == 2
    assert result["total_chapters"] == 3
    assert result["download_status"] == {"state": "STARTED", "queue_size": 2}


@pytest.mark.asyncio
async def test_download_delete_requires_confirmation(monkeypatch):
    """delete 未确认时直接报错，且不发出任何 HTTP 请求。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("未确认时不应发出请求")

    _mock_httpx(monkeypatch, handler)

    with pytest.raises(APIException) as exc:
        await adapter.download(None, task_id="42", action="delete")

    assert exc.value.status_code == 400
    assert exc.value.message == "删除漫画下载缓存前需要用户确认，请传 confirm=true"


@pytest.mark.asyncio
async def test_replace_requires_confirmation(monkeypatch):
    """replace 未确认时直接报错，且不发出任何 HTTP 请求。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("未确认时不应发出请求")

    _mock_httpx(monkeypatch, handler)

    with pytest.raises(APIException) as exc:
        await adapter.replace(None, subscription_id="42")

    assert exc.value.status_code == 400
    assert "confirm=true" in exc.value.message


@pytest.mark.asyncio
async def test_graphql_errors_become_api_exception(monkeypatch):
    """GraphQL errors[].message 转成 502 APIException。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"errors": [{"message": "Manga not found"}]}
        )

    _mock_httpx(monkeypatch, handler)

    with pytest.raises(APIException) as exc:
        await adapter.detail(None, item_id="42")

    assert exc.value.status_code == 502
    assert "Suwayomi 调用失败" in exc.value.message
    assert "Manga not found" in exc.value.message


@pytest.mark.asyncio
async def test_pick_source_uses_configured_priority_then_chinese():
    state = _state(source_priority=["99", "88"])
    sources = [
        {"id": "0", "name": "Local", "lang": "localsourcelang"},
        {"id": "1", "name": "English", "lang": "en"},
        {"id": "2", "name": "中文", "lang": "zh"},
        {"id": "88", "name": "Selected", "lang": "en"},
    ]

    async def fake_gql(_state, _query, _variables):
        return {"sources": {"nodes": sources}}

    original_gql = adapter._gql
    adapter._gql = fake_gql
    try:
        assert await adapter._pick_source(state) == "88"
        state.source_priority = ["missing"]
        assert await adapter._pick_source(state) == "2"
    finally:
        adapter._gql = original_gql


@pytest.mark.asyncio
async def test_adapter_logs_in_with_configured_credentials(monkeypatch):
    """配置了账号密码时，先登录 simple_login 再执行 GraphQL。"""

    async def fake_resolve_app(db, app_names, instance_id=None):
        return _state(username="admin", password="secret")

    monkeypatch.setattr(adapter, "resolve_app", fake_resolve_app)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/login.html" and request.method == "GET":
            return httpx.Response(
                200,
                text=(
                    '<input type="hidden" name="csrf-token" value="csrf-1"/>'
                ),
                headers={"Set-Cookie": "JSESSIONID=page; Path=/"},
            )
        if request.url.path == "/login.html" and request.method == "POST":
            return httpx.Response(
                303,
                headers={
                    "Location": "/",
                    "Set-Cookie": "JSESSIONID=session; Path=/",
                },
            )
        if "JSESSIONID=session" not in request.headers.get("cookie", ""):
            return httpx.Response(
                200,
                json={
                    "errors": [
                        {
                            "message": (
                                "Unauthorized\\n\\n"
                                "suwayomi.tachidesk.server.user."
                                "UnauthorizedException"
                            )
                        }
                    ]
                },
            )
        return httpx.Response(
            200, json={"data": {"manga": {"id": 42, "title": "测试漫画"}}}
        )

    _mock_httpx(monkeypatch, handler)

    data = await adapter.detail(None, item_id="42")

    assert data["detail"]["item_id"] == "42"
    assert calls == [
        ("POST", "/api/graphql"),
        ("GET", "/login.html"),
        ("POST", "/login.html"),
        ("POST", "/api/graphql"),
    ]
