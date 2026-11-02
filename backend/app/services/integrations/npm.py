"""Nginx Proxy Manager 客户端。

封装 NPM 的登录、反向代理主机与证书列表接口。
OneTap 创建的代理主机在 ``meta.onetap_instance_id`` 中记录应用实例 ID，
更新和删除只操作自己创建的主机，不会影响用户手工配置的条目。
"""

from typing import Any
from urllib.parse import urlsplit

import httpx

from app.core.exceptions import APIException


ONETAP_META_KEY = "onetap_instance_id"


class NpmError(APIException):
    """Nginx Proxy Manager 调用失败。"""


class NpmClient:
    """Nginx Proxy Manager HTTP API 客户端。"""

    TIMEOUT = 20.0

    def __init__(self, base_url: str, email: str, password: str):
        url = (base_url or "").strip().rstrip("/")
        if not url:
            raise NpmError("请填写 Nginx Proxy Manager 地址", 400)
        if not url.startswith(("http://", "https://")):
            url = f"http://{url}"
        self.base_url = url
        self.email = (email or "").strip()
        self.password = password or ""
        self.token: str | None = None
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "NpmClient":
        self._client = httpx.AsyncClient(
            timeout=self.TIMEOUT,
            follow_redirects=True,
            trust_env=False,
        )
        return self

    async def __aexit__(self, *_args) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("NpmClient 必须在 async with 中使用")
        return self._client

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        auth: bool = True,
    ) -> Any:
        headers = {}
        if auth:
            if not self.token:
                await self.login()
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            response = await self.client.request(
                method,
                f"{self.base_url}{path}",
                json=json,
                headers=headers,
            )
        except httpx.HTTPError as exc:
            raise NpmError(f"无法连接 Nginx Proxy Manager：{exc}", 502) from exc

        if response.status_code >= 400:
            message = _error_message(response)
            if response.status_code in (401, 403):
                raise NpmError(
                    "Nginx Proxy Manager 登录失败，请检查邮箱和密码", 401
                )
            raise NpmError(message, 502)

        if response.status_code == 204 or not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise NpmError("Nginx Proxy Manager 返回内容不是合法 JSON", 502) from exc

    async def login(self) -> None:
        if not self.email or not self.password:
            raise NpmError("请填写 Nginx Proxy Manager 邮箱和密码", 400)
        data = await self._request(
            "POST",
            "/api/tokens",
            json={"identity": self.email, "secret": self.password},
            auth=False,
        )
        if isinstance(data, dict) and data.get("requires_2fa"):
            raise NpmError(
                "该 Nginx Proxy Manager 账号启用了两步验证，暂不支持自动登录", 400
            )
        token = data.get("token") if isinstance(data, dict) else None
        if not token:
            raise NpmError("登录成功但未返回 token", 502)
        self.token = str(token)

    async def test(self) -> None:
        await self.login()

    async def list_proxy_hosts(self) -> list[dict]:
        data = await self._request("GET", "/api/nginx/proxy-hosts")
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]

    def _find_managed(
        self, hosts: list[dict], instance_id: int
    ) -> dict | None:
        wanted = str(instance_id)
        for host in hosts:
            meta = host.get("meta")
            if isinstance(meta, dict) and str(meta.get(ONETAP_META_KEY, "")) == wanted:
                return host
        return None

    async def save_proxy_host(self, instance_id: int, payload: dict) -> int | None:
        hosts = await self.list_proxy_hosts()
        existing = self._find_managed(hosts, instance_id)
        if existing:
            payload = dict(payload)
            host_id = existing.get("id")
            if payload.get("certificate_id") == "new":
                current = existing.get("certificate_id")
                payload["certificate_id"] = current or "new"
            await self._request(
                "PUT",
                f"/api/nginx/proxy-hosts/{host_id}",
                json=payload,
            )
            return host_id
        created = await self._request(
            "POST", "/api/nginx/proxy-hosts", json=payload
        )
        if isinstance(created, dict) and created.get("id"):
            return int(created["id"])
        return None

    async def delete_proxy_host(self, instance_id: int) -> bool:
        existing = self._find_managed(await self.list_proxy_hosts(), instance_id)
        if not existing or not existing.get("id"):
            return False
        await self._request("DELETE", f"/api/nginx/proxy-hosts/{existing['id']}")
        return True

    async def list_certificates(self) -> list[dict]:
        data = await self._request("GET", "/api/nginx/certificates")
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]


def _error_message(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        data = None
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict) and error.get("message"):
            return f"Nginx Proxy Manager：{error['message']}"
        if data.get("message"):
            return f"Nginx Proxy Manager：{data['message']}"
    return f"Nginx Proxy Manager 请求失败（HTTP {response.status_code}）"


def _split_target(target_url: str) -> tuple[str, str, int]:
    parsed = urlsplit(target_url)
    scheme = (parsed.scheme or "http").lower()
    if scheme not in ("http", "https"):
        scheme = "http"
    host = parsed.hostname or ""
    if not host:
        raise NpmError("无法解析应用后端地址，请手动填写", 400)
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port is None:
        port = 443 if scheme == "https" else 80
    return scheme, host, port


def build_proxy_host(
    instance_id: int,
    *,
    domains: list[str],
    target_url: str,
    certificate_id: int | str = 0,
    ssl_forced: bool = True,
) -> dict:
    """构造 NPM 代理主机请求体。"""
    scheme, host, port = _split_target(target_url)
    cert = certificate_id if certificate_id in (0, "new") else int(certificate_id or 0)
    return {
        "domain_names": list(domains),
        "forward_scheme": scheme,
        "forward_host": host,
        "forward_port": port,
        "access_list_id": 0,
        "certificate_id": cert,
        "ssl_forced": bool(cert) and ssl_forced,
        "caching_enabled": False,
        "block_exploits": True,
        "advanced_config": "",
        "meta": {
            "onetap_managed": True,
            ONETAP_META_KEY: str(instance_id),
        },
        "allow_websocket_upgrade": True,
        "http2_support": bool(cert),
        "enabled": True,
        "locations": [],
        "hsts_enabled": False,
        "hsts_subdomains": False,
        "trust_forwarded_proto": True,
    }
