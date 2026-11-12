"""飞牛影视客户端认证与账号验证测试。"""

import hashlib
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.services.apps.fnos_media import FnosMediaClient


def _resp(status_code: int, json_data=None, text: str | None = None) -> httpx.Response:
    """构造带 request 信息的 httpx Response。"""
    request = httpx.Request("POST", "http://fnos:5666")
    if text is not None:
        return httpx.Response(status_code, text=text, request=request)
    return httpx.Response(status_code, json=json_data, request=request)


def _client_with(responses: dict[str, httpx.Response]):
    """按请求路径返回预置响应，并记录每次调用。"""
    instance = AsyncMock()
    instance.__aenter__ = AsyncMock(return_value=instance)
    instance.__aexit__ = AsyncMock(return_value=None)

    async def request(method: str, url: str, **kwargs):
        path = httpx.URL(url).path
        return responses[path]

    instance.request = AsyncMock(side_effect=request)
    return instance


@pytest.mark.asyncio
async def test_verify_basic_logs_in_with_hashed_password():
    """v2 登录发送 SHA-256 密码，成功后返回 valid=True。"""
    mocked = _client_with(
        {
            "/v/api/v2/user/loginByPassword": _resp(
                200, json_data={"code": 0, "data": {"token": "token-1"}}
            )
        }
    )
    with patch(
        "app.services.apps.fnos_media.client.httpx.AsyncClient",
        return_value=mocked,
    ):
        result = await FnosMediaClient().verify_auth(
            {
                "url": "http://192.168.1.20:5666",
                "auth_type": "basic",
                "username": "Cacty",
                "password": "secret",
            }
        )

    assert result.valid is True
    assert "用户名/密码认证成功" in (result.message or "")
    mocked.request.assert_awaited_once()
    _, kwargs = mocked.request.call_args
    body = kwargs["content"]
    assert hashlib.sha256(b"secret").hexdigest() in body
    assert kwargs["headers"]["authx"].startswith("nonce=")


@pytest.mark.asyncio
async def test_verify_basic_wrong_password_does_not_fallback():
    """v2 返回密码错误时直接失败，不再回退 v1 重复提交凭据。"""
    mocked = _client_with(
        {
            "/v/api/v2/user/loginByPassword": _resp(
                200,
                json_data={
                    "code": -15,
                    "msg": "Password Incorrect",
                    "data": None,
                },
            )
        }
    )
    with patch(
        "app.services.apps.fnos_media.client.httpx.AsyncClient",
        return_value=mocked,
    ):
        result = await FnosMediaClient().verify_auth(
            {
                "url": "http://192.168.1.20:5666",
                "auth_type": "basic",
                "username": "Cacty",
                "password": "wrong",
            }
        )

    assert result.valid is False
    assert result.message == "用户名或密码错误"
    mocked.request.assert_awaited_once()


@pytest.mark.asyncio
async def test_verify_basic_falls_back_to_v1_when_v2_missing():
    """旧版服务端没有 v2 接口时回退 v1 明文登录。"""
    mocked = _client_with(
        {
            "/v/api/v2/user/loginByPassword": _resp(
                501, text='{"message":"Not Implemented, please update."}'
            ),
            "/v/api/v1/login": _resp(
                200, json_data={"code": 0, "data": {"token": "token-v1"}}
            ),
        }
    )
    with patch(
        "app.services.apps.fnos_media.client.httpx.AsyncClient",
        return_value=mocked,
    ):
        result = await FnosMediaClient().verify_auth(
            {
                "url": "http://192.168.1.20:5666/v",
                "auth_type": "basic",
                "username": "Cacty",
                "password": "secret",
            }
        )

    assert result.valid is True
    assert mocked.request.await_count == 2
    second_call = mocked.request.await_args_list[1]
    assert second_call.args[1] == "http://192.168.1.20:5666/v/api/v1/login"
    assert '"password":"secret"' in second_call.kwargs["content"]


@pytest.mark.asyncio
async def test_verify_api_key_uses_token_header():
    """API Key（登录令牌）通过 Authorization 头校验。"""
    mocked = _client_with(
        {
            "/v/api/v1/user/info": _resp(
                200, json_data={"code": 0, "data": {"username": "Cacty"}}
            )
        }
    )
    with patch(
        "app.services.apps.fnos_media.client.httpx.AsyncClient",
        return_value=mocked,
    ):
        result = await FnosMediaClient().verify_auth(
            {
                "url": "http://192.168.1.20:5666",
                "auth_type": "api_key",
                "api_key": "token-1",
            }
        )

    assert result.valid is True
    _, kwargs = mocked.request.call_args
    assert kwargs["headers"]["Authorization"] == "token-1"


@pytest.mark.asyncio
async def test_verify_api_key_rejected():
    """令牌无效时返回 valid=False。"""
    mocked = _client_with(
        {
            "/v/api/v1/user/info": _resp(
                200, json_data={"code": -2, "msg": "Auth Failed", "data": None}
            )
        }
    )
    with patch(
        "app.services.apps.fnos_media.client.httpx.AsyncClient",
        return_value=mocked,
    ):
        result = await FnosMediaClient().verify_auth(
            {
                "url": "http://192.168.1.20:5666",
                "auth_type": "api_key",
                "api_key": "bad",
            }
        )

    assert result.valid is False
    assert "认证失败" in (result.message or "")


@pytest.mark.asyncio
async def test_verify_none_checks_version_endpoint():
    """无认证时仅确认接口可达。"""
    mocked = _client_with(
        {
            "/v/api/v1/sys/version": _resp(
                200,
                json_data={
                    "code": 0,
                    "data": {"version": "0.9.8", "mediasrvVersion": "0.8.45"},
                },
            )
        }
    )
    with patch(
        "app.services.apps.fnos_media.client.httpx.AsyncClient",
        return_value=mocked,
    ):
        result = await FnosMediaClient().verify_auth(
            {"url": "http://192.168.1.20:5666", "auth_type": "none"}
        )

    assert result.valid is True
    assert "正常访问" in (result.message or "")


@pytest.mark.asyncio
async def test_provision_marks_valid_credentials():
    """账号验证成功即可视为已配置。"""
    mocked = _client_with(
        {
            "/v/api/v2/user/loginByPassword": _resp(
                200, json_data={"code": 0, "data": {"token": "token-1"}}
            )
        }
    )
    with patch(
        "app.services.apps.fnos_media.client.httpx.AsyncClient",
        return_value=mocked,
    ):
        result = await FnosMediaClient().provision(
            "http://192.168.1.20:5666", "Cacty", "secret"
        )

    assert result.ok is True
    assert result.message == "账号已验证通过"


@pytest.mark.asyncio
async def test_provision_reports_login_failure():
    """密码错误时返回失败原因，供 app_configs 展示。"""
    mocked = _client_with(
        {
            "/v/api/v2/user/loginByPassword": _resp(
                200,
                json_data={
                    "code": -15,
                    "msg": "Password Incorrect",
                    "data": None,
                },
            )
        }
    )
    with patch(
        "app.services.apps.fnos_media.client.httpx.AsyncClient",
        return_value=mocked,
    ):
        result = await FnosMediaClient().provision(
            "http://192.168.1.20:5666", "Cacty", "wrong"
        )

    assert result.ok is False
    assert result.message == "用户名或密码错误"
