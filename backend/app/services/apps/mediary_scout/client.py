"""巡影 Mediary Scout 应用客户端实现。

实例首次启动时需要创建站主账号（用户名 + 密码），多用户模式下通过
``POST /api/auth/register`` 完成；单用户模式（``MEDIA_TRACK_MULTI_USER=0``）
则只设置访问密码（``POST /api/auth/password``）。实例状态由
``GET /api/auth/bootstrap`` 返回的 ``needsClaim`` / ``singleUser`` /
``passwordSet`` 判断，已初始化时改用 ``POST /api/auth/login`` 验证统一凭据。
"""

from __future__ import annotations

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
from app.services.apps.registry import register_client


class MediaryScoutClient(AppClient):
    """巡影 Mediary Scout 网盘媒体库客户端。"""

    name = "mediary-scout"
    TIMEOUT = 10.0
    PROVISION_TIMEOUT = 300.0
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
        """等待服务就绪，创建站主账号或验证已有账号。"""
        base = str(url or "").strip().rstrip("/")
        if not base:
            return ProvisionResult(ok=False, message="访问地址不能为空")

        username = str(username or "").strip() or "admin"
        deadline = time.monotonic() + self.PROVISION_TIMEOUT
        last_error = "服务未就绪"

        while time.monotonic() < deadline:
            try:
                async with httpx.AsyncClient(
                    timeout=self.TIMEOUT,
                    follow_redirects=True,
                ) as client:
                    bootstrap = await self._bootstrap(client, base)
                    if bootstrap is None:
                        last_error = "认证接口未就绪"
                    elif bootstrap.get("singleUser"):
                        result = await self._provision_single_user(
                            client, base, password, bootstrap
                        )
                        if result is not None:
                            return result
                        last_error = "访问密码验证失败"
                    else:
                        if bootstrap.get("needsClaim"):
                            resp = await client.post(
                                f"{base}/api/auth/register",
                                json={"username": username, "password": password},
                            )
                            if resp.status_code < 400:
                                return ProvisionResult(
                                    ok=True,
                                    username=username,
                                    message="站主账号已创建并验证通过",
                                )
                            last_error = self._error_message(
                                resp, "创建站主账号失败"
                            )
                        if await self._login_ok(
                            client, base, username, password
                        ):
                            return ProvisionResult(
                                ok=True,
                                username=username,
                                message="账号已配置并验证通过",
                            )
                        if not bootstrap.get("needsClaim"):
                            last_error = "登录失败，实例可能已有其他账号"
            except httpx.RequestError as exc:
                last_error = f"无法连接到应用: {exc}"
            await asyncio.sleep(5)

        return ProvisionResult(
            ok=False,
            message=f"自动配置超时: {last_error}",
            username=username,
        )

    async def verify_auth(self, config: dict) -> AuthVerifyResult:
        """校验访问地址与认证信息。"""
        base = str(config.get("url") or "").strip().rstrip("/")
        if not base:
            return AuthVerifyResult(valid=False, message="访问地址不能为空")

        auth_type = config.get("auth_type") or "none"
        if auth_type in ("api_key", "apikey"):
            return AuthVerifyResult(
                valid=False, message="巡影不支持 API Key 认证"
            )
        if auth_type == "basic":
            username = str(config.get("username") or "")
            password = str(config.get("password") or "")
            if not username or not password:
                return AuthVerifyResult(
                    valid=False, message="用户名和密码不能为空"
                )

        try:
            async with httpx.AsyncClient(
                timeout=self.TIMEOUT,
                follow_redirects=True,
            ) as client:
                if auth_type == "basic":
                    if await self._login_ok(client, base, username, password):
                        return AuthVerifyResult(
                            valid=True, message="账号密码验证成功"
                        )
                    return AuthVerifyResult(
                        valid=False, message="账号密码验证失败"
                    )
                resp = await client.get(f"{base}/api/auth/bootstrap")
        except httpx.TimeoutException:
            return AuthVerifyResult(
                valid=False, message="连接超时，请检查地址与网络"
            )
        except httpx.RequestError as exc:
            return AuthVerifyResult(valid=False, message=f"无法连接到应用: {exc}")

        if resp.status_code == 200:
            return AuthVerifyResult(valid=True, message="服务可访问")
        return AuthVerifyResult(
            valid=False, message=f"服务响应异常: HTTP {resp.status_code}"
        )

    async def _provision_single_user(
        self,
        client: httpx.AsyncClient,
        base: str,
        password: str,
        bootstrap: dict,
    ) -> ProvisionResult | None:
        """单用户模式：设置访问密码后按密码登录验证。"""
        if not bootstrap.get("passwordSet"):
            resp = await client.post(
                f"{base}/api/auth/password",
                json={"password": password},
            )
            if resp.status_code < 400:
                return ProvisionResult(
                    ok=True, message="访问密码已设置并验证通过"
                )
            return None
        if await self._login_single_user(client, base, password):
            return ProvisionResult(ok=True, message="访问密码验证通过")
        return None

    async def _bootstrap(
        self, client: httpx.AsyncClient, base: str
    ) -> dict | None:
        """读取实例认证状态；服务未就绪时返回 None。"""
        resp = await client.get(f"{base}/api/auth/bootstrap")
        if resp.status_code != 200:
            return None
        try:
            payload = resp.json()
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    async def _login_ok(
        self,
        client: httpx.AsyncClient,
        base: str,
        username: str,
        password: str,
    ) -> bool:
        """用户名密码登录是否成功。"""
        if not username or not password:
            return False
        resp = await client.post(
            f"{base}/api/auth/login",
            json={"username": username, "password": password},
        )
        return resp.status_code < 400

    async def _login_single_user(
        self,
        client: httpx.AsyncClient,
        base: str,
        password: str,
    ) -> bool:
        """单用户模式仅校验访问密码。"""
        if not password:
            return False
        resp = await client.post(
            f"{base}/api/auth/login",
            json={"username": "", "password": password},
        )
        return resp.status_code < 400

    @staticmethod
    def _error_message(resp: httpx.Response, fallback: str) -> str:
        """从错误响应中提取可读信息。"""
        try:
            payload = resp.json()
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            message = payload.get("error") or payload.get("message")
            if message:
                return f"{fallback}: {message}"
        return f"{fallback}: HTTP {resp.status_code}"


register_client(MediaryScoutClient.name, MediaryScoutClient)
