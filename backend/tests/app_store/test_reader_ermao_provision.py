"""Reader / 二毛读书账号自动配置测试。"""

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — 注册所有模型到 Base.metadata
from app.database import Base
from app.models.system_config import SystemConfig
from app.services.apps.ermao_library import ErmaoLibraryClient
from app.services.apps.reader import ReaderAppClient, ReaderSession
from app.services.orchestration.credentials import prepare_app_credentials


def _resp(
    status_code: int,
    json_data=None,
    method: str = "GET",
    url: str = "http://test",
) -> httpx.Response:
    return httpx.Response(
        status_code, json=json_data, request=httpx.Request(method, url)
    )


def _mock_http(module: str):
    patcher = patch(module)
    MockClient = patcher.start()
    instance = MockClient.return_value
    instance.__aenter__ = AsyncMock(return_value=instance)
    instance.__aexit__ = AsyncMock(return_value=None)
    instance.get = AsyncMock()
    instance.post = AsyncMock()
    instance.request = AsyncMock()
    instance.aclose = AsyncMock()
    return patcher, instance


def _get_env(config: dict, env_key: str, key: str) -> str | None:
    for row in config.get(env_key) or []:
        if row.get("key") == key:
            return row.get("value")
    return None


async def _make_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    return engine, SessionLocal()


# ---------- Reader ----------


@pytest.mark.asyncio
async def test_reader_provision_creates_admin_and_returns_token():
    patcher, instance = _mock_http(
        "app.services.apps.reader.app_client.httpx.AsyncClient"
    )
    try:
        instance.post = AsyncMock(
            return_value=_resp(
                200,
                json_data={
                    "isSuccess": True,
                    "data": {"accessToken": "admin:reader-token"},
                },
                method="POST",
            )
        )

        result = await ReaderAppClient().provision(
            url="http://192.168.1.17:9013",
            username="admin",
            password="Unified#Pass1",
        )

        assert result.ok is True
        assert result.username == "admin"
        assert result.api_key is None
        _, kwargs = instance.post.call_args
        assert kwargs["json"] == {
            "username": "admin",
            "password": "Unified#Pass1",
            "isLogin": False,
        }
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_reader_provision_logs_in_existing_admin():
    patcher, instance = _mock_http(
        "app.services.apps.reader.app_client.httpx.AsyncClient"
    )
    try:
        instance.post = AsyncMock(
            side_effect=[
                _resp(
                    200,
                    json_data={
                        "isSuccess": False,
                        "errorMsg": "用户名已被占用",
                    },
                    method="POST",
                ),
                _resp(
                    200,
                    json_data={
                        "isSuccess": True,
                        "data": {"accessToken": "admin:reader-token"},
                    },
                    method="POST",
                ),
            ]
        )

        result = await ReaderAppClient().provision(
            url="http://192.168.1.17:9013",
            username="admin",
            password="Unified#Pass1",
        )

        assert result.ok is True
        assert result.api_key is None
        assert instance.post.await_count == 2
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_reader_provision_resets_password_with_previous():
    patcher, instance = _mock_http(
        "app.services.apps.reader.app_client.httpx.AsyncClient"
    )
    try:
        instance.post = AsyncMock(
            side_effect=[
                _resp(
                    200,
                    json_data={
                        "isSuccess": False,
                        "errorMsg": "用户名已被占用",
                    },
                    method="POST",
                ),
                _resp(
                    200,
                    json_data={"isSuccess": False, "errorMsg": "密码错误"},
                    method="POST",
                ),
                _resp(
                    200,
                    json_data={
                        "isSuccess": True,
                        "data": {"accessToken": "admin:old"},
                    },
                    method="POST",
                ),
                _resp(200, json_data={"isSuccess": True}, method="POST"),
                _resp(
                    200,
                    json_data={
                        "isSuccess": True,
                        "data": {"accessToken": "admin:new"},
                    },
                    method="POST",
                ),
            ]
        )

        result = await ReaderAppClient().provision(
            url="http://192.168.1.17:9013",
            username="admin",
            password="Unified#Pass2",
            previous_password="Unified#Pass1",
        )

        assert result.ok is True
        assert result.api_key is None
        reset_call = instance.post.call_args_list[3]
        assert reset_call.args[0].endswith("/reader3/resetPassword")
        assert reset_call.kwargs["json"] == {
            "username": "admin",
            "password": "Unified#Pass2",
        }
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_reader_provision_reports_password_mismatch():
    patcher, instance = _mock_http(
        "app.services.apps.reader.app_client.httpx.AsyncClient"
    )
    try:
        instance.post = AsyncMock(
            side_effect=[
                _resp(
                    200,
                    json_data={
                        "isSuccess": False,
                        "errorMsg": "用户名已被占用",
                    },
                    method="POST",
                ),
                _resp(
                    200,
                    json_data={"isSuccess": False, "errorMsg": "密码错误"},
                    method="POST",
                ),
            ]
        )

        result = await ReaderAppClient().provision(
            url="http://192.168.1.17:9013",
            username="admin",
            password="Unified#Pass1",
        )

        assert result.ok is False
        assert "密码与统一密码不一致" in (result.message or "")
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_reader_verify_auth_modes():
    patcher, instance = _mock_http(
        "app.services.apps.reader.app_client.httpx.AsyncClient"
    )
    try:
        instance.post = AsyncMock(
            return_value=_resp(
                200, json_data={"isSuccess": True}, method="POST"
            )
        )
        ok = await ReaderAppClient().verify_auth(
            {
                "url": "http://192.168.1.17:9013",
                "auth_type": "basic",
                "username": "admin",
                "password": "Unified#Pass1",
            }
        )
        assert ok.valid is True

        instance.get = AsyncMock(
            return_value=_resp(
                200,
                json_data={
                    "isSuccess": True,
                    "data": {"secure": True},
                },
            )
        )
        none = await ReaderAppClient().verify_auth(
            {
                "url": "http://192.168.1.17:9013",
                "auth_type": "none",
            }
        )
        assert none.valid is False
        assert "登录认证" in (none.message or "")

        instance.get = AsyncMock(
            return_value=_resp(
                200,
                json_data={
                    "isSuccess": False,
                    "errorMsg": "请登录后使用",
                },
            )
        )
        invalid_token = await ReaderAppClient().verify_auth(
            {
                "url": "http://192.168.1.17:9013",
                "auth_type": "api_key",
                "api_key": "admin:bad-token",
            }
        )
        assert invalid_token.valid is False
        assert "请登录后使用" in (invalid_token.message or "")
    finally:
        patcher.stop()


