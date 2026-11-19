"""巡影 Mediary Scout（网盘影视）适配器测试：用 httpx.MockTransport 模拟 Agent API。"""

import httpx
import pytest

from app.core.exceptions import APIException
from app.services.integrations.discovery import DeployedApp
from app.services.resources import mediary_scout as adapter


def _state(**overrides) -> DeployedApp:
    values = {
        "instance_id": 11,
        "app_name": "mediary-scout",
        "display_name": "巡影 Mediary Scout",
        "instance_name": "movie-cloud",
        "status": "running",
        "url": "http://mediary-scout:3000",
        "api_key": "agent-token",
    }
    values.update(overrides)
    return DeployedApp(**values)


@pytest.fixture(autouse=True)
def _patch_state(monkeypatch):
    async def fake_resolve_app(db, app_names, instance_id=None):
        return _state(instance_id=instance_id or 11)

    monkeypatch.setattr(adapter, "resolve_app", fake_resolve_app)
    adapter._SESSION_CACHE.clear()


def _mock_httpx(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )


@pytest.mark.asyncio
async def test_subscriptions_maps_library(monkeypatch):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.headers["Authorization"] == "Bearer agent-token"
        return httpx.Response(
            200,
            json={
                "count": 1,
                "items": [
                    {
                        "title": "沙丘",
                        "tmdbId": 438631,
                        "type": "movie",
                        "year": 2021,
                        "seasonNumber": 1,
                        "status": "active",
                        "totalEpisodes": 1,
                        "obtainedCount": 1,
                        "missingAired": [],
                        "connectedStorageId": "cs_quark",
                    }
                ],
            },
        )

    _mock_httpx(monkeypatch, handler)

    result = await adapter.subscriptions(None)

    assert seen[0].url.path == "/api/agent/library"
    assert result["variant"] == "cloud"
    assert result["total"] == 1
    item = result["items"][0]
    assert item["subscription_id"] == "438631:s1"
    assert item["type"] == "电影"
    assert item["obtained_episode"] == 1


@pytest.mark.asyncio
async def test_subscribe_posts_acquire_and_maps_match(monkeypatch):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "status": "requested",
                "matched": {"tmdbId": 438631, "title": "沙丘", "year": 2021},
                "message": "已加入后台队列",
            },
        )

    _mock_httpx(monkeypatch, handler)

    result = await adapter.subscribe(
        None,
        name="沙丘",
        options={"type": "电影", "year": 2021},
    )

    assert seen[0].url.path == "/api/agent/acquire"
    import json

    body = json.loads(seen[0].content)
    assert body["query"] == "沙丘"
    assert body["type"] == "movie"
    assert result["status"] == "requested"
    assert result["variant"] == "cloud"
    assert result["tmdb_id"] == 438631
    assert result["title"] == "沙丘"


@pytest.mark.asyncio
async def test_subscribe_with_tmdb_id_skips_ambiguous_search(monkeypatch):
    """从搜索结果选中条目后，带 tmdb_id 直接入队，不再走片名匹配。"""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "status": "requested",
                "matched": {"tmdbId": 438631, "title": "沙丘", "year": 2021},
            },
        )

    _mock_httpx(monkeypatch, handler)

    result = await adapter.subscribe(
        None,
        item_id="438631",
        name="沙丘",
        options={"type": "电影", "tmdb_id": 438631, "year": "2021"},
    )

    import json

    body = json.loads(seen[0].content)
    assert seen[0].url.path == "/api/agent/acquire"
    assert body["tmdbId"] == 438631
    assert body["type"] == "movie"
    assert result["status"] == "requested"


@pytest.mark.asyncio
async def test_subscribe_logs_in_and_sends_session_cookie(monkeypatch):
    """巡影多用户模式下入队要会话：先用装配保存的账号登录，再带 cookie 调 acquire。"""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/auth/login":
            return httpx.Response(
                200,
                json={"ok": True},
                headers={"set-cookie": "mt_session=sess_test; Path=/; HttpOnly"},
            )
        return httpx.Response(
            200,
            json={
                "status": "requested",
                "matched": {"tmdbId": 438631, "title": "沙丘", "year": 2021},
            },
        )

    _mock_httpx(monkeypatch, handler)

    async def fake_resolve_app(db, app_names, instance_id=None):
        return _state(instance_id=11, username="admin", password="secret")

    monkeypatch.setattr(adapter, "resolve_app", fake_resolve_app)

    result = await adapter.subscribe(
        None, item_id="438631", options={"tmdb_id": 438631, "type": "电影"}
    )

    assert [r.url.path for r in seen][:2] == ["/api/auth/login", "/api/agent/acquire"]
    assert seen[1].headers.get("Cookie") == "mt_session=sess_test"
    assert result["status"] == "requested"


