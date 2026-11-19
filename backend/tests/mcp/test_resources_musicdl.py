"""go-music-dl 音乐域适配器测试：不联网，用 MockTransport 拦截 HTTP。"""

import json

import httpx
import pytest
import pytest_asyncio

from app.core.exceptions import APIException
from app.models.app_store import App
from app.models.orchestration import AppInstance

SEARCH_HTML = """
<html><body>
<ul>
    <li class="song-card"
        data-id="228908"
        data-source="kuwo"
        data-album-id=""
        data-album="叶惠美"
        data-duration="269"
        data-name="晴天"
        data-artist="周杰伦"
        data-cover="https://img3.kuwo.cn/cover.jpg"
        data-sort-size=""
        data-sort-bitrate=""
        data-extra='{&#34;rid&#34;:&#34;228908&#34;}'>
        <div class="song-info"></div>
    </li>
    <li class="song-card" data-id="123" data-name="没有来源"
        data-artist="某人">
    </li>
    <li class="song-card" data-source="kuwo" data-name="没有 ID" data-id="">
    </li>
    <li class="song-card" data-id="456" data-source="bilibili"
        data-name="A &amp; B" data-artist="UP" data-duration="12.5"
        data-cover="https://i0.hdslb.com/a.jpg?a=1&amp;b=2">
    </li>
</ul>
</body></html>
"""

NO_RESULTS_HTML = """
<html><body><div class="no-results">未找到符合条件的资源</div></body></html>
"""


def _mock_httpx(monkeypatch, handler):
    """用 httpx.MockTransport 替换 AsyncClient，拦截客户端的 HTTP 请求。"""
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: real_client(transport=transport, **kwargs)
    )


@pytest_asyncio.fixture
async def music_instance(mcp_env):
    Session = mcp_env["session"]
    async with Session() as db:
        app_record = App(
            name="music-dl",
            display_name="Music-DL",
            yaml_template="services: {}",
        )
        db.add(app_record)
        await db.flush()
        instance = AppInstance(
            app_id=app_record.id,
            instance_name="music",
            status="running",
            config={"url": "http://music-dl:9012"},
        )
        db.add(instance)
        await db.commit()
        await db.refresh(instance)
        return instance


@pytest.mark.asyncio
async def test_search_parses_song_cards(mcp_env, music_instance, monkeypatch):
    """解析真实卡片片段：多行属性、HTML 实体、缺字段卡片被跳过。"""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["q"] = request.url.params.get("q")
        seen["type"] = request.url.params.get("type")
        seen["sources"] = request.url.params.get_list("sources")
        return httpx.Response(200, text=SEARCH_HTML)

    _mock_httpx(monkeypatch, handler)

    from app.services.resources import musicdl

    async with mcp_env["session"]() as db:
        result = await musicdl.search(
            db, keyword="晴天", sources=["kuwo"], count=8
        )

        assert seen["path"] == "/music/search"
        assert seen["q"] == "晴天"
        assert seen["type"] == "song"
        assert seen["sources"] == ["kuwo"]
        assert result["instance_id"] == music_instance.id
        assert len(result["items"]) == 2

        first = result["items"][0]
        assert first["item_id"] == "kuwo:228908"
        assert first["title"] == "晴天"
        assert first["artist"] == "周杰伦"
        assert first["album"] == "叶惠美"
        assert first["duration"] == 269
        assert first["source"] == "kuwo"
        assert first["cover"] == "https://img3.kuwo.cn/cover.jpg"
        # data-extra 里的 &#34; 已还原成可再次提交的 JSON 字符串
        assert first["extra"] == '{"rid":"228908"}'
        assert json.loads(first["extra"]) == {"rid": "228908"}

        second = result["items"][1]
        assert second["item_id"] == "bilibili:456"
        assert second["title"] == "A & B"
        assert second["duration"] == 12
        assert second["cover"] == "https://i0.hdslb.com/a.jpg?a=1&b=2"

        page_two = await musicdl.search(
            db, keyword="晴天", sources=["kuwo"], count=1, page=2
        )
        assert [item["item_id"] for item in page_two["items"]] == ["bilibili:456"]


@pytest.mark.asyncio
async def test_search_quality_sorts_before_pagination(
    mcp_env, music_instance, monkeypatch
):
    inspected = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/search"):
            return httpx.Response(200, text=SEARCH_HTML)
        song_id = request.url.params["id"]
        inspected[song_id] = request.url.params["duration"]
        bitrate = "128 kbps" if song_id == "228908" else "1411 kbps"
        return httpx.Response(
            200,
            json={"valid": True, "size": "4.1 MB", "bitrate": bitrate},
        )

    _mock_httpx(monkeypatch, handler)

    from app.services.resources import musicdl

    async with mcp_env["session"]() as db:
        result = await musicdl.search(db, keyword="晴天", count=1, sort="quality")

    assert [item["item_id"] for item in result["items"]] == ["bilibili:456"]
    assert result["items"][0]["bitrate"] == "1411 kbps"
    assert result["items"][0]["size"] == "4.1 MB"
    assert result["items"][0]["resolvable"] is True
    assert inspected == {"228908": "269", "456": "12"}


