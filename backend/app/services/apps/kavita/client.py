"""Kavita 应用客户端实现。

Kavita 首次启动通过 ``POST /api/Account/register`` 创建管理员，
登录接口 ``POST /api/Account/login`` 会返回可用于第三方访问的 API Key。
"""

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


class KavitaClient(AppClient):
    """Kavita 漫画与电子书服务器客户端。"""

    name = "kavita"
    TIMEOUT = 10.0
    # 首次启动要执行数据库迁移并从 GitHub 下载邮件模板，网络较慢时
    # 单个模板最长约 100 秒，全部完成前不会监听端口
    PROVISION_TIMEOUT = 900.0
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
        """注册首个管理员并返回登录接口生成的 API Key。"""
        base_url = str(url or "").rstrip("/")
        if not base_url:
            return ProvisionResult(ok=False, message="访问地址不能为空")

        deadline = time.monotonic() + self.PROVISION_TIMEOUT
        last_error = "等待服务监听端口（首次启动需完成数据库迁移）"
        register_attempted = False

        while time.monotonic() < deadline:
            # 心跳读取该状态，避免进度里只显示容器日志（Kavita 的迁移日志
            # 会打上 Fatal 级别但明确标注 This is not an error）
            self.last_status = last_error
            try:
                async with httpx.AsyncClient(
                    timeout=self.TIMEOUT,
                    follow_redirects=True,
                ) as client:
                    health = await client.get(f"{base_url}/api/health")
                    if health.status_code != 200:
                        last_error = f"服务启动中: HTTP {health.status_code}"
                        await asyncio.sleep(5)
                        continue

                    login = await self._login(
                        client, base_url, username, password
                    )
                    if login is not None:
                        return ProvisionResult(
                            ok=True,
                            message="账号已配置并验证通过",
                            api_key=login,
                            username=username,
                        )

                    if not register_attempted:
                        resp = await client.post(
                            f"{base_url}/api/Account/register",
                            json={"username": username, "password": password},
                        )
                        if resp.status_code < 400:
                            register_attempted = True
                            login = await self._login(
                                client, base_url, username, password
                            )
                            if login is not None:
                                return ProvisionResult(
                                    ok=True,
                                    message="账号已配置并验证通过",
                                    api_key=login,
                                    username=username,
                                )
                            last_error = "创建管理员后登录失败"
                        elif resp.status_code in (400, 401, 403):
                            # 已有管理员或凭据不合规，重试注册没有意义
                            register_attempted = True
                            last_error = (
                                f"创建管理员失败: HTTP {resp.status_code}"
                            )
                        else:
                            # 5xx/429 等服务端暂时不可用：保留本次机会，
                            # 下一轮继续尝试注册
                            last_error = (
                                f"创建管理员失败: HTTP {resp.status_code}"
                            )
                    else:
                        last_error = "已有管理员账号，但统一密码登录失败"

                    if previous_password and previous_password != password:
                        old_key = await self._login(
                            client, base_url, username, previous_password
                        )
                        if old_key:
                            last_error = (
                                "服务已有管理员且密码与统一密码不一致，"
                                "请手动重置密码或重新部署数据卷"
                            )
            except httpx.RequestError as exc:
                last_error = f"无法连接到应用（服务启动中）: {exc}"
            await asyncio.sleep(5)

        return ProvisionResult(ok=False, message=f"自动配置超时: {last_error}")

    async def _login(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        username: str,
        password: str,
    ) -> str | None:
        """登录并返回 API Key。"""
        resp = await client.post(
            f"{base_url}/api/Account/login",
            json={"username": username, "password": password},
        )
        if resp.status_code != 200:
            return None
        try:
            payload = resp.json()
        except json.JSONDecodeError:
            return None
        key = payload.get("apiKey")
        return str(key) if key else None

    async def verify_auth(self, config: dict) -> AuthVerifyResult:
        """校验访问地址与认证信息。"""
        base_url = str(config.get("url") or "").rstrip("/")
        if not base_url:
            return AuthVerifyResult(valid=False, message="访问地址不能为空")

        auth_type = config.get("auth_type") or "none"
        try:
            if auth_type == "basic":
                return await self._verify_basic(
                    base_url,
                    config.get("username", ""),
                    config.get("password", ""),
                )
            if auth_type in ("api_key", "apikey"):
                return await self._verify_api_key(
                    base_url, config.get("api_key", "")
                )
            return await self._verify_none(base_url)
        except httpx.TimeoutException:
            return AuthVerifyResult(valid=False, message="连接超时，请检查地址与网络")
        except httpx.RequestError as exc:
            return AuthVerifyResult(valid=False, message=f"无法连接到应用: {exc}")

    async def _verify_basic(
        self,
        base_url: str,
        username: str,
        password: str,
    ) -> AuthVerifyResult:
        if not username or not password:
            return AuthVerifyResult(valid=False, message="用户名和密码不能为空")
        async with httpx.AsyncClient(
            timeout=self.TIMEOUT, follow_redirects=True
        ) as client:
            key = await self._login(client, base_url, username, password)
        if key:
            return AuthVerifyResult(valid=True, message="账号密码验证成功")
        return AuthVerifyResult(valid=False, message="账号密码验证失败")

    async def _verify_api_key(
        self,
        base_url: str,
        api_key: str,
    ) -> AuthVerifyResult:
        if not api_key:
            return AuthVerifyResult(valid=False, message="API Key 不能为空")
        async with httpx.AsyncClient(
            timeout=self.TIMEOUT, follow_redirects=True
        ) as client:
            resp = await client.get(
                f"{base_url}/api/Account",
                headers={"x-api-key": api_key},
            )
        if resp.status_code == 200:
            return AuthVerifyResult(valid=True, message="API Key 认证成功")
        if resp.status_code in (401, 403):
            return AuthVerifyResult(valid=False, message="API Key 认证失败")
        return AuthVerifyResult(
            valid=False, message=f"服务响应异常: HTTP {resp.status_code}"
        )

    async def _verify_none(self, base_url: str) -> AuthVerifyResult:
        async with httpx.AsyncClient(
            timeout=self.TIMEOUT, follow_redirects=True
        ) as client:
            resp = await client.get(f"{base_url}/api/health")
        if resp.status_code == 200:
            return AuthVerifyResult(valid=True, message="服务可访问")
        return AuthVerifyResult(
            valid=False, message=f"服务响应异常: HTTP {resp.status_code}"
        )


register_client(KavitaClient.name, KavitaClient)