@pytest.mark.asyncio
async def test_subscribe_fills_poster_from_tmdb(monkeypatch):
    """带 tmdb_id 订阅时用 TMDB 详情补标题/年份/海报与演职员。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/agent/acquire":
            return httpx.Response(
                200,
                json={
                    "status": "requested",
                    "matched": {"tmdbId": 55448, "title": "", "year": None, "type": "tv"},
                },
            )
        if request.url.path == "/tv/55448/credits":
            return httpx.Response(
                200,
                json={
                    "crew": [{"name": "王晶", "job": "Director"}],
                    "cast": [{"name": "李亚鹏"}, {"name": "郭晋安"}],
                },
            )
        assert request.url.path == "/tv/55448"
        return httpx.Response(
            200,
            json={
                "name": "天下第一",
                "first_air_date": "2005-03-05",
                "poster_path": "/abc.jpg",
            },
        )

    _mock_httpx(monkeypatch, handler)

    result = await adapter.subscribe(
        None, item_id="55448", options={"tmdb_id": 55448, "type": "电视剧"}
    )

    assert result["title"] == "天下第一"
    assert result["year"] == "2005"
    assert result["poster"] == "https://image.tmdb.org/t/p/w342/abc.jpg"
    assert result["directors"] == ["王晶"]
    assert result["actors"] == ["李亚鹏", "郭晋安"]


@pytest.mark.asyncio
async def test_tmdb_poster_retries_once(monkeypatch):
    """TMDB 代理偶发失败时重试一次，海报兜底成功。"""
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/movie/1858/credits":
            return httpx.Response(404)
        attempts["n"] += 1
        assert request.url.path == "/movie/1858"
        if attempts["n"] == 1:
            return httpx.Response(502)
        return httpx.Response(
            200,
            json={
                "title": "变形金刚",
                "release_date": "2007-07-03",
                "poster_path": "/97.jpg",
            },
        )

    _mock_httpx(monkeypatch, handler)

    poster = await adapter.tmdb_poster(1858, "电影")

    assert poster == "https://image.tmdb.org/t/p/w342/97.jpg"
    assert attempts["n"] == 2


@pytest.mark.asyncio
async def test_subscribe_relogins_once_when_session_expired(monkeypatch):
    """会话失效（200 + unsupported + 未登录）时清缓存、重新登录再试一次。"""
    logins = 0
    acquires = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal logins, acquires
        if request.url.path == "/api/auth/login":
            logins += 1
            return httpx.Response(
                200,
                json={"ok": True},
                headers={"set-cookie": f"mt_session=sess{logins}; Path=/"},
            )
        if request.url.path != "/api/agent/acquire":
            return httpx.Response(404, json={"error": "not found"})
        acquires += 1
        if acquires == 1:
            return httpx.Response(
                200,
                json={
                    "status": "unsupported",
                    "message": "未登录，请先登录后再操作。",
                    "matched": {"tmdbId": 413577, "title": "", "year": None, "type": "movie"},
                },
            )
        return httpx.Response(
            200,
            json={
                "status": "requested",
                "matched": {"tmdbId": 413577, "title": "举起手来！", "year": 2005},
            },
        )

    _mock_httpx(monkeypatch, handler)

    async def fake_resolve_app(db, app_names, instance_id=None):
        return _state(instance_id=11, username="admin", password="secret")

    monkeypatch.setattr(adapter, "resolve_app", fake_resolve_app)

    result = await adapter.subscribe(None, item_id="413577", options={"type": "电影"})

    assert logins == 2
    assert acquires == 2
    assert result["status"] == "requested"


@pytest.mark.asyncio
async def test_subscribe_rejects_unsupported_status(monkeypatch):
    """巡影对未入队的情况也回 200（如多用户模式未登录），不能当成功。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "unsupported",
                "message": "未登录，请先登录后再操作。",
                "matched": {"tmdbId": 55448, "title": "", "year": None, "type": "tv"},
            },
        )

    _mock_httpx(monkeypatch, handler)

    with pytest.raises(APIException) as exc_info:
        await adapter.subscribe(None, name="天下第一", options={"type": "电视剧"})
    assert "未登录" in str(exc_info.value)
    assert "没有接受" in str(exc_info.value)


