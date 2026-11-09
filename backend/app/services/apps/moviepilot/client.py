"""MoviePilot 应用客户端实现。"""

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
from app.services.apps.moviepilot.inspection import MoviePilotInspectionMixin
from app.services.apps.moviepilot.integration import MoviePilotIntegrationMixin
from app.services.apps.registry import register_client


class MoviePilotClient(MoviePilotIntegrationMixin, MoviePilotInspectionMixin, AppClient):
    """MoviePilot 应用客户端。"""

    name = "moviepilot"
    TIMEOUT = 10.0
    # 首次启动会下载 200MB+ 的浏览器内核并初始化数据库，慢网络下可能
    # 超过 30 分钟；给足等待时间，避免刚部署完就判失败
    PROVISION_TIMEOUT = 3600.0
    # 登录被拒且没有旧密码可改密时，再等下去大概率不会恢复
    AUTH_FAILURE_GRACE_SECONDS = 120.0
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
        """等待 MoviePilot 就绪并完成管理员初始化与账号复验。

        账号密码通过 SUPERUSER/SUPERUSER_PASSWORD 环境变量注入；新版还需要
        调用首次初始化接口创建管理员。修复场景下用 previous_password 登录后
        调用用户更新接口改密，再复验。
        """
        base_url = str(url or "").rstrip("/")
        if not base_url:
            return ProvisionResult(ok=False, message="访问地址不能为空")

        deadline = time.monotonic() + self.PROVISION_TIMEOUT
        auth_deadline: float | None = None
        last_error = "服务未就绪"
        password_change_attempted = False

        while time.monotonic() < deadline:
            retry_delay = 5.0
            # 心跳读取该状态，避免进度里只有容器日志看不出卡在哪一步
            self.last_status = last_error
            try:
                async with httpx.AsyncClient(
                    timeout=self.TIMEOUT,
                    follow_redirects=True,
                ) as client:
                    initialization_resp = await client.get(
                        f"{base_url}/api/v1/login/initialization"
                    )
                    initialization_data = None
                    if initialization_resp.status_code == 200:
                        try:
                            initialization_payload = initialization_resp.json()
                            if isinstance(initialization_payload, dict):
                                initialization_data = initialization_payload.get("data")
                        except json.JSONDecodeError:
                            pass

                    if (
                        isinstance(initialization_data, dict)
                        and initialization_data.get("initialized") is False
                    ):
                        if not api_key:
                            return ProvisionResult(
                                ok=False,
                                message="缺少 API Token，无法初始化 MoviePilot 管理员",
                            )
                        initialize_resp = await client.post(
                            f"{base_url}/api/v1/login/initialization",
                            json={
                                "username": username,
                                "password": password,
                                "confirm_password": password,
                                "api_key": api_key,
                            },
                        )
                        initialize_result = None
                        if initialize_resp.status_code == 200:
                            try:
                                initialize_result = initialize_resp.json()
                            except json.JSONDecodeError:
                                pass
                        if (
                            isinstance(initialize_result, dict)
                            and initialize_result.get("success") is True
                        ):
                            last_error = "管理员初始化完成，等待登录复验"
                        elif initialize_resp.status_code == 409:
                            last_error = "管理员已初始化，等待登录复验"
                        else:
                            last_error = (
                                "管理员初始化失败: "
                                f"HTTP {initialize_resp.status_code}"
                            )
                        await asyncio.sleep(retry_delay)
                        continue
                    if initialization_resp.status_code not in (200, 404):
                        last_error = (
                            "初始化状态检查失败: "
                            f"HTTP {initialization_resp.status_code}"
                        )
                        await asyncio.sleep(retry_delay)
                        continue

                    token_resp = await client.post(
                        f"{base_url}/api/v1/login/access-token",
                        data={"username": username, "password": password},
                    )
                    if token_resp.status_code == 200:
                        access_token = token_resp.json().get("access_token")
                        if not access_token:
                            last_error = "登录接口未返回 Token"
                            await asyncio.sleep(retry_delay)
                            continue
                        user_resp = await client.get(
                            f"{base_url}/api/v1/user/",
                            headers={"Authorization": f"Bearer {access_token}"},
                        )
                        if user_resp.status_code == 200:
                            return ProvisionResult(
                                ok=True,
                                message="账号已配置并验证通过",
                                api_key=api_key,
                            )
                        last_error = f"Token 验证失败: HTTP {user_resp.status_code}"
                    elif token_resp.status_code in (401, 403):
                        if previous_password and not password_change_attempted:
                            password_change_attempted = True
                            change_error = await self._try_change_password(
                                client, base_url, username, previous_password, password
                            )
                            if change_error is None:
                                last_error = "密码已更新，等待复验"
                            else:
                                last_error = change_error
                        else:
                            last_error = "登录被拒绝，请检查管理员账号和密码"
                            if auth_deadline is None:
                                auth_deadline = (
                                    time.monotonic()
                                    + self.AUTH_FAILURE_GRACE_SECONDS
                                )
                            elif time.monotonic() >= auth_deadline:
                                return ProvisionResult(
                                    ok=False, message=last_error
                                )
                    elif token_resp.status_code == 429:
                        try:
                            retry_delay = max(
                                retry_delay,
                                float(token_resp.headers.get("Retry-After", "5")),
                            )
                        except ValueError:
                            retry_delay = 30.0
                        last_error = (
                            "登录尝试过于频繁，"
                            f"等待 {int(retry_delay)} 秒后重试"
                        )
                    else:
                        last_error = f"服务响应异常: HTTP {token_resp.status_code}"
            except httpx.RequestError as exc:
                last_error = f"无法连接到应用: {exc}"
            await asyncio.sleep(retry_delay)

        return ProvisionResult(ok=False, message=f"自动配置超时: {last_error}")

    async def _try_change_password(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        username: str,
        old_password: str,
        new_password: str,
    ) -> str | None:
        """用旧密码登录后通过用户更新接口改密，成功返回 None，失败返回原因。"""
        login_resp = await client.post(
            f"{base_url}/api/v1/login/access-token",
            data={"username": username, "password": old_password},
        )
        if login_resp.status_code != 200:
            return "旧密码登录失败，无法自动改密"
        token = login_resp.json().get("access_token")
        if not token:
            return "旧密码登录未返回 Token，无法自动改密"
        headers = {"Authorization": f"Bearer {token}"}

        users_resp = await client.get(f"{base_url}/api/v1/user/", headers=headers)
        if users_resp.status_code != 200:
            return f"查询用户列表失败: HTTP {users_resp.status_code}"
        try:
            users = users_resp.json()
        except json.JSONDecodeError:
            return "用户列表接口返回异常，无法自动改密"
        target = next((u for u in users if u.get("name") == username), None)
        if target is None:
            return f"未找到用户 {username}，无法自动改密"

        update_resp = await client.put(
            f"{base_url}/api/v1/user/",
            headers=headers,
            json={**target, "password": new_password},
        )
        if update_resp.status_code != 200:
            return f"修改密码请求失败: HTTP {update_resp.status_code}"
        try:
            result = update_resp.json()
        except json.JSONDecodeError:
            return "修改密码接口返回异常"
        if not result.get("success"):
            return f"修改密码失败: {result.get('message') or '未知原因'}"
        return None

    async def verify_auth(self, config: dict) -> AuthVerifyResult:
        """校验 MoviePilot 访问地址与认证配置。"""
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
                f"{base_url}/api/v1/user/",
                headers={"X-API-KEY": api_key},
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
            token_resp = await client.post(
                f"{base_url}/api/v1/login/access-token",
                data={"username": username, "password": password},
            )
            if token_resp.status_code in (401, 403):
                return AuthVerifyResult(valid=False, message="用户名或密码错误")
            token_resp.raise_for_status()

            try:
                token_data = token_resp.json()
            except json.JSONDecodeError:
                return AuthVerifyResult(valid=False, message="登录接口返回异常")

            access_token = token_data.get("access_token")
            if not access_token:
                return AuthVerifyResult(valid=False, message="登录接口未返回 Token")

            user_resp = await client.get(
                f"{base_url}/api/v1/user/",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if user_resp.status_code in (401, 403):
                return AuthVerifyResult(valid=False, message="Token 验证失败")
            user_resp.raise_for_status()
            return AuthVerifyResult(valid=True, message="用户名/密码认证成功")

    async def _verify_none(self, base_url: str) -> AuthVerifyResult:
        """无认证时仅确认应用可达。"""
        async with httpx.AsyncClient(timeout=self.TIMEOUT, follow_redirects=True) as client:
            response = await client.get(base_url)
            response.raise_for_status()
            return AuthVerifyResult(valid=True, message="应用可正常访问")


register_client(MoviePilotClient.name, MoviePilotClient)
