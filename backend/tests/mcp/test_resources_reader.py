"""小说域适配器测试：用 MockTransport 拦截 Reader 的 HTTP 请求，不连数据库。"""

import json

import httpx
import pytest

from app.core.exceptions import APIException
from app.services.integrations.discovery import DeployedApp
from app.services.resources import reader as adapter


def _mock_httpx(monkeypatch, handler):
    """用 httpx.MockTransport 替换 AsyncClient，拦截客户端的 HTTP 请求。"""
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: real_client(transport=transport, **kwargs)
    )


def _app(instance_id: int = 7) -> DeployedApp:
    return DeployedApp(
        instance_id=instance_id,
        app_name="reader",
        display_name="Reader",
        instance_name="reader-1",
        status="running",
        url="http://reader.local",
        username="reader-user",
        password="reader-pass",
    )


def _use_app(monkeypatch, app: DeployedApp | None = None) -> None:
    async def fake_resolve(db, app_names, instance_id=None):
        assert app_names == ("reader",)
        return app or _app()

    monkeypatch.setattr(adapter, "resolve_app", fake_resolve)


class _FakeReader:
    """按路径返回固定响应，并记录请求便于断言。"""

    def __init__(
        self,
        *,
        search: list | None = None,
        shelf: list | None = None,
        shelf_after_save: list | None = None,
        chapters: list | None = None,
        chapter_error: bool = False,
        search_error: str | None = None,
        source_books: list | None = None,
        source_counts: dict[str, int] | None = None,
        source_search_error: str | None = None,
    ) -> None:
        self.search = search or []
        self.shelf = shelf or []
        self.shelf_after_save = shelf_after_save
        self.shelf_reads = 0
        self.chapters = chapters or []
        self.chapter_error = chapter_error
        self.search_error = search_error
        self.source_books = source_books or []
        self.source_counts = source_counts or {}
        self.source_search_error = source_search_error
        self.calls: list[dict] = []
        self.saved: dict | None = None
        self.deleted: dict | None = None
        self.cached: dict | None = None
        self.switched: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.calls.append(
            {
                "method": request.method,
                "path": request.url.path,
                "params": dict(request.url.params),
                "body": body,
            }
        )
        path = request.url.path
        if path == "/reader3/login":
            return httpx.Response(
                200, json={"isSuccess": True, "data": {"accessToken": "token-1"}}
            )
        if path == "/reader3/searchBookMulti":
            if self.search_error:
                return httpx.Response(
                    200, json={"isSuccess": False, "errorMsg": self.search_error}
                )
            return httpx.Response(
                200,
                json={
                    "isSuccess": True,
                    "data": {"list": self.search, "lastIndex": 0},
                },
            )
        if path == "/reader3/getShelfBookWithCacheInfo":
            self.shelf_reads += 1
            shelf = self.shelf
            if self.shelf_after_save is not None and self.shelf_reads > 1:
                shelf = self.shelf_after_save
            return httpx.Response(200, json={"isSuccess": True, "data": shelf})
        if path == "/reader3/getChapterList":
            if self.chapter_error:
                return httpx.Response(
                    200, json={"isSuccess": False, "errorMsg": "目录获取失败"}
                )
            return httpx.Response(
                200, json={"isSuccess": True, "data": self.chapters}
            )
        if path == "/reader3/searchBookSource":
            if self.source_search_error:
                return httpx.Response(
                    200,
                    json={"isSuccess": False, "errorMsg": self.source_search_error},
                )
            return httpx.Response(
                200,
                json={
                    "isSuccess": True,
                    "data": {"list": self.source_books, "lastIndex": 0},
                },
            )
        if path == "/reader3/setBookSource":
            self.switched.append(body)
            candidate = next(
                (
                    item
                    for item in self.source_books
                    if item.get("bookUrl") == body.get("newUrl")
                    and item.get("origin") == body.get("bookSourceUrl")
                ),
                {},
            )
            return httpx.Response(
                200,
                json={
                    "isSuccess": True,
                    "data": {
                        **candidate,
                        "bookUrl": body.get("newUrl"),
                        "origin": body.get("bookSourceUrl"),
                        "totalChapterNum": self.source_counts.get(
                            body.get("newUrl"), 0
                        ),
                    },
                },
            )
        if path == "/reader3/saveBook":
            self.saved = body
            return httpx.Response(
                200, json={"isSuccess": True, "data": {"bookUrl": body.get("bookUrl")}}
            )
        if path == "/reader3/deleteBook":
            self.deleted = body
            return httpx.Response(200, json={"isSuccess": True})
        if path == "/reader3/cacheBookOnServer":
            self.cached = body
            return httpx.Response(200, json={"isSuccess": True})
        return httpx.Response(404, json={"isSuccess": False, "errorMsg": "unknown"})


