"""Navidrome 应用客户端实现。

Navidrome 首次启动没有管理员，通过 ``POST /auth/createAdmin`` 创建；
已有管理员时用账号密码登录，并通过 ``PUT /api/user/{id}`` 改密。
曲库目录由编排共享卷和环境变量 ``ND_MUSICFOLDER`` 提供，无需额外配置。
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


class NavidromeClient(AppClient):
    """Navidrome 私人音乐流媒体服务器客户端。"""

    name = "navidrome"
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
        download_dir: str | None = None,
    ) -> ProvisionResult:
        """创建首个管理员，或登录已有管理员并同步密码。"""
        base = str(url or "").strip().rstrip("/")
        if not base:
            return ProvisionResult(ok=False, message="访问地址不能为空")

        deadline = time.monotonic() + self.PROVISION_TIMEOUT
        last_error = "服务未就绪"

        while time.monotonic() < deadline:
            try:
                async with httpx.AsyncClient(
                    timeout=self.TIMEOUT,
                    follow_redirects=True,
                ) as client:
                    ping = await client.get(f"{base}/ping")
                    if ping.status_code != 200:
                        last_error = f"服务响应异常: HTTP {ping.status_code}"
                        await asyncio.sleep(5)
                        continue

                    login = await self._login(
                        client, base, username, password
                    )
                    if login is not None:
                        return ProvisionResult(
                            ok=True,
                            message="账号已配置并验证通过",
                            username=username,
                        )

                    if previous_password and previous_password != password:
                        old = await self._login(
                            client, base, username, previous_password
                        )
                        if old is not None:
                            ok, error = await self._change_password(
                                client,
                                base,
                                old,
                                previous_password,
                                password,
                            )
                            if ok:
                                return ProvisionResult(
                                    ok=True,
                                    message="账号密码已更新并验证通过",
                                    username=username,
                                )
                            return ProvisionResult(
                                ok=False,
                                message=f"修改密码失败: {error}",
                            )

                    created = await self._create_admin(
                        client, base, username, password
                    )
                    if created:
                        return ProvisionResult(
                            ok=True,
                            message="管理员账号已创建并验证通过",
                            username=username,
                        )

                    return ProvisionResult(
                        ok=False,
                        message=(
                            "服务已有管理员且密码与统一密码不一致，"
                            "请手动重置密码或重新部署数据卷"
                        ),
                    )
            except httpx.RequestError as exc:
                last_error = f"无法连接到应用: {exc}"
            await asyncio.sleep(5)

        return ProvisionResult(ok=False, message=f"自动配置超时: {last_error}")

    async def verify_auth(self, config: dict) -> AuthVerifyResult:
        """校验 Navidrome 访问地址与账号密码。"""
        base = str(config.get("url") or "").strip().rstrip("/")
        if not base:
            return AuthVerifyResult(valid=False, message="访问地址不能为空")

        auth_type = config.get("auth_type") or "none"
        if auth_type in ("api_key", "apikey"):
            return AuthVerifyResult(
                valid=False, message="Navidrome 不支持 API Key，请使用账号密码"
            )
        if auth_type != "basic":
            return AuthVerifyResult(
                valid=False, message="Navidrome 需要账号密码认证"
            )

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
                login = await self._login(client, base, username, password)
                if login is None:
                    return AuthVerifyResult(
                        valid=False, message="用户名或密码错误"
                    )
                return AuthVerifyResult(valid=True, message="登录成功")
        except httpx.TimeoutException:
            return AuthVerifyResult(
                valid=False, message="连接超时，请检查地址与网络"
            )
        except httpx.RequestError as exc:
            return AuthVerifyResult(
                valid=False, message=f"无法连接到应用: {exc}"
            )

    async def scan_library(
        self,
        url: str,
        username: str,
        password: str,
    ) -> tuple[bool, str]:
        """登录后通过 Subsonic startScan 触发曲库扫描。"""
        base = str(url or "").strip().rstrip("/")
        if not base:
            return False, "Navidrome 访问地址不能为空"
        try:
            async with httpx.AsyncClient(
                timeout=self.TIMEOUT,
                follow_redirects=True,
            ) as client:
                login = await self._login(client, base, username, password)
                if login is None:
                    return False, "Navidrome 登录失败，请检查账号密码"
                salt = str(login.get("subsonicSalt") or "")
                token = str(login.get("subsonicToken") or "")
                if not salt or not token:
                    return False, "Navidrome 登录响应缺少 Subsonic 凭据"
                resp = await client.get(
                    f"{base}/rest/startScan.view",
                    params={
                        "u": username,
                        "t": token,
                        "s": salt,
                        "v": "1.16.1",
                        "c": "onetap",
                        "f": "json",
                    },
                )
        except httpx.TimeoutException:
            return False, "连接 Navidrome 超时"
        except httpx.RequestError as exc:
            return False, f"无法连接到 Navidrome: {exc}"

        if resp.status_code != 200:
            return False, f"Navidrome 扫描接口返回 HTTP {resp.status_code}"
        try:
            payload = resp.json()
        except (json.JSONDecodeError, ValueError):
            return False, "Navidrome 扫描接口返回异常"
        body = (
            payload.get("subsonic-response")
            if isinstance(payload, dict)
            else None
        ) or {}
        if body.get("status") == "ok":
            return True, "已触发 Navidrome 扫描"
        error = (body.get("error") or {}).get("message")
        return False, error or "Navidrome 扫描失败"

    async def _login(
        self,
        client: httpx.AsyncClient,
        base: str,
        username: str,
        password: str,
    ) -> dict | None:
        """登录并返回包含 token/user 信息的响应体。"""
        resp = await client.post(
            f"{base}/auth/login",
            json={"username": username, "password": password},
        )
        if resp.status_code != 200:
            return None
        try:
            payload = resp.json()
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(payload, dict) or not payload.get("token"):
            return None
        return payload

    async def _create_admin(
        self,
        client: httpx.AsyncClient,
        base: str,
        username: str,
        password: str,
    ) -> bool:
        """首次启动创建管理员（已有管理员时接口返回 4xx）。"""
        resp = await client.post(
            f"{base}/auth/createAdmin",
            json={"username": username, "password": password},
        )
        if resp.status_code != 200:
            return False
        try:
            payload = resp.json()
        except (json.JSONDecodeError, ValueError):
            return False
        return isinstance(payload, dict) and bool(payload.get("token"))

    async def _change_password(
        self,
        client: httpx.AsyncClient,
        base: str,
        login: dict,
        current_password: str,
        new_password: str,
    ) -> tuple[bool, str | None]:
        """用当前会话修改自己的密码。"""
        token = str(login.get("token") or "")
        user_id = str(login.get("id") or "")
        if not token or not user_id:
            return False, "登录响应缺少用户信息"

        headers = {"x-nd-authorization": f"Bearer {token}"}
        resp = await client.get(f"{base}/api/user/{user_id}", headers=headers)
        if resp.status_code != 200:
            return False, f"读取用户信息失败: HTTP {resp.status_code}"
        try:
            user = resp.json()
        except (json.JSONDecodeError, ValueError):
            return False, "用户信息返回异常"
        if not isinstance(user, dict):
            return False, "用户信息返回异常"

        payload = {
            "id": user_id,
            "userName": user.get("userName") or login.get("username"),
            "name": user.get("name") or "",
            "email": user.get("email") or "",
            "isAdmin": bool(user.get("isAdmin", True)),
            "scrobbleFilter": user.get("scrobbleFilter") or "",
            "password": new_password,
            "currentPassword": current_password,
        }
        resp = await client.put(
            f"{base}/api/user/{user_id}", json=payload, headers=headers
        )
        if resp.status_code >= 400:
            return False, self._extract_error(resp) or (
                f"保存密码失败: HTTP {resp.status_code}"
            )

        if await self._login(
            client, base, str(payload["userName"]), new_password
        ) is None:
            return False, "修改密码后登录验证失败"
        return True, None

    @staticmethod
    def _extract_error(resp: httpx.Response) -> str | None:
        try:
            payload = resp.json()
        except (json.JSONDecodeError, ValueError):
            return None
        if isinstance(payload, dict):
            error = payload.get("error")
            if error:
                return str(error)
        return None


register_client(NavidromeClient.name, NavidromeClient)