# ---------- 二毛读书 ----------


@pytest.mark.asyncio
async def test_ermao_provision_initializes_admin():
    patcher, instance = _mock_http(
        "app.services.apps.reader.client.httpx.AsyncClient"
    )
    try:
        instance.request = AsyncMock(
            side_effect=[
                _resp(
                    200,
                    json_data={"ok": True, "data": {"initialized": False}},
                ),
                _resp(201, json_data={"ok": True, "data": {}}, method="POST"),
            ]
        )

        result = await ErmaoLibraryClient().provision(
            url="http://192.168.1.17:9014",
            username="admin",
            password="Unified#Pass1",
        )

        assert result.ok is True
        assert result.username == "admin@example.com"
        setup_call = instance.request.call_args_list[1]
        assert setup_call.args[:2] == ("POST", "/api/auth/setup")
        assert setup_call.kwargs["json"]["email"] == "admin@example.com"
        assert setup_call.kwargs["json"]["password"] == "Unified#Pass1"
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_ermao_provision_logs_in_existing_admin():
    patcher, instance = _mock_http(
        "app.services.apps.reader.client.httpx.AsyncClient"
    )
    try:
        instance.request = AsyncMock(
            side_effect=[
                _resp(
                    200,
                    json_data={"ok": True, "data": {"initialized": True}},
                ),
                _resp(200, json_data={"ok": True, "data": {}}, method="POST"),
            ]
        )

        result = await ErmaoLibraryClient().provision(
            url="http://192.168.1.17:9014",
            username="admin@example.com",
            password="Unified#Pass1",
        )

        assert result.ok is True
        assert result.username == "admin@example.com"
        login_call = instance.request.call_args_list[1]
        assert login_call.args[:2] == ("POST", "/api/auth/login")
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_ermao_provision_changes_password_with_previous():
    patcher, instance = _mock_http(
        "app.services.apps.reader.client.httpx.AsyncClient"
    )
    try:
        instance.request = AsyncMock(
            side_effect=[
                _resp(
                    200,
                    json_data={"ok": True, "data": {"initialized": True}},
                ),
                _resp(
                    401,
                    json_data={
                        "ok": False,
                        "error": {"message": "邮箱或密码不正确"},
                    },
                    method="POST",
                ),
                _resp(
                    200,
                    json_data={"ok": True, "data": {}},
                    method="POST",
                ),
                _resp(200, json_data={"ok": True, "data": {}}, method="PATCH"),
                _resp(
                    200,
                    json_data={"ok": True, "data": {}},
                    method="POST",
                ),
            ]
        )

        result = await ErmaoLibraryClient().provision(
            url="http://192.168.1.17:9014",
            username="admin@example.com",
            password="Unified#Pass2",
            previous_password="Unified#Pass1",
        )

        assert result.ok is True
        change_call = instance.request.call_args_list[3]
        assert change_call.args[:2] == (
            "PATCH",
            "/api/auth/account/password",
        )
        assert change_call.kwargs["json"] == {
            "currentPassword": "Unified#Pass1",
            "newPassword": "Unified#Pass2",
        }
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_ermao_provision_rejects_short_password():
    result = await ErmaoLibraryClient().provision(
        url="http://192.168.1.17:9014",
        username="admin",
        password="short",
    )

    assert result.ok is False
    assert "至少需要 10 位" in (result.message or "")


