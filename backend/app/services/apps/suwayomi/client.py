"""Suwayomi GraphQL 请求与扩展仓库装配。"""

from __future__ import annotations

import asyncio
import re
import time
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx

from app.core.exceptions import APIException

_TIMEOUT = 60.0

# simple_login 登录页：CSRF 隐藏域与错误提示
_LOGIN_CSRF_PATTERN = re.compile(r'name="csrf-token"\s+value="([^"]+)"')
_LOGIN_ERROR_PATTERN = re.compile(r'<div class="error">([^<]*)</div>')


class SuwayomiClientError(RuntimeError):
    """Suwayomi 接口或扩展装配失败。"""


class SuwayomiAuthError(SuwayomiClientError):
    """Suwayomi 登录失败，重试也无法恢复。"""


def _is_unauthorized(exc: SuwayomiClientError) -> bool:
    message = str(exc)
    return "Unauthorized" in message or "401" in message


async def execute_graphql(
    base_url: str,
    query: str,
    variables: dict[str, Any] | None = None,
    *,
    username: str = "",
    password: str = "",
) -> dict[str, Any]:
    try:
        async with SuwayomiClient(
            base_url, username=username, password=password
        ) as client:
            return await client.gql(query, variables)
    except SuwayomiClientError as exc:
        raise APIException(f"Suwayomi 调用失败：{exc}", 502) from exc


