"""Jellyfin 配置健康检测与 Webhook 反配能力（Emby 复用）。

认证检测同时校验 API Key 与账号密码：
- API Key 失效但账号密码可登录时，可自动重新登录并更新 API Key；
- API Key 仍有效但账号密码不匹配时，可用管理员 Token 把密码重置为配置值。

Webhook 插件（jellyfin-plugin-webhook）安装后需重启，其配置为复杂的多类型
destination 对象，官方仅提供 Web UI 配置路径，没有稳定的纯 API 配置形态，
因此 Webhook 反配采用降级方案：检测项给出手动配置指引，建议的 webhook URL
由装配阶段写入 integrations 供前端展示。
"""

import json
import logging
from dataclasses import dataclass, field

import httpx

from app.services.apps.base import FixResultItem, InspectItem, InspectReport

logger = logging.getLogger(__name__)

# 支持自动修复的认证类检测项
_AUTH_FIX_ITEMS = {"auth", "credentials"}


@dataclass
class _AuthResolution:
    """认证解析结果：可用 Token、版本信息与待展示的认证检测项。"""

    token: str | None = None
    version: str | None = None
    items: list[InspectItem] = field(default_factory=list)


class JellyfinInspectionMixin:
    """Jellyfin 配置健康检测能力。"""

    TIMEOUT = 10.0

    async def inspect(self, ctx: dict) -> InspectReport:
        """检测认证、媒体库与 Webhook 插件状态。"""
        base_url = str(ctx.get("url") or "").rstrip("/")
        if not base_url:
            return InspectReport(
                supported=True,
                items=[
                    InspectItem(
                        id="auth",
                        title="API Key 认证",
                        status="error",
                        message="缺少访问地址，无法检测",
                    )
                ],
            )

        username = str(ctx.get("username") or "")
        password = str(ctx.get("password") or "")
        api_key = str(ctx.get("api_key") or "")

        try:
            async with httpx.AsyncClient(
                timeout=self.TIMEOUT, follow_redirects=True
            ) as client:
                resolution = await self._resolve_auth(
                    client, base_url, api_key, username, password
                )
                items = list(resolution.items)
                if resolution.token is None:
                    return InspectReport(supported=True, items=items)

                headers = self._token_headers(resolution.token)
                items.append(
                    await self._inspect_libraries(client, base_url, headers, ctx)
                )
                plugin_item, plugin = await self._inspect_webhook_plugin(
                    client, base_url, headers
                )
                items.append(plugin_item)
                items.append(
                    await self._inspect_webhook_configured(
                        client, base_url, headers, ctx, plugin
                    )
                )
                return InspectReport(supported=True, items=items)
        except httpx.HTTPError as exc:
            return InspectReport(
                supported=True,
                items=[
                    InspectItem(
                        id="auth",
                        title="API Key 认证",
                        status="error",
                        message=f"无法连接到应用: {exc}",
                    )
                ],
            )

    async def apply_fix(self, ctx: dict, item_ids: list[str]) -> list[FixResultItem]:
        """修复认证类检测项：恢复 API Key 与账号密码的一致性。"""
        results: list[FixResultItem] = []
        targets = [item_id for item_id in item_ids if item_id in _AUTH_FIX_ITEMS]
        for item_id in item_ids:
            if item_id not in _AUTH_FIX_ITEMS:
                results.append(
                    FixResultItem(
                        id=item_id,
                        ok=False,
                        message="该检测项需手动处理，不支持自动修复",
                    )
                )
        if not targets:
            return results

        ok, message = await self._recover_auth(ctx)
        results.extend(
            FixResultItem(id=item_id, ok=ok, message=message)
            for item_id in targets
        )
        return results

    # ---------------- 认证解析 ----------------

    async def _resolve_auth(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        api_key: str,
        username: str,
        password: str,
    ) -> _AuthResolution:
        """分别校验 API Key 与账号密码，返回可用的管理员 Token。"""
        api_key_ok = False
        version: str | None = None
        api_key_error: str | None = None

        if api_key:
            status, info = await self._system_info(client, base_url, api_key)
            if status == 200:
                api_key_ok = True
                version = (info or {}).get("Version")
            elif status in (401, 403):
                api_key_error = "API Key 已失效"
            else:
                return _AuthResolution(
                    items=[
                        InspectItem(
                            id="auth",
                            title="API Key 认证",
                            status="error",
                            message=f"服务响应异常: HTTP {status}",
                        )
                    ]
                )
        else:
            api_key_error = "未配置 API Key"

        creds_configured = bool(username and password)
        creds_login = None
        creds_status: int | None = None
        creds_admin = False
        login_info: dict | None = None
        if creds_configured:
            creds_login = await self._login(client, base_url, username, password)
            if creds_login:
                creds_status, login_info = await self._system_info(
                    client, base_url, creds_login["AccessToken"]
                )
                creds_admin = creds_status == 200

        # 登录成功但 Token 校验失败通常不是权限问题（如认证头形态不兼容）
        creds_token_rejected = creds_login is not None and creds_status not in (200, 403)
        creds_forbidden = creds_login is not None and creds_status == 403

        token = api_key if api_key_ok else None
        if token is None and creds_admin:
            token = creds_login["AccessToken"]
            version = (login_info or {}).get("Version")

        # 两种凭据都不可用时，判断全新实例是否还能通过启动向导恢复
        wizard_incomplete = False
        if token is None and not creds_admin:
            wizard_incomplete = await self._startup_wizard_incomplete(
                client, base_url
            )

        items: list[InspectItem] = []
        if api_key_ok:
            items.append(
                InspectItem(
                    id="auth",
                    title="API Key 认证",
                    status="ok",
                    message="认证成功",
                    current_value=version,
                )
            )
        elif creds_admin:
            items.append(
                InspectItem(
                    id="auth",
                    title="API Key 认证",
                    status="error",
                    message=f"{api_key_error}，账号密码可正常登录，可自动重新获取",
                    fixable=True,
                )
            )
        else:
            if creds_forbidden:
                detail = "账号可登录但无管理员权限，无法完成管理操作"
            elif creds_token_rejected:
                detail = "账号可登录但 Token 校验失败，可自动重新认证"
            elif creds_configured:
                detail = "账号密码也无法登录"
            else:
                detail = "未保存账号密码，无法自动修复"
            items.append(
                InspectItem(
                    id="auth",
                    title="API Key 认证",
                    status="error",
                    message=f"{api_key_error}；{detail}",
                    fixable=creds_configured
                    and (wizard_incomplete or creds_token_rejected),
                )
            )

        if creds_configured:
            if creds_admin:
                items.append(
                    InspectItem(
                        id="credentials",
                        title="账号密码",
                        status="ok",
                        message="账号密码验证通过",
                        current_value=username,
                    )
                )
            elif creds_forbidden:
                items.append(
                    InspectItem(
                        id="credentials",
                        title="账号密码",
                        status="warn",
                        message="账号可登录但无管理员权限，管理操作可能失败",
                        current_value=username,
                        fixable=api_key_ok,
                    )
                )
            elif creds_token_rejected:
                items.append(
                    InspectItem(
                        id="credentials",
                        title="账号密码",
                        status="warn",
                        message="账号密码登录成功，但服务端未接受返回的 Token，可自动重新认证",
                        current_value=username,
                        fixable=True,
                    )
                )
            else:
                items.append(
                    InspectItem(
                        id="credentials",
                        title="账号密码",
                        status="warn" if api_key_ok else "error",
                        message=(
                            "账号密码登录失败，可基于有效 API Key 重置为目标密码"
                            if api_key_ok
                            else "账号密码登录失败，且当前 API Key 无效"
                        ),
                        current_value=username,
                        fixable=api_key_ok or wizard_incomplete,
                    )
                )

        return _AuthResolution(token=token, version=version, items=items)

    # ---------------- 认证修复 ----------------

    async def _recover_auth(self, ctx: dict) -> tuple[bool, str]:
        """恢复认证：优先账号密码登录，其次用有效 Token 重置密码。"""
        base_url = str(ctx.get("url") or "").rstrip("/")
        username = str(ctx.get("username") or "")
        password = str(ctx.get("password") or "")
        api_key = str(ctx.get("api_key") or "")

        if not base_url:
            return False, "访问地址不能为空，无法修复"
        if not username or not password:
            return False, "缺少账号密码，无法自动修复"

        try:
            async with httpx.AsyncClient(
                timeout=self.TIMEOUT, follow_redirects=True
            ) as client:
                login = await self._login(client, base_url, username, password)
                if login:
                    token = login["AccessToken"]
                    status, _ = await self._system_info(client, base_url, token)
                    if status == 200:
                        # 用持久化设备重新登录：检测设备的会话会随下次检测被顶掉，
                        # 不能直接作为长期 API Token 保存
                        persisted = await self._login(
                            client,
                            base_url,
                            username,
                            password,
                            device_id=self._API_DEVICE_ID,
                        )
                        if persisted is None:
                            return False, "账号密码验证成功，但重新登录获取 Token 失败"
                        return await self._persist_recovered(
                            ctx,
                            persisted["AccessToken"],
                            username,
                            password,
                            "已使用账号密码重新认证并更新 API Key",
                        )
                    # 账号可登录但非管理员：继续尝试用有效 API Key 重置管理员密码

                if api_key:
                    status, _ = await self._system_info(client, base_url, api_key)
                    if status == 200:
                        return await self._reset_password(
                            client, ctx, base_url, api_key, username, password
                        )

                if await self._startup_wizard_incomplete(client, base_url):
                    return await self._finish_startup_wizard(
                        client, ctx, base_url, username, password
                    )
        except httpx.HTTPError as exc:
            return False, f"无法连接到应用: {exc}"

        return (
            False,
            "API Key 与账号密码均无法登录，请在 Jellyfin 中重置管理员密码后重试",
        )

    async def _reset_password(
        self,
        client: httpx.AsyncClient,
        ctx: dict,
        base_url: str,
        token: str,
        username: str,
        password: str,
    ) -> tuple[bool, str]:
        """用管理员 Token 把目标账号密码重置为配置值，并更新 API Key。"""
        headers = self._token_headers(token)
        user = await self._find_user(client, base_url, headers, username)
        if user is None:
            return False, "未找到可重置的管理员账号"
        user_id = user.get("Id")
        if not user_id:
            return False, "账号信息缺少 ID，无法重置密码"
        display_name = str(user.get("Name") or username)

        resp = await client.post(
            f"{base_url}/Users/{user_id}/Password",
            headers=headers,
            json={"CurrentPw": "", "NewPw": password},
        )
        if resp.status_code not in (200, 204):
            # 服务端要求当前密码：先清除密码再设置目标密码（管理员操作）
            reset = await client.post(
                f"{base_url}/Users/{user_id}/Password",
                headers=headers,
                json={"ResetPassword": True},
            )
            if reset.status_code not in (200, 204):
                return False, f"重置密码失败: HTTP {reset.status_code}"
            resp = await client.post(
                f"{base_url}/Users/{user_id}/Password",
                headers=headers,
                json={"CurrentPw": "", "NewPw": password},
            )
            if resp.status_code not in (200, 204):
                return False, f"设置新密码失败: HTTP {resp.status_code}"

        login = await self._login(
            client, base_url, display_name, password, device_id=self._API_DEVICE_ID
        )
        if login is None:
            return False, "密码已重置，但使用新密码登录失败"
        return await self._persist_recovered(
            ctx,
            login["AccessToken"],
            display_name,
            password,
            f"已重置账号 {display_name} 的密码并更新 API Key",
        )

    async def _finish_startup_wizard(
        self,
        client: httpx.AsyncClient,
        ctx: dict,
        base_url: str,
        username: str,
        password: str,
    ) -> tuple[bool, str]:
        """启动向导未完成时创建管理员并完成向导。"""
        # 全新实例（Jellyfin 12）数据库里还没有用户时 POST 会返回 404，
        # 先 GET 触发服务端创建默认用户
        await client.get(f"{base_url}/Startup/User")
        resp = await client.post(
            f"{base_url}/Startup/User",
            json={"Name": username, "Password": password},
        )
        if resp.status_code not in (200, 204):
            return False, "启动向导已完成且现有凭据无效，无法自动创建管理员"
        await client.post(f"{base_url}/Startup/Complete")

        login = await self._login(
            client, base_url, username, password, device_id=self._API_DEVICE_ID
        )
        if login is None:
            return False, "管理员已创建，但登录验证失败"
        status, _ = await self._system_info(client, base_url, login["AccessToken"])
        if status != 200:
            return False, "管理员已创建，但账号没有管理权限"
        return await self._persist_recovered(
            ctx,
            login["AccessToken"],
            username,
            password,
            "已通过启动向导重建管理员并更新 API Key",
        )

    async def _persist_recovered(
        self,
        ctx: dict,
        token: str,
        username: str,
        password: str,
        message: str,
    ) -> tuple[bool, str]:
        """保存恢复后的凭据并重新装配依赖方（如 MoviePilot 中的媒体服务器）。"""
        save = ctx.get("save_app_config")
        if not callable(save):
            return False, "认证已恢复，但当前上下文无法保存新配置"
        try:
            await save(
                {
                    "api_key": token,
                    "auth_type": "basic",
                    "username": username,
                    "password": password,
                    "provisioned": True,
                }
            )
        except Exception:
            logger.exception("保存应用认证信息失败")
            return False, "认证已恢复，但保存新配置失败"

        wire = ctx.get("wire")
        if callable(wire):
            try:
                await wire()
            except Exception:
                logger.exception("认证恢复后重新装配失败")
        return True, message

    async def _find_user(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        headers: dict,
        username: str,
    ) -> dict | None:
        """查找目标用户；同名用户非管理员时回退到 Token 所属管理员。"""
        try:
            resp = await client.get(f"{base_url}/Users", headers=headers)
        except httpx.HTTPError:
            resp = None
        if resp is not None and resp.status_code == 200:
            try:
                users = resp.json()
            except ValueError:
                users = None
            if isinstance(users, list):
                matched = None
                lowered = username.lower()
                for user in users:
                    if not isinstance(user, dict):
                        continue
                    if str(user.get("Name") or "").lower() == lowered:
                        matched = user
                        break
                if matched is not None:
                    policy = matched.get("Policy") or {}
                    if policy.get("IsAdministrator", True):
                        return matched

        try:
            me = await client.get(f"{base_url}/Users/Me", headers=headers)
        except httpx.HTTPError:
            return None
        if me.status_code != 200:
            return None
        try:
            data = me.json()
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    # ---------------- HTTP 辅助 ----------------

    async def _system_info(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        token: str | None,
    ) -> tuple[int | None, dict | None]:
        """读取系统信息，返回状态码与响应体（Token 无效时为 401/403）。"""
        if not token:
            return None, None
        resp = await client.get(
            f"{base_url}/System/Info", headers=self._token_headers(token)
        )
        if resp.status_code != 200:
            return resp.status_code, None
        try:
            data = resp.json()
        except ValueError:
            return 200, None
        return 200, data if isinstance(data, dict) else None

    async def _login(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        username: str,
        password: str,
        device_id: str | None = None,
    ) -> dict | None:
        """用户名密码登录，成功返回包含 AccessToken 的响应体。

        Jellyfin 12 中同一设备重复登录会使旧 Token 失效，因此默认使用
        独立的检测设备号，避免顶掉已保存的 API Token。
        """
        if not username or not password:
            return None
        try:
            resp = await client.post(
                f"{base_url}/Users/AuthenticateByName",
                headers={
                    "Authorization": self._device_header(
                        device_id or self._INSPECT_DEVICE_ID
                    )
                },
                json={"Username": username, "Pw": password},
            )
        except httpx.HTTPError:
            return None
        if resp.status_code != 200:
            return None
        try:
            data = resp.json()
        except ValueError:
            return None
        if not isinstance(data, dict) or not data.get("AccessToken"):
            return None
        return data

    async def _startup_wizard_incomplete(
        self,
        client: httpx.AsyncClient,
        base_url: str,
    ) -> bool:
        """启动向导未完成时可重新创建管理员（新实例场景）。"""
        try:
            resp = await client.get(f"{base_url}/System/Info/Public")
        except httpx.HTTPError:
            return False
        if resp.status_code != 200:
            return False
        try:
            data = resp.json()
        except ValueError:
            return False
        return isinstance(data, dict) and data.get("StartupWizardCompleted") is False

    def _token_headers(self, token: str) -> dict:
        """Jellyfin/Emby 统一的 Token 与设备信息头。

        Jellyfin 12 起只接受 Authorization 头里的 ``Token="..."`` 形态，
        单独的 ``X-Emby-Token`` 会被拒绝；同时保留该头兼容旧版本。
        """
        return {
            "Authorization": f'{self._AUTHORIZATION_HEADER}, Token="{token}"',
            "X-Emby-Token": token,
        }

    @staticmethod
    def _device_header(device_id: str) -> str:
        """构造不含 Token 的设备信息头（登录/校验用）。"""
        return (
            'MediaBrowser Client="OneTap", Device="NAS", '
            f'DeviceId="{device_id}", Version="1.0.0"'
        )

    # ---------------- 媒体库 / Webhook 检测 ----------------

    async def _inspect_libraries(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        headers: dict,
        ctx: dict,
    ) -> InspectItem:
        """检测媒体库：至少一个媒体库且位置与共享媒体目录有交集。"""
        resp = await client.get(f"{base_url}/Library/VirtualFolders", headers=headers)
        resp.raise_for_status()
        folders = resp.json() if isinstance(resp.json(), list) else []
        if not folders:
            return InspectItem(
                id="libraries",
                title="媒体库",
                status="error",
                message="尚未创建媒体库，请在媒体服务器中添加媒体库",
            )

        locations = [
            loc
            for folder in folders
            for loc in (folder.get("Locations") or [])
        ]
        expected_dir = self._expected_media_dir(ctx.get("shared_config"))
        if not expected_dir:
            return InspectItem(
                id="libraries",
                title="媒体库",
                status="ok",
                message=f"已配置 {len(folders)} 个媒体库",
                current_value=", ".join(locations) or None,
            )
        expected = expected_dir.rstrip("/")
        matched = any(
            loc == expected or loc.startswith(expected + "/") or expected.startswith(loc.rstrip("/") + "/")
            for loc in locations
        )
        return InspectItem(
            id="libraries",
            title="媒体库",
            status="ok" if matched else "warn",
            message=(
                None
                if matched
                else "媒体库位置与共享媒体目录无交集，下载的媒体将无法入库"
            ),
            current_value=", ".join(locations) or None,
            expected_value=expected_dir,
        )

    async def _inspect_webhook_plugin(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        headers: dict,
    ) -> tuple[InspectItem, dict | None]:
        """检测 Webhook 插件是否安装，返回检测项与插件信息。"""
        plugin = await self._find_webhook_plugin(client, base_url, headers)
        if plugin is None:
            return InspectItem(
                id="webhook_plugin",
                title="Webhook 插件",
                status="warn",
                message="未安装 Webhook 插件（Emby 可使用内置通知）；"
                "如需联动 MoviePilot 请在插件目录安装 Webhook 并重启",
            ), None
        return InspectItem(
            id="webhook_plugin",
            title="Webhook 插件",
            status="ok",
            message="已安装",
            current_value=plugin.get("Version"),
        ), plugin

    async def _inspect_webhook_configured(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        headers: dict,
        ctx: dict,
        plugin: dict | None,
    ) -> InspectItem:
        """检测 Webhook 是否已指向 MoviePilot。

        纯 API 无法可靠写入插件配置（降级方案），该项始终不 fixable；
        通过只读接口检查插件配置中是否已出现 MoviePilot 回调地址。
        """
        wiring = ctx.get("mediaserver_wiring") or {}
        webhook_url = wiring.get("webhook_url")
        if plugin is None:
            return InspectItem(
                id="webhook_configured",
                title="Webhook 反配 MoviePilot",
                status="warn",
                message="安装 Webhook 插件后再检测",
                expected_value=webhook_url,
            )
        try:
            config_text = await self._read_plugin_configuration(
                client, base_url, headers, plugin
            )
        except httpx.HTTPError:
            config_text = None
        if config_text is None:
            return InspectItem(
                id="webhook_configured",
                title="Webhook 反配 MoviePilot",
                status="warn",
                message="无法读取插件配置，请在 Webhook 插件中确认已添加 MoviePilot 回调地址",
                expected_value=webhook_url,
            )
        configured = (
            webhook_url in config_text
            if webhook_url
            else "/api/v1/webhook" in config_text
        )
        return InspectItem(
            id="webhook_configured",
            title="Webhook 反配 MoviePilot",
            status="ok" if configured else "warn",
            message=(
                "已配置 MoviePilot 回调"
                if configured
                else "请在 Webhook 插件中添加 Generic 目标，地址填期望值"
            ),
            current_value=webhook_url if configured else None,
            expected_value=webhook_url,
        )

    async def setup_webhook(
        self,
        ctx: dict,
        webhook_url: str,
        api_token: str,
    ) -> tuple[bool, str | None]:
        """配置 Webhook 指向 MoviePilot（降级方案：仅校验前置并返回手动指引）。

        Webhook 插件无稳定配置 API，不执行写入，避免产生半配置状态。
        """
        base_url = str(ctx.get("url") or "").rstrip("/")
        api_key = ctx.get("api_key") or ""
        headers = self._token_headers(api_key)
        try:
            async with httpx.AsyncClient(
                timeout=self.TIMEOUT, follow_redirects=True
            ) as client:
                plugin = await self._find_webhook_plugin(client, base_url, headers)
        except httpx.HTTPError as exc:
            return False, f"无法连接到应用: {exc}"
        if plugin is None:
            return False, "未安装 Webhook 插件，请先在插件目录安装并重启媒体服务器"
        return (
            False,
            "Webhook 插件无稳定配置 API，请在媒体服务器 → 插件 → Webhook 中"
            f"手动添加 Generic 目标，地址填: {webhook_url}",
        )

    async def _find_webhook_plugin(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        headers: dict,
    ) -> dict | None:
        """在已安装插件列表中查找 Webhook 插件。"""
        try:
            resp = await client.get(f"{base_url}/Plugins", headers=headers)
            if resp.status_code != 200:
                return None
            plugins = resp.json()
        except (httpx.HTTPError, json.JSONDecodeError):
            return None
        if not isinstance(plugins, list):
            return None
        for plugin in plugins:
            if "webhook" in str(plugin.get("Name") or "").lower():
                return plugin
        return None

    @staticmethod
    async def _read_plugin_configuration(
        client: httpx.AsyncClient,
        base_url: str,
        headers: dict,
        plugin: dict,
    ) -> str | None:
        """只读获取插件配置原文（用于检查是否已配置回调地址）。"""
        plugin_id = plugin.get("Id")
        if not plugin_id:
            return None
        resp = await client.get(
            f"{base_url}/Plugins/{plugin_id}/Configuration", headers=headers
        )
        resp.raise_for_status()
        return resp.text

    @staticmethod
    def _expected_media_dir(shared_config: dict | None) -> str | None:
        """从编排共享配置推导媒体目录。

        优先取 container_path 含 media 的挂载；单共享根目录时
        （如影视自动化的 /data）媒体库位于其下的 media 子目录。
        """
        volumes = (shared_config or {}).get("volumes") or []
        for volume in volumes:
            container_path = str(volume.get("container_path") or "")
            if "media" in container_path:
                return container_path
        for volume in volumes:
            root = str(volume.get("container_path") or "").strip().rstrip("/")
            if root and root != "/":
                return f"{root}/media"
        return None