@pytest.mark.asyncio
async def test_subscribe_with_tmdb_id_requires_type():
    """带 tmdbId 时巡影会跳过 TMDB 查询并按剧集处理，必须显式给 type。"""
    with pytest.raises(APIException) as exc_info:
        await adapter.subscribe(None, item_id="438631", options={"tmdb_id": 438631})
    assert "type" in str(exc_info.value)


@pytest.mark.asyncio
async def test_subscribe_returns_candidates_on_ambiguous(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={
                "candidates": [
                    {
                        "tmdbId": 1429,
                        "title": "进击的巨人",
                        "year": 2013,
                        "type": "tv",
                        "score": 150,
                    }
                ]
            },
        )

    _mock_httpx(monkeypatch, handler)

    result = await adapter.subscribe(None, name="进击的巨人")

    assert result["status"] == "ambiguous"
    assert result["candidates"][0]["tmdb_id"] == 1429
    assert result["candidates"][0]["type"] == "电视剧"


@pytest.mark.asyncio
async def test_subscribe_rejects_missing_title():
    with pytest.raises(APIException):
        await adapter.subscribe(None, name="")


@pytest.mark.asyncio
async def test_downloads_maps_activity(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["limit"] == "5"
        return httpx.Response(
            200,
            json={
                "active": [
                    {
                        "workflowRunId": "run-1",
                        "status": "running",
                        "kind": "acquire",
                        "title": "沙丘",
                        "seasonNumber": 1,
                        "startedAt": "2026-11-18T10:00:00Z",
                    }
                ],
                "recent": [{"id": 1, "kind": "completed", "title": "沙丘"}],
            },
        )

    _mock_httpx(monkeypatch, handler)

    result = await adapter.downloads(None, limit=5)

    assert result["items"][0]["task_id"] == "run-1"
    assert result["items"][0]["title"] == "沙丘"
    assert result["recent"][0]["title"] == "沙丘"


@pytest.mark.asyncio
async def test_search_uses_tmdb_proxy_and_maps_results(monkeypatch):
    """网盘方案的只读搜索走巡影同款 TMDB 代理，不触发转存。"""
    monkeypatch.setenv("TMDB_PROXY_BASE_URL", "https://tmdb.test")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.url.host == "tmdb.test"
        assert request.url.path == "/search/multi"
        assert request.url.params["query"] == "沙丘"
        assert "Authorization" not in request.headers
        return httpx.Response(
            200,
            json={
                "page": 1,
                "results": [
                    {
                        "media_type": "movie",
                        "id": 438631,
                        "title": "沙丘",
                        "original_title": "Dune",
                        "release_date": "2021-09-15",
                        "overview": "保罗·厄崔迪前往厄拉科斯。",
                        "vote_average": 7.79,
                        "poster_path": "/abc.jpg",
                    },
                    {
                        "media_type": "tv",
                        "id": 1429,
                        "name": "进击的巨人",
                        "first_air_date": "2013-04-07",
                        "vote_average": 8.7,
                    },
                    {"media_type": "person", "id": 1, "name": "某人"},
                ],
            },
        )

    _mock_httpx(monkeypatch, handler)

    result = await adapter.search(None, keyword="沙丘")

    assert len(seen) == 1
    assert result["variant"] == "cloud"
    assert result["total"] == 2
    movie = result["items"][0]
    assert movie["item_id"] == "438631"
    assert movie["media_id"] == "438631"
    assert movie["tmdb_id"] == 438631
    assert movie["type"] == "电影"
    assert movie["year"] == "2021"
    assert movie["vote"] == 7.8
    assert movie["poster"] == "https://image.tmdb.org/t/p/w342/abc.jpg"
    assert result["items"][1]["type"] == "电视剧"


@pytest.mark.asyncio
async def test_missing_token_explains_how_to_enable(monkeypatch):
    async def fake_resolve_app(db, app_names, instance_id=None):
        return _state(api_key="")

    monkeypatch.setattr(adapter, "resolve_app", fake_resolve_app)

    with pytest.raises(APIException) as exc_info:
        await adapter.subscriptions(None)
    assert "Agent API" in str(exc_info.value)
