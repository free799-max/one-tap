"""Music Tag Web 应用客户端实现。

Music Tag Web 是 Django + Vue 应用，免费版只有内置管理员 ``admin``，
用户名不可修改。用户管理接口返回 403，无法通过 API 改密，因此自动
配置在容器内执行 Django 管理命令重置内置账号密码；即使数据卷里的
旧密码已不可知（例如统一密码后来被改过），也能直接恢复登录。
目录由编排共享卷直接挂载到 ``/app/media``，无需额外设置。
"""

import asyncio
import json
import posixpath
import re
import time

import httpx

from app.services.apps.base import (
    AppClient,
    AppClientError,
    AuthVerifyResult,
    CommandRunner,
    LogsReader,
    ProvisionResult,
)
from app.services.apps.registry import register_client

# 免费版默认管理员
_DEFAULT_USERNAME = "admin"
_DEFAULT_PASSWORD = "admin"

# 自动整理默认值，与 Music Tag Web 后台刮削的常用配置保持一致
DEFAULT_SOURCES: tuple[str, ...] = ("netease", "qmusic")
DEFAULT_MODIFY_FIELDS: tuple[str, ...] = (
    "title",
    "artist",
    "album",
    "album_img",
    "year",
    "lyrics",
)
DEFAULT_DIR_LIST: tuple[dict[str, str], ...] = (
    {"value": "artist", "value2": ""},
    {"value": "album", "value2": ""},
)

AUDIO_EXTENSIONS = frozenset(
    {
        ".mp3",
        ".flac",
        ".m4a",
        ".aac",
        ".wav",
        ".ape",
        ".ogg",
        ".opus",
        ".wma",
        ".dsf",
        ".dff",
        ".dts",
        ".wv",
        ".aiff",
        ".aif",
        ".alac",
        ".tta",
    }
)

AUDIT_ACTION_SCRAPE = "自动刮削"
AUDIT_ACTION_TIDY = "整理文件夹"

_AUDIT_COUNT_PATTERN = re.compile(r"(成功|匹配|待候选|失败):(\d+)")


def _join_path(parent: str, name: str) -> str:
    """拼接容器内路径，避免重复斜杠。"""
    return f"{parent.rstrip('/')}/{name.lstrip('/')}"


def _audit_terminal(item: dict) -> bool:
    """判断审计记录是否已经结束。"""
    if item.get("complete_at"):
        return True
    if "共0个文件" in str(item.get("extra") or ""):
        return True
    return str(item.get("status") or "") in {"failed", "error"}


def scrape_succeeded(item: dict) -> tuple[bool, str]:
    """解析自动刮削审计记录，返回 (是否成功, 说明)。"""
    extra = str(item.get("extra") or "")
    status = str(item.get("status") or "")
    if "共0个文件" in extra:
        return False, f"接口没有识别到文件: {extra}"
    if status == "failed":
        return False, extra or "刮削任务失败"
    numbers = {
        key: int(value) for key, value in _AUDIT_COUNT_PATTERN.findall(extra)
    }
    if numbers:
        failed = numbers.get("失败", 0)
        candidates = numbers.get("待候选", 0)
        succeeded = numbers.get("成功", 0)
        return (
            failed == 0 and (succeeded >= 1 or candidates == 0),
            extra or "刮削完成",
        )
    if status == "success" and "失败" not in extra:
        return True, extra or "刮削完成"
    return False, extra or f"刮削状态: {status}"


def tidy_succeeded(item: dict) -> tuple[bool, str]:
    """解析整理文件夹审计记录，返回 (是否成功, 说明)。"""
    extra = str(item.get("extra") or "")
    status = str(item.get("status") or "")
    if status == "success" and ("完成" in extra or not extra):
        return True, extra or "整理完成"
    return False, extra or f"整理状态: {status}"


