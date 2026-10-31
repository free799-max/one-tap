"""Jellyfin 应用客户端实现。"""

import asyncio
import json
import time

import httpx

from app.services.apps.base import (
    AppClient,
    AuthVerifyResult,
    LogsReader,
    ProvisionResult,
)
from app.services.apps.jellyfin.inspection import JellyfinInspectionMixin
from app.services.apps.registry import register_client


class JellyfinClient(JellyfinInspectionMixin, AppClient):
    """Jellyfin 应用客户端。"""

    name = "jellyfin"
    TIMEOUT = 10.0
    # 首次启动要初始化数据库与元数据，慢机器上可能超过 10 分钟
    PROVISION_TIMEOUT = 1800.0
    # 向导已完成但账号密码不匹配时，无需继续等到超时
    AUTH_FAILURE_GRACE_SECONDS = 60.0
    supports_provision = True

    async def provision(
        self,
        url: str,
        username: str,
        password: str,
        api_key: str | None = None,
        logs_reader: LogsReader | None = None,
        previous_password: str | None = None,
        host_header_port: int | None = None,
    ) -> ProvisionResult:
        """把统一账号密码配置进 Jellyfin 并复验。

        首次启动时 Startup 向导 API 无需认证：创建管理员并完成向导，
        再登录换取 AccessToken 作为 api_key 返回。
        向导已完成（如修复场景、配置卷复用）时降级为直接验证目标凭据。
        """
        base_url = str(url or "").rstrip("/")
        if not base_url:
            return ProvisionResult(ok=False, message="访问地址不能为空")

        deadline = time.monotonic() + self.PROVISION_TIMEOUT
        auth_deadline: float | None = None
        last_error = "服务未就绪"
        wizard_handled = False
        wizard_completed = False

        while time.monotonic() < deadline:
            # 心跳读取该状态，避免进度里只有容器日志看不出卡在哪一步
            self.last_status = last_error
            try:
                async with httpx.AsyncClient(
                    timeout=self.TIMEOUT,
                    follow_redirects=True,
                ) as client:
                    public = await client.get(f"{base_url}/System/Info/Public")
                    if public.status_code != 200:
                        last_error = f"服务响应异常: HTTP {public.status_code}"
                        await asyncio.sleep(5)
                        continue
                    try:
                        public_info = public.json()
                    except json.JSONDecodeError:
                        public_info = {}

                    if not wizard_handled and not public_info.get(
                        "StartupWizardCompleted"
                    ):
                        # Jellyfin 12 首次启动时数据库里还没有用户，
                        # POST /Startup/User 会一直返回 404；先 GET 同一路径，
                        # 触发服务端创建默认用户（与 Web 向导的行为一致）
                        await client.get(f"{base_url}/Startup/User")
                        user_resp = await client.post(
                            f"{base_url}/Startup/User",
                            json={"Name": username, "Password": password},
                        )
                        if user_resp.status_code in (200, 204):
                            # 完成向导（失败不致命：管理员已创建）
                            complete_resp = await client.post(
                                f"{base_url}/Startup/Complete"
                            )
                            wizard_completed = complete_resp.status_code in (
                                200,
                                204,
                            )
                            wizard_handled = True
                        elif user_resp.status_code in (400, 401, 403):
                            # 已有管理员账号，降级为直接验证目标凭据
                            wizard_handled = True
                        else:
                            # 服务仍在初始化（5xx）等异常，不能据此认定向导已结束，
                            # 下一轮重试
                            last_error = (
                                f"创建管理员失败: HTTP {user_resp.status_code}"
                            )
                            await asyncio.sleep(5)
                            continue

                    login_resp = await client.post(
                        f"{base_url}/Users/AuthenticateByName",
                        headers={"Authorization": self._AUTHORIZATION_HEADER},
                        json={"Username": username, "Pw": password},
                    )
                    if login_resp.status_code == 200:
                        try:
                            login_data = login_resp.json()
                        except json.JSONDecodeError:
                            login_data = {}
                        token = login_data.get("AccessToken")
                        if not token:
                            last_error = "登录接口未返回 Token"
                            await asyncio.sleep(5)
                            continue
                        if (
                            not public_info.get("StartupWizardCompleted")
                            and not wizard_completed
                        ):
                            # 管理员已存在（复用配置卷）时向导标记可能仍未写入，
                            # 补一次完成请求，避免 Web UI 继续展示初始化向导
                            complete_resp = await client.post(
                                f"{base_url}/Startup/Complete"
                            )
                            wizard_completed = complete_resp.status_code in (
                                200,
                                204,
                            )
                        return ProvisionResult(
                            ok=True,
                            message="账号已配置并验证通过",
                            api_key=token,
                        )

                    if login_resp.status_code in (401, 403):
                        # 配置卷复用（修复）时服务当前密码是旧密码：
                        # 用旧密码登录后改为目标密码
                        if previous_password and previous_password != password:
                            changed = await self._change_password(
                                client, base_url, username, previous_password, password
                            )
                            if changed:
                                auth_deadline = None
                                continue
                        last_error = "登录失败，服务可能已有其他管理员账号"
                        # 向导已完成仍登录失败，再等下去大概率也不会恢复
                        if auth_deadline is None:
                            auth_deadline = (
                                time.monotonic() + self.AUTH_FAILURE_GRACE_SECONDS
                            )
                        elif time.monotonic() >= auth_deadline:
                            return ProvisionResult(ok=False, message=last_error)
                    elif login_resp.status_code >= 500:
                        # 服务启动中（Jellyfin 初始化时会短暂返回 503），继续等待
                        last_error = f"服务启动中: HTTP {login_resp.status_code}"
                    else:
                        last_error = (
                            f"登录接口响应异常: HTTP {login_resp.status_code}"
                        )
            except httpx.RequestError as exc:
                last_error = f"无法连接到应用: {exc}"
            await asyncio.sleep(5)

        return ProvisionResult(ok=False, message=f"自动配置超时: {last_error}")

    async def _change_password(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        username: str,
        current_password: str,
        new_password: str,
    ) -> bool:
        """用当前密码登录后修改为目标密码，成功返回 True。"""
        try:
            login_resp = await client.post(
                f"{base_url}/Users/AuthenticateByName",
                headers={"Authorization": self._AUTHORIZATION_HEADER},
                json={"Username": username, "Pw": current_password},
            )
            if login_resp.status_code != 200:
                return False
            login_data = login_resp.json()
            token = login_data.get("AccessToken")
            user_id = (login_data.get("User") or {}).get("Id")
            if not token or not user_id:
                return False
            auth_headers = self._token_headers(token)
            resp = await client.post(
                f"{base_url}/Users/{user_id}/Password",
                headers=auth_headers,
                json={"CurrentPw": current_password, "NewPw": new_password},
            )
            return resp.status_code in (200, 204)
        except (httpx.RequestError, json.JSONDecodeError):
            return False

    # Jellyfin/Emby 统一要求的设备信息头（持久化 Token 使用该设备）
    _AUTHORIZATION_HEADER = (
        'MediaBrowser Client="OneTap", Device="NAS", DeviceId="onetap", Version="1.0.0"'
    )
    # 持久化 Token 使用的设备号
    _API_DEVICE_ID = "onetap"
    # 仅用于校验账号密码的独立设备号：Jellyfin 12 中同设备重新登录会顶掉旧 Token，
    # 校验必须使用不同设备，避免把已保存的 API Token 挤下线
    _INSPECT_DEVICE_ID = "onetap-inspect"
    # 手动“验证认证”使用的独立设备号（校验后不保存 Token）
    _VERIFY_DEVICE_ID = "onetap-verify"

    async def verify_auth(self, config: dict) -> AuthVerifyResult:
        """校验 Jellyfin 访问地址与认证配置。"""
        base_url = str(config.get("url") or "").rstrip("/")
        if not base_url:
            return AuthVerifyResult(valid=False, message="访问地址不能为空")

        auth_type = config.get("auth_type") or "none"

        try:
            if auth_type in ("api_key", "apikey"):
                return await self._verify_api_key(base_url, config.get("api_key", ""))
            if auth_type == "basic":
                return await self._verify_basic(
                    base_url,
                    config.get("username", ""),
                    config.get("password", ""),
                )
            return await self._verify_none(base_url)
        except httpx.TimeoutException:
            return AuthVerifyResult(valid=False, message="连接超时，请检查地址与网络")
        except httpx.RequestError as exc:
            return AuthVerifyResult(valid=False, message=f"无法连接到应用: {exc}")

    async def _verify_api_key(self, base_url: str, api_key: str) -> AuthVerifyResult:
        """使用 API Key 验证。"""
        if not api_key:
            return AuthVerifyResult(valid=False, message="API Key 不能为空")

        async with httpx.AsyncClient(timeout=self.TIMEOUT, follow_redirects=True) as client:
            response = await client.get(
                f"{base_url}/System/Info",
                headers=self._token_headers(api_key),
            )
            if response.status_code in (401, 403):
                return AuthVerifyResult(valid=False, message="API Key 认证失败")
            response.raise_for_status()
            return AuthVerifyResult(valid=True, message="API Key 认证成功")

    async def _verify_basic(
        self,
        base_url: str,
        username: str,
        password: str,
    ) -> AuthVerifyResult:
        """使用用户名/密码验证。"""
        if not username or not password:
            return AuthVerifyResult(valid=False, message="用户名和密码不能为空")

        async with httpx.AsyncClient(timeout=self.TIMEOUT, follow_redirects=True) as client:
            login_resp = await client.post(
                f"{base_url}/Users/AuthenticateByName",
                headers={
                    "Authorization": self._device_header(self._VERIFY_DEVICE_ID)
                },
                json={"Username": username, "Pw": password},
            )
            if login_resp.status_code in (401, 403):
                return AuthVerifyResult(valid=False, message="用户名或密码错误")
            login_resp.raise_for_status()

            try:
                login_data = login_resp.json()
            except json.JSONDecodeError:
                return AuthVerifyResult(valid=False, message="登录接口返回异常")

            access_token = login_data.get("AccessToken")
            if not access_token:
                return AuthVerifyResult(valid=False, message="登录接口未返回 Token")

            info_resp = await client.get(
                f"{base_url}/System/Info",
                headers=self._token_headers(access_token),
            )
            if info_resp.status_code in (401, 403):
                return AuthVerifyResult(valid=False, message="Token 验证失败")
            info_resp.raise_for_status()
            return AuthVerifyResult(valid=True, message="用户名/密码认证成功")

    async def _verify_none(self, base_url: str) -> AuthVerifyResult:
        """无认证时仅确认应用可达。"""
        async with httpx.AsyncClient(timeout=self.TIMEOUT, follow_redirects=True) as client:
            response = await client.get(f"{base_url}/System/Info/Public")
            response.raise_for_status()
            return AuthVerifyResult(valid=True, message="应用可正常访问")


register_client(JellyfinClient.name, JellyfinClient)
