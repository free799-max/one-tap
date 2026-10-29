"""Komga 应用客户端实现。

Komga 首次启动通过 ``POST /api/v1/claim`` 创建管理员（邮箱 + 密码），
之后使用 Basic Auth 调用 API，并可为当前用户创建 API Key。
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


class KomgaClient(AppClient):
    """Komga 漫画媒体服务器客户端。"""

    name = "komga"
    TIMEOUT = 10.0
    PROVISION_TIMEOUT = 300.0
    supports_provision = True
    API_KEY_COMMENT = "onetap"

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
        """Claim 服务器、创建管理员并生成 API Key。"""
        base_url = str(url or "").rstrip("/")
        if not base_url:
            return ProvisionResult(ok=False, message="访问地址不能为空")

        emails = self._candidate_emails(username)
        email = emails[0]
        deadline = time.monotonic() + self.PROVISION_TIMEOUT
        last_error = "服务未就绪"

        while time.monotonic() < deadline:
            try:
                async with httpx.AsyncClient(
                    timeout=self.TIMEOUT,
                    follow_redirects=True,
                ) as client:
                    claim = await client.get(f"{base_url}/api/v1/claim")
                    if claim.status_code == 200:
                        try:
                            claimed = bool(claim.json().get("isClaimed"))
                        except json.JSONDecodeError:
                            claimed = True
                        if not claimed:
                            resp = await client.post(
                                f"{base_url}/api/v1/claim",
                                headers={
                                    "X-Komga-Email": email,
                                    "X-Komga-Password": password,
                                },
                            )
                            if resp.status_code >= 400:
                                last_error = (
                                    f"创建管理员失败: HTTP {resp.status_code}"
                                )
                                await asyncio.sleep(5)
                                continue

                    if await self._auth_ok(client, base_url, email, password):
                        new_key, key_error = await self._ensure_api_key(
                            client, base_url, email, password
                        )
                        if new_key:
                            return ProvisionResult(
                                ok=True,
                                message="账号已配置并验证通过",
                                api_key=new_key,
                                username=email,
                            )
                        return ProvisionResult(
                            ok=False,
                            message=f"账号已配置，但 API Key 创建失败: {key_error}",
                            username=email,
                        )

                    if previous_password and previous_password != password:
                        changed = await self._change_password(
                            client,
                            base_url,
                            emails,
                            previous_password,
                            password,
                        )
                        if changed:
                            continue
                    last_error = "登录失败，服务可能已有其他管理员账号"
            except httpx.RequestError as exc:
                last_error = f"无法连接到应用: {exc}"
            await asyncio.sleep(5)

        return ProvisionResult(
            ok=False, message=f"自动配置超时: {last_error}", username=email
        )

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
            for email in self._candidate_emails(username):
                if await self._auth_ok(client, base_url, email, password):
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
                f"{base_url}/api/v2/users/me",
                headers={"X-API-Key": api_key},
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
            resp = await client.get(f"{base_url}/api/v1/claim")
        if resp.status_code == 200:
            return AuthVerifyResult(valid=True, message="服务可访问")
        return AuthVerifyResult(
            valid=False, message=f"服务响应异常: HTTP {resp.status_code}"
        )

    @staticmethod
    def _candidate_emails(username: str) -> list[str]:
        """Komga 使用邮箱登录；把普通用户名映射为固定本地邮箱。"""
        raw = (username or "").strip()
        if not raw:
            return [""]
        if "@" in raw:
            return [raw]
        return [f"{raw}@onetap.local", raw]

    async def _auth_ok(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        email: str,
        password: str,
    ) -> bool:
        """验证邮箱密码是否可登录。"""
        if not email or not password:
            return False
        resp = await client.get(
            f"{base_url}/api/v2/users/me", auth=(email, password)
        )
        return resp.status_code == 200

    async def _ensure_api_key(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        email: str,
        password: str,
    ) -> tuple[str | None, str | None]:
        """复用或创建当前用户的 API Key。"""
        auth = (email, password)
        resp = await client.get(
            f"{base_url}/api/v2/users/me/api-keys", auth=auth
        )
        if resp.status_code == 200:
            try:
                keys = resp.json()
            except json.JSONDecodeError:
                keys = []
            if isinstance(keys, list):
                for key in keys:
                    if isinstance(key, dict) and key.get("key"):
                        if key.get("comment") == self.API_KEY_COMMENT:
                            return str(key["key"]), None
                if len(keys) == 1 and isinstance(keys[0], dict):
                    key = keys[0].get("key")
                    if key:
                        return str(key), None

        resp = await client.post(
            f"{base_url}/api/v2/users/me/api-keys",
            auth=auth,
            json={"comment": self.API_KEY_COMMENT},
        )
        if resp.status_code >= 400:
            return None, f"HTTP {resp.status_code}"
        try:
            payload = resp.json()
        except json.JSONDecodeError:
            return None, "接口返回异常"
        key = payload.get("key") if isinstance(payload, dict) else None
        if not key:
            return None, "接口未返回密钥"
        return str(key), None

    async def _change_password(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        emails: list[str],
        old_password: str,
        new_password: str,
    ) -> bool:
        """用旧密码登录后修改为目标密码。"""
        for email in emails:
            if not await self._auth_ok(client, base_url, email, old_password):
                continue
            resp = await client.patch(
                f"{base_url}/api/v2/users/me/password",
                auth=(email, old_password),
                json={"password": new_password},
            )
            if resp.status_code in (200, 204):
                return True
        return False


register_client(KomgaClient.name, KomgaClient)
