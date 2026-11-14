"""MCP 聚合配置与原生上游实例发现。"""

import asyncio
from dataclasses import dataclass
import re
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import APIException
from app.models.integration import IntegrationConfig
from app.services.integrations.discovery import DeployedApp, list_deployed_apps


MCP_CONFIG_KIND = "mcp"
NATIVE_MCP_APPS = {"moviepilot"}
MEDIA_APP_NAMES = {
    "moviepilot",
    "comic-auto",
    "suwayomi",
    "music-dl",
    "music-tag-web",
    "navidrome",
    "reader",
    "ermao-library",
    "komga",
    "kavita",
}
DEFAULT_UPSTREAM_TOOLS = (
    "media.search",
    "library.exists",
    "subscription.find",
    "subscription.list",
    "subscription.files",
    "subscription.execution.get",
    "download.tasks.active",
    "download.history.list",
)
DEFAULT_LOCAL_TOOLS = (
    "onetap_api_catalog",
    "media_targets_list",
    "music_organize_status",
    "reader_sync_status",
)
LOCAL_TOOL_DEFINITIONS = {
    "media_targets_list": {
        "title": "查询媒体自动化目标",
        "description": "列出 OneTap 已部署的媒体应用和原生 MCP 支持情况。",
        "read_only": True,
    },
    "music_organize_status": {
        "title": "查询音乐整理状态",
        "description": "查询指定实例组的音乐刮削整理进度和统计。",
        "read_only": True,
    },
    "reader_sync_status": {
        "title": "查询书籍同步状态",
        "description": "查询指定实例组的 Reader 导出同步进度和统计。",
        "read_only": True,
    },
    "music_organize_run": {
        "title": "执行音乐整理",
        "description": "立即启动一轮音乐刮削、整理和媒体库扫描。",
        "read_only": False,
    },
    "reader_sync_run": {
        "title": "执行书籍同步",
        "description": "立即启动一轮 Reader 章节缓存、EPUB 导出和书库扫描。",
        "read_only": False,
    },
    "onetap_api_catalog": {
        "title": "查询 OneTap 完整接口目录",
        "description": "搜索全部 OneTap API，按 operationId 查看参数、请求体和响应 Schema。",
        "read_only": True,
    },
    "onetap_api_request": {
        "title": "调用已授权的 OneTap API",
        "description": "按 operationId 调用管理员明确授权的接口，不能指定任意 URL 或请求头。",
        "read_only": False,
    },
}


@dataclass(frozen=True)
class UpstreamState:
    app: DeployedApp
    prefix: str
    enabled: bool
    allowed_tools: tuple[str, ...]

    @property
    def available(self) -> bool:
        return self.app.status == "running" and bool(
            self.app.has_persisted_url and self.app.url and self.app.api_key
        )

    @property
    def unavailable_reason(self) -> str:
        if self.app.status != "running":
            return f"实例状态为 {self.app.status}"
        if not self.app.has_persisted_url:
            return "实例未配置持久化访问地址"
        if not self.app.url:
            return "实例缺少访问地址"
        if not self.app.api_key:
            return "实例缺少 API Key"
        return ""


def upstream_prefix(app: DeployedApp) -> str:
    return f"{app.app_name.replace('-', '_')}_{app.instance_id}"


def public_tool_name(prefix: str, native_name: str) -> str:
    return f"{prefix}_{native_name}"


def split_public_tool_name(name: str) -> tuple[str, str] | None:
    for app_name in NATIVE_MCP_APPS:
        prefix = f"{app_name.replace('-', '_')}_"
        if not name.startswith(prefix):
            continue
        remainder = name[len(prefix) :]
        instance_id, separator, native_name = remainder.partition("_")
        if separator and instance_id.isdigit() and native_name:
            return instance_id, native_name
    return None


def _deduplicate(apps: list[DeployedApp]) -> list[DeployedApp]:
    grouped: dict[tuple[str, str], list[DeployedApp]] = {}
    for app in apps:
        grouped.setdefault((app.app_name, app.instance_name), []).append(app)
    result: list[DeployedApp] = []
    for candidates in grouped.values():
        latest = max(candidates, key=lambda item: item.instance_id)
        usable = [item for item in candidates if item.status != "error"]
        result.append(max(usable, key=lambda item: item.instance_id) if usable else latest)
    return sorted(result, key=lambda item: item.instance_id)


