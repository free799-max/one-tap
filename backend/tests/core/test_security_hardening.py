"""安全加固回归测试。

覆盖：
- 注册闸门（首个用户/关闭 403/开启放行）
- 危险端点的管理员权限矩阵（非 admin 应 403）
- 镜像搜索 await 回归
- 登录频率限制
- 部署任务 SSE meta 脱敏
- /auth/verify 的 SSRF 防护
"""

import pytest

from app.config import settings
from app.services.orchestration.deploy_task_service import DeployTaskManager, sanitize_meta
from app.services.orchestration import OrchestrationService


async def _register_and_login(client, username: str, password: str = "pass123456") -> str:
    """注册用户并登录，返回 access_token。"""
    resp = await client.post("/api/auth/register", json={
        "username": username, "password": password,
    })
    assert resp.status_code == 201, resp.text
    login_resp = await client.post("/api/auth/login", json={
        "username": username, "password": password,
    })
    assert login_resp.status_code == 200, login_resp.text
    return login_resp.json()["data"]["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------- 注册闸门 ----------


@pytest.mark.asyncio
async def test_first_user_always_allowed_and_becomes_admin(client, monkeypatch):
    """注册关闭时首个用户仍可注册并成为 admin。"""
    monkeypatch.setattr(settings, "REGISTRATION_OPEN", False)
    resp = await client.post("/api/auth/register", json={
        "username": "firstuser", "password": "pass123456",
    })
    assert resp.status_code == 201
    assert resp.json()["data"]["role"] == "admin"


@pytest.mark.asyncio
async def test_second_user_forbidden_when_registration_closed(client, monkeypatch):
    """已有用户且注册关闭时，新注册返回 403。"""
    monkeypatch.setattr(settings, "REGISTRATION_OPEN", False)
    resp = await client.post("/api/auth/register", json={
        "username": "user1", "password": "pass123456",
    })
    assert resp.status_code == 201

    resp = await client.post("/api/auth/register", json={
        "username": "user2", "password": "pass123456",
    })
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_second_user_allowed_when_registration_open(client, monkeypatch):
    """注册开启时，后续用户可注册且为普通 user。"""
    monkeypatch.setattr(settings, "REGISTRATION_OPEN", True)
    await client.post("/api/auth/register", json={
        "username": "user1", "password": "pass123456",
    })
    resp = await client.post("/api/auth/register", json={
        "username": "user2", "password": "pass123456",
    })
    assert resp.status_code == 201
    assert resp.json()["data"]["role"] == "user"


@pytest.mark.asyncio
async def test_register_short_password_rejected(client):
    """注册密码少于 8 位时被拒绝。"""
    resp = await client.post("/api/auth/register", json={
        "username": "shortpw", "password": "abc",
    })
    assert resp.status_code == 422


# ---------- 登录频率限制 ----------


@pytest.mark.asyncio
async def test_login_rate_limited_after_repeated_failures(client):
    """同一账号连续登录失败超过上限后返回 429。"""
    await client.post("/api/auth/register", json={
        "username": "ratelimituser", "password": "pass123456",
    })
    for _ in range(10):
        resp = await client.post("/api/auth/login", json={
            "username": "ratelimituser", "password": "wrong-pass",
        })
        assert resp.status_code == 401
    resp = await client.post("/api/auth/login", json={
        "username": "ratelimituser", "password": "wrong-pass",
    })
    assert resp.status_code == 429


# ---------- 管理员权限矩阵 ----------

# (method, path, json_body) —— 均为危险/变更类端点，非 admin 必须 403
DANGEROUS_ENDPOINTS = [
    ("POST", "/api/docker/compose", {"project_name": "p1", "content": "services: {}"}),
    ("PUT", "/api/docker/compose/1", {"description": "x"}),
    ("POST", "/api/docker/compose/1/edit", {"content": "services: {}"}),
    ("DELETE", "/api/docker/compose/1", None),
    ("POST", "/api/docker/compose/1/versions", {"content": "services: {}"}),
    ("POST", "/api/docker/compose/1/versions/1/rollback", None),
    ("POST", "/api/docker/compose/1/action", {"action": "up"}),
    ("POST", "/api/docker/compose/import", {"project_ids": [], "container_ids": []}),
    ("POST", "/api/docker/containers/abc/action", {"action": "stop"}),
    ("POST", "/api/docker/containers", {"image": "nginx"}),
    ("POST", "/api/docker/containers/batch-action", {"ids": ["abc"], "action": "stop"}),
    ("POST", "/api/docker/containers/abc/exec", {"command": "ls"}),
    ("DELETE", "/api/docker/images/abc", None),
    ("POST", "/api/docker/images/prune", None),
    ("POST", "/api/docker/images/pull", {"image": "nginx"}),
    ("POST", "/api/docker/images/batch-delete", {"ids": ["abc"]}),
    ("PUT", "/api/settings", {}),
    ("POST", "/api/settings/directories", {"path": "/tmp", "name": "x"}),
    ("PUT", "/api/settings/directories", {"old_path": "/tmp/x", "new_name": "y"}),
    ("DELETE", "/api/settings/directories", {"path": "/tmp/x"}),
    ("POST", "/api/docker/host/directories", {"path": "/tmp", "name": "x"}),
    ("PUT", "/api/docker/host/directories", {"old_path": "/tmp/x", "new_name": "y"}),
    ("DELETE", "/api/docker/host/directories", {"path": "/tmp/x"}),
    ("DELETE", "/api/docker/host/networks/abc", None),
    ("POST", "/api/docker/registries", {"name": "r", "search_api_url": "https://example.com"}),
    ("PUT", "/api/docker/registries/1", {"name": "r2"}),
    ("DELETE", "/api/docker/registries/1", None),
    ("POST", "/api/docker/registries/1/set-default", None),
    ("POST", "/api/orchestrations/foo/deploy", {"instance_name": "i", "selected_apps": []}),
    ("POST", "/api/orchestrations/foo/import", {"instance_name": "i", "selected_apps": []}),
    ("PATCH", "/api/orchestrations/instances/1", {"instance_name": "renamed"}),
    ("DELETE", "/api/orchestrations/instances/1", None),
    ("POST", "/api/orchestrations/auth/verify", {"app_name": "qbittorrent", "url": "http://localhost:8080"}),
    ("POST", "/api/apps/foo/deploy", {"instance_name": "i", "config": {}}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,body", DANGEROUS_ENDPOINTS)
async def test_dangerous_endpoints_require_admin(client, method, path, body):
    """非管理员访问危险端点应返回 403。"""
    await _register_and_login(client, "adminuser")
    user_token = await _register_and_login(client, "normaluser")

    resp = await client.request(method, path, json=body, headers=_auth(user_token))
    assert resp.status_code == 403, f"{method} {path} 应拒绝普通用户，实际 {resp.status_code}"


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,body", DANGEROUS_ENDPOINTS)
async def test_dangerous_endpoints_pass_admin_check(client, method, path, body, monkeypatch):
    """管理员访问危险端点不应被 403 拦截（后续业务错误允许出现）。

    测试中屏蔽真实的 Docker/部署副作用，仅验证权限层放行。
    """
    from app.services.apps.base import AuthVerifyResult
    from app.services.docker import docker_manager
    from app.services.orchestration import orchestration_service
    from app.services.orchestration.deploy_task_service import deploy_task_manager

    # 阻断真实 Docker 调用与后台部署任务，避免测试产生副作用
    monkeypatch.setattr(docker_manager, "check_available", lambda: False)
    monkeypatch.setattr(
        deploy_task_manager, "create_task",
        lambda *a, **k: "fake-task-id",
    )
    # 不真正启动后台部署协程
    monkeypatch.setattr(
        "app.api.docker.compose.create_background_task",
        lambda coro: coro.close(),
    )
    monkeypatch.setattr(
        orchestration_service, "verify_app_auth",
        lambda *a, **k: _async_result(AuthVerifyResult(valid=False, message="测试中禁用")),
    )

    admin_token = await _register_and_login(client, "adminuser")

    resp = await client.request(method, path, json=body, headers=_auth(admin_token))
    assert resp.status_code != 403, f"{method} {path} 不应拒绝管理员"


async def _async_result(value):
    return value


# ---------- 镜像搜索 await 回归 ----------


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeHttpClient:
    """模拟 httpx.AsyncClient 的异步上下文管理器。"""

    def __init__(self, payload=None, error: Exception | None = None):
        self._payload = payload
        self._error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, **kwargs):
        if self._error is not None:
            raise self._error
        return _FakeResponse(self._payload)


_HUB_PAYLOAD = {
    "count": 1,
    "results": [
        {
            "repo_name": "library/nginx",
            "short_description": "Official nginx",
            "star_count": 100,
            "pull_count": 999,
            "is_official": True,
            "is_automated": False,
        }
    ],
}


@pytest.mark.asyncio
async def test_search_images_returns_results(monkeypatch):
    """search_images 默认走 Docker Hub，返回解析后的结果（await 回归）。"""
    from app.services.docker.image_service import ImageService

    monkeypatch.setattr(
        "app.services.docker.image_service.httpx.AsyncClient",
        lambda *a, **k: _FakeHttpClient(payload=_HUB_PAYLOAD),
    )
    result = await ImageService().search_images("nginx")
    assert isinstance(result, dict)
    assert result["total"] == 1
    assert result["results"][0]["name"] == "library/nginx"
    assert result["results"][0]["official"] is True


@pytest.mark.asyncio
async def test_search_images_fallback_to_mirror(monkeypatch):
    """主搜索接口失败时回退到 mirror 地址。"""
    import httpx as _httpx

    from app.services.docker.image_service import ImageService

    mirror_payload = {"total": 1, "results": [{"name": "mirror/nginx"}]}

    def _factory(*a, **k):
        return None

    calls = []

    class _RouterClient(_FakeHttpClient):
        async def get(self, url, **kwargs):
            calls.append(url)
            if "bad-api" in url:
                raise _httpx.RequestError("boom")
            return _FakeResponse(mirror_payload)

    monkeypatch.setattr(
        "app.services.docker.image_service.httpx.AsyncClient",
        lambda *a, **k: _RouterClient(),
    )
    result = await ImageService().search_images(
        "nginx",
        api_url="https://bad-api.example.com/search",
        mirror_url="https://mirror.example.com/search",
    )
    assert result["results"][0]["name"] == "mirror/nginx"
    assert any("bad-api" in u for u in calls)
    assert any("mirror.example.com" in u for u in calls)


@pytest.mark.asyncio
async def test_search_images_all_failed_raises(monkeypatch):
    """所有搜索源失败时抛出 RuntimeError。"""
    import httpx as _httpx

    from app.services.docker.image_service import ImageService

    monkeypatch.setattr(
        "app.services.docker.image_service.httpx.AsyncClient",
        lambda *a, **k: _FakeHttpClient(error=_httpx.RequestError("boom")),
    )
    with pytest.raises(RuntimeError, match="镜像搜索"):
        await ImageService().search_images(
            "nginx",
            api_url="https://bad-api.example.com/search",
        )


# ---------- 部署任务 meta 脱敏 ----------


def test_sanitize_meta_masks_sensitive_fields():
    """meta 中 rendered_yaml 被移除，password/secret/token 类字段被打码。"""
    meta = {
        "rendered_yaml": "services:\n  app:\n    environment:\n      - PASSWORD=plain",
        "password": "plain-password",
        "api_key": "ak-123",
        "access_token": "tk",
        "project_name": "demo",
        "user_id": 1,
    }
    result = sanitize_meta(meta)
    assert "rendered_yaml" not in result
    assert result["password"] == "***"
    assert result["api_key"] == "***"
    assert result["access_token"] == "***"
    assert result["project_name"] == "demo"
    assert result["user_id"] == 1


def test_deploy_task_history_meta_is_sanitized():
    """任务历史/SSE 通知中的 _meta 不含明文敏感信息。"""
    manager = DeployTaskManager()
    task_id = manager.create_task(
        "app_deploy",
        meta={
            "rendered_yaml": "PASSWORD=plain",
            "project_name": "demo",
        },
    )
    history = manager.get_task_history(task_id)
    assert history
    meta = history[0]["_meta"]
    assert "rendered_yaml" not in meta
    assert meta["project_name"] == "demo"

    manager.update_progress(task_id, "preparing", 10, "准备中")
    task = manager.get_task(task_id)
    # 存储的原始 meta 保留（后台任务自身需要使用），仅对外推送脱敏
    assert task["meta"]["rendered_yaml"] == "PASSWORD=plain"


def test_deploy_task_list_active_ids_only_running():
    """任务列表仅返回进行中的任务 ID，完成后即移除。"""
    manager = DeployTaskManager()
    running = manager.create_task("app_deploy", meta={"project_name": "a"})
    finished = manager.create_task("app_deploy", meta={"project_name": "b"})
    manager.complete_task(finished)

    assert manager.list_active_task_ids() == [running]

    manager.fail_task(running, "失败")
    assert manager.list_active_task_ids() == []


# ---------- /auth/verify SSRF 防护 ----------


@pytest.mark.asyncio
async def test_verify_url_blocks_link_local_metadata():
    """169.254.0.0/16 等 link-local/元数据地址被拒绝。"""
    service = OrchestrationService()
    rejection = await service._check_verify_url("http://169.254.169.254/latest/meta-data")
    assert rejection is not None
    assert "禁止访问" in rejection


@pytest.mark.asyncio
async def test_verify_url_allows_private_and_loopback():
    """内网与回环地址（含 Docker 网段）放行。"""
    service = OrchestrationService()
    assert await service._check_verify_url("http://192.168.1.10:8080") is None
    assert await service._check_verify_url("http://127.0.0.1:3000") is None
    assert await service._check_verify_url("http://172.17.0.2:8096") is None


@pytest.mark.asyncio
async def test_verify_url_invalid_host():
    """无法解析的主机名返回拒绝原因。"""
    service = OrchestrationService()
    rejection = await service._check_verify_url("http://nonexistent.invalid.local")
    assert rejection is not None
