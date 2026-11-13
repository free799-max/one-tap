"""Music Tag Web 部署后账号自动配置测试。"""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.services.apps.music_tag_web import MusicTagWebClient


def _resp(
    status_code: int,
    json_data=None,
    text: str | None = None,
    method: str = "POST",
    url: str = "http://test",
) -> httpx.Response:
    if text is not None:
        return httpx.Response(
            status_code, text=text, request=httpx.Request(method, url)
        )
    return httpx.Response(
        status_code, json=json_data, request=httpx.Request(method, url)
    )


def _token(ok: bool = True) -> dict:
    if not ok:
        return {"result": False, "msg": "用户名或密码错误"}
    return {
        "result": True,
        "token": "jwt-token",
        "user": {"id": 1, "username": "admin"},
    }


def _mock_http():
    patcher = patch("httpx.AsyncClient")
    MockClient = patcher.start()
    instance = MockClient.return_value
    instance.__aenter__ = AsyncMock(return_value=instance)
    instance.__aexit__ = AsyncMock(return_value=None)
    instance.get = AsyncMock()
    instance.post = AsyncMock()
    return patcher, instance


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.asyncio
async def test_music_tag_web_provision_uses_default_admin_and_resets_password():
    """首次部署用默认 admin/admin 登录，再通过容器命令改密并复验。"""
    patcher, instance = _mock_http()
    runner = AsyncMock(return_value=(0, "password-updated"))
    try:
        instance.post = AsyncMock(
            side_effect=[
                _resp(200, json_data=_token(False)),  # 目标密码登录失败
                _resp(200, json_data=_token(True)),  # 默认 admin/admin 登录成功
                _resp(200, json_data=_token(True)),  # 改密后复验
            ]
        )

        result = await MusicTagWebClient().provision(
            url="http://192.168.1.17:10001",
            username="admin",
            password="Unified#Pass1",
            command_runner=runner,
        )

        assert result.ok is True
        assert result.username == "admin"
        runner.assert_awaited_once()
        args, _ = runner.await_args
        assert args[0][:2] == ["python", "manage.py"]
        assert args[1]["MTW_TARGET_USERNAME"] == "admin"
        assert args[1]["MTW_TARGET_PASSWORD"] == "Unified#Pass1"
        assert args[2] == "/app"
        # 第三次登录使用统一密码
        _, login_kwargs = instance.post.call_args_list[2]
        assert login_kwargs["json"]["password"] == "Unified#Pass1"
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_music_tag_web_provision_already_configured():
    """目标密码已生效时直接成功，不执行容器命令。"""
    patcher, instance = _mock_http()
    runner = AsyncMock()
    try:
        instance.post = AsyncMock(return_value=_resp(200, json_data=_token(True)))

        result = await MusicTagWebClient().provision(
            url="http://192.168.1.17:10001",
            username="admin",
            password="Unified#Pass1",
            command_runner=runner,
        )

        assert result.ok is True
        assert instance.post.await_count == 1
        runner.assert_not_awaited()
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_music_tag_web_provision_previous_password():
    """统一密码变化时先用旧密码登录再改密。"""
    patcher, instance = _mock_http()
    runner = AsyncMock(return_value=(0, "password-updated"))
    try:
        instance.post = AsyncMock(
            side_effect=[
                _resp(200, json_data=_token(False)),  # 统一密码失败
                _resp(200, json_data=_token(True)),  # 旧密码登录成功
                _resp(200, json_data=_token(True)),  # 改密后复验
            ]
        )

        result = await MusicTagWebClient().provision(
            url="http://192.168.1.17:10001",
            username="admin",
            password="Unified#Pass1",
            previous_password="Stored#Pass1",
            command_runner=runner,
        )

        assert result.ok is True
        _, old_login = instance.post.call_args_list[1]
        assert old_login["json"]["password"] == "Stored#Pass1"
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_music_tag_web_provision_without_container_runner():
    """缺少容器执行能力且无法通过 API 改密时明确失败。"""
    patcher, instance = _mock_http()
    try:
        instance.post = AsyncMock(
            side_effect=[
                _resp(200, json_data=_token(False)),
                _resp(200, json_data=_token(True)),
            ]
        )

        result = await MusicTagWebClient().provision(
            url="http://192.168.1.17:10001",
            username="admin",
            password="Unified#Pass1",
        )

        assert result.ok is False
        assert "缺少容器执行能力" in (result.message or "")
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_music_tag_web_provision_resets_unknown_password():
    """数据卷密码已不可知时直接在容器内重置内置 admin。"""
    patcher, instance = _mock_http()
    runner = AsyncMock(return_value=(0, "password-updated"))
    try:
        instance.post = AsyncMock(
            side_effect=[
                _resp(200, json_data=_token(False)),  # 目标密码失败
                _resp(200, json_data=_token(False)),  # 默认 admin/admin 失败
                _resp(200, json_data=_token(True)),  # 重置后复验成功
            ]
        )

        result = await MusicTagWebClient().provision(
            url="http://192.168.1.17:10001",
            username="free9527",
            password="Unified#Pass1",
            command_runner=runner,
        )

        assert result.ok is True
        assert result.username == "admin"
        runner.assert_awaited_once()
        args, _ = runner.await_args
        assert args[1]["MTW_TARGET_USERNAME"] == "admin"
        assert args[1]["MTW_TARGET_PASSWORD"] == "Unified#Pass1"
    finally:
        patcher.stop()


@pytest.mark.asyncio
async def test_music_tag_web_verify_auth_uses_admin_account():
    """校验账号时忽略历史用户名，始终使用内置 admin。"""
    patcher, instance = _mock_http()
    try:
        instance.post = AsyncMock(return_value=_resp(200, json_data=_token(True)))

        result = await MusicTagWebClient().verify_auth(
            {
                "url": "http://192.168.1.17:10001",
                "auth_type": "basic",
                "username": "free9527",
                "password": "Unified#Pass1",
            }
        )

        assert result.valid is True
        _, login_kwargs = instance.post.call_args
        assert login_kwargs["json"]["username"] == "admin"
    finally:
        patcher.stop()