# ---------- 部署凭据 ----------


@pytest.mark.asyncio
async def test_prepare_reader_credentials_enables_secure_mode():
    engine, session = await _make_session()
    try:
        session.add(
            SystemConfig(id=1, default_admin_username="admin")
        )
        await session.commit()

        config = {
            "reader_env": [
                {"key": "TZ", "value": "Asia/Shanghai"},
            ]
        }
        creds = await prepare_app_credentials(session, "reader", config)

        assert creds is not None
        assert creds["username"] == "admin"
        assert creds["password"]
        assert _get_env(config, "reader_env", "READER_APP_SECURE") == "true"
        assert _get_env(config, "reader_env", "READER_APP_SECUREKEY") == creds["password"]
        # Reader 通过登录接口创建用户，不写入用户名环境变量
        assert _get_env(config, "reader_env", "READER_APP_USERNAME") is None
        assert _get_env(config, "reader_env", "TZ") == "Asia/Shanghai"
    finally:
        await session.close()
        await engine.dispose()


# ---------- 访问令牌 ----------


@pytest.mark.asyncio
async def test_reader_session_relogins_and_uses_access_token():
    """Reader 每次登录轮换令牌，有账号密码时应重新登录并带上新令牌。"""
    patcher, instance = _mock_http(
        "app.services.apps.reader.client.httpx.AsyncClient"
    )
    try:
        instance.post = AsyncMock(
            return_value=_resp(
                200,
                json_data={
                    "isSuccess": True,
                    "data": {"accessToken": "admin:new-token"},
                },
                method="POST",
            )
        )
        instance.get = AsyncMock(
            return_value=_resp(
                200, json_data={"isSuccess": True, "data": []}
            )
        )

        async with ReaderSession(
            "http://192.168.1.17:9013",
            "admin:stale-token",
            username="admin",
            password="Unified#Pass1",
        ) as session:
            shelf = await session.shelf()

        assert shelf == []
        assert session.token == "admin:new-token"
        instance.post.assert_awaited_once()
        _, get_kwargs = instance.get.call_args
        assert get_kwargs["params"]["accessToken"] == "admin:new-token"
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_prepare_reader_credentials_carries_previous_password():
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
            "reader",
            {"reader_env": []},
            fallback_password="Unified#Pass1",
        )

        assert creds is not None
        assert creds["password"] == "Unified#Pass2"
        assert creds["previous_password"] == "Unified#Pass1"
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_prepare_ermao_credentials_returns_account():
    engine, session = await _make_session()
    try:
        session.add(
            SystemConfig(id=1, default_admin_username="admin")
        )
        await session.commit()

        creds = await prepare_app_credentials(session, "ermao-library", {})

        assert creds is not None
        assert creds["username"] == "admin"
        assert creds["password"]
    finally:
        await session.close()
        await engine.dispose()
