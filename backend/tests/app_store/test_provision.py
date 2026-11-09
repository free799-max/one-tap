"""部署账号自动配置（凭据注入与 provision）测试。"""

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — 注册所有模型到 Base.metadata
from app.database import Base
from app.models.system_config import SystemConfig
from app.services.apps.jellyfin import JellyfinClient
from app.services.apps.moviepilot import MoviePilotClient
from app.services.apps.qbittorrent import QBittorrentClient
from app.services.orchestration.credentials import prepare_app_credentials


def _resp(
    status_code: int,
    json_data=None,
    text: str | None = None,
    method: str = "GET",
    url: str = "http://test",
) -> httpx.Response:
    if text is not None:
        return httpx.Response(status_code, text=text, request=httpx.Request(method, url))
    return httpx.Response(status_code, json=json_data, request=httpx.Request(method, url))


def _mock_http():
    """返回 httpx.AsyncClient 的 mock 补丁与其共享实例。"""
    patcher = patch("httpx.AsyncClient")
    MockClient = patcher.start()
    instance = MockClient.return_value
    instance.__aenter__ = AsyncMock(return_value=instance)
    instance.__aexit__ = AsyncMock(return_value=None)
    instance.get = AsyncMock()
    instance.post = AsyncMock()
    instance.put = AsyncMock()
    return patcher, instance


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def _make_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    return engine, SessionLocal()


def _get_env(config: dict, env_key: str, key: str) -> str | None:
    for row in config.get(env_key) or []:
        if row.get("key") == key:
            return row.get("value")
    return None


# ---------- prepare_app_credentials ----------