@pytest.mark.asyncio
async def test_search_no_results_returns_empty(mcp_env, music_instance, monkeypatch):
    _mock_httpx(monkeypatch, lambda request: httpx.Response(200, text=NO_RESULTS_HTML))

    from app.services.resources import musicdl

    async with mcp_env["session"]() as db:
        result = await musicdl.search(db, keyword="不存在的歌")
    assert result["items"] == []


@pytest.mark.asyncio
async def test_detail_maps_inspect_success_and_failure(
    mcp_env, music_instance, monkeypatch
):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/music/inspect"
        seen.append(dict(request.url.params))
        if request.url.params.get("id") == "0":
            return httpx.Response(
                200,
                json={
                    "valid": False,
                    "status": "retryable",
                    "reason": "media_unreachable",
                    "retryable": True,
                    "retry_after_ms": 2000,
                },
            )
        return httpx.Response(
            200,
            json={
                "valid": True,
                "status": "valid",
                "url": "http://kw-er.kuwo.cn/track.mp3",
                "size": "4.1 MB",
                "bitrate": "128 kbps",
                "preview_only": False,
            },
        )

    _mock_httpx(monkeypatch, handler)

    from app.services.resources import musicdl

    async with mcp_env["session"]() as db:
        result = await musicdl.detail(
            db, item_id="kuwo:228908", extra='{"rid":"228908"}'
        )
        detail = result["detail"]
        assert detail["item_id"] == "kuwo:228908"
        assert detail["source"] == "kuwo"
        assert detail["id"] == "228908"
        assert detail["resolvable"] is True
        assert detail["size"] == "4.1 MB"
        assert detail["bitrate"] == "128 kbps"
        assert detail["raw"]["status"] == "valid"
        assert "reason" not in detail
        assert seen[0]["extra"] == '{"rid":"228908"}'

        failed = await musicdl.detail(db, item_id="kuwo:0")
        assert failed["detail"]["resolvable"] is False
        assert failed["detail"]["reason"] == "media_unreachable"
        assert failed["detail"]["raw"]["retryable"] is True

        with pytest.raises(APIException) as exc:
            await musicdl.detail(db, item_id="228908")
    assert exc.value.status_code == 400
    assert "source:id" in exc.value.message


@pytest.mark.asyncio
async def test_subscribe_posts_song_with_request_header(
    mcp_env, music_instance, monkeypatch
):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/inspect"):
            return httpx.Response(200, json={"valid": True, "size": "10.0 MB"})
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["header"] = request.headers.get("X-Requested-With")
        captured["body"] = json.loads(request.content)
        return httpx.Response(202, json={"added": 1, "existing": 0})

    _mock_httpx(monkeypatch, handler)

    from app.services.resources import musicdl

    async with mcp_env["session"]() as db:
        result = await musicdl.subscribe(
            db,
            item_id="kuwo:228908",
            name="晴天",
            options={
                "artist": "周杰伦",
                "album": "叶惠美",
                "cover": "https://img3.kuwo.cn/cover.jpg",
                "extra": '{"rid":"228908"}',
            },
        )

    assert result["instance_id"] == music_instance.id
    assert result["result"] == {"added": 1, "existing": 0}
    assert result["size"] == "10.0 MB"
    assert captured["method"] == "POST"
    assert captured["path"] == "/music/api/downloads/queue"
    assert captured["header"] == "XMLHttpRequest"
    body = captured["body"]
    assert isinstance(body["request_id"], str)
    assert 0 < len(body["request_id"]) <= 128
    assert body["songs"] == [
        {
            "id": "228908",
            "source": "kuwo",
            "name": "晴天",
            "artist": "周杰伦",
            "album": "叶惠美",
            "cover": "https://img3.kuwo.cn/cover.jpg",
            "extra": '{"rid":"228908"}',
        }
    ]


@pytest.mark.asyncio
async def test_subscribe_requires_source_and_id(mcp_env, music_instance, monkeypatch):
    calls = []
    _mock_httpx(
        monkeypatch,
        lambda request: calls.append(request) or httpx.Response(202, json={}),
    )

    from app.services.resources import musicdl

    async with mcp_env["session"]() as db:
        with pytest.raises(APIException) as exc:
            await musicdl.subscribe(db, item_id="晴天", name="晴天")
    assert exc.value.status_code == 400
    assert "source" in exc.value.message
    assert calls == []