class MusicTagWebSession:
    """带 JWT 的 Music Tag Web 会话，供自动整理流程复用。"""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self._client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
        )

    async def __aenter__(self) -> "MusicTagWebSession":
        return self

    async def __aexit__(self, *_exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _json(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
    ) -> dict:
        """调用接口并校验统一返回结构。"""
        try:
            resp = await self._client.request(
                method,
                f"{self.base_url}{path}",
                json=payload,
                headers={
                    "Authorization": f"JWT {self.token}",
                    "Accept": "application/json",
                },
            )
        except httpx.RequestError as exc:
            raise AppClientError(f"请求 Music Tag Web 失败: {exc}") from exc

        if resp.status_code != 200:
            body = (resp.text or "")[:300]
            raise AppClientError(
                f"{method} {path} 返回 HTTP {resp.status_code}: {body}"
            )
        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise AppClientError(
                f"{method} {path} 返回非 JSON 内容: {(resp.text or '')[:200]}"
            ) from exc
        if not isinstance(data, dict):
            raise AppClientError(f"{method} {path} 返回数据格式异常")
        if data.get("result") is False:
            message = data.get("message") or data.get("code") or "未知错误"
            raise AppClientError(f"{method} {path} 失败: {message}")
        return data

    async def list_children(self, directory: str) -> list[dict]:
        """列出目录的直接子项（与网页文件管理器相同的接口）。"""
        data = await self._json(
            "POST",
            "/apimt/file_list/",
            {
                "file_path": directory,
                "sorted_fields": [],
                "search_word": "",
                "refresh": True,
            },
        )
        roots = (data.get("data") or {}).get("file_list_data") or []
        if not roots:
            return []
        return list(roots[0].get("children") or [])

    async def walk_audio_files(
        self,
        root: str,
        *,
        max_files: int = 500,
        max_dirs: int = 500,
    ) -> list[dict]:
        """递归扫描目录下的音频文件，返回容器内路径与文件信息。"""
        root = root.rstrip("/") or "/"
        stack = [root]
        seen: set[str] = set()
        found: list[dict] = []

        while stack and len(found) < max_files and len(seen) < max_dirs:
            directory = stack.pop()
            if directory in seen:
                continue
            seen.add(directory)
            for item in await self.list_children(directory):
                name = str(item.get("name") or item.get("title") or "").strip()
                if not name:
                    continue
                path = _join_path(directory, name)
                icon = str(item.get("icon") or "")
                if icon.startswith("icon-folder"):
                    stack.append(path)
                    continue
                if posixpath.splitext(name)[1].lower() not in AUDIO_EXTENSIONS:
                    continue
                found.append(
                    {
                        "path": path,
                        "name": name,
                        "directory": directory,
                        "size": int(item.get("size") or 0),
                        "mtime": str(item.get("update_time") or ""),
                    }
                )
                if len(found) >= max_files:
                    break
        return found

    async def latest_audit_id(self) -> int:
        """当前审计日志的最大 ID，用于匹配本轮新任务。"""
        data = await self._json(
            "GET", "/apimt/audit_log/?page=1&page_size=1"
        )
        items = (data.get("data") or {}).get("items") or []
        if not items:
            return 0
        return max(int(item.get("id") or 0) for item in items)

    async def wait_audit(
        self,
        action: str,
        after_id: int,
        timeout: float,
        *,
        poll_interval: float = 2.0,
    ) -> dict:
        """等待指定动作的新审计记录结束并返回记录。

        免费版不开放审计详情接口，无法按文件路径精确匹配；这里按
        ``action`` 与触发前的最大 ID 匹配，自动整理串行执行时足够可靠。
        """
        deadline = time.monotonic() + timeout
        while True:
            data = await self._json(
                "GET", "/apimt/audit_log/?page=1&page_size=20"
            )
            items = (data.get("data") or {}).get("items") or []
            candidates = [
                item
                for item in items
                if int(item.get("id") or 0) > after_id
                and str(item.get("action") or "") == action
            ]
            if candidates:
                item = min(candidates, key=lambda row: int(row.get("id") or 0))
                if _audit_terminal(item):
                    return item
            if time.monotonic() >= deadline:
                raise AppClientError(
                    f"等待「{action}」完成超时（{timeout:.0f} 秒）"
                )
            await asyncio.sleep(poll_interval)

    async def scrape_file(
        self,
        *,
        directory: str,
        filename: str,
        sources: tuple[str, ...] | list[str],
        modify_fields: tuple[str, ...] | list[str],
        select_mode: str = "hard",
        skip_tag: bool = False,
        folder_album: bool = False,
    ) -> None:
        """触发单文件自动刮削。"""
        music_info = {
            "select_mode": select_mode,
            "source_list": list(sources),
            "modify_list": list(modify_fields),
            "is_skip_tag": skip_tag,
            "is_folder_album": folder_album,
            "scrape_rate": 1,
        }
        await self._json(
            "POST",
            "/apimt/batch_auto_update_id3/",
            {
                "file_full_path": directory,
                "select_data": [
                    {
                        "checked": True,
                        "icon": "icon-script-file",
                        "name": filename,
                        "title": filename,
                    }
                ],
                "modify_list": list(modify_fields),
                "music_info": music_info,
            },
        )

    async def tidy_file(
        self,
        *,
        directory: str,
        filename: str,
        root_path: str,
        dir_list: list[dict[str, str]],
    ) -> None:
        """触发单文件整理到目标曲库目录。"""
        await self._json(
            "POST",
            "/apimt/tidy_folder/",
            {
                "file_full_path": directory,
                "select_data": [
                    {
                        "checked": True,
                        "icon": "icon-script-file",
                        "name": filename,
                        "title": filename,
                    }
                ],
                "root_path": root_path,
                "dir_list": [dict(item) for item in dir_list],
            },
        )

# 容器内改密脚本：账号固定为内置 admin；不存在时创建，存在时直接重置
_CHANGE_PASSWORD_SCRIPT = (
    "import os\n"
    "from django.contrib.auth import get_user_model\n"
    "User = get_user_model()\n"
    "name = os.environ['MTW_TARGET_USERNAME']\n"
    "password = os.environ['MTW_TARGET_PASSWORD']\n"
    "user = User.objects.filter(username=name).first()\n"
    "if user is None:\n"
    "    user = User.objects.create_superuser(\n"
    "        username=name, email='', password=password\n"
    "    )\n"
    "else:\n"
    "    user.set_password(password)\n"
    "    user.is_superuser = True\n"
    "    user.is_staff = True\n"
    "    user.save()\n"
    "print('password-updated')\n"
)


class MusicTagWebClient(AppClient):
    """Music Tag Web 音乐标签编辑器客户端。"""

    name = "music-tag-web"
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
        command_runner: CommandRunner | None = None,
    ) -> ProvisionResult:
        """登录（或直接重置内置账号）并把密码改为统一密码。"""
        base = str(url or "").strip().rstrip("/")
        if not base:
            return ProvisionResult(ok=False, message="访问地址不能为空")

        # 免费版只有内置 admin 账号，用户名不跟随系统统一用户名
        username = _DEFAULT_USERNAME
        deadline = time.monotonic() + self.PROVISION_TIMEOUT
        last_error = "服务未就绪"

        while time.monotonic() < deadline:
            try:
                async with httpx.AsyncClient(
                    timeout=self.TIMEOUT,
                    follow_redirects=True,
                ) as client:
                    login = await self._login(client, base, username, password)
                    if login is not None:
                        return ProvisionResult(
                            ok=True,
                            message="账号已配置并验证通过",
                            username=username,
                        )

                    # 配置卷复用：统一密码变化时用旧密码登录后改密
                    for candidate in (previous_password, _DEFAULT_PASSWORD):
                        if not candidate or candidate == password:
                            continue
                        if await self._login(
                            client, base, username, candidate
                        ) is not None:
                            return await self._change_password(
                                client,
                                base,
                                username,
                                password,
                                command_runner,
                            )

                    # 数据卷里的密码既不是目标密码也不是已知旧密码时，
                    # 直接在容器内重置内置 admin，避免实例永久无法登录
                    if command_runner is not None:
                        return await self._change_password(
                            client,
                            base,
                            username,
                            password,
                            command_runner,
                        )

                    last_error = "用户名或密码错误（默认账号 admin/admin 亦不可用）"
                    return ProvisionResult(
                        ok=False, message=f"登录失败: {last_error}"
                    )
            except httpx.RequestError as exc:
                last_error = f"无法连接到应用: {exc}"
            await asyncio.sleep(5)

        return ProvisionResult(ok=False, message=f"自动配置超时: {last_error}")

    async def verify_auth(self, config: dict) -> AuthVerifyResult:
        """校验 Music Tag Web 访问地址与账号密码。"""
        base = str(config.get("url") or "").strip().rstrip("/")
        if not base:
            return AuthVerifyResult(valid=False, message="访问地址不能为空")

        auth_type = config.get("auth_type") or "none"
        if auth_type in ("api_key", "apikey"):
            return AuthVerifyResult(
                valid=False, message="Music Tag Web 不支持 API Key，请使用账号密码"
            )
        if auth_type != "basic":
            return AuthVerifyResult(
                valid=False, message="Music Tag Web 需要账号密码认证"
            )

        # 免费版只有内置 admin 账号，忽略配置里遗留的历史用户名
        username = _DEFAULT_USERNAME
        password = str(config.get("password") or "")
        if not password:
            return AuthVerifyResult(valid=False, message="密码不能为空")

        try:
            async with httpx.AsyncClient(
                timeout=self.TIMEOUT,
                follow_redirects=True,
            ) as client:
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

    async def open_organize_session(
        self,
        url: str,
        username: str,
        password: str,
        *,
        timeout: float = 60.0,
    ) -> MusicTagWebSession:
        """登录并返回自动整理复用的会话；登录失败抛 AppClientError。"""
        base = str(url or "").strip().rstrip("/")
        if not base:
            raise AppClientError("Music Tag Web 访问地址不能为空")
        # 免费版只有内置 admin 账号
        username = _DEFAULT_USERNAME
        try:
            async with httpx.AsyncClient(
                timeout=self.TIMEOUT,
                follow_redirects=True,
            ) as client:
                token = await self._login(client, base, username, password)
        except httpx.RequestError as exc:
            raise AppClientError(f"无法连接到 Music Tag Web: {exc}") from exc
        if not token:
            raise AppClientError("Music Tag Web 登录失败，请检查账号密码")
        return MusicTagWebSession(base, token, timeout=timeout)

    async def _login(
        self,
        client: httpx.AsyncClient,
        base: str,
        username: str,
        password: str,
    ) -> str | None:
        """登录并返回 JWT；失败返回 None。"""
        resp = await client.post(
            f"{base}/apimt/token/",
            json={"username": username, "password": password},
        )
        if resp.status_code != 200:
            return None
        try:
            payload = resp.json()
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(payload, dict) or not payload.get("result"):
            return None
        token = payload.get("token")
        return str(token) if token else None

    async def _change_password(
        self,
        client: httpx.AsyncClient,
        base: str,
        username: str,
        password: str,
        command_runner: CommandRunner | None,
    ) -> ProvisionResult:
        """在容器内重置内置账号密码，并复验登录。"""
        if command_runner is None:
            return ProvisionResult(
                ok=False,
                message=(
                    "无法登录且缺少容器执行能力，"
                    "无法把密码重置为统一密码"
                ),
            )

        exit_code, output = await command_runner(
            ["python", "manage.py", "shell", "-c", _CHANGE_PASSWORD_SCRIPT],
            {
                "MTW_TARGET_USERNAME": username,
                "MTW_TARGET_PASSWORD": password,
            },
            "/app",
        )
        if exit_code != 0 or "password-updated" not in (output or ""):
            detail = (output or "").strip().splitlines()
            return ProvisionResult(
                ok=False,
                message=(
                    "修改管理员密码失败"
                    + (f": {detail[-1][:200]}" if detail else "")
                ),
            )

        if await self._login(client, base, username, password) is None:
            return ProvisionResult(
                ok=False, message="修改密码后登录验证失败"
            )
        return ProvisionResult(
            ok=True,
            message="账号已配置并验证通过",
            username=username,
        )


register_client(MusicTagWebClient.name, MusicTagWebClient)