@pytest.mark.asyncio
async def test_prepare_moviepilot_with_unified_password():
    """设置统一密码后，moviepilot env 注入统一凭据并生成 API_TOKEN。"""
    engine, session = await _make_session()
    try:
        session.add(
            SystemConfig(
                id=1,
                default_admin_username="nasadmin",
                default_admin_password="Unified#Pass1",
            )
        )
        await session.commit()

        config = {
            "moviepilot_env": [
                {"key": "SUPERUSER", "value": "admin"},
                {"key": "SUPERUSER_PASSWORD", "value": "random-frontend"},
                {"key": "TZ", "value": "Asia/Shanghai"},
            ]
        }
        creds = await prepare_app_credentials(session, "moviepilot", config)

        assert creds is not None
        assert creds["username"] == "nasadmin"
        assert creds["password"] == "Unified#Pass1"
        assert _get_env(config, "moviepilot_env", "SUPERUSER") == "nasadmin"
        assert (
            _get_env(config, "moviepilot_env", "SUPERUSER_PASSWORD")
            == "Unified#Pass1"
        )
        api_token = _get_env(config, "moviepilot_env", "API_TOKEN")
        assert api_token and len(api_token) >= 16
        assert creds["api_key"] == api_token
        # 无关 env 不受影响
        assert _get_env(config, "moviepilot_env", "TZ") == "Asia/Shanghai"
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_prepare_moviepilot_without_unified_password():
    """未设置统一密码时保留用户已填密码，仍生成 API_TOKEN。"""
    engine, session = await _make_session()
    try:
        session.add(
            SystemConfig(id=1, default_admin_username="admin", default_admin_password="")
        )
        await session.commit()

        config = {
            "moviepilot_env": [
                {"key": "SUPERUSER", "value": "admin"},
                {"key": "SUPERUSER_PASSWORD", "value": "user-filled"},
            ]
        }
        creds = await prepare_app_credentials(session, "moviepilot", config)

        assert creds is not None
        assert creds["password"] == "user-filled"
        assert (
            _get_env(config, "moviepilot_env", "SUPERUSER_PASSWORD") == "user-filled"
        )
        assert _get_env(config, "moviepilot_env", "API_TOKEN")
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_prepare_moviepilot_random_when_all_empty():
    """统一密码为空且用户未填时随机生成密码。"""
    engine, session = await _make_session()
    try:
        session.add(
            SystemConfig(id=1, default_admin_username="admin", default_admin_password="")
        )
        await session.commit()

        config = {"moviepilot_env": [{"key": "SUPERUSER_PASSWORD", "value": ""}]}
        creds = await prepare_app_credentials(session, "moviepilot", config)

        assert creds is not None
        assert creds["password"]
        assert (
            _get_env(config, "moviepilot_env", "SUPERUSER_PASSWORD")
            == creds["password"]
        )
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_prepare_qbittorrent_provision_only():
    """qbittorrent 为运行时装配应用：只返回凭据，不注入 env。"""
    engine, session = await _make_session()
    try:
        session.add(
            SystemConfig(
                id=1,
                default_admin_username="nasadmin",
                default_admin_password="Unified#Pass1",
            )
        )
        await session.commit()

        config = {"qbittorrent_env": [{"key": "TZ", "value": "Asia/Shanghai"}]}
        creds = await prepare_app_credentials(session, "qbittorrent", config)

        assert creds == {"username": "nasadmin", "password": "Unified#Pass1"}
        assert _get_env(config, "qbittorrent_env", "TZ") == "Asia/Shanghai"
        assert "password" not in {r["key"].lower() for r in config["qbittorrent_env"]}
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_prepare_unsupported_app_returns_none():
    """不支持自动配置的应用返回 None。"""
    engine, session = await _make_session()
    try:
        session.add(SystemConfig(id=1))
        await session.commit()
        assert await prepare_app_credentials(session, "nginx", {}) is None
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_prepare_email_app_uses_configured_domain():
    """需要邮箱登录的应用按统一后缀组装用户名。"""
    engine, session = await _make_session()
    try:
        session.add(
            SystemConfig(
                id=1,
                default_admin_username="nasadmin",
                default_admin_password="Unified#Pass1",
                default_email_domain="example.com",
            )
        )
        await session.commit()

        email_creds = await prepare_app_credentials(session, "komga", {})
        assert email_creds == {
            "username": "nasadmin@example.com",
            "password": "Unified#Pass1",
        }

        plain_creds = await prepare_app_credentials(session, "qbittorrent", {})
        assert plain_creds == {
            "username": "nasadmin",
            "password": "Unified#Pass1",
        }
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_prepare_email_app_keeps_explicit_email():
    """统一用户名本身已是邮箱时不重复追加后缀。"""
    engine, session = await _make_session()
    try:
        session.add(
            SystemConfig(
                id=1,
                default_admin_username="me@nas.local",
                default_admin_password="Unified#Pass1",
                default_email_domain="example.com",
            )
        )
        await session.commit()

        creds = await prepare_app_credentials(session, "ermao-library", {})
        assert creds is not None
        assert creds["username"] == "me@nas.local"
    finally:
        await session.close()
        await engine.dispose()


# ---------- qbittorrent provision ----------


