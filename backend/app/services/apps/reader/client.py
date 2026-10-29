"""Reader 与二毛读书的网页端接口封装。

Reader（hectorqin/reader）没有官方 SDK，自动化所需的书架、章节缓存、
EPUB 导出都通过 ``/reader3/*`` 接口完成；二毛读书使用 Cookie 会话，
提供初始化、登录、书库列表、扫描与导入任务查询。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx


class ReaderClientError(Exception):
    """Reader 接口调用失败。"""


class ErmaoClientError(Exception):
    """二毛接口调用失败。"""


def _error_message(payload: Any, fallback: str) -> str:
    """从二毛的错误响应中提取可读信息。"""
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("code")
            details = error.get("details")
            if isinstance(details, list):
                messages = [
                    str(item.get("message"))
                    for item in details
                    if isinstance(item, dict) and item.get("message")
                ]
                if messages:
                    return f"{message or '请求失败'}: {'; '.join(messages[:3])}"
            if message:
                return str(message)
        message = payload.get("message")
        if message:
            return str(message)
    return fallback


class ReaderSession:
    """一次 Reader 自动化会话，复用 Cookie 与连接。"""

    def __init__(
        self,
        base_url: str,
        token: str = "",
        *,
        username: str = "",
        password: str = "",
        timeout: float = 300.0,
        download_timeout: float = 900.0,
    ) -> None:
        base = (base_url or "").strip().rstrip("/")
        if not base:
            raise ReaderClientError("Reader 地址为空")
        self.base = base
        self.username = (username or "").strip()
        self.password = password or ""
        self.token = (token or "").strip()
        self._timeout = httpx.Timeout(timeout, connect=20.0)
        self._download_timeout = httpx.Timeout(
            download_timeout, connect=20.0
        )
        headers = {"Accept": "application/json, text/plain, */*"}
        self._client = httpx.AsyncClient(
            base_url=self.base,
            headers=headers,
            timeout=self._timeout,
            follow_redirects=True,
        )

    async def __aenter__(self) -> "ReaderSession":
        # Reader 每次登录都会轮换 accessToken，有账号密码时优先重新登录，
        # 避免使用 app_configs 中可能已失效的令牌
        if self.username and self.password:
            await self.login(self.username, self.password)
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        await self.aclose()
        return False

    async def aclose(self) -> None:
        await self._client.aclose()

    async def login(self, username: str, password: str) -> None:
        """登录并保存会话 Cookie 与 accessToken。"""
        try:
            response = await self._client.post(
                "/reader3/login",
                json={
                    "username": username,
                    "password": password,
                    "isLogin": True,
                },
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ReaderClientError(f"Reader 登录失败: {exc}") from exc
        if not isinstance(payload, dict) or not payload.get("isSuccess"):
            message = (
                payload.get("errorMsg")
                if isinstance(payload, dict)
                else None
            )
            raise ReaderClientError(
                str(message or "Reader 登录失败，请检查账号密码")
            )
        data = payload.get("data")
        token = data.get("accessToken") if isinstance(data, dict) else None
        if token:
            self.token = str(token)

    def _params(self, params: dict[str, Any] | None) -> dict[str, Any]:
        """登录认证开启后所有读接口都需要 accessToken。"""
        merged = dict(params or {})
        if self.token:
            merged.setdefault("accessToken", self.token)
        return merged

    async def _json(
        self, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        try:
            response = await self._client.get(path, params=self._params(params))
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ReaderClientError(f"请求 {path} 失败: {exc}") from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise ReaderClientError(
                f"{path} 返回的不是 JSON: {response.text[:200]}"
            ) from exc
        if not isinstance(payload, dict):
            raise ReaderClientError(f"{path} 返回结构异常")
        return payload

    async def shelf(self) -> list[dict[str, Any]]:
        """读取书架及缓存状态。"""
        payload = await self._json("/reader3/getShelfBookWithCacheInfo")
        if not payload.get("isSuccess"):
            raise ReaderClientError(
                str(payload.get("errorMsg") or "读取 Reader 书架失败")
            )
        data = payload.get("data")
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    async def chapter_list(self, book_url: str) -> list[dict[str, Any]]:
        """读取书源目录（用于判断最新章节数）。"""
        payload = await self._json(
            "/reader3/getChapterList", {"url": book_url}
        )
        if not payload.get("isSuccess"):
            raise ReaderClientError(
                str(payload.get("errorMsg") or "读取目录失败")
            )
        data = payload.get("data")
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    async def cache_chapter(self, book_url: str, index: int) -> bool:
        """让 Reader 缓存指定章节；返回是否成功。"""
        payload = await self._json(
            "/reader3/getBookContent", {"url": book_url, "index": index}
        )
        return bool(payload.get("isSuccess"))

    async def export_epub(self, book_url: str, destination: Path) -> None:
        """下载 EPUB 到指定文件（先写临时文件再原子替换）。"""
        part = destination.with_name(f".{destination.name}.download")
        try:
            async with self._client.stream(
                "GET",
                "/reader3/exportBook",
                params=self._params({"url": book_url, "isEpub": "1"}),
                timeout=self._download_timeout,
            ) as response:
                response.raise_for_status()
                with part.open("wb") as handle:
                    async for chunk in response.aiter_bytes():
                        handle.write(chunk)
            with part.open("rb") as handle:
                head = handle.read(2)
            if head != b"PK":
                raise ReaderClientError("导出结果不是 EPUB")
            part.replace(destination)
        except httpx.HTTPError as exc:
            raise ReaderClientError(f"导出 EPUB 失败: {exc}") from exc
        finally:
            part.unlink(missing_ok=True)


class ErmaoClient:
    """二毛读书 API 客户端（Cookie 会话）。"""

    def __init__(self, base_url: str, *, timeout: float = 60.0) -> None:
        base = (base_url or "").strip().rstrip("/")
        if not base:
            raise ErmaoClientError("二毛地址为空")
        self.base = base
        self._client = httpx.AsyncClient(
            base_url=self.base,
            timeout=httpx.Timeout(timeout, connect=20.0),
            follow_redirects=True,
        )

    async def __aenter__(self) -> "ErmaoClient":
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        await self.aclose()
        return False

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _json(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = await self._client.request(
                method, path, json=json, params=params
            )
        except httpx.HTTPError as exc:
            raise ErmaoClientError(f"请求 {path} 失败: {exc}") from exc
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if response.status_code >= 400:
            raise ErmaoClientError(
                _error_message(payload, f"HTTP {response.status_code} {path}")
            )
        if not isinstance(payload, dict):
            raise ErmaoClientError(f"{path} 返回结构异常")
        if payload.get("ok") is False:
            raise ErmaoClientError(_error_message(payload, f"{path} 调用失败"))
        return payload

    async def setup_status(self) -> bool:
        """系统是否已初始化。"""
        payload = await self._json("GET", "/api/auth/setup/status")
        data = payload.get("data")
        return bool(data.get("initialized")) if isinstance(data, dict) else False

    async def setup(self, email: str, password: str, name: str = "OneTap") -> None:
        """首次初始化管理员账号。"""
        await self._json(
            "POST",
            "/api/auth/setup",
            json={"email": email, "password": password, "name": name},
        )

    async def login(self, email: str, password: str) -> None:
        """登录并保存会话 Cookie。"""
        await self._json(
            "POST",
            "/api/auth/login",
            json={"email": email, "password": password},
        )

    async def me(self) -> dict[str, Any]:
        """读取当前登录用户。"""
        payload = await self._json("GET", "/api/auth/me")
        data = payload.get("data")
        return data if isinstance(data, dict) else {}

    async def change_email(self, current_password: str, email: str) -> None:
        """修改当前账号邮箱（需要当前密码）。"""
        await self._json(
            "PATCH",
            "/api/auth/account/email",
            json={
                "email": email,
                "currentPassword": current_password,
            },
        )

    async def change_password(
        self, current_password: str, new_password: str
    ) -> None:
        """修改当前账号密码；成功后服务端会注销当前会话。"""
        await self._json(
            "PATCH",
            "/api/auth/account/password",
            json={
                "currentPassword": current_password,
                "newPassword": new_password,
            },
        )

    async def libraries(self) -> list[dict[str, Any]]:
        """列出书库。"""
        payload = await self._json("GET", "/api/libraries")
        data = payload.get("data")
        items = data.get("libraries") if isinstance(data, dict) else None
        return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []

    async def create_library(
        self, root_path: str, name: str, organization_mode: str = "FLAT"
    ) -> dict[str, Any]:
        """创建书库。"""
        payload = await self._json(
            "POST",
            "/api/libraries",
            json={
                "rootPath": root_path,
                "name": name,
                "organizationMode": organization_mode,
            },
        )
        data = payload.get("data")
        library = data.get("library") if isinstance(data, dict) else None
        return library if isinstance(library, dict) else {}

    async def scan(self, library_id: str) -> dict[str, Any]:
        """触发书库扫描。"""
        payload = await self._json(
            "POST", f"/api/libraries/{library_id}/scan"
        )
        data = payload.get("data")
        return data if isinstance(data, dict) else {}

    async def import_tasks(
        self, library_id: str, page_size: int = 50
    ) -> list[dict[str, Any]]:
        """读取最近的导入任务。"""
        payload = await self._json(
            "GET",
            f"/api/libraries/{library_id}/import-tasks",
            params={"pageSize": page_size, "page": 1},
        )
        data = payload.get("data")
        tasks = data.get("tasks") if isinstance(data, dict) else None
        return [item for item in tasks if isinstance(item, dict)] if isinstance(tasks, list) else []
