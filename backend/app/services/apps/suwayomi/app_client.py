"""Suwayomi 账号验证与部署后确认。

Suwayomi 默认以 simple_login 模式启动，账号密码通过容器环境变量
``AUTH_MODE`` / ``AUTH_USERNAME`` / ``AUTH_PASSWORD`` 注入。旧实例
（authMode=NONE）不会因升级重建容器，这里会在自动配置时通过
GraphQL ``setSettings`` 补开简单登录，避免必须手动「修复」。
"""

from __future__ import annotations

import asyncio
import time

from app.services.apps.base import (
    AppClient,
    AuthVerifyResult,
    CommandRunner,
    LogsReader,
    ProvisionResult,
)
from app.services.apps.registry import register_client
from app.services.apps.suwayomi.client import (
    SuwayomiAuthError,
    SuwayomiClient,
    SuwayomiClientError,
)


class SuwayomiAppClient(AppClient):
    """Suwayomi 漫画服务器客户端。"""

    name = "suwayomi"
    supports_provision = True
    TIMEOUT = 15.0
    PROVISION_TIMEOUT = 300.0

    async def verify_auth(self, config: dict) -> AuthVerifyResult:
        """验证访问地址与账号密码（无认证实例也允许通过）。"""
        url = str(config.get("url") or "").strip()
        if not url:
            return AuthVerifyResult(valid=False, message="访问地址不能为空")
        username = str(config.get("username") or "")
        password = str(config.get("password") or "")
        try:
            async with SuwayomiClient(
                url, username=username, password=password, timeout=self.TIMEOUT
            ) as client:
                settings = await client.gql(
                    "query { settings { authMode authUsername } }"
                )
        except SuwayomiClientError as exc:
            return AuthVerifyResult(valid=False, message=str(exc))
        auth_mode = str((settings.get("settings") or {}).get("authMode") or "")
        return AuthVerifyResult(valid=True, message=f"连接成功（{auth_mode or 'NONE'}）")

    async def provision(
        self,
        url: str,
        username: str,
        password: str,
        api_key: str | None = None,
        logs_reader: LogsReader | None = None,
        previous_password: str | None = None,
        host_header_port: int | None = None,
        download_dir: str | None = None,
        command_runner: CommandRunner | None = None,
    ) -> ProvisionResult:
        """等待服务就绪，并在旧实例未开启认证时补开简单登录。"""
        base = str(url or "").strip().rstrip("/")
        if not base:
            return ProvisionResult(ok=False, message="访问地址不能为空")

        deadline = time.monotonic() + self.PROVISION_TIMEOUT
        last_error = "服务未就绪"
        while time.monotonic() < deadline:
            try:
                async with SuwayomiClient(
                    base, username=username, password=password
                ) as client:
                    settings = (
                        await client.gql(
                            "query { settings { authMode authUsername } }"
                        )
                    ).get("settings") or {}
                    auth_mode = str(settings.get("authMode") or "NONE").upper()
                    if auth_mode == "NONE":
                        # 升级前部署的实例没有 AUTH_* 环境变量，容器不会重建；
                        # 这里通过服务端设置补开简单登录，密码写入 server.conf。
                        await client.gql(
                            """
                            mutation EnableSimpleLogin(
                              $user: String!
                              $password: String!
                            ) {
                              setSettings(input: {settings: {
                                authMode: SIMPLE_LOGIN
                                authUsername: $user
                                authPassword: $password
                              }}) {
                                settings { authMode authUsername }
                              }
                            }
                            """,
                            {"user": username, "password": password},
                        )
                        settings = (
                            await client.gql(
                                "query { settings { authMode authUsername } }"
                            )
                        ).get("settings") or {}
                        auth_mode = str(
                            settings.get("authMode") or ""
                        ).upper()
                    if auth_mode not in {"SIMPLE_LOGIN", "BASIC_AUTH"}:
                        return ProvisionResult(
                            ok=False,
                            message=(
                                f"当前认证模式为 {auth_mode or 'UNKNOWN'}，"
                                "未开启简单登录"
                            ),
                        )
                return ProvisionResult(
                    ok=True,
                    username=username,
                    message="简单登录账号已验证通过",
                )
            except SuwayomiAuthError as exc:
                return ProvisionResult(ok=False, message=str(exc))
            except SuwayomiClientError as exc:
                last_error = str(exc)
                await asyncio.sleep(5)
        return ProvisionResult(
            ok=False, message=f"Suwayomi 未就绪：{last_error}"
        )


register_client(SuwayomiAppClient.name, SuwayomiAppClient)