@pytest.mark.asyncio
async def test_qbittorrent_provision_via_temp_password():
    """解析日志中的临时密码登录，setPreferences 改密后新凭据复验通过。"""
    patcher, instance = _mock_http()
    try:
        instance.post = AsyncMock(
            side_effect=[
                _resp(200, text="Fails.", method="POST"),  # 目标凭据登录失败
                _resp(200, text="Ok.", method="POST"),  # 临时密码登录成功
                _resp(200, text="{}", method="POST"),  # setPreferences
                _resp(200, text="Ok.", method="POST"),  # 新凭据复验成功
            ]
        )

        async def logs_reader() -> str:
            return (
                "The WebUI administrator username is: admin\n"
                "A temporary password is provided for this session: AbCdEf123\n"
            )

        result = await QBittorrentClient().provision(
            url="http://192.168.1.10:9005",
            username="nasadmin",
            password="Unified#Pass1",
            logs_reader=logs_reader,
        )

        assert result.ok is True
        assert instance.post.await_count == 4
        # 第三次调用为 setPreferences，且携带目标账号密码
        args, kwargs = instance.post.call_args_list[2]
        assert args[0].endswith("/api/v2/app/setPreferences")
        import json as _json

        payload = _json.loads(kwargs["data"]["json"])
        assert payload == {
            "web_ui_username": "nasadmin",
            "web_ui_password": "Unified#Pass1",
        }
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_qbittorrent_provision_via_temp_password_chinese():
    """中文界面日志中的临时密码同样可解析。"""
    patcher, instance = _mock_http()
    try:
        instance.post = AsyncMock(
            side_effect=[
                _resp(200, text="Fails.", method="POST"),  # 目标凭据登录失败
                _resp(200, text="Ok.", method="POST"),  # 临时密码登录成功
                _resp(200, text="{}", method="POST"),  # setPreferences
                _resp(200, text="Ok.", method="POST"),  # 新凭据复验成功
            ]
        )

        async def logs_reader() -> str:
            return (
                "WebUI 管理员用户名是：admin\n"
                "未设置 WebUI 管理员密码。为此会话提供了一个临时密码：G3jQmn3mG\n"
            )

        result = await QBittorrentClient().provision(
            url="http://192.168.1.10:9005",
            username="nasadmin",
            password="Unified#Pass1",
            logs_reader=logs_reader,
        )

        assert result.ok is True
        assert instance.post.await_count == 4
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_qbittorrent_provision_already_configured():
    """目标凭据已生效时直接成功，不调用 setPreferences。"""
    patcher, instance = _mock_http()
    try:
        instance.post = AsyncMock(
            return_value=_resp(200, text="Ok.", method="POST")
        )

        result = await QBittorrentClient().provision(
            url="http://192.168.1.10:9005",
            username="nasadmin",
            password="Unified#Pass1",
        )

        assert result.ok is True
        assert instance.post.await_count == 1
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_qbittorrent_provision_timeout():
    """服务一直不可达时超时返回失败。"""
    patcher, instance = _mock_http()
    try:
        instance.post = AsyncMock(
            side_effect=httpx.ConnectError("connection refused")
        )
        client = QBittorrentClient()
        client.PROVISION_TIMEOUT = 0.5

        with patch("app.services.apps.qbittorrent.client.asyncio.sleep", new=AsyncMock()):
            result = await client.provision(
                url="http://192.168.1.10:9005",
                username="a",
                password="b",
            )

        assert result.ok is False
        assert "自动配置超时" in (result.message or "")
    finally:
        patcher.stop()


# ---------- jellyfin provision ----------


@pytest.mark.asyncio
async def test_jellyfin_provision_wizard_flow():
    """向导流程：创建管理员 -> 完成向导 -> 登录换取 Token。"""
    patcher, instance = _mock_http()
    try:
        instance.get = AsyncMock(return_value=_resp(200, json_data={"Version": "10.9"}))
        instance.post = AsyncMock(
            side_effect=[
                _resp(204, text="", method="POST"),  # /Startup/User
                _resp(204, text="", method="POST"),  # /Startup/Complete
                _resp(200, json_data={"AccessToken": "jf-token"}, method="POST"),
            ]
        )

        result = await JellyfinClient().provision(
            url="http://192.168.1.10:9006",
            username="nasadmin",
            password="Unified#Pass1",
        )

        assert result.ok is True
        assert result.api_key == "jf-token"
        # 先 GET 触发服务端初始化默认用户，再 POST 设置账号密码
        get_urls = [c.args[0] for c in instance.get.call_args_list]
        assert get_urls[-1].endswith("/Startup/User")
        args, kwargs = instance.post.call_args_list[0]
        assert args[0].endswith("/Startup/User")
        assert kwargs["json"] == {"Name": "nasadmin", "Password": "Unified#Pass1"}
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_jellyfin_provision_wizard_completed_fallback():
    """向导标记已完成时跳过 Startup 接口，直接验证目标凭据。"""
    patcher, instance = _mock_http()
    try:
        instance.get = AsyncMock(
            return_value=_resp(
                200,
                json_data={"Version": "10.9", "StartupWizardCompleted": True},
            )
        )
        instance.post = AsyncMock(
            side_effect=[
                _resp(200, json_data={"AccessToken": "jf-token"}, method="POST"),
            ]
        )

        result = await JellyfinClient().provision(
            url="http://192.168.1.10:9006",
            username="nasadmin",
            password="Unified#Pass1",
        )

        assert result.ok is True
        assert result.api_key == "jf-token"
        # 不再调用 /Startup/User 或 /Startup/Complete
        urls = [c.args[0] for c in instance.post.call_args_list]
        assert not any(
            u.endswith("/Startup/User") or u.endswith("/Startup/Complete")
            for u in urls
        )
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_jellyfin_provision_completes_wizard_when_admin_exists():
    """管理员已存在但向导标记未写入时，登录成功后补完成向导。"""
    patcher, instance = _mock_http()
    try:
        instance.get = AsyncMock(
            return_value=_resp(
                200,
                json_data={"Version": "12.2", "StartupWizardCompleted": False},
            )
        )
        instance.post = AsyncMock(
            side_effect=[
                _resp(403, text="", method="POST"),  # /Startup/User 已有管理员
                _resp(200, json_data={"AccessToken": "jf-token"}, method="POST"),
                _resp(204, text="", method="POST"),  # /Startup/Complete
            ]
        )

        result = await JellyfinClient().provision(
            url="http://192.168.1.10:9006",
            username="nasadmin",
            password="Unified#Pass1",
        )

        assert result.ok is True
        urls = [c.args[0] for c in instance.post.call_args_list]
        assert urls[-1].endswith("/Startup/Complete")
    finally:
        patcher.stop()


