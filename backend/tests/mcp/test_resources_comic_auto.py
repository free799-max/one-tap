"""Comic Auto（本地漫画）适配器测试：用 httpx.MockTransport 模拟 REST API。"""

import json

import httpx
import pytest

from app.core.exceptions import APIException
from app.services.integrations.discovery import DeployedApp
from app.services.resources import comic_auto as adapter


def _state(**overrides) -> DeployedApp:
    values = {
        "instance_id": 9,
        "app_name": "comic-auto",
        "display_name": "Comic Auto",
        "instance_name": "comic-local",
        "status": "running",
        "url": "http://comic-auto:8055",
        "api_key": "key-123",
    }
    values.update(overrides)
    return DeployedApp(**values)


@pytest.fixture(autouse=True)
def _patch_state(monkeypatch):
    async def fake_resolve_app(db, app_names, instance_id=None):
        return _state(instance_id=instance_id or 9)

    monkeypatch.setattr(adapter, "resolve_app", fake_resolve_app)


def _mock_httpx(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )


def _ok(data):
    return httpx.Response(200, json={"code": 0, "msg": "success", "data": data})


SEARCH_ITEM = {
    "name": "海贼王",
    "category": "chinese",
    "cover_url": "http://img/1.jpg",
    "crawling_type": "copymanga",
    "station_url": "https://api.example.com",
    "comic_href": "/comic/onepiece",
}


@pytest.mark.asyncio
async def test_search_maps_items_and_uses_api_key(monkeypatch):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/v1/app/explore/search/":
            return _ok(
                {
                    "items": [
                        {**SEARCH_ITEM, "local_subscription_id": None},
                        {
                            "name": "授权漫画",
                            "category": "chinese",
                            "cover_url": "http://img/2.jpg",
                            "author": "作者",
                            "chapter_count": 120,
                            "is_license": True,
                            "subscription_id": 88,
                            "comic_id": 66,
                            "local_subscription_id": 12,
                            "local_sub_status": "subscribing",
                        },
                    ],
                    "total": 2,
                    "page": 1,
                    "has_more": False,
                    "source": "live",
                }
            )
        raise AssertionError(f"未匹配的请求 {request.method} {request.url}")

    _mock_httpx(monkeypatch, handler)

    result = await adapter.search(None, keyword="海贼王")

    assert seen[0].headers["x-api-key"] == "key-123"
    assert json.loads(seen[0].content) == {"keyword": "海贼王", "page": 1}
    assert result["variant"] == "local"
    assert result["instance_id"] == 9
    first = result["items"][0]
    assert first["item_id"] == "/comic/onepiece"
    assert first["crawling_type"] == "copymanga"
    assert first["station_url"] == "https://api.example.com"
    assert first["in_library"] is False
    licensed = result["items"][1]
    assert licensed["is_license"] is True
    assert licensed["license_subscription_id"] == 88
    assert licensed["license_comic_id"] == 66
    assert licensed["local_subscription_id"] == 12
    assert licensed["in_library"] is True


def _license_item(name: str) -> dict:
    return {
        "name": name,
        "category": "comic",
        "cover_url": "http://img/x.jpg",
        "is_license": True,
        "subscription_id": 1,
        "comic_id": 2,
        "local_subscription_id": None,
    }