class SuwayomiClient:
    def __init__(
        self,
        base_url: str,
        *,
        username: str = "",
        password: str = "",
        timeout: float = _TIMEOUT,
    ) -> None:
        self.base_url = base_url.strip().rstrip("/")
        if not self.base_url:
            raise SuwayomiClientError("Suwayomi 访问地址为空")
        self.username = (username or "").strip()
        self.password = password or ""
        self._login_done = False
        # basic_auth 模式只认 Authorization 头；simple_login 模式该头会被忽略，
        # 由 _login() 走登录页换取 JSESSIONID。
        auth = (
            httpx.BasicAuth(self.username, self.password)
            if self.username
            else None
        )
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=15.0),
            follow_redirects=True,
            trust_env=False,
            auth=auth,
        )

    async def __aenter__(self) -> "SuwayomiClient":
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        await self.aclose()
        return False

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _login(self) -> None:
        """通过登录页建立 simple_login 会话。

        simple_login 模式下 GraphQL 只认登录页写入的 JSESSIONID，
        Basic 认证头会被忽略。登录页不存在（none / basic_auth）时直接跳过；
        登录失败的凭据错误不重试，避免服务就绪检查空转。
        """
        if not self.username:
            return
        try:
            page = await self._client.get(f"{self.base_url}/login.html")
        except httpx.HTTPError as exc:
            raise SuwayomiAuthError(f"访问登录页失败：{exc}") from exc
        if page.status_code == 404:
            self._login_done = True
            return
        if page.status_code >= 400:
            raise SuwayomiAuthError(
                f"访问登录页失败：HTTP {page.status_code}"
            )

        form = {"user": self.username, "pass": self.password}
        match = _LOGIN_CSRF_PATTERN.search(page.text)
        if match:
            form["csrf-token"] = match.group(1)
        try:
            response = await self._client.post(
                f"{self.base_url}/login.html",
                data=form,
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            raise SuwayomiAuthError(f"登录请求失败：{exc}") from exc
        if response.status_code in {301, 302, 303, 307, 308}:
            self._login_done = True
            return
        error = _LOGIN_ERROR_PATTERN.search(response.text)
        message = error.group(1).strip() if error else (
            f"HTTP {response.status_code}"
        )
        raise SuwayomiAuthError(f"Suwayomi 登录失败：{message}")

    async def gql(
        self, query: str, variables: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        # 先直接请求：authMode=NONE / basic_auth 时不需要登录页。
        # 只有被服务端判定未认证时才走 simple_login 表单登录。
        if self.username and not self._login_done:
            try:
                data = await self._gql_once(query, variables)
            except SuwayomiClientError as exc:
                if not _is_unauthorized(exc):
                    raise
                await self._login()
                self._login_done = True
                return await self._gql_once(query, variables)
            self._login_done = True
            return data
        try:
            return await self._gql_once(query, variables)
        except SuwayomiClientError as exc:
            if not self.username or not _is_unauthorized(exc):
                raise
            # 会话过期或服务端重启后 Cookie 失效，重新登录再试一次
            self._login_done = False
            await self._login()
            self._login_done = True
            return await self._gql_once(query, variables)

    async def _gql_once(
        self, query: str, variables: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        try:
            response = await self._client.post(
                f"{self.base_url}/api/graphql",
                json={"query": query, "variables": variables or {}},
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SuwayomiClientError(str(exc)) from exc
        errors = payload.get("errors") if isinstance(payload, dict) else None
        if errors:
            message = "；".join(
                str(item.get("message"))
                for item in errors
                if isinstance(item, dict) and item.get("message")
            )
            raise SuwayomiClientError(message or "未知 GraphQL 错误")
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise SuwayomiClientError("响应缺少 data")
        return data

    async def wait_ready(self, timeout: float = 300.0) -> None:
        deadline = time.monotonic() + timeout
        while True:
            try:
                await self.extension_stores()
                return
            except SuwayomiAuthError:
                raise
            except SuwayomiClientError:
                if time.monotonic() >= deadline:
                    raise
                await asyncio.sleep(5)

    async def extension_stores(self) -> list[dict[str, Any]]:
        data = await self.gql(
            """
            query { extensionStores { nodes { name indexUrl isLegacy } } }
            """
        )
        return (data.get("extensionStores") or {}).get("nodes") or []

    async def proxy_settings(self) -> dict[str, Any]:
        data = await self.gql(
            """
            query {
              settings {
                socksProxyEnabled socksProxyVersion socksProxyHost socksProxyPort
                socksProxyUsername socksProxyPassword
              }
            }
            """
        )
        return data.get("settings") or {}

    async def set_proxy_settings(self, settings: dict[str, Any]) -> None:
        await self.gql(
            """
            mutation SetProxy($settings: PartialSettingsTypeInput!) {
              setSettings(input: {settings: $settings}) {
                settings { socksProxyEnabled }
              }
            }
            """,
            {"settings": settings},
        )

    @asynccontextmanager
    async def _temporary_proxy(self, proxy: str | None):
        if not proxy:
            yield
            return
        parsed = urlsplit(proxy)
        if not parsed.hostname:
            raise SuwayomiClientError("系统代理地址无效")
        original = await self.proxy_settings()
        settings = {
            "socksProxyEnabled": True,
            "socksProxyVersion": 5,
            "socksProxyHost": parsed.hostname,
            "socksProxyPort": str(parsed.port or (443 if parsed.scheme == "https" else 80)),
            "socksProxyUsername": unquote(parsed.username or ""),
            "socksProxyPassword": unquote(parsed.password or ""),
        }
        try:
            await self.set_proxy_settings(settings)
            yield
        finally:
            await self.set_proxy_settings(original)

    @staticmethod
    def _store_aliases(url: str) -> set[str]:
        aliases = {url.rstrip("/")}
        if url.endswith("/index.min.json"):
            aliases.add(url[: -len("/index.min.json")] + "/repo.json")
        return aliases

    async def ensure_store(self, urls: list[str]) -> str:
        existing = await self.extension_stores()
        existing_urls = {
            str(item.get("indexUrl") or "").rstrip("/") for item in existing
        }
        for url in urls:
            if self._store_aliases(url) & existing_urls:
                return next(iter(self._store_aliases(url) & existing_urls))
        errors = []
        for url in urls:
            try:
                data = await self.gql(
                    """
                    mutation AddStore($url: String!) {
                      addExtensionStore(input: {indexUrl: $url}) {
                        extensionStore { indexUrl }
                      }
                    }
                    """,
                    {"url": url},
                )
                store = (data.get("addExtensionStore") or {}).get(
                    "extensionStore"
                ) or {}
                return str(store.get("indexUrl") or url)
            except SuwayomiClientError as exc:
                errors.append(f"{url}: {exc}")
        raise SuwayomiClientError("；".join(errors) or "扩展仓库不可用")

    async def fetch_extensions(self) -> list[dict[str, Any]]:
        data = await self.gql(
            """
            mutation {
              fetchExtensions(input: {}) {
                extensions {
                  pkgName name lang isInstalled isObsolete isNsfw storeIndexUrl
                }
              }
            }
            """
        )
        return (data.get("fetchExtensions") or {}).get("extensions") or []

    async def install_extensions(self, package_names: list[str]) -> None:
        if not package_names:
            return
        await self.gql(
            """
            mutation Install($ids: [String!]!) {
              updateExtensions(input: {ids: $ids, patch: {install: true}}) {
                extensions { pkgName isInstalled }
              }
            }
            """,
            {"ids": package_names},
        )

    async def sources(self) -> list[dict[str, Any]]:
        data = await self.gql(
            """
            query {
              sources(first: 500) {
                nodes { id name lang extension { pkgName } }
              }
            }
            """
        )
        return (data.get("sources") or {}).get("nodes") or []

    async def probe_source(self, source_id: str) -> bool:
        try:
            await asyncio.wait_for(
                self.gql(
                    """
                    mutation Probe($source: LongString!) {
                      fetchSourceManga(input: {
                        source: $source, query: "海贼王", page: 1, type: SEARCH
                      }) { mangas { id } }
                    }
                    """,
                    {"source": source_id},
                ),
                timeout=25,
            )
            return True
        except (SuwayomiClientError, TimeoutError):
            return False

    async def prioritize_healthy_sources(
        self, sources: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        semaphore = asyncio.Semaphore(6)

        async def check(source: dict[str, Any]) -> tuple[dict[str, Any], bool]:
            async with semaphore:
                return source, await self.probe_source(str(source["id"]))

        checked = await asyncio.gather(*(check(source) for source in sources))
        healthy = [{**source, "healthy": True} for source, ok in checked if ok]
        unavailable = [
            {**source, "healthy": False} for source, ok in checked if not ok
        ]
        return [*healthy, *unavailable]

    @staticmethod
    def _extension_matches_store(
        extension: dict[str, Any], store_url: str, aliases: set[str]
    ) -> bool:
        extension_store = str(extension.get("storeIndexUrl") or "").rstrip("/")
        return not extension_store or extension_store == store_url or extension_store in aliases

    async def install_repository(
        self,
        repository: dict[str, Any],
        *,
        auto_filter: bool = True,
        proxy: str | None = None,
    ) -> dict[str, Any]:
        primary_url = repository["url"]
        attempts = [(primary_url, None)]
        if proxy:
            attempts.append((primary_url, proxy))
        attempts.extend(
            (url, None) for url in repository.get("mirror_urls") or []
        )
        urls = list(dict.fromkeys(url for url, _ in attempts))
        aliases = set().union(*(self._store_aliases(url) for url in urls))
        errors = []
        for url, attempt_proxy in attempts:
            try:
                async with self._temporary_proxy(attempt_proxy):
                    store_url = await self.ensure_store([url])
                    extensions = await self.fetch_extensions()
                    candidates = [
                        item
                        for item in extensions
                        if self._extension_matches_store(item, store_url, aliases)
                    ]
                    if auto_filter:
                        candidates = [
                            item
                            for item in candidates
                            if str(item.get("lang") or "").lower().startswith("zh")
                            and not item.get("isObsolete")
                        ]
                    package_names = list(
                        dict.fromkeys(
                            str(item.get("pkgName") or "")
                            for item in candidates
                            if item.get("pkgName")
                        )
                    )
                    pending = list(
                        dict.fromkeys(
                            str(item.get("pkgName") or "")
                            for item in candidates
                            if item.get("pkgName") and not item.get("isInstalled")
                        )
                    )
                    if not package_names:
                        return {
                            "installed_ids": [],
                            "source_ids": [],
                            "extension_count": 0,
                            "source_count": 0,
                            "healthy_count": 0,
                        }
                    await self.install_extensions(pending)
                    installed = set(package_names)
                    online_sources = []
                    deadline = time.monotonic() + 60
                    while True:
                        sources = await self.sources()
                        online_sources = [
                            item
                            for item in sources
                            if str(item.get("id") or "") != "0"
                            and str((item.get("extension") or {}).get("pkgName") or "")
                            in installed
                        ]
                        if online_sources or time.monotonic() >= deadline:
                            break
                        await asyncio.sleep(3)
                break
            except SuwayomiClientError as exc:
                route = "系统代理" if attempt_proxy else "直连"
                errors.append(f"{url}（{route}）: {exc}")
        else:
            raise SuwayomiClientError("；".join(errors) or "扩展仓库不可用")

        if auto_filter and online_sources:
            online_sources = await self.prioritize_healthy_sources(online_sources)
        healthy_count = (
            sum(bool(item.get("healthy")) for item in online_sources)
            if auto_filter
            else None
        )
        return {
            "installed_ids": package_names,
            "source_ids": [str(item["id"]) for item in online_sources],
            "extension_count": len(package_names),
            "source_count": len(online_sources),
            "healthy_count": healthy_count,
        }