@pytest.mark.asyncio
async def test_search_normalizes_books_and_tolerates_html(monkeypatch):
    """搜索结果归一化：去 HTML、缺 bookUrl 跳过、字段名容错、count 截断。"""
    fake = _FakeReader(
        search=[
            {
                "name": "<b>三体</b>",
                "author": "刘慈欣",
                "bookUrl": "http://source/1",
                "origin": "https://source.example",
                "originName": "示例书源",
                "coverUrl": "http://img/1.jpg",
                "intro": "<p>地球往事&amp;三体</p>",
                "latestChapterTitle": "第三十九章",
                "totalChapterNum": 39,
            },
            {"title": "没有地址的书"},
            {"url": "http://source/2", "title": "缺字段的书", "totalChapterNum": "abc"},
            {"name": "多余条目", "bookUrl": "http://source/3"},
        ]
    )
    _mock_httpx(monkeypatch, fake)
    _use_app(monkeypatch)

    result = await adapter.search(object(), keyword="三体", count=2)

    assert result["instance_id"] == 7
    assert [item["item_id"] for item in result["items"]] == [
        "http://source/1",
        "http://source/2",
    ]
    first, second = result["items"]
    assert first["title"] == "三体"
    assert first["intro"] == "地球往事&三体"
    assert first["total_chapters"] == 39
    assert first["origin_name"] == "示例书源"
    assert second["title"] == "缺字段的书"
    assert second["author"] is None
    assert second["total_chapters"] == 0

    search_call = next(
        call for call in fake.calls if call["path"] == "/reader3/searchBookMulti"
    )
    assert search_call["params"]["key"] == "三体"
    assert search_call["params"]["lastIndex"] == "-1"
    assert search_call["params"]["accessToken"] == "token-1"


@pytest.mark.asyncio
async def test_search_surfaces_reader_error(monkeypatch):
    """未配置书源时把 Reader 的错误信息转成 APIException。"""
    fake = _FakeReader(search_error="未配置书源")
    _mock_httpx(monkeypatch, fake)
    _use_app(monkeypatch)

    with pytest.raises(APIException) as excinfo:
        await adapter.search(object(), keyword="三体")

    assert "未配置书源" in str(excinfo.value)
    assert excinfo.value.status_code == 502


@pytest.mark.asyncio
async def test_subscriptions_compute_progress(monkeypatch):
    """书架转订阅列表：进度 = 已缓存/总数，缺失地址的条目跳过。"""
    fake = _FakeReader(
        shelf=[
            {
                "name": "三体",
                "author": "刘慈欣",
                "bookUrl": "http://source/1",
                "totalChapterNum": 10,
                "cachedChapterCount": 4,
            },
            {"name": "无地址"},
            {
                "name": "新书",
                "bookUrl": "http://source/2",
                "totalChapterNum": 0,
                "cachedChapterCount": 0,
            },
        ]
    )
    _mock_httpx(monkeypatch, fake)
    _use_app(monkeypatch)

    result = await adapter.subscriptions(object())

    assert result["instance_id"] == 7
    assert len(result["items"]) == 2
    first, second = result["items"]
    assert first["subscription_id"] == "http://source/1"
    assert first["progress"] == 0.4
    assert first["cached_chapters"] == 4
    assert first["total_chapters"] == 10
    assert second["progress"] == 0


@pytest.mark.asyncio
async def test_downloads_maps_cache_status(monkeypatch):
    """缓存状态映射为 done / caching / idle。"""
    fake = _FakeReader(
        shelf=[
            {"name": "完结", "bookUrl": "a", "totalChapterNum": 10, "cachedChapterCount": 10},
            {"name": "进行", "bookUrl": "b", "totalChapterNum": 10, "cachedChapterCount": 3},
            {"name": "未开始", "bookUrl": "c", "totalChapterNum": 10, "cachedChapterCount": 0},
        ]
    )
    _mock_httpx(monkeypatch, fake)
    _use_app(monkeypatch)

    result = await adapter.downloads(object())

    assert [item["status"] for item in result["items"]] == ["done", "caching", "idle"]
    assert result["items"][1]["progress"] == 0.3