@pytest.mark.asyncio
async def test_search_aggregates_upstream_pages_when_count_exceeds_page_size(monkeypatch):
    pages: list[int] = []
    chunks = {
        1: [_license_item(f"p1-{i}") for i in range(1, 11)],
        2: [_license_item(f"p2-{i}") for i in range(1, 11)],
        3: [_license_item(f"p3-{i}") for i in range(1, 6)],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/app/explore/search/":
            page = json.loads(request.content)["page"]
            pages.append(page)
            return _ok(
                {
                    "items": chunks[page],
                    "total": 25,
                    "page": page,
                    "has_more": page < 3,
                    "source": "license",
                }
            )
        raise AssertionError(f"未匹配的请求 {request.method} {request.url}")

    _mock_httpx(monkeypatch, handler)

    result = await adapter.search(None, keyword="开局", count=20)

    # 上游每页固定 10 条，count=20 时需要翻两页
    assert pages == [1, 2]
    assert len(result["items"]) == 20
    assert result["items"][0]["name"] == "p1-1"
    assert result["items"][-1]["name"] == "p2-10"
    assert result["total"] == 25
    assert result["has_more"] is True


@pytest.mark.asyncio
async def test_search_maps_page_and_count_to_upstream_pages(monkeypatch):
    pages: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/app/explore/search/":
            page = json.loads(request.content)["page"]
            pages.append(page)
            return _ok(
                {
                    "items": [_license_item(f"p{page}-{i}") for i in range(1, 11)],
                    "total": 100,
                    "page": page,
                    "has_more": page < 10,
                    "source": "license",
                }
            )
        raise AssertionError(f"未匹配的请求 {request.method} {request.url}")

    _mock_httpx(monkeypatch, handler)

    result = await adapter.search(None, keyword="开局", page=2, count=15)

    # page=2/count=15 → 从第 16 条开始：上游第 2 页第 6 条到第 3 页
    assert pages == [2, 3]
    assert len(result["items"]) == 15
    assert result["items"][0]["name"] == "p2-6"
    assert result["items"][-1]["name"] == "p3-10"


@pytest.mark.asyncio
async def test_search_does_not_claim_more_pages_for_unpaginated_sources(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/app/explore/search/":
            return _ok(
                {
                    "items": [_license_item(f"r-{i}") for i in range(1, 9)],
                    "total": 8,
                    "page": 1,
                    "has_more": False,
                    "source": "record",
                }
            )
        raise AssertionError(f"未匹配的请求 {request.method} {request.url}")

    _mock_httpx(monkeypatch, handler)

    result = await adapter.search(None, keyword="开局", count=5)

    assert len(result["items"]) == 5
    # 记录表结果不分页，截断后剩下的条目不能当成「还有下一页」
    assert result["has_more"] is False


@pytest.mark.asyncio
async def test_subscribe_passes_search_fields(monkeypatch):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/app/subscriptions/":
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                202,
                json={
                    "code": 0,
                    "msg": "success",
                    "data": {
                        "subscription_id": 12,
                        "task_id": "task-1",
                        "status": "PENDING",
                        "websocket_url": "/ws/tasks/task-1/",
                        "message": "订阅创建成功",
                    },
                },
            )
        if request.url.path == "/api/v1/app/subscriptions/12/":
            return _ok(
                {
                    "name": "海贼王",
                    "author": "尾田荣一郎",
                    "tags": "冒险,热血",
                    "category": "comic",
                    "status": "ONGOING",
                    "cover_url": "http://img/1.jpg",
                    "latest_chapter": "第1100话",
                    "chapter_list": [{"title": "第1话"}, {"title": "第2话"}],
                }
            )
        raise AssertionError(f"未匹配的请求 {request.method} {request.url}")

    _mock_httpx(monkeypatch, handler)

    result = await adapter.subscribe(
        None,
        item_id="/comic/onepiece",
        options={
            **SEARCH_ITEM,
            "age_rating": 18,
            "ignored": "不要透传",
        },
    )

    assert captured["body"]["name"] == "海贼王"
    assert captured["body"]["crawling_type"] == "copymanga"
    assert captured["body"]["comic_href"] == "/comic/onepiece"
    assert captured["body"]["age_rating"] == 18
    assert "ignored" not in captured["body"]
    assert result["variant"] == "local"
    assert result["subscription_id"] == "12"
    assert result["queued"] is True
    assert result["library"] is None
    assert result["meta"]["author"] == "尾田荣一郎"
    assert result["meta"]["tags"] == "冒险,热血"
    assert result["meta"]["comic_status"] == "ONGOING"
    assert result["meta"]["cover"] == "http://img/1.jpg"
    assert result["meta"]["total_chapters"] == 2


@pytest.mark.asyncio
async def test_subscribe_by_name_without_search_fields(monkeypatch):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/app/subscriptions/":
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                202,
                json={
                    "code": 0,
                    "msg": "success",
                    "data": {"subscription_id": 21, "task_id": "task-2"},
                },
            )
        # 详情接口失败不影响订阅，只少掉通知卡片字段
        return httpx.Response(500, json={"code": 1, "msg": "boom", "data": None})

    _mock_httpx(monkeypatch, handler)

    result = await adapter.subscribe(
        None,
        item_id="火影忍者",
        options={"category": "comic"},
    )

    assert captured["body"] == {"name": "火影忍者", "category": "comic"}
    assert result["subscription_id"] == "21"
    assert result["queued"] is True
    assert result["meta"] == {}