# ---------- moviepilot provision ----------


@pytest.mark.asyncio
async def test_moviepilot_provision_verify_success():
    """登录成功且用户接口可访问时返回注入的 api_key。"""
    patcher, instance = _mock_http()
    try:
        instance.post = AsyncMock(
            return_value=_resp(
                200, json_data={"access_token": "mp-token"}, method="POST"
            )
        )
        instance.get = AsyncMock(return_value=_resp(200, json_data={"name": "admin"}))

        result = await MoviePilotClient().provision(
            url="http://192.168.1.10:9001",
            username="nasadmin",
            password="Unified#Pass1",
            api_key="mp-api-token-24chars-xxx",
        )

        assert result.ok is True
        assert result.api_key == "mp-api-token-24chars-xxx"
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_moviepilot_provision_initializes_admin():
    """新版首次启动时先创建管理员，再登录复验。"""
    patcher, instance = _mock_http()
    try:
        instance.get = AsyncMock(
            side_effect=[
                _resp(
                    200,
                    json_data={
                        "success": True,
                        "data": {
                            "initialized": False,
                            "configured_username": "nasadmin",
                        },
                    },
                ),
                _resp(
                    200,
                    json_data={"success": True, "data": {"initialized": True}},
                ),
                _resp(200, json_data={"name": "nasadmin"}),
            ]
        )
        instance.post = AsyncMock(
            side_effect=[
                _resp(200, json_data={"success": True}, method="POST"),
                _resp(
                    200,
                    json_data={"access_token": "mp-token"},
                    method="POST",
                ),
            ]
        )

        with patch("app.services.apps.moviepilot.client.asyncio.sleep", new=AsyncMock()):
            result = await MoviePilotClient().provision(
                url="http://192.168.1.10:9001",
                username="nasadmin",
                password="Unified#Pass1",
                api_key="mp-api-token-24chars-xxx",
            )

        assert result.ok is True
        initialize_call = instance.post.call_args_list[0]
        assert initialize_call.args[0].endswith("/api/v1/login/initialization")
        assert initialize_call.kwargs["json"] == {
            "username": "nasadmin",
            "password": "Unified#Pass1",
            "confirm_password": "Unified#Pass1",
            "api_key": "mp-api-token-24chars-xxx",
        }
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_moviepilot_provision_respects_login_retry_after():
    """登录限流时按 Retry-After 等待，避免持续请求加重限流。"""
    patcher, instance = _mock_http()
    try:
        limited_resp = _resp(429, json_data={"detail": "请求过于频繁"}, method="POST")
        limited_resp.headers["Retry-After"] = "17"
        instance.get = AsyncMock(
            side_effect=[
                _resp(
                    200,
                    json_data={"success": True, "data": {"initialized": True}},
                ),
                _resp(
                    200,
                    json_data={"success": True, "data": {"initialized": True}},
                ),
                _resp(200, json_data={"name": "nasadmin"}),
            ]
        )
        instance.post = AsyncMock(
            side_effect=[
                limited_resp,
                _resp(
                    200,
                    json_data={"access_token": "mp-token"},
                    method="POST",
                ),
            ]
        )

        with patch(
            "app.services.apps.moviepilot.client.asyncio.sleep", new=AsyncMock()
        ) as sleep:
            result = await MoviePilotClient().provision(
                url="http://192.168.1.10:9001",
                username="nasadmin",
                password="Unified#Pass1",
                api_key="mp-api-token-24chars-xxx",
            )

        assert result.ok is True
        sleep.assert_awaited_once_with(17.0)
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_moviepilot_provision_change_password_with_previous():
    """修复场景：目标密码被拒时用旧密码登录，经用户更新接口改密后复验通过。"""
    patcher, instance = _mock_http()
    try:
        instance.post = AsyncMock(
            side_effect=[
                _resp(401, method="POST"),  # 目标密码登录被拒
                _resp(200, json_data={"access_token": "old-token"}, method="POST"),
                _resp(200, json_data={"access_token": "new-token"}, method="POST"),
            ]
        )
        instance.get = AsyncMock(
            return_value=_resp(
                200,
                json_data=[
                    {
                        "id": 1,
                        "name": "nasadmin",
                        "email": "",
                        "is_active": True,
                        "is_superuser": True,
                    }
                ],
            )
        )
        instance.put = AsyncMock(
            return_value=_resp(200, json_data={"success": True}, method="PUT")
        )

        with patch("app.services.apps.moviepilot.client.asyncio.sleep", new=AsyncMock()):
            result = await MoviePilotClient().provision(
                url="http://192.168.1.10:9001",
                username="nasadmin",
                password="Unified#Pass1",
                api_key="mp-api-token",
                previous_password="old-random-16",
            )

        assert result.ok is True
        assert result.api_key == "mp-api-token"
        # 改密请求携带完整用户信息与目标新密码
        _, kwargs = instance.put.call_args
        assert kwargs["json"]["id"] == 1
        assert kwargs["json"]["name"] == "nasadmin"
        assert kwargs["json"]["password"] == "Unified#Pass1"
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_moviepilot_provision_login_rejected_timeout():
    """登录一直被拒绝时超时失败。"""
    patcher, instance = _mock_http()
    try:
        instance.post = AsyncMock(return_value=_resp(401, method="POST"))
        client = MoviePilotClient()
        client.PROVISION_TIMEOUT = 0.5

        with patch("app.services.apps.moviepilot.client.asyncio.sleep", new=AsyncMock()):
            result = await client.provision(
                url="http://192.168.1.10:9001",
                username="a",
                password="b",
            )

        assert result.ok is False
        assert "自动配置超时" in (result.message or "")
    finally:
        patcher.stop()


