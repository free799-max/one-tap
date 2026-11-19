"""OpenList 应用客户端实现。

OpenList 4.x 首次启动没有管理员账号，网页端通过初始化向导写入站主
账号；这里复用同一套公开接口：``GET /api/public/init_status`` 判断是否
已初始化，未初始化时 ``POST /api/public/init/setup`` 一次性写入账号，
已初始化时用 ``POST /api/auth/login`` 验证统一凭据，必要时用旧密码
登录后经 ``POST /api/me/update`` 改密。
"""

from __future__ import annotations

import asyncio
import json
import time

import httpx

from app.services.apps.base import (
    AppClient,
    AppClientError,
    AuthVerifyResult,
    LogsReader,
    ProvisionResult,
)
from app.services.apps.registry import register_client


class OpenListClient(AppClient):
    """OpenList 网盘聚合文件列表程序客户端。"""

    name = "openlist"
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
        """初始化站主账号，或验证/更新已有管理员密码。"""
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
                    status = await self._init_status(client, base)
                    if status is None:
                        last_error = "初始化状态接口未就绪"
                    elif not status.get("initialized"):
                        created, error = await self._init_setup(
                            client, base, username, password
                        )
                        if created:
                            token = await self._login(
                                client, base, username, password
                            )
                            if token:
                                return ProvisionResult(
                                    ok=True,
                                    username=username,
                                    message="站主账号已创建并验证通过",
                                )
                            last_error = "初始化成功但登录验证失败"
                        else:
                            last_error = error or "初始化站主账号失败"
                    else:
                        token = await self._login(
                            client, base, username, password
                        )
                        if token:
                            return ProvisionResult(
                                ok=True,
                                username=username,
                                message="账号已存在并验证通过",
                            )
                        if previous_password and previous_password != password:
                            old = await self._login(
                                client, base, username, previous_password
                            )
                            if old:
                                updated, error = await self._update_password(
                                    client, base, old, username, password
                                )
                                if updated and await self._login(
                                    client, base, username, password
                                ):
                                    return ProvisionResult(
                                        ok=True,
                                        username=username,
                                        message="账号密码已更新并验证通过",
                                    )
                                last_error = f"修改密码失败: {error}"
                            else:
                                last_error = "已有管理员密码与统一密码不一致"
                        else:
                            return ProvisionResult(
                                ok=False,
                                message=(
                                    "OpenList 已初始化且密码与统一密码不一致，"
                                    "请在网页端修改密码，或清空数据卷后重新部署"
                                ),
                            )
            except httpx.RequestError as exc:
                last_error = f"无法连接到应用: {exc}"
            await asyncio.sleep(5)

        return ProvisionResult(ok=False, message=f"自动配置超时: {last_error}")

    async def verify_auth(self, config: dict) -> AuthVerifyResult:
        """校验 OpenList 访问地址与账号密码。"""
        base = str(config.get("url") or "").strip().rstrip("/")
        if not base:
            return AuthVerifyResult(valid=False, message="访问地址不能为空")

        auth_type = config.get("auth_type") or "none"
        if auth_type in ("api_key", "apikey"):
            return AuthVerifyResult(
                valid=False, message="OpenList 不支持 API Key，请使用账号密码"
            )

        try:
            async with httpx.AsyncClient(
                timeout=self.TIMEOUT,
                follow_redirects=True,
            ) as client:
                if auth_type != "basic":
                    status = await self._init_status(client, base)
                    if status is None:
                        return AuthVerifyResult(
                            valid=False, message="无法读取初始化状态"
                        )
                    return AuthVerifyResult(valid=True, message="服务可访问")

                username = str(config.get("username") or "")
                password = str(config.get("password") or "")
                if not username or not password:
                    return AuthVerifyResult(
                        valid=False, message="用户名和密码不能为空"
                    )
                token = await self._login(client, base, username, password)
                if token is None:
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

    @staticmethod
    async def _init_status(
        client: httpx.AsyncClient, base: str
    ) -> dict | None:
        """读取初始化状态，接口未就绪时返回 None。"""
        resp = await client.get(f"{base}/api/public/init_status")
        if resp.status_code != 200:
            return None
        payload = _as_dict(resp)
        if payload is None or payload.get("code") != 200:
            return None
        data = payload.get("data")
        return data if isinstance(data, dict) else None

    async def list_storages(
        self,
        url: str,
        username: str,
        password: str,
    ) -> list[dict]:
        """登录后读取 OpenList 的存储列表。"""
        base = str(url or "").strip().rstrip("/")
        if not base:
            raise AppClientError("OpenList 访问地址不能为空")
        async with httpx.AsyncClient(
            timeout=self.TIMEOUT,
            follow_redirects=True,
        ) as client:
            token = await self._login(client, base, username, password)
            if token is None:
                raise AppClientError("OpenList 登录失败，请检查账号密码")
            resp = await client.get(
                f"{base}/api/admin/storage/list",
                params={"page": 1, "per_page": 200},
                headers={"Authorization": token},
            )
            payload = _as_dict(resp)
            if payload is None or payload.get("code") != 200:
                message = (payload or {}).get("message") or "读取存储列表失败"
                raise AppClientError(str(message))
            data = payload.get("data")
            content = data.get("content") if isinstance(data, dict) else None
            return content if isinstance(content, list) else []

    async def save_storage(
        self,
        url: str,
        username: str,
        password: str,
        *,
        mount_path: str,
        driver: str,
        addition: dict,
        remark: str,
        storage_id: int | None = None,
    ) -> int:
        """创建或更新一个存储，返回存储 ID。"""
        base = str(url or "").strip().rstrip("/")
        if not base:
            raise AppClientError("OpenList 访问地址不能为空")
        body = {
            "mount_path": mount_path,
            "order": 0,
            "driver": driver,
            "cache_expiration": 30,
            "status": "work",
            "remark": remark,
            "webdav_policy": "native_proxy",
            "disable_index": False,
            "enable_sign": False,
            "seed_policy": "inherit",
            "addition": json.dumps(addition, ensure_ascii=False),
        }
        endpoint = "/api/admin/storage/create"
        if storage_id is not None:
            body["id"] = storage_id
            endpoint = "/api/admin/storage/update"

        async with httpx.AsyncClient(
            timeout=self.TIMEOUT,
            follow_redirects=True,
        ) as client:
            token = await self._login(client, base, username, password)
            if token is None:
                raise AppClientError("OpenList 登录失败，请检查账号密码")
            resp = await client.post(
                f"{base}{endpoint}",
                json=body,
                headers={"Authorization": token},
            )
            payload = _as_dict(resp)
            if payload is None or payload.get("code") != 200:
                message = (payload or {}).get("message") or "保存存储失败"
                raise AppClientError(str(message))
            data = payload.get("data")
            created_id = data.get("id") if isinstance(data, dict) else None
            result_id = created_id or storage_id
            if result_id is None:
                raise AppClientError("存储已保存但未返回 ID")
            # 新建/更新的存储需要重新加载才会出现在文件列表里
            await client.post(
                f"{base}/api/admin/storage/load_all",
                headers={"Authorization": token},
            )
            return int(result_id)

    async def delete_storage(
        self,
        url: str,
        username: str,
        password: str,
        storage_id: int,
    ) -> None:
        """删除一个存储（校验失败的新存储回滚用）。"""
        base = str(url or "").strip().rstrip("/")
        if not base:
            raise AppClientError("OpenList 访问地址不能为空")
        async with httpx.AsyncClient(
            timeout=self.TIMEOUT,
            follow_redirects=True,
        ) as client:
            token = await self._login(client, base, username, password)
            if token is None:
                raise AppClientError("OpenList 登录失败，请检查账号密码")
            resp = await client.post(
                f"{base}/api/admin/storage/delete",
                params={"id": storage_id},
                headers={"Authorization": token},
            )
            payload = _as_dict(resp)
            if payload is None or payload.get("code") != 200:
                message = (payload or {}).get("message") or "删除存储失败"
                raise AppClientError(str(message))

    async def list_files(
        self,
        url: str,
        username: str,
        password: str,
        path: str,
        per_page: int = 10,
    ) -> list[dict]:
        """列出某个挂载路径下的条目，用于校验存储可用。"""
        base = str(url or "").strip().rstrip("/")
        if not base:
            raise AppClientError("OpenList 访问地址不能为空")
        async with httpx.AsyncClient(
            timeout=self.TIMEOUT,
            follow_redirects=True,
        ) as client:
            token = await self._login(client, base, username, password)
            if token is None:
                raise AppClientError("OpenList 登录失败，请检查账号密码")
            resp = await client.post(
                f"{base}/api/fs/list",
                json={
                    "path": path,
                    "password": "",
                    "page": 1,
                    "per_page": per_page,
                    "refresh": True,
                },
                headers={"Authorization": token},
            )
            payload = _as_dict(resp)
            if payload is None or payload.get("code") != 200:
                message = (payload or {}).get("message") or "列目录失败"
                raise AppClientError(str(message))
            data = payload.get("data")
            content = data.get("content") if isinstance(data, dict) else None
            return content if isinstance(content, list) else []

    @staticmethod
    async def _init_setup(
        client: httpx.AsyncClient,
        base: str,
        username: str,
        password: str,
    ) -> tuple[bool, str | None]:
        """写入站主账号；接口已初始化或校验失败时回传原因。"""
        resp = await client.post(
            f"{base}/api/public/init/setup",
            json={"username": username, "password": password},
        )
        payload = _as_dict(resp)
        if payload is None:
            return False, f"初始化接口返回异常: HTTP {resp.status_code}"
        if payload.get("code") == 200:
            return True, None
        return False, str(payload.get("message") or "初始化失败")

    @staticmethod
    async def _login(
        client: httpx.AsyncClient,
        base: str,
        username: str,
        password: str,
    ) -> str | None:
        """登录并返回访问令牌。"""
        resp = await client.post(
            f"{base}/api/auth/login",
            json={"username": username, "password": password},
        )
        payload = _as_dict(resp)
        if payload is None or payload.get("code") != 200:
            return None
        data = payload.get("data")
        if not isinstance(data, dict):
            return None
        token = str(data.get("token") or "").strip()
        return token or None

    @staticmethod
    async def _update_password(
        client: httpx.AsyncClient,
        base: str,
        token: str,
        username: str,
        password: str,
    ) -> tuple[bool, str | None]:
        """用已有会话把当前用户密码改成统一密码。"""
        resp = await client.post(
            f"{base}/api/me/update",
            json={"username": username, "password": password},
            headers={"Authorization": token},
        )
        payload = _as_dict(resp)
        if payload is None:
            return False, f"更新账号接口返回异常: HTTP {resp.status_code}"
        if payload.get("code") == 200:
            return True, None
        return False, str(payload.get("message") or "更新账号失败")


def _as_dict(resp: httpx.Response) -> dict | None:
    """解析 OpenList 的 {code, message, data} 响应体。"""
    try:
        payload = resp.json()
    except (json.JSONDecodeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


register_client(OpenListClient.name, OpenListClient)
