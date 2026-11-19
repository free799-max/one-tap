"""MoviePilot 集成配置能力：通过系统设置接口装配下载器/媒体服务器。

MoviePilot 的结构化系统配置通过 ``GET/POST /api/v1/system/setting/{key}``
读写，认证头为 ``X-API-KEY: <API_TOKEN>``。响应为
``{"success": true, "data": {"value": ...}}`` 包装（部分版本 data 直接是值，
解析时做容错）。
"""

import json
import logging
import posixpath
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import ValidationError

from app.schemas.orchestration.moviepilot import (
    DirectoriesUpdateRequest,
    MediaLibrariesUpdateRequest,
)

logger = logging.getLogger(__name__)


class MoviePilotIntegrationMixin:
    """MoviePilot 下载器/媒体服务器自动装配能力。"""

    TIMEOUT = 10.0

    async def get_setting(
        self,
        base_url: str,
        api_key: str,
        key: str,
    ) -> tuple[bool, Any, str | None]:
        """读取系统设置项，返回 (是否成功, 值, 失败原因)。"""
        url = f"{base_url}/api/v1/system/setting/{key}"
        try:
            async with httpx.AsyncClient(timeout=self.TIMEOUT, follow_redirects=True) as client:
                resp = await client.get(url, headers={"X-API-KEY": api_key})
        except httpx.RequestError as exc:
            return False, None, f"无法连接到应用: {exc}"
        if resp.status_code >= 400:
            return False, None, f"读取系统设置失败: HTTP {resp.status_code}"
        try:
            payload = resp.json()
        except json.JSONDecodeError:
            return False, None, "系统设置接口返回异常"
        if isinstance(payload, dict) and payload.get("success") is False:
            return False, None, payload.get("message") or "读取系统设置失败"
        # 容错：标准结构为 {"success": true, "data": {"value": ...}}，
        # 部分版本 data 直接是值
        data = payload.get("data") if isinstance(payload, dict) else payload
        if isinstance(data, dict) and "value" in data:
            return True, data["value"], None
        return True, data, None

    async def get_classification_policy(
        self,
        base_url: str,
        api_key: str,
    ) -> tuple[bool, dict | None, str | None]:
        """读取 MoviePilot 当前媒体分类策略。"""
        ok, data, err = await self._api_get(
            base_url,
            api_key,
            "/api/v1/media/classification/policy",
        )
        if not ok:
            return False, None, f"读取分类策略失败: {err}"
        if not isinstance(data, dict):
            return False, None, "分类策略接口返回异常"
        return True, data, None

    async def list_storage_files(
        self,
        base_url: str,
        api_key: str,
        path: str,
    ) -> tuple[bool, list[dict], str | None]:
        """列出 MoviePilot 本地存储目录内容。"""
        try:
            async with httpx.AsyncClient(
                timeout=self.TIMEOUT, follow_redirects=True
            ) as client:
                resp = await client.post(
                    f"{base_url}/api/v1/storage/list",
                    headers={"X-API-KEY": api_key},
                    json={"storage": "local", "path": path},
                )
        except httpx.RequestError as exc:
            return False, [], f"无法连接到应用: {exc}"
        ok, data, err = self._parse_response(resp)
        if not ok:
            return False, [], err
        if not isinstance(data, list):
            return False, [], "目录列表接口返回异常"
        return True, [item for item in data if isinstance(item, dict)], None

    async def storage_dir_exists(
        self,
        base_url: str,
        api_key: str,
        path: str,
        cache: dict[str, tuple[bool, list[dict], str | None]] | None = None,
    ) -> tuple[bool, bool, str | None]:
        """检测本地目录是否存在；不存在的路径不会被空列表误判为存在。"""
        target = posixpath.normpath(str(path or "").strip())
        if not target.startswith("/"):
            return False, False, "目录路径必须是绝对路径"
        if target == "/":
            return True, True, None

        parent = posixpath.dirname(target)
        name = posixpath.basename(target)
        if cache is not None and parent in cache:
            ok, entries, err = cache[parent]
        else:
            ok, entries, err = await self.list_storage_files(
                base_url, api_key, parent
            )
            if cache is not None:
                cache[parent] = (ok, entries, err)
        if not ok:
            return False, False, err
        exists = any(
            str(item.get("type") or "") == "dir"
            and (
                str(item.get("name") or "") == name
                or posixpath.normpath(str(item.get("path") or "")) == target
            )
            for item in entries
        )
        return True, exists, None

    async def set_setting(
        self,
        base_url: str,
        api_key: str,
        key: str,
        value: Any,
    ) -> tuple[bool, str | None]:
        """写入系统设置项，返回 (是否成功, 失败原因)。

        MoviePilot 接受裸值作为请求体；部分版本要求包一层
        ``{"value": ...}``，失败时按此重试。
        """
        url = f"{base_url}/api/v1/system/setting/{key}"
        headers = {"X-API-KEY": api_key}
        try:
            async with httpx.AsyncClient(timeout=self.TIMEOUT, follow_redirects=True) as client:
                resp = await client.post(url, headers=headers, json=value)
                if resp.status_code in (400, 404, 405, 415, 422):
                    resp = await client.post(url, headers=headers, json={"value": value})
        except httpx.RequestError as exc:
            return False, f"无法连接到应用: {exc}"
        if resp.status_code >= 400:
            return False, f"写入系统设置失败: HTTP {resp.status_code}"
        try:
            payload = resp.json()
        except json.JSONDecodeError:
            return True, None
        if isinstance(payload, dict) and payload.get("success") is False:
            return False, payload.get("message") or "写入系统设置失败"
        return True, None

    async def setup_downloader(
        self,
        base_url: str,
        api_key: str,
        *,
        name: str,
        type_: str,
        host: str,
        port: int | None = None,
        username: str,
        password: str,
    ) -> tuple[bool, str | None]:
        """按 name 向 Downloaders 设置项 upsert 一个下载器并回读验证。

        MoviePilot 的 DownloaderConf 为嵌套结构：连接参数放在 ``config``
        子字典中（host 不含端口，port 单独一个字段），由下载器模块按
        ``Downloader(**config)`` 实例化。
        """
        parsed = urlparse(self._build_host_url(host, None))
        scheme = parsed.scheme or "http"
        entry = {
            "name": name,
            "type": type_,
            "default": True,
            "enabled": True,
            "config": {
                "host": f"{scheme}://{parsed.hostname}",
                "port": port or parsed.port or (443 if scheme == "https" else 80),
                "username": username,
                "password": password,
            },
            "path_mapping": [],
        }
        return await self._upsert_setting_entry(base_url, api_key, "Downloaders", entry)

    async def setup_mediaserver(
        self,
        base_url: str,
        api_key: str,
        *,
        name: str,
        type_: str,
        host: str,
        api_key_ms: str,
        play_host: str | None = None,
        webhook_host: str | None = None,
        source_username: str | None = None,
        source_password: str | None = None,
        access_code: str | None = None,
        **_: Any,
    ) -> tuple[bool, str | None]:
        """按 name 向 MediaServers 设置项 upsert 一个媒体服务器并回读验证。

        Jellyfin/Emby 的 ``config`` 为 host/apikey/play_host；飞牛影视
        （MoviePilot 侧类型 ``trimemedia``）使用账号密码登录，``config``
        为 host/username/password/access_code/play_host。
        webhook 回调地址不写入 MoviePilot（MP 侧是被回调方），
        webhook_host 仅为 Jellyfin/Emby 侧后续配置预留，当前不使用。
        """
        if type_ in ("fnos-media", "trimemedia"):
            config: dict[str, Any] = {
                "host": self._build_host_url(host, None),
            }
            if source_username:
                config["username"] = source_username
            if source_password:
                config["password"] = source_password
            if access_code:
                config["access_code"] = access_code
            if play_host:
                config["play_host"] = self._build_host_url(play_host, None)
            entry = {
                "name": name,
                # MoviePilot 的媒体服务器类型标识使用 trimemedia
                "type": "trimemedia",
                "enabled": True,
                "config": config,
                "sync_libraries": [],
            }
            return await self._upsert_setting_entry(
                base_url, api_key, "MediaServers", entry
            )

        config = {
            "host": self._build_host_url(host, None),
            "apikey": api_key_ms,
        }
        if play_host:
            config["play_host"] = self._build_host_url(play_host, None)
        entry = {
            "name": name,
            "type": type_,
            "enabled": True,
            "config": config,
            "sync_libraries": [],
        }
        return await self._upsert_setting_entry(base_url, api_key, "MediaServers", entry)

    async def get_directory_panel(
        self,
        base_url: str,
        api_key: str,
        shared_config: dict | None = None,
    ) -> tuple[bool, dict | None, str | None]:
        """读取目录、默认目录模板及媒体服务器媒体库详情。"""
        ok, directory_value, err = await self.get_setting(
            base_url, api_key, "Directories"
        )
        if not ok:
            return False, None, f"读取目录配置失败: {err}"

        ok, server_value, err = await self.get_setting(
            base_url, api_key, "MediaServers"
        )
        if not ok:
            return False, None, f"读取媒体服务器配置失败: {err}"

        ctx = {"shared_config": shared_config or {}}
        detected = {
            "download_path": self._shared_container_path(ctx, "download"),
            "library_path": self._shared_container_path(ctx, "library"),
        }
        media_servers = []
        for entry in server_value if isinstance(server_value, list) else []:
            if not isinstance(entry, dict) or not entry.get("name"):
                continue
            name = str(entry["name"])
            libs_ok, libraries, libs_err = await self._fetch_library_details(
                base_url, api_key, name
            )
            media_servers.append(
                {
                    "name": name,
                    "type": str(entry.get("type") or ""),
                    "enabled": bool(entry.get("enabled")),
                    "sync_libraries": self._normalize_sync_libraries(
                        entry.get("sync_libraries")
                    ),
                    "libraries": libraries,
                    "error": None if libs_ok else libs_err or "读取媒体库失败",
                }
            )

        return True, {
            "supported": True,
            "directories": self._normalize_directories(directory_value),
            "detected": detected,
            "directory_template": self._default_directory(**detected),
            "media_servers": media_servers,
        }, None

    async def save_directories(
        self,
        base_url: str,
        api_key: str,
        directories: list[dict],
        shared_config: dict | None = None,
    ) -> tuple[bool, dict | None, str | None]:
        """校验并整体保存 Directories，随后回读完整面板。"""
        try:
            request = DirectoriesUpdateRequest.model_validate(
                {"directories": directories}
            )
        except ValidationError as exc:
            return False, None, f"目录配置校验失败: {exc}"

        submitted = request.model_dump()["directories"]
        ok, err = await self.set_setting(
            base_url, api_key, "Directories", submitted
        )
        if not ok:
            return False, None, err or "写入目录配置失败"
        ok, panel, err = await self.get_directory_panel(
            base_url, api_key, shared_config
        )
        if not ok:
            return False, None, f"写入后回读失败: {err}"

        readback = {item["name"]: item for item in panel["directories"]}
        for expected in submitted:
            actual = readback.get(expected["name"])
            if actual is None:
                return False, None, (
                    f"写入后回读验证失败: 目录 {expected['name']} 不存在"
                )
            for key, value in expected.items():
                if key not in actual or actual[key] != value:
                    return False, None, (
                        f"写入后回读验证失败: 目录 {expected['name']} "
                        f"字段 {key} 不一致"
                    )
        return True, panel, None

    async def save_media_libraries(
        self,
        base_url: str,
        api_key: str,
        media_servers: list[dict],
        shared_config: dict | None = None,
    ) -> tuple[bool, dict | None, str | None]:
        """保存媒体库同步范围，随后回读完整面板。"""
        try:
            request = MediaLibrariesUpdateRequest.model_validate(
                {"media_servers": media_servers}
            )
        except ValidationError as exc:
            return False, None, f"媒体库同步配置校验失败: {exc}"

        submitted = request.model_dump()["media_servers"]
        ok, err = await self._update_media_library_sync(
            base_url, api_key, submitted
        )
        if not ok:
            return False, None, err
        ok, panel, err = await self.get_directory_panel(
            base_url, api_key, shared_config
        )
        if not ok:
            return False, None, f"写入后回读失败: {err}"

        readback = {item["name"]: item for item in panel["media_servers"]}
        for expected in submitted:
            actual = readback.get(expected["name"])
            if actual is None:
                return False, None, (
                    f"写入后回读验证失败: 媒体服务器 {expected['name']} 不存在"
                )
            expected_sync = self._normalize_sync_libraries(
                expected["sync_libraries"]
            )
            actual_sync = self._normalize_sync_libraries(
                actual.get("sync_libraries")
            )
            if actual_sync != expected_sync:
                return False, None, (
                    f"写入后回读验证失败: 媒体服务器 {expected['name']} "
                    "sync_libraries 不一致"
                )
        return True, panel, None

    async def _update_media_library_sync(
        self,
        base_url: str,
        api_key: str,
        media_servers: list[dict],
    ) -> tuple[bool, str | None]:
        """仅更新目标服务器的 sync_libraries，保留其他配置与服务器。"""
        try:
            request = MediaLibrariesUpdateRequest.model_validate(
                {"media_servers": media_servers}
            )
        except ValidationError as exc:
            return False, f"媒体库同步配置校验失败: {exc}"

        ok, value, err = await self.get_setting(base_url, api_key, "MediaServers")
        if not ok:
            return False, f"读取媒体服务器配置失败: {err}"
        servers = [
            dict(entry)
            for entry in (value if isinstance(value, list) else [])
            if isinstance(entry, dict)
        ]
        indexes_by_name: dict[str, list[int]] = {}
        for index, server in enumerate(servers):
            if server.get("name"):
                indexes_by_name.setdefault(str(server["name"]), []).append(index)

        updates = request.model_dump()["media_servers"]
        for update in updates:
            if update["name"] not in indexes_by_name:
                return False, f"媒体服务器不存在: {update['name']}"
            sync_libraries = update["sync_libraries"]
            if not sync_libraries or sync_libraries == ["all"]:
                continue
            libs_ok, libraries, libs_err = await self._fetch_library_details(
                base_url, api_key, update["name"]
            )
            if not libs_ok:
                return False, f"读取媒体服务器 {update['name']} 的媒体库失败: {libs_err}"
            valid_ids = {library["id"] for library in libraries}
            invalid_ids = [item for item in sync_libraries if item not in valid_ids]
            if invalid_ids:
                return False, (
                    f"媒体服务器 {update['name']} 的媒体库 ID 不存在: "
                    + ", ".join(invalid_ids)
                )

        for update in updates:
            for index in indexes_by_name[update["name"]]:
                servers[index]["sync_libraries"] = update["sync_libraries"]
        if not updates:
            return True, None
        ok, err = await self.set_setting(base_url, api_key, "MediaServers", servers)
        if not ok:
            return False, err or "写入媒体库同步配置失败"
        return True, None

    async def _fetch_library_details(
        self, base_url: str, api_key: str, server_name: str
    ) -> tuple[bool, list[dict], str | None]:
        """读取并归一化指定媒体服务器的媒体库详情。"""
        from urllib.parse import quote

        ok, data, err = await self._api_get(
            base_url,
            api_key,
            f"/api/v1/mediaserver/library?server={quote(server_name)}",
        )
        if not ok:
            return False, [], err
        if not isinstance(data, list):
            return False, [], "媒体库接口返回异常"

        libraries = []
        for item in data:
            if not isinstance(item, dict) or item.get("id") is None:
                continue
            path = item.get("path")
            if isinstance(path, str):
                paths = [path]
            elif isinstance(path, list):
                paths = [str(value) for value in path if value is not None]
            else:
                paths = []
            libraries.append(
                {
                    "id": str(item["id"]),
                    "name": str(item.get("name") or ""),
                    "path": paths,
                    "type": (
                        None if item.get("type") is None else str(item.get("type"))
                    ),
                }
            )
        return True, libraries, None

    @staticmethod
    def _normalize_sync_libraries(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value if item is not None]
        if isinstance(value, str):
            return [value]
        return []

    @staticmethod
    def _normalize_directories(value: Any) -> list[dict]:
        directories = []
        for entry in value if isinstance(value, list) else []:
            if not isinstance(entry, dict):
                continue
            normalized = dict(entry)
            for key in (
                "name",
                "download_path",
                "library_path",
                "transfer_type",
                "media_type",
                "media_category",
            ):
                normalized[key] = str(normalized.get(key) or "")
            directories.append(normalized)
        return directories

    async def list_plugins(
        self,
        base_url: str,
        api_key: str,
    ) -> tuple[bool, dict, str | None]:
        """查询已安装插件与插件市场列表。

        返回 (是否成功, {"installed": [...], "market": [...]}, 失败原因)。
        市场接口失败时降级为只返回已安装列表。
        """
        headers = {"X-API-KEY": api_key}
        try:
            async with httpx.AsyncClient(
                timeout=self.TIMEOUT, follow_redirects=True
            ) as client:
                resp = await client.get(f"{base_url}/api/v1/plugin/", headers=headers)
                if resp.status_code >= 400:
                    return False, {}, f"查询插件列表失败: HTTP {resp.status_code}"
                installed = self._parse_plugin_list(resp)

                market: list = []
                try:
                    market_resp = await client.get(
                        f"{base_url}/api/v1/plugin/market", headers=headers
                    )
                    if market_resp.status_code == 200:
                        market = self._parse_plugin_list(market_resp)
                except httpx.RequestError:
                    logger.warning("查询 MoviePilot 插件市场失败，仅返回已安装列表")
        except httpx.RequestError as exc:
            return False, {}, f"无法连接到应用: {exc}"
        return True, {"installed": installed, "market": market}, None

    async def install_plugin(
        self,
        base_url: str,
        api_key: str,
        plugin_id: str,
        repo_url: str | None = None,
    ) -> tuple[bool, str | None]:
        """安装插件，返回 (是否成功, 失败原因)。

        不同版本的仓库地址参数形态不一致，404/422 时依次尝试
        query 参数与不携带参数两种形态。
        """
        headers = {"X-API-KEY": api_key}
        url = f"{base_url}/api/v1/plugin/install/{plugin_id}"
        attempts: list[dict] = []
        if repo_url:
            attempts.append({"params": {"repo_url": repo_url}})
        attempts.append({})
        try:
            async with httpx.AsyncClient(
                timeout=self.TIMEOUT, follow_redirects=True
            ) as client:
                resp = None
                for kwargs in attempts:
                    resp = await client.post(url, headers=headers, **kwargs)
                    if resp.status_code not in (404, 422):
                        break
        except httpx.RequestError as exc:
            return False, f"无法连接到应用: {exc}"
        if resp.status_code >= 400:
            return False, f"安装插件失败: HTTP {resp.status_code}"
        try:
            payload = resp.json()
        except json.JSONDecodeError:
            return True, None
        if isinstance(payload, dict) and payload.get("success") is False:
            return False, payload.get("message") or "安装插件失败"
        return True, None

    @staticmethod
    def _parse_plugin_list(resp: httpx.Response) -> list:
        """容错解析插件列表：裸列表或 {"data": ...}/{"data": {"value": ...}} 包装。"""
        try:
            payload = resp.json()
        except json.JSONDecodeError:
            return []
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, dict):
                data = data.get("value")
            if isinstance(data, list):
                return data
        return []

    async def _upsert_setting_entry(
        self,
        base_url: str,
        api_key: str,
        key: str,
        entry: dict,
    ) -> tuple[bool, str | None]:
        """读取设置列表，按 name upsert 条目后写回并回读验证。"""
        ok, current, err = await self.get_setting(base_url, api_key, key)
        if not ok:
            return False, err
        # 容错：空值/非 list 一律视为空列表
        items = (
            [dict(i) for i in current if isinstance(i, dict)]
            if isinstance(current, list)
            else []
        )
        # 同名条目保留其余自定义字段，仅覆盖装配相关字段；
        # config 子字典做键级合并，保留用户在应用侧自加的连接参数
        for idx, item in enumerate(items):
            if item.get("name") == entry["name"]:
                merged = {**item, **entry}
                if isinstance(item.get("config"), dict) and isinstance(
                    entry.get("config"), dict
                ):
                    merged["config"] = {**item["config"], **entry["config"]}
                items[idx] = merged
                break
        else:
            items.append(entry)

        ok, err = await self.set_setting(base_url, api_key, key, items)
        if not ok:
            return False, err

        ok, after, err = await self.get_setting(base_url, api_key, key)
        if not ok:
            return False, f"写入后回读验证失败: {err}"
        names = (
            {i.get("name") for i in after if isinstance(i, dict)}
            if isinstance(after, list)
            else set()
        )
        if entry["name"] in names:
            return True, None
        return False, "写入后回读验证失败: 条目不存在"

    @staticmethod
    def _build_host_url(host: str, port: int | None) -> str:
        """拼接带协议与端口的完整访问地址。"""
        host = str(host).rstrip("/")
        if "://" not in host:
            host = f"http://{host}"
        if port is not None and urlparse(host).port is None:
            host = f"{host}:{port}"
        return host
