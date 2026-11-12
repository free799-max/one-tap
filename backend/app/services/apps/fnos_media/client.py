"""飞牛影视（fnOS 影视）应用客户端实现。

飞牛影视没有官方 API 文档，Web 端使用 ``/v/api/v1`` 私有接口并要求
``authx`` 签名；新版服务端把登录迁移到 ``/api/v2``，旧版仍保留 v1 明文
登录。本客户端只做可达性与凭据校验：飞牛影视是 fnOS 自带服务，不由本
平台部署，因此不会修改它已有的账号密码。
"""

import hashlib
import json
import random
import time
from typing import Any

import httpx

from app.services.apps.base import (
    AppClient,
    AuthVerifyResult,
    LogsReader,
    ProvisionResult,
)
from app.services.apps.registry import register_client


class FnosMediaClient(AppClient):
    """飞牛影视外部服务客户端。"""

    name = "fnos-media"
    supports_provision = True
    TIMEOUT = 10.0

    #: Web/客户端固定使用的应用标识，登录接口要求携带
    _APP_NAME = "trimemedia-web"
    #: 参与 authx 签名的公开客户端密钥（飞牛 Web 端内置常量）
    _CLIENT_KEY = "16CCEB3D-AB42-077D-36A1-F355324E4237"
    #: authx 签名盐值（飞牛 Web 端内置常量）
    _SIGN_SALT = "NDzZTVxnRKP8Z0jXg1VAMonaG8akvh"

    async def verify_auth(self, config: dict) -> AuthVerifyResult:
        """校验飞牛影视访问地址与认证配置。"""
        base_url = self._base_url(config.get("url"))
        if not base_url:
            return AuthVerifyResult(valid=False, message="访问地址不能为空")

        auth_type = config.get("auth_type") or "none"
        try:
            async with httpx.AsyncClient(
                timeout=self.TIMEOUT, follow_redirects=True
            ) as client:
                if auth_type in ("api_key", "apikey"):
                    return await self._verify_token(
                        client, base_url, str(config.get("api_key") or "")
                    )
                if auth_type == "basic":
                    return await self._verify_basic(
                        client,
                        base_url,
                        str(config.get("username") or ""),
                        str(config.get("password") or ""),
                    )
                return await self._verify_reachable(client, base_url)
        except httpx.TimeoutException:
            return AuthVerifyResult(valid=False, message="连接超时，请检查地址与网络")
        except httpx.RequestError as exc:
            return AuthVerifyResult(valid=False, message=f"无法连接到应用: {exc}")

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
        """验证飞牛影视账号可登录，作为外部服务的“自动配置”。

        飞牛影视运行在 fnOS 宿主机上，平台不会创建或重置它的账号，
        因此这里只确认用户填写的凭据可用，供后续装配使用。
        """
        base_url = self._base_url(url)
        if not base_url:
            return ProvisionResult(ok=False, message="访问地址不能为空")
        if not username or not password:
            return ProvisionResult(ok=False, message="用户名和密码不能为空")

        try:
            async with httpx.AsyncClient(
                timeout=self.TIMEOUT, follow_redirects=True
            ) as client:
                token, error = await self._login(
                    client, base_url, username, password
                )
        except httpx.RequestError as exc:
            return ProvisionResult(ok=False, message=f"无法连接到应用: {exc}")
        if token:
            return ProvisionResult(ok=True, message="账号已验证通过")
        return ProvisionResult(ok=False, message=error or "账号验证失败")

    async def _verify_reachable(
        self, client: httpx.AsyncClient, base_url: str
    ) -> AuthVerifyResult:
        """无认证时确认飞牛影视接口可达。"""
        response = await self._request(client, base_url, "GET", "/api/v1/sys/version")
        payload = self._payload(response)
        if payload is not None and payload.get("code") == 0:
            return AuthVerifyResult(valid=True, message="应用可正常访问")
        return AuthVerifyResult(
            valid=False,
            message=self._error_message(payload, response, "应用接口响应异常"),
        )

    async def _verify_basic(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        username: str,
        password: str,
    ) -> AuthVerifyResult:
        """使用用户名/密码登录验证。"""
        if not username or not password:
            return AuthVerifyResult(
                valid=False, message="用户名和密码不能为空"
            )
        token, error = await self._login(client, base_url, username, password)
        if not token:
            return AuthVerifyResult(valid=False, message=error)
        return AuthVerifyResult(valid=True, message="用户名/密码认证成功")

    async def _verify_token(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        token: str,
    ) -> AuthVerifyResult:
        """使用登录令牌（配置里的 API Key）验证。"""
        if not token:
            return AuthVerifyResult(valid=False, message="API Key 不能为空")
        response = await self._request(
            client, base_url, "GET", "/api/v1/user/info", token=token
        )
        payload = self._payload(response)
        if payload is not None and payload.get("code") == 0:
            return AuthVerifyResult(valid=True, message="API Key 认证成功")
        if payload is not None and payload.get("code") in (-1, -2):
            return AuthVerifyResult(valid=False, message="API Key 认证失败")
        return AuthVerifyResult(
            valid=False,
            message=self._error_message(payload, response, "API Key 认证失败"),
        )

    async def _login(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        username: str,
        password: str,
    ) -> tuple[str | None, str | None]:
        """登录并返回 (token, 失败原因)。

        优先 v2 接口（密码发 SHA-256 摘要）；v2 不存在时回退 v1 明文接口。
        v2 返回明确业务失败（如密码错误）时不再回退，避免重复提交凭据。
        """
        hashed = hashlib.sha256(password.encode()).hexdigest()
        response = await self._request(
            client,
            base_url,
            "POST",
            "/api/v2/user/loginByPassword",
            {
                "username": username,
                "password": hashed,
                "app_name": self._APP_NAME,
            },
        )
        payload = self._payload(response)
        if payload is not None and "code" in payload:
            return self._parse_login(payload)

        response = await self._request(
            client,
            base_url,
            "POST",
            "/api/v1/login",
            {
                "username": username,
                "password": password,
                "app_name": self._APP_NAME,
            },
        )
        payload = self._payload(response)
        if payload is not None and "code" in payload:
            return self._parse_login(payload)
        return None, self._error_message(payload, response, "登录接口响应异常")

    @staticmethod
    def _parse_login(payload: dict[str, Any]) -> tuple[str | None, str | None]:
        """解析登录响应中的 token 或错误原因。"""
        if payload.get("code") == 0:
            token = (payload.get("data") or {}).get("token")
            if token:
                return str(token), None
            return None, "登录接口未返回 Token"
        message = str(payload.get("msg") or "")
        if "password" in message.lower() or "密码" in message:
            return None, "用户名或密码错误"
        return None, message or f"登录失败（code={payload.get('code')}）"

    @staticmethod
    def _error_message(
        payload: dict[str, Any] | None,
        response: httpx.Response,
        fallback: str,
    ) -> str:
        """从业务响应或 HTTP 状态中提取可读错误信息。"""
        if payload is not None:
            message = str(payload.get("msg") or payload.get("message") or "")
            if message and message != fallback:
                return message
        if response.status_code >= 400:
            return f"{fallback}: HTTP {response.status_code}"
        return fallback

    @staticmethod
    def _payload(response: httpx.Response) -> dict[str, Any] | None:
        """解析 JSON 响应；非业务接口（HTML/404/501）返回 None。"""
        if response.status_code in (404, 405, 501):
            # 旧版/新版接口不存在时由调用方决定回退或报错
            return None
        try:
            payload = response.json()
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    async def _request(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        token: str | None = None,
    ) -> httpx.Response:
        """发送带 authx 签名的飞牛影视请求。"""
        body = (
            json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
            if payload is not None
            else ""
        )
        headers = {
            "Accept": "application/json",
            "authx": self._authx(path, body),
        }
        if token:
            headers["Authorization"] = token
        if payload is not None:
            headers["Content-Type"] = "application/json"
        return await client.request(
            method,
            f"{base_url}/v{path}",
            headers=headers,
            content=body or None,
        )

    def _authx(self, path: str, body: str = "") -> str:
        """按飞牛客户端协议计算单次请求的签名头。"""
        if not path.startswith("/v"):
            path = "/v" + path
        nonce = str(random.randint(100000, 999999))
        timestamp = str(int(time.time() * 1000))
        data_hash = hashlib.md5(body.encode()).hexdigest()
        sign = hashlib.md5(
            "_".join(
                [
                    self._SIGN_SALT,
                    path,
                    nonce,
                    timestamp,
                    data_hash,
                    self._CLIENT_KEY,
                ]
            ).encode()
        ).hexdigest()
        return f"nonce={nonce}&timestamp={timestamp}&sign={sign}"

    @staticmethod
    def _base_url(url: object) -> str:
        """规范化访问地址：去掉结尾的 ``/v``，请求时统一带 API 前缀。"""
        base = str(url or "").strip().rstrip("/")
        if base.endswith("/v"):
            base = base[:-2].rstrip("/")
        return base


register_client(FnosMediaClient.name, FnosMediaClient)
