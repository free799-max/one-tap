"""qBittorrent 配置健康检测与自动修复能力。

通过 ``/api/v2/app/preferences`` 读取/修复 WebUI 偏好设置，
登录沿用客户端已有的 basic 登录逻辑（必要时按容器内端口伪装 Host 头）。
"""

import json
import logging
from urllib.parse import urlparse

import httpx

from app.services.apps.base import FixResultItem, InspectItem, InspectReport

logger = logging.getLogger(__name__)


class QBittorrentInspectionMixin:
    """qBittorrent 配置健康检测能力。"""

    TIMEOUT = 10.0

    async def inspect(self, ctx: dict) -> InspectReport:
        """检测账号认证与关键偏好设置。"""
        base_url = str(ctx.get("url") or "").rstrip("/")
        username = ctx.get("username") or ""
        password = ctx.get("password") or ""
        if not base_url or not username or not password:
            return InspectReport(
                supported=True,
                items=[
                    InspectItem(
                        id="auth",
                        title="账号认证",
                        status="error",
                        message="缺少访问地址或账号密码，无法检测",
                    )
                ],
            )

        headers = self._session_headers(ctx, base_url)
        try:
            async with httpx.AsyncClient(
                timeout=self.TIMEOUT, follow_redirects=True
            ) as client:
                if not await self._try_login(
                    client, base_url, username, password, headers=headers
                ):
                    # 认证失败时后续项无意义，只报 auth 一项
                    return InspectReport(
                        supported=True,
                        items=[
                            InspectItem(
                                id="auth",
                                title="账号认证",
                                status="error",
                                message="登录被拒绝，请检查用户名/密码",
                            )
                        ],
                    )
                auth_item = InspectItem(
                    id="auth", title="账号认证", status="ok", message="登录成功"
                )
                try:
                    version_resp = await client.get(
                        f"{base_url}/api/v2/app/version", headers=headers
                    )
                    if version_resp.status_code == 200:
                        auth_item.current_value = version_resp.text.strip()
                except httpx.RequestError:
                    pass
                prefs_resp = await client.get(
                    f"{base_url}/api/v2/app/preferences", headers=headers
                )
                prefs_resp.raise_for_status()
                prefs = prefs_resp.json()
        except httpx.RequestError as exc:
            return InspectReport(
                supported=True,
                items=[
                    InspectItem(
                        id="auth",
                        title="账号认证",
                        status="error",
                        message=f"无法连接到应用: {exc}",
                    )
                ],
            )

        return InspectReport(
            supported=True, items=[auth_item, *self._build_items(ctx, prefs)]
        )

    async def apply_fix(self, ctx: dict, item_ids: list[str]) -> list[FixResultItem]:
        """通过 setPreferences 修复可自动修复的检测项。"""
        preferences: dict = {}
        fixable_ids: set[str] = set()
        for item_id in item_ids:
            patch = self._fix_preferences(ctx, item_id)
            if patch is None:
                continue
            fixable_ids.add(item_id)
            preferences.update(patch)

        results = [
            FixResultItem(id=item_id, ok=False, message="该检测项不支持自动修复")
            for item_id in item_ids
            if item_id not in fixable_ids
        ]
        if not preferences:
            return results

        base_url = str(ctx.get("url") or "").rstrip("/")
        headers = self._session_headers(ctx, base_url)
        try:
            async with httpx.AsyncClient(
                timeout=self.TIMEOUT, follow_redirects=True
            ) as client:
                if not await self._try_login(
                    client,
                    base_url,
                    ctx.get("username") or "",
                    ctx.get("password") or "",
                    headers=headers,
                ):
                    error = "登录被拒绝，无法修复"
                    return results + [
                        FixResultItem(id=item_id, ok=False, message=error)
                        for item_id in item_ids
                        if item_id in fixable_ids
                    ]
                resp = await client.post(
                    f"{base_url}/api/v2/app/setPreferences",
                    data={"json": json.dumps(preferences)},
                    headers=headers,
                )
                resp.raise_for_status()
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            return results + [
                FixResultItem(id=item_id, ok=False, message=f"修复失败: {exc}")
                for item_id in item_ids
                if item_id in fixable_ids
            ]

        return results + [
            FixResultItem(id=item_id, ok=True, message="已修复")
            for item_id in item_ids
            if item_id in fixable_ids
        ]

    def _build_items(self, ctx: dict, prefs: dict) -> list[InspectItem]:
        """根据当前偏好设置构建各检测项。"""
        items: list[InspectItem] = []

        # Host 头校验：开启时经映射端口访问会被 401
        host_header_on = prefs.get("web_ui_host_header_validation_enabled", True)
        items.append(
            InspectItem(
                id="host_header_validation",
                title="Host 头校验",
                status="warn" if host_header_on else "ok",
                message=(
                    "已开启，经 Docker 映射端口访问可能被拒绝（401）"
                    if host_header_on
                    else None
                ),
                current_value="开启" if host_header_on else "关闭",
                expected_value="关闭",
                fixable=bool(host_header_on),
            )
        )

        # 监听端口：应与映射到宿主端口的容器内端口一致
        expected_port = ctx.get("expected_listen_port")
        if expected_port:
            current_port = prefs.get("listen_port")
            ok = current_port == expected_port
            items.append(
                InspectItem(
                    id="listening_port",
                    title="监听端口",
                    status="ok" if ok else "warn",
                    message=None if ok else "与容器端口映射不一致，可能影响连接性",
                    current_value=str(current_port) if current_port else None,
                    expected_value=str(expected_port),
                    fixable=not ok,
                )
            )

        # 队列管理：建议开启，避免大量任务同时做种/下载拖垮磁盘
        queueing_on = prefs.get("queueing_enabled", False)
        items.append(
            InspectItem(
                id="queueing",
                title="队列管理",
                status="ok" if queueing_on else "warn",
                message=None if queueing_on else "建议开启队列管理以限制并发任务",
                current_value="开启" if queueing_on else "关闭",
                expected_value="开启",
                fixable=not queueing_on,
            )
        )

        # 网络发现：DHT/PeX/LSD 建议全部开启，提升 peer 发现能力
        discovery = {k: bool(prefs.get(k, False)) for k in ("dht", "pex", "lsd")}
        all_on = all(discovery.values())
        items.append(
            InspectItem(
                id="network_discovery",
                title="网络发现（DHT/PeX/LSD）",
                status="ok" if all_on else "warn",
                message=None
                if all_on
                else "部分未开启: "
                + ", ".join(k.upper() for k, on in discovery.items() if not on),
                current_value=", ".join(
                    k.upper() for k, on in discovery.items() if on
                )
                or "全部关闭",
                expected_value="全部开启",
                fixable=not all_on,
            )
        )
        return items

    def _fix_preferences(self, ctx: dict, item_id: str) -> dict | None:
        """返回修复指定检测项需要写入的偏好设置，不可修复返回 None。"""
        if item_id == "host_header_validation":
            return {"web_ui_host_header_validation_enabled": False}
        if item_id == "listening_port":
            expected_port = ctx.get("expected_listen_port")
            return {"listen_port": expected_port} if expected_port else None
        if item_id == "queueing":
            return {"queueing_enabled": True}
        if item_id == "network_discovery":
            return {"dht": True, "pex": True, "lsd": True}
        return None

    def _session_headers(self, ctx: dict, base_url: str) -> dict:
        """构造会话请求头；端口映射场景按容器内端口伪装 Host/Referer。"""
        parsed = urlparse(base_url)
        host_header_port = ctx.get("host_header_port")
        if host_header_port:
            authority = f"{parsed.hostname}:{host_header_port}"
            return {"Host": authority, "Referer": f"{parsed.scheme}://{authority}"}
        return {"Referer": base_url}