# ---------- 修复场景：密钥继承与旧密码复用 ----------


def test_inherit_stored_secrets_overrides_sensitive_env():
    """已存储的密钥类 env 覆盖新配置中的随机值，非密钥项不受影响。"""
    from app.services.orchestration.credentials import inherit_stored_secrets

    stored = {
        "postgresql_env": [
            {"key": "POSTGRES_PASSWORD", "value": "old-pg-pass"},
            {"key": "POSTGRES_USER", "value": "moviepilot"},
        ],
        "redis_env": [{"key": "REDIS_PASSWORD", "value": "old-redis-pass"}],
        "moviepilot_env": [
            {"key": "API_TOKEN", "value": "old-api-token-123456"},
            {"key": "SUPERUSER_PASSWORD", "value": "old-mp-pass"},
            {"key": "TZ", "value": "Asia/Shanghai"},
        ],
        "ports": [{"local_port": 9001, "container_port": 3000}],
    }
    config = {
        "postgresql_env": [{"key": "POSTGRES_PASSWORD", "value": "new-random"}],
        "moviepilot_env": [
            {"key": "SUPERUSER_PASSWORD", "value": "new-random"},
            {"key": "API_TOKEN", "value": "new-random-token"},
        ],
    }

    inherit_stored_secrets(stored, config)

    assert _get_env(config, "postgresql_env", "POSTGRES_PASSWORD") == "old-pg-pass"
    # 非密钥变量不继承
    assert _get_env(config, "postgresql_env", "POSTGRES_USER") is None
    # 存储配置中有而新配置没有的密钥数组整组补入
    assert _get_env(config, "redis_env", "REDIS_PASSWORD") == "old-redis-pass"
    assert _get_env(config, "moviepilot_env", "API_TOKEN") == "old-api-token-123456"
    assert _get_env(config, "moviepilot_env", "SUPERUSER_PASSWORD") == "old-mp-pass"
    # 非 env 数组（ports 等）不处理
    assert "ports" not in config