def _clean_tool_names(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        name = str(value).strip()
        if re.fullmatch(r"[A-Za-z0-9._-]{1,96}", name) is None:
            raise APIException("工具名称格式不正确", 400)
        if name not in result:
            result.append(name)
    return tuple(result)


class McpConfigService:
    def __init__(self):
        self._config_lock = asyncio.Lock()

    async def _record(
        self, db: AsyncSession, create: bool = True
    ) -> IntegrationConfig | None:
        query = sa.select(IntegrationConfig).where(
            IntegrationConfig.kind == MCP_CONFIG_KIND
        )
        if create:
            query = query.with_for_update()
        result = await db.execute(query)
        record = result.scalar_one_or_none()
        if record is None and create:
            record = IntegrationConfig(kind=MCP_CONFIG_KIND, enabled=False, config={})
            db.add(record)
            await db.flush()
        return record

    async def get_config(self, db: AsyncSession) -> dict[str, Any]:
        record = await self._record(db, create=False)
        config = (record.config or {}) if record is not None else {}
        local_tools = config.get("local_tools")
        api_operation_ids = config.get("api_operation_ids")
        return {
            "enabled": bool(record.enabled) if record is not None else False,
            "upstreams": dict(config.get("upstreams") or {}),
            "local_tools": list(
                local_tools if isinstance(local_tools, list) else DEFAULT_LOCAL_TOOLS
            ),
            "api_operation_ids": list(
                api_operation_ids if isinstance(api_operation_ids, list) else []
            ),
        }

    async def set_enabled(self, db: AsyncSession, enabled: bool) -> dict[str, Any]:
        async with self._config_lock:
            record = await self._record(db)
            assert record is not None
            record.enabled = enabled
            await db.commit()
            return await self.get_config(db)

    async def _apps(self, db: AsyncSession, host: str) -> list[DeployedApp]:
        apps = await list_deployed_apps(db, host)
        return _deduplicate(
            [app for app in apps if app.app_name in NATIVE_MCP_APPS]
        )

    async def list_upstreams(
        self, db: AsyncSession, host: str
    ) -> list[UpstreamState]:
        config = await self.get_config(db)
        configured = config["upstreams"]
        states: list[UpstreamState] = []
        for app in await self._apps(db, host):
            entry = configured.get(str(app.instance_id)) or {}
            allowed = entry.get("allowed_tools")
            states.append(
                UpstreamState(
                    app=app,
                    prefix=upstream_prefix(app),
                    enabled=bool(entry.get("enabled", False)),
                    allowed_tools=_clean_tool_names(
                        allowed if isinstance(allowed, list) else list(DEFAULT_UPSTREAM_TOOLS)
                    ),
                )
            )
        return states

    async def get_upstream(
        self, db: AsyncSession, instance_id: int, host: str
    ) -> UpstreamState:
        for state in await self.list_upstreams(db, host):
            if state.app.instance_id == instance_id:
                return state
        raise APIException("原生 MCP 应用实例不存在", 404)

    async def update_upstream(
        self,
        db: AsyncSession,
        *,
        instance_id: int,
        host: str,
        enabled: bool,
        allowed_tools: list[str] | None,
    ) -> UpstreamState:
        async with self._config_lock:
            current = await self.get_upstream(db, instance_id, host)
            record = await self._record(db)
            assert record is not None
            config = dict(record.config or {})
            upstreams = dict(config.get("upstreams") or {})
            upstreams[str(instance_id)] = {
                "enabled": enabled,
                "allowed_tools": list(
                    _clean_tool_names(allowed_tools)
                    if allowed_tools is not None
                    else current.allowed_tools
                ),
            }
            config["upstreams"] = upstreams
            record.config = config
            await db.commit()
            return await self.get_upstream(db, instance_id, host)

    async def prune_upstreams(self, db: AsyncSession, valid_ids: set[int]) -> None:
        async with self._config_lock:
            record = await self._record(db)
            assert record is not None
            config = dict(record.config or {})
            upstreams = dict(config.get("upstreams") or {})
            retained = {
                key: value
                for key, value in upstreams.items()
                if key.isdigit() and int(key) in valid_ids
            }
            if retained == upstreams:
                return
            config["upstreams"] = retained
            record.config = config
            await db.commit()

    async def local_tools(self, db: AsyncSession) -> list[dict[str, Any]]:
        config = await self.get_config(db)
        enabled = set(config["local_tools"])
        return [
            {
                "name": name,
                **definition,
                "enabled": name in enabled,
            }
            for name, definition in LOCAL_TOOL_DEFINITIONS.items()
        ]

    async def update_local_tools(
        self, db: AsyncSession, enabled_tools: list[str]
    ) -> list[dict[str, Any]]:
        async with self._config_lock:
            unknown = set(enabled_tools) - set(LOCAL_TOOL_DEFINITIONS)
            if unknown:
                raise APIException("包含未知的本地工具", 400)
            record = await self._record(db)
            assert record is not None
            config = dict(record.config or {})
            config["local_tools"] = list(dict.fromkeys(enabled_tools))
            record.config = config
            await db.commit()
            return await self.local_tools(db)

    async def update_api_operations(
        self, db: AsyncSession, operation_ids: list[str]
    ) -> dict[str, Any]:
        cleaned = list(dict.fromkeys(str(value).strip() for value in operation_ids))
        if any(re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value) is None for value in cleaned):
            raise APIException("API operationId 格式不正确", 400)

        from app.services.mcp.openapi_catalog import (
            api_operation_ids,
            get_api_operation,
        )

        unknown = set(cleaned) - api_operation_ids()
        if unknown:
            raise APIException("包含未知的 API operationId", 400)
        blocked = [
            operation_id
            for operation_id in cleaned
            if not (get_api_operation(operation_id) or {}).get("invocable", False)
        ]
        if blocked:
            raise APIException("包含禁止通过 MCP 调用的 API", 400)

        async with self._config_lock:
            record = await self._record(db)
            assert record is not None
            config = dict(record.config or {})
            config["api_operation_ids"] = cleaned
            record.config = config
            await db.commit()
            return await self.get_config(db)

    async def is_api_operation_enabled(
        self, db: AsyncSession, operation_id: str
    ) -> bool:
        config = await self.get_config(db)
        return operation_id in set(config["api_operation_ids"])

    async def is_local_tool_enabled(self, db: AsyncSession, name: str) -> bool:
        config = await self.get_config(db)
        return name in set(config["local_tools"])


mcp_config_service = McpConfigService()