@pytest.mark.asyncio
async def test_subscriptions_and_downloads_map_queue_fields(
    mcp_env, music_instance, monkeypatch
):
    queue_payload = {
        "tasks": [
            {
                "ID": 7,
                "Name": "晴天",
                "Artist": "周杰伦",
                "Source": "kuwo",
                "Status": "downloading",
                "Path": "",
                "Error": "",
                "Attempts": 1,
            },
            {
                "id": 8,
                "name": "小夜曲",
                "artist": "周杰伦",
                "source": "netease",
                "status": "success",
                "path": "/music/小夜曲.mp3",
                "error": "",
            },
        ],
        "total": 2,
        "counts": {"downloading": 1, "success": 1},
        "page": 1,
        "total_pages": 1,
    }

    _mock_httpx(monkeypatch, lambda request: httpx.Response(200, json=queue_payload))

    from app.services.resources import musicdl

    async with mcp_env["session"]() as db:
        subs = await musicdl.subscriptions(db)
        assert subs["note"] == "音乐域用下载队列代替订阅"
        assert [item["subscription_id"] for item in subs["items"]] == ["7", "8"]
        assert subs["items"][0] == {
            "subscription_id": "7",
            "title": "晴天",
            "artist": "周杰伦",
            "source": "kuwo",
            "status": "downloading",
            "progress": None,
        }

        result = await musicdl.downloads(db, limit=20)
        assert result["counts"] == {"downloading": 1, "success": 1}
        assert result["items"] == [
            {
                "task_id": "7",
                "title": "晴天",
                "artist": "周杰伦",
                "source": "kuwo",
                "status": "downloading",
                "progress": None,
                "path": "",
                "size": None,
                "error": "",
            },
            {
                "task_id": "8",
                "title": "小夜曲",
                "artist": "周杰伦",
                "source": "netease",
                "status": "success",
                "progress": None,
                "path": "/music/小夜曲.mp3",
                "size": None,
                "error": "",
            },
        ]


@pytest.mark.asyncio
async def test_download_actions_and_delete_confirmation(
    mcp_env, music_instance, monkeypatch
):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json={"status": "ok"})

    _mock_httpx(monkeypatch, handler)

    from app.services.resources import musicdl

    async with mcp_env["session"]() as db:
        with pytest.raises(APIException) as exc:
            await musicdl.download(db, task_id="7", action="delete")
        assert exc.value.status_code == 400
        assert "confirm=true" in exc.value.message
        assert calls == []

        await musicdl.download(db, task_id="7", action="start")
        await musicdl.download(db, task_id="7", action="stop")
        await musicdl.download(db, task_id="7", action="delete", confirm=True)

        with pytest.raises(APIException):
            await musicdl.download(db, task_id="7", action="bogus")

    assert calls == [
        ("POST", "/music/api/downloads/queue/7/retry"),
        ("POST", "/music/api/downloads/queue/7/cancel"),
        ("DELETE", "/music/api/downloads/queue/7"),
    ]


@pytest.mark.asyncio
async def test_unsubscribe_deletes_queue_task(mcp_env, music_instance, monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(
            (
                request.method,
                request.url.path,
                request.headers.get("X-Requested-With"),
            )
        )
        return httpx.Response(200, json={"status": "ok"})

    _mock_httpx(monkeypatch, handler)

    from app.services.resources import musicdl

    async with mcp_env["session"]() as db:
        result = await musicdl.unsubscribe(db, subscription_id="7")

    assert result["subscription_id"] == "7"
    assert result["result"] == {"status": "ok"}
    assert calls == [
        ("DELETE", "/music/api/downloads/queue/7", "XMLHttpRequest")
    ]


@pytest.mark.asyncio
async def test_replace_requires_confirmation_and_song_options(
    mcp_env, music_instance, monkeypatch
):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "POST" and request.url.path.endswith("/cancel"):
            # 旧任务可能已结束，取消失败不应阻止替换
            return httpx.Response(409, json={"error": "only waiting tasks can be canceled"})
        if request.method == "DELETE":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(202, json={"added": 1, "existing": 0})

    _mock_httpx(monkeypatch, handler)

    from app.services.resources import musicdl

    async with mcp_env["session"]() as db:
        with pytest.raises(APIException) as exc:
            await musicdl.replace(db, subscription_id="7")
        assert exc.value.status_code == 400
        assert "confirm=true" in exc.value.message

        with pytest.raises(APIException) as exc:
            await musicdl.replace(db, subscription_id="7", confirm=True)
        assert exc.value.message == "替换音乐需要提供歌曲信息（options）"

        result = await musicdl.replace(
            db,
            subscription_id="7",
            confirm=True,
            options={
                "id": "228908",
                "source": "kuwo",
                "name": "晴天",
                "artist": "周杰伦",
            },
        )

    assert calls == [
        ("POST", "/music/api/downloads/queue/7/cancel"),
        ("DELETE", "/music/api/downloads/queue/7"),
        ("POST", "/music/api/downloads/queue"),
    ]
    assert result["subscription_id"] == "7"
    assert result["removed"] is True
    assert result["result"] == {"added": 1, "existing": 0}
    assert result["request_id"]


@pytest.mark.asyncio
async def test_http_error_becomes_api_exception(mcp_env, music_instance, monkeypatch):
    _mock_httpx(
        monkeypatch,
        lambda request: httpx.Response(500, json={"error": "内部错误"}),
    )

    from app.services.resources import musicdl

    async with mcp_env["session"]() as db:
        with pytest.raises(APIException) as exc:
            await musicdl.search(db, keyword="晴天")
    assert exc.value.status_code == 502
    assert "music-dl 调用失败" in exc.value.message
    assert "内部错误" in exc.value.message