@pytest.mark.asyncio
async def test_subscribe_requires_book_info():
    """缺少 bookUrl 或书名时直接提示需要搜索结果。"""
    with pytest.raises(APIException) as excinfo:
        await adapter.subscribe(None, item_id="", options={})

    assert "小说订阅需要搜索结果里的书籍信息" in str(excinfo.value)
    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_subscribe_posts_normalized_book(monkeypatch):
    """归一化搜索结果能还原成 saveBook 需要的书籍对象。"""
    fake = _FakeReader()
    _mock_httpx(monkeypatch, fake)
    _use_app(monkeypatch)

    result = await adapter.subscribe(
        object(),
        item_id="http://source/1",
        name="三体",
        options={
            "item_id": "http://source/1",
            "title": "三体",
            "author": "刘慈欣",
            "origin": "https://source.example",
            "origin_name": "示例书源",
            "cover": "http://img/1.jpg",
            "intro": "<p>简介</p>",
            "latest_chapter": "第三十九章",
            "total_chapters": 39,
        },
    )

    assert result["instance_id"] == 7
    assert result["result"]["bookUrl"] == "http://source/1"
    assert fake.saved is not None
    assert fake.saved["name"] == "三体"
    assert fake.saved["bookUrl"] == "http://source/1"
    assert fake.saved["originName"] == "示例书源"
    assert fake.saved["coverUrl"] == "http://img/1.jpg"
    assert fake.saved["latestChapterTitle"] == "第三十九章"
    assert fake.saved["totalChapterNum"] == 39
    assert fake.saved["canUpdate"] is True
    assert "item_id" not in fake.saved

    save_call = next(call for call in fake.calls if call["path"] == "/reader3/saveBook")
    assert save_call["method"] == "POST"
    assert save_call["params"]["accessToken"] == "token-1"


@pytest.mark.asyncio
async def test_subscribe_fills_cover_from_shelf(monkeypatch):
    """搜索结果常缺封面：保存后用书架条目的缓存封面补全，通知卡片才有图。"""
    fake = _FakeReader(
        shelf_after_save=[
            {
                "bookUrl": "http://source/1",
                "name": "完美世界",
                "author": "辰东",
                "coverUrl": "/assets/admin/covers/abc.jpg",
            }
        ]
    )
    _mock_httpx(monkeypatch, fake)
    _use_app(monkeypatch)

    result = await adapter.subscribe(
        object(),
        item_id="http://source/1",
        name="完美世界",
        options={
            "title": "完美世界",
            "author": "辰东",
            "origin": "https://source.example",
            "origin_name": "示例书源",
        },
    )

    # 相对路径补成 Reader 实例的绝对地址，通知渠道才能直接下载
    assert (
        result["result"]["coverUrl"]
        == "http://reader.local/assets/admin/covers/abc.jpg"
    )


@pytest.mark.asyncio
async def test_subscribe_auto_selects_source_with_most_chapters(monkeypatch):
    """首次订阅会比较同书名同作者书源，切换到章节最多的源。"""
    current = {
        "name": "三体",
        "author": "刘慈欣",
        "origin": "https://current.example",
        "originName": "当前源",
        "bookUrl": "http://source/1",
    }
    source_b = {
        "name": "三体",
        "author": "刘慈欣",
        "origin": "https://b.example",
        "originName": "B 源",
        "bookUrl": "http://source/b",
    }
    source_c = {
        "name": "三体",
        "author": "刘慈欣",
        "origin": "https://c.example",
        "originName": "C 源",
        "bookUrl": "http://source/c",
    }
    unrelated = {
        "name": "三体前传",
        "author": "刘慈欣",
        "origin": "https://other.example",
        "originName": "无关源",
        "bookUrl": "http://source/other",
    }
    fake = _FakeReader(
        source_books=[current, source_b, source_c, unrelated],
        source_counts={"http://source/b": 120, "http://source/c": 150},
    )
    _mock_httpx(monkeypatch, fake)
    _use_app(monkeypatch)

    result = await adapter.subscribe(
        object(),
        item_id="http://source/1",
        name="三体",
        options={
            "title": "三体",
            "author": "刘慈欣",
            "origin": "https://current.example",
            "origin_name": "当前源",
        },
    )

    selection = result["source_selection"]
    assert selection["checked"] == 2
    assert [item["total_chapters"] for item in selection["candidates"]] == [
        120,
        150,
    ]
    assert selection["selected"]["origin_name"] == "C 源"
    assert selection["selected"]["total_chapters"] == 150
    assert result["result"]["bookUrl"] == "http://source/c"
    assert [item["newUrl"] for item in fake.switched] == [
        "http://source/b",
        "http://source/c",
    ]


