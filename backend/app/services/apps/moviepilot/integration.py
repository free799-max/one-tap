"""MoviePilot 集成配置能力：通过系统设置接口装配下载器/媒体服务器。

MoviePilot 的结构化系统配置通过 ``GET/POST /api/v1/system/setting/{key}``
读写，认证头为 ``X-API-KEY: <API_TOKEN>``。响应为
``{"success": true, "data": {"value": ...}}`` 包装（部分版本 data 直接是值，
解析时做容错）。
"""

import json
import logging
from typing import Any
from urllib.parse import urlparse

import httpx

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