@pytest.mark.asyncio
async def test_subscribe_fills_meta_from_search_when_detail_is_empty(monkeypatch):
    """订阅详情要等刮削才有状态/章节数；创建时用同名搜索结果补全通知卡片。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/app/subscriptions/":
            return httpx.Response(
                202,
                json={
                    "code": 0,
                    "msg": "success",
                    "data": {"subscription_id": 31, "task_id": "task-3"},
                },
            )
        if request.url.path == "/api/v1/app/subscriptions/31/":
            return _ok(
                {
                    "id": 31,
                    "name": "重生之超级战神",
                    "author": "有马文化",
                    "category": "comic",
                    "status": None,
                    "tags": None,
                    "latest_chapter": None,
                    "chapter_list": None,
                    "license_comic_id": 279,
                }
            )
        if request.url.path == "/api/v1/app/explore/search/":
            return _ok(
                {
                    "items": [
                        {
                            "name": "重生之超级战神",
                            "author": "有马文化",
                            "status": "ONGOING",
                            "chapter_count": 49,
                            "update_info": "更新至48话",
                        }
                    ],
                    "total": 1,
                    "source": "license",
                }
            )
        raise AssertionError(f"未匹配的请求 {request.method} {request.url}")

    _mock_httpx(monkeypatch, handler)

    result = await adapter.subscribe(
        None, item_id="重生之超级战神", options={"name": "重生之超级战神", "category": "comic"}
    )

    assert result["meta"]["author"] == "有马文化"
    assert result["meta"]["comic_status"] == "ONGOING"
    assert result["meta"]["total_chapters"] == 49
    assert result["meta"]["latest_chapter"] == "更新至48话"


@pytest.mark.asyncio
async def test_subscribe_requires_name(monkeypatch):
    _mock_httpx(monkeypatch, lambda request: _ok({}))

    with pytest.raises(APIException) as exc_info:
        await adapter.subscribe(
            None, item_id="/comic/onepiece", options={"category": "comic"}
        )
    assert "name" in str(exc_info.value)


@pytest.mark.asyncio
async def test_subscribe_reports_already_subscribed(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"code": 1, "msg": "该漫画已订阅: 海贼王", "data": None},
        )

    _mock_httpx(monkeypatch, handler)

    result = await adapter.subscribe(
        None,
        item_id="/comic/onepiece",
        options=SEARCH_ITEM,
    )

    assert result["already_subscribed"] is True
    assert "已订阅" in result["message"]


@pytest.mark.asyncio
async def test_detail_requires_search_fields(monkeypatch):
    _mock_httpx(monkeypatch, lambda request: _ok({}))

    with pytest.raises(APIException) as exc_info:
        await adapter.detail(None, item_id="/comic/onepiece")
    assert "crawling_type" in str(exc_info.value)


@pytest.mark.asyncio
async def test_detail_returns_chapter_count(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/app/explore/comic-detail/"
        return _ok(
            {
                "name": "海贼王",
                "author": "尾田荣一郎",
                "cover_url": "http://img/1.jpg",
                "summary": "简介",
                "status": "连载中",
                "chapter_list": [{"title": "第1话"}, {"title": "第2话"}],
            }
        )

    _mock_httpx(monkeypatch, handler)

    result = await adapter.detail(
        None,
        item_id="/comic/onepiece",
        options={**SEARCH_ITEM, "name": "海贼王"},
    )

    assert result["detail"]["total_chapters"] == 2
    assert result["detail"]["author"] == "尾田荣一郎"


@pytest.mark.asyncio
async def test_downloads_maps_chapter_summary(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/app/subscriptions/":
            return _ok(
                {
                    "items": [
                        {"id": 12, "name": "海贼王", "sub_status": "subscribing"},
                        {"id": 13, "name": "火影忍者", "sub_status": "subscribing"},
                    ],
                    "total": 2,
                    "page": 1,
                    "page_size": 20,
                    "total_pages": 1,
                }
            )
        if request.url.path == "/api/v1/app/subscriptions/12/chapters/":
            return _ok({"summary": {"total": 10, "downloaded": 10}})
        if request.url.path == "/api/v1/app/subscriptions/13/chapters/":
            return _ok({"summary": {"total": 10, "downloaded": 3}})
        raise AssertionError(f"未匹配的请求 {request.url}")

    _mock_httpx(monkeypatch, handler)

    result = await adapter.downloads(None)

    items = {item["task_id"]: item for item in result["items"]}
    assert items["12"]["total_chapters"] == 10
    assert items["12"]["downloaded_chapters"] == 10
    assert items["12"]["status"] == "done"
    assert items["13"]["status"] == "partial"


@pytest.mark.asyncio
async def test_download_start_triggers_process(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/app/subscriptions/12/process/"
        return httpx.Response(
            202,
            json={
                "code": 0,
                "msg": "success",
                "data": {
                    "task_id": "task-9",
                    "status": "PENDING",
                    "websocket_url": "/ws/tasks/task-9/",
                    "message": "正在处理订阅",
                },
            },
        )

    _mock_httpx(monkeypatch, handler)

    result = await adapter.download(None, task_id="12", action="start")

    assert result["action"] == "start"
    assert result["queued"] is True
    assert result["async_task_id"] == "task-9"


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["stop", "delete"])
async def test_download_unsupported_actions(monkeypatch, action):
    _mock_httpx(monkeypatch, lambda request: _ok({}))

    with pytest.raises(APIException):
        await adapter.download(None, task_id="12", action=action)