@pytest.mark.asyncio
async def test_subscribe_restores_initial_source_when_it_has_most_chapters(
    monkeypatch,
):
    """候选源章节都更少时，比较结束后切回原源。"""
    current = {
        "name": "三体",
        "author": "刘慈欣",
        "origin": "https://current.example",
        "originName": "当前源",
        "bookUrl": "http://source/1",
    }
    source_b = {
        "name": "三体",
        "author": "刘慈欣",
        "origin": "https://b.example",
        "originName": "B 源",
        "bookUrl": "http://source/b",
    }
    source_c = {
        "name": "三体",
        "author": "刘慈欣",
        "origin": "https://c.example",
        "originName": "C 源",
        "bookUrl": "http://source/c",
    }
    fake = _FakeReader(
        shelf=[],
        chapters=[{"index": index, "title": f"第 {index} 章"} for index in range(5)],
        source_books=[current, source_b, source_c],
        source_counts={
            "http://source/1": 5,
            "http://source/b": 2,
            "http://source/c": 3,
        },
    )
    _mock_httpx(monkeypatch, fake)
    _use_app(monkeypatch)

    result = await adapter.subscribe(
        object(),
        item_id="http://source/1",
        name="三体",
        options={
            "title": "三体",
            "author": "刘慈欣",
            "origin": "https://current.example",
            "origin_name": "当前源",
        },
    )

    assert [item["newUrl"] for item in fake.switched] == [
        "http://source/b",
        "http://source/c",
        "http://source/1",
    ]
    assert result["source_selection"]["selected"]["origin_name"] == "当前源"
    assert result["source_selection"]["selected"]["total_chapters"] == 5
    assert result["result"]["bookUrl"] == "http://source/1"


@pytest.mark.asyncio
async def test_subscribe_skips_source_comparison_for_existing_book(monkeypatch):
    """已在书架的书不重复保存，也不改动当前书源。"""
    fake = _FakeReader(
        shelf=[
            {
                "name": "三体",
                "author": "刘慈欣",
                "bookUrl": "http://source/1",
                "origin": "https://current.example",
                "originName": "当前源",
            }
        ]
    )
    _mock_httpx(monkeypatch, fake)
    _use_app(monkeypatch)

    result = await adapter.subscribe(
        object(),
        item_id="http://source/1",
        name="三体",
        options={"author": "刘慈欣", "origin": "https://current.example"},
    )

    assert result["source_selection"] == {"skipped": "already_subscribed"}
    assert fake.saved is None
    assert fake.switched == []


@pytest.mark.asyncio
async def test_unsubscribe_sends_book_name(monkeypatch):
    """退订先从书架取书名，再调用 deleteBook。"""
    fake = _FakeReader(
        shelf=[{"name": "三体", "bookUrl": "http://source/1"}]
    )
    _mock_httpx(monkeypatch, fake)
    _use_app(monkeypatch)

    result = await adapter.unsubscribe(object(), subscription_id="http://source/1")

    assert result["result"] == {"removed": "http://source/1"}
    assert fake.deleted == {"bookUrl": "http://source/1", "name": "三体"}


@pytest.mark.asyncio
async def test_detail_degrades_when_chapter_list_fails(monkeypatch):
    """目录接口失败时返回书架信息与空章节目录，并带 warning。"""
    fake = _FakeReader(
        shelf=[
            {
                "name": "三体",
                "bookUrl": "http://source/1",
                "author": "刘慈欣",
                "totalChapterNum": 10,
                "cachedChapterCount": 2,
            }
        ],
        chapter_error=True,
    )
    _mock_httpx(monkeypatch, fake)
    _use_app(monkeypatch)

    result = await adapter.detail(object(), item_id="http://source/1")

    detail = result["detail"]
    assert detail["title"] == "三体"
    assert detail["total_chapters"] == 10
    assert detail["cached_chapters"] == 2
    assert detail["chapters"] == []
    assert "目录" in detail["warning"]


@pytest.mark.asyncio
async def test_download_start_posts_cache_task(monkeypatch):
    """start 通过 cacheBookOnServer 提交缓存，stop/delete 明确拒绝。"""
    fake = _FakeReader()
    _mock_httpx(monkeypatch, fake)
    _use_app(monkeypatch)

    result = await adapter.download(object(), task_id="http://source/1", action="start")

    assert result["result"] == {"task_id": "http://source/1", "action": "start"}
    assert fake.cached == {"bookUrlList": ["http://source/1"]}
    cache_call = next(
        call for call in fake.calls if call["path"] == "/reader3/cacheBookOnServer"
    )
    assert cache_call["method"] == "POST"
    assert cache_call["params"]["accessToken"] == "token-1"

    for action in ("stop", "delete"):
        with pytest.raises(APIException) as excinfo:
            await adapter.download(object(), task_id="http://source/1", action=action)
        assert "暂不支持停止或删除缓存任务" in str(excinfo.value)
        assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_replace_requires_confirm():
    """未确认时直接拒绝，不会发出任何请求。"""
    with pytest.raises(APIException) as excinfo:
        await adapter.replace(None, subscription_id="http://source/1")

    assert "替换资源前需要用户确认" in str(excinfo.value)
    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_replace_requeues_cache(monkeypatch):
    """确认后只是重新提交缓存任务（Reader 不会删除已有文件）。"""
    fake = _FakeReader()
    _mock_httpx(monkeypatch, fake)
    _use_app(monkeypatch)

    result = await adapter.replace(
        object(), subscription_id="http://source/1", confirm=True
    )

    assert result["result"] == {"requeued": "http://source/1"}
    assert fake.cached == {"bookUrlList": ["http://source/1"]}