def test_inherit_stored_secrets_no_stored_config():
    """无已存储配置时不做任何修改。"""
    from app.services.orchestration.credentials import inherit_stored_secrets

    config = {"postgresql_env": [{"key": "POSTGRES_PASSWORD", "value": "keep"}]}
    inherit_stored_secrets(None, config)
    inherit_stored_secrets({}, config)
    assert _get_env(config, "postgresql_env", "POSTGRES_PASSWORD") == "keep"


@pytest.mark.asyncio
async def test_prepare_qbittorrent_fallback_password_reused():
    """未设统一密码时，provision 类应用复用上次已配置的密码。"""
    engine, session = await _make_session()
    try:
        session.add(SystemConfig(id=1))
        await session.commit()

        creds = await prepare_app_credentials(
            session, "jellyfin", {}, fallback_password="Stored#Pass1"
        )

        assert creds == {"username": "admin", "password": "Stored#Pass1"}
        assert "previous_password" not in creds
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_prepare_fallback_becomes_previous_when_unified_differs():
    """统一密码与已存储密码不一致时，已存储密码作为 previous_password 改密。"""
    engine, session = await _make_session()
    try:
        session.add(
            SystemConfig(
                id=1,
                default_admin_username="nasadmin",
                default_admin_password="Unified#Pass1",
            )
        )
        await session.commit()

        creds = await prepare_app_credentials(
            session, "qbittorrent", {}, fallback_password="Stored#Pass1"
        )

        assert creds == {
            "username": "nasadmin",
            "password": "Unified#Pass1",
            "previous_password": "Stored#Pass1",
        }
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_prepare_env_fallback_when_env_matches_target():
    """上一轮失败可能把 env 密码写成目标密码，此时应以落库旧密码登录改密。"""
    engine, session = await _make_session()
    try:
        session.add(
            SystemConfig(
                id=1,
                default_admin_username="admin",
                default_admin_password="Unified#Pass2",
            )
        )
        await session.commit()

        creds = await prepare_app_credentials(
            session,
            "moviepilot",
            {
                "moviepilot_env": [
                    {"key": "SUPERUSER_PASSWORD", "value": "Unified#Pass2"}
                ]
            },
            fallback_password="Unified#Pass1",
        )

        assert creds is not None
        assert creds["password"] == "Unified#Pass2"
        assert creds["previous_password"] == "Unified#Pass1"
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_qbittorrent_provision_via_previous_password():
    """配置卷复用时无临时密码输出，用 previous_password 登录后改为目标密码。"""
    patcher, instance = _mock_http()
    try:
        instance.post = AsyncMock(
            side_effect=[
                _resp(200, text="Fails.", method="POST"),  # 目标凭据登录失败
                _resp(200, text="Ok.", method="POST"),  # previous_password 登录成功
                _resp(200, text="{}", method="POST"),  # setPreferences
                _resp(200, text="Ok.", method="POST"),  # 新凭据复验成功
            ]
        )

        async def logs_reader() -> str:
            return "WebUI 已初始化，无临时密码\n"

        result = await QBittorrentClient().provision(
            url="http://192.168.1.10:9005",
            username="nasadmin",
            password="Unified#Pass1",
            logs_reader=logs_reader,
            previous_password="Stored#Pass1",
        )

        assert result.ok is True
        assert instance.post.await_count == 4
        # 第二次调用用旧凭据登录
        _, kwargs = instance.post.call_args_list[1]
        assert kwargs["data"] == {
            "username": "nasadmin",
            "password": "Stored#Pass1",
        }
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_jellyfin_provision_change_password_with_previous():
    """向导已完成且目标密码不符时，用旧密码登录改密后复验通过。"""
    patcher, instance = _mock_http()
    try:
        instance.get = AsyncMock(
            return_value=_resp(
                200,
                json_data={"Version": "10.9", "StartupWizardCompleted": False},
            )
        )
        instance.post = AsyncMock(
            side_effect=[
                _resp(403, text="", method="POST"),  # /Startup/User 已有管理员
                _resp(401, text="", method="POST"),  # 目标密码登录失败
                _resp(  # 旧密码登录成功
                    200,
                    json_data={"AccessToken": "old-token", "User": {"Id": "uid1"}},
                    method="POST",
                ),
                _resp(204, text="", method="POST"),  # /Users/uid1/Password 改密
                _resp(  # 新密码复验成功
                    200, json_data={"AccessToken": "new-token"}, method="POST"
                ),
                _resp(204, text="", method="POST"),  # /Startup/Complete
            ]
        )

        result = await JellyfinClient().provision(
            url="http://192.168.1.10:9006",
            username="nasadmin",
            password="Unified#Pass1",
            previous_password="Stored#Pass1",
        )

        assert result.ok is True
        assert result.api_key == "new-token"
        # 改密请求打到用户密码接口，携带新旧密码
        args, kwargs = instance.post.call_args_list[3]
        assert args[0].endswith("/Users/uid1/Password")
        assert kwargs["json"] == {
            "CurrentPw": "Stored#Pass1",
            "NewPw": "Unified#Pass1",
        }
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_jellyfin_provision_retries_wizard_when_service_not_ready():
    """首次启动时 /Startup/User 暂时 404 不能当成向导已完成，应继续重试。"""
    patcher, instance = _mock_http()
    try:
        instance.get = AsyncMock(
            return_value=_resp(200, json_data={"Version": "12.2.0"})
        )
        instance.post = AsyncMock(
            side_effect=[
                _resp(404, text="", method="POST"),  # 服务尚未初始化完成
                _resp(204, text="", method="POST"),  # 重试成功创建管理员
                _resp(204, text="", method="POST"),  # /Startup/Complete
                _resp(200, json_data={"AccessToken": "jf-token"}, method="POST"),
            ]
        )

        with patch(
            "app.services.apps.jellyfin.client.asyncio.sleep",
            new=AsyncMock(),
        ):
            result = await JellyfinClient().provision(
                url="http://192.168.1.10:9006",
                username="nasadmin",
                password="Unified#Pass1",
            )

        assert result.ok is True
        assert result.api_key == "jf-token"
        urls = [c.args[0] for c in instance.post.call_args_list]
        assert urls[0].endswith("/Startup/User")
        assert urls[1].endswith("/Startup/User")
        assert any(url.endswith("/Startup/Complete") for url in urls)
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_jellyfin_provision_retries_503_login():
    """Jellyfin 初始化期间登录接口 503 时应继续等待而不是抛异常。"""
    patcher, instance = _mock_http()
    try:
        instance.get = AsyncMock(
            return_value=_resp(
                200, json_data={"Version": "12.2.0", "StartupWizardCompleted": True}
            )
        )
        instance.post = AsyncMock(
            side_effect=[
                _resp(503, text="Service Unavailable", method="POST"),
                _resp(200, json_data={"AccessToken": "jf-token"}, method="POST"),
            ]
        )

        with patch(
            "app.services.apps.jellyfin.client.asyncio.sleep",
            new=AsyncMock(),
        ):
            result = await JellyfinClient().provision(
                url="http://192.168.1.10:9006",
                username="nasadmin",
                password="Unified#Pass1",
            )

        assert result.ok is True
        assert result.api_key == "jf-token"
        assert instance.post.await_count == 2
    finally:
        patcher.stop()
