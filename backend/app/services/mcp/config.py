"""MCP 聚合配置与原生上游实例发现。

聚合端只做原生 MCP 的透传：配置只有全局开关，已部署且可读的实例
会自动注册，其全部原生工具都会透传。不同应用的 MCP 端点路径各异
（MoviePilot 是 ``/api/v1/mcp``，Comic Auto 是 ``/api/mcp``）。
"""

import asyncio
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import APIException
from app.models.integration import IntegrationConfig
from app.services.integrations.discovery import DeployedApp, list_deployed_apps


MCP_CONFIG_KIND = "mcp"

# 原生 MCP 应用 → Streamable HTTP 端点路径
NATIVE_MCP_PATHS: dict[str, str] = {
    "moviepilot": "/api/v1/mcp",
    "comic-auto": "/api/mcp",
}
NATIVE_MCP_APPS = set(NATIVE_MCP_PATHS)


@dataclass(frozen=True)
class UpstreamState:
    app: DeployedApp
    prefix: str

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
    # 前缀可能相互包含，长的优先匹配（comic_auto_ 与 moviepilot_ 目前不冲突）
    for app_name in sorted(NATIVE_MCP_APPS, key=len, reverse=True):
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
            record = IntegrationConfig(kind=MCP_CONFIG_KIND, enabled=True, config={})
            db.add(record)
            await db.flush()
        return record

    async def get_config(self, db: AsyncSession) -> dict[str, Any]:
        record = await self._record(db, create=False)
        return {"enabled": bool(record.enabled) if record is not None else True}

    async def set_enabled(self, db: AsyncSession, enabled: bool) -> dict[str, Any]:
        async with self._config_lock:
            record = await self._record(db)
            assert record is not None
            record.enabled = enabled
            if record.config:
                record.config = {}
            await db.commit()
            return await self.get_config(db)

    async def _apps(
        self, db: AsyncSession, host: str, app_names: set[str] | None = None
    ) -> list[DeployedApp]:
        apps = await list_deployed_apps(db, host)
        allowed = NATIVE_MCP_APPS if app_names is None else set(app_names)
        return _deduplicate(
            [app for app in apps if app.app_name in allowed]
        )

    async def list_upstreams(
        self,
        db: AsyncSession,
        host: str,
        app_names: set[str] | None = None,
    ) -> list[UpstreamState]:
        return [
            UpstreamState(app=app, prefix=upstream_prefix(app))
            for app in await self._apps(db, host, app_names)
        ]

    async def get_upstream(
        self, db: AsyncSession, instance_id: int, host: str
    ) -> UpstreamState:
        for state in await self.list_upstreams(db, host):
            if state.app.instance_id == instance_id:
                return state
        raise APIException("原生 MCP 应用实例不存在", 404)


mcp_config_service = McpConfigService()
