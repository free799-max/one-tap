"""内置与自定义源地址目录及实例级自动装配。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.exceptions import APIException
from app.models.orchestration import AppInstance, AppOrchestrationInstance
from app.services.apps.reader.client import ReaderClientError, ReaderSession
from app.services.apps.suwayomi import SuwayomiClient, SuwayomiClientError

SUPPORTED_RESOURCE_SOURCE_APPS = frozenset({"reader", "suwayomi"})
_CATALOG_DIR = Path(__file__).resolve().parents[2] / "source_catalogs"
_BAD_SOURCE_GROUPS = ("搜索暂不可用", "暂不可用", "人机验证", "梯子")
_MAX_SOURCE_FILE_BYTES = 25 * 1024 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _source_label(app_name: str) -> str:
    return "书源" if app_name == "reader" else "漫源"


@lru_cache(maxsize=2)
def load_catalog(app_name: str) -> dict[str, Any]:
    if app_name not in SUPPORTED_RESOURCE_SOURCE_APPS:
        raise APIException("该应用不支持源配置", 400)
    label = _source_label(app_name)
    path = _CATALOG_DIR / f"{app_name}.json"
    try:
        catalog = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise APIException(f"{label}目录读取失败：{exc}", 500) from exc
    items = catalog.get("items")
    if not isinstance(items, list) or not items:
        raise APIException(f"{label}目录为空", 500)
    ids = []
    for item in items:
        if not isinstance(item, dict):
            raise APIException(f"{label}目录包含无效条目", 500)
        item_id = str(item.get("id") or "").strip()
        parsed = urlparse(str(item.get("url") or ""))
        if not item_id or parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise APIException(f"{label}目录条目无效：{item_id or item}", 500)
        for url in item.get("mirror_urls") or []:
            mirror = urlparse(str(url))
            if mirror.scheme not in {"http", "https"} or not mirror.netloc:
                raise APIException(f"{label}镜像地址无效：{url}", 500)
        ids.append(item_id)
    if len(ids) != len(set(ids)):
        raise APIException(f"{label}目录存在重复 ID", 500)
    return catalog


def _normalize_custom_sources(raw: object) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    sources = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "").strip()
        name = str(item.get("name") or "").strip()
        url = str(item.get("url") or "").strip()
        parsed = urlparse(url)
        if (
            not item_id.startswith("custom-")
            or len(item_id) > 100
            or not name
            or len(name) > 100
            or len(url) > 2048
            or parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or item_id in seen
        ):
            continue
        sources.append({"id": item_id, "name": name, "url": url})
        seen.add(item_id)
    return sources


def normalize_config(app_name: str, raw: object) -> dict[str, Any]:
    stored = raw if isinstance(raw, dict) else {}
    items = load_catalog(app_name)["items"]
    custom_sources = _normalize_custom_sources(stored.get("custom_sources"))
    known_ids = {item["id"] for item in items} | {
        item["id"] for item in custom_sources
    }
    if isinstance(stored.get("selected_ids"), list):
        selected_ids = list(
            dict.fromkeys(
                str(item) for item in stored["selected_ids"] if str(item) in known_ids
            )
        )
    else:
        selected_ids = [
            item["id"] for item in items if item.get("default_selected", True)
        ]
    config = {
        "enabled": bool(stored.get("enabled", True)),
        "auto_filter": bool(stored.get("auto_filter", True)),
        "selected_ids": selected_ids,
        "custom_sources": custom_sources,
    }
    if app_name == "reader":
        try:
            limit = int(stored.get("book_source_limit") or 200)
        except (TypeError, ValueError):
            limit = 200
        config["book_source_limit"] = min(100000, max(1, limit))
    if isinstance(stored.get("last_result"), dict):
        config["last_result"] = stored["last_result"]
    return config


def _options(app_name: str, config: dict[str, Any]) -> list[dict[str, Any]]:
    options = [
        {
            "id": item["id"],
            "name": item.get("name") or item["id"],
            "description": item["url"],
            "default_selected": bool(item.get("default_selected", True)),
            "filtered": False,
            "custom": False,
        }
        for item in load_catalog(app_name)["items"]
    ]
    options.extend(
        {
            "id": item["id"],
            "name": item["name"],
            "description": item["url"],
            "default_selected": False,
            "filtered": False,
            "custom": True,
        }
        for item in config.get("custom_sources", [])
    )
    return options


def _repositories(app_name: str, config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    repositories = {
        item["id"]: item for item in load_catalog(app_name)["items"]
    }
    repositories.update(
        {item["id"]: item for item in config.get("custom_sources", [])}
    )
    return repositories


async def _load_group(db, group_id: int) -> AppOrchestrationInstance:
    result = await db.execute(
        select(AppOrchestrationInstance)
        .where(AppOrchestrationInstance.id == group_id)
        .options(
            selectinload(AppOrchestrationInstance.instances).selectinload(
                AppInstance.app
            )
        )
    )
    group = result.scalar_one_or_none()
    if group is None:
        raise APIException("编排实例不存在", 404)
    return group


def _check_app(group: AppOrchestrationInstance, app_name: str) -> None:
    if not any(
        instance.app and instance.app.name == app_name
        for instance in group.instances
    ):
        raise APIException("应用不在该实例组中", 404)


class ResourceSourceService:
    async def get_panel(self, db, group_id: int, app_name: str) -> dict[str, Any]:
        group = await _load_group(db, group_id)
        _check_app(group, app_name)
        if app_name not in SUPPORTED_RESOURCE_SOURCE_APPS:
            return {"supported": False, "config": {}, "options": []}
        entry = (group.app_configs or {}).get(app_name) or {}
        config = normalize_config(app_name, entry.get("resource_sources"))
        return {
            "supported": True,
            "config": config,
            "options": _options(app_name, config),
            "last_result": config.get("last_result"),
        }

    async def save_config(
        self, db, group_id: int, app_name: str, raw: dict[str, Any]
    ) -> dict[str, Any]:
        if app_name not in SUPPORTED_RESOURCE_SOURCE_APPS:
            raise APIException("该应用不支持源配置", 400)
        group = await _load_group(db, group_id)
        _check_app(group, app_name)
        custom_sources = raw.get("custom_sources") or []
        custom_ids = [item["id"] for item in custom_sources]
        if len(custom_ids) != len(set(custom_ids)):
            raise APIException(f"自定义{_source_label(app_name)} ID 不能重复", 400)
        known_ids = {item["id"] for item in load_catalog(app_name)["items"]} | set(
            custom_ids
        )
        selected = raw.get("selected_ids")
        if selected is not None:
            unknown = set(selected) - known_ids
            if unknown:
                raise APIException(
                    f"存在未知{_source_label(app_name)}：{', '.join(sorted(unknown))}",
                    400,
                )
        configs = dict(group.app_configs or {})
        entry = dict(configs.get(app_name) or {})
        previous = entry.get("resource_sources") or {}
        merged = dict(previous if isinstance(previous, dict) else {})
        merged.update(
            {
                "enabled": raw.get("enabled", True),
                "auto_filter": raw.get("auto_filter", True),
                "selected_ids": raw.get("selected_ids", []),
                "custom_sources": custom_sources,
            }
        )
        if app_name == "reader" and raw.get("book_source_limit") is not None:
            merged["book_source_limit"] = raw["book_source_limit"]
        entry["resource_sources"] = normalize_config(app_name, merged)
        configs[app_name] = entry
        group.app_configs = configs
        await db.commit()
        return await self.get_panel(db, group_id, app_name)

    @staticmethod
    def _filter_book_sources(
        payload: object, *, auto_filter: bool
    ) -> list[dict[str, Any]]:
        if isinstance(payload, dict):
            payload = payload.get("data") or payload.get("sources") or []
        if not isinstance(payload, list):
            raise ReaderClientError("书源地址返回的不是 JSON 数组")
        selected: dict[str, dict[str, Any]] = {}
        for source in payload:
            if not isinstance(source, dict):
                continue
            url = str(source.get("bookSourceUrl") or "").strip()
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                continue
            if source.get("bookSourceType", 0) != 0:
                continue
            rule_search = source.get("ruleSearch") or {}
            if not isinstance(rule_search, dict):
                rule_search = {}
            if not source.get("searchUrl") and not rule_search.get("url"):
                continue
            if auto_filter:
                if not source.get("enabled", False):
                    continue
                group = str(source.get("bookSourceGroup") or "")
                if any(keyword in group for keyword in _BAD_SOURCE_GROUPS):
                    continue
            source["bookSourceUrl"] = url
            selected[url] = source
        return list(selected.values())

    async def _fetch_book_sources(
        self,
        client: httpx.AsyncClient,
        repository: dict[str, Any],
        *,
        auto_filter: bool,
        proxy_client: httpx.AsyncClient | None = None,
    ) -> list[dict[str, Any]]:
        primary_url = repository["url"]
        attempts = [(client, primary_url)]
        if proxy_client is not None:
            attempts.append((proxy_client, primary_url))
        attempts.extend(
            (client, url) for url in repository.get("mirror_urls") or []
        )
        errors = []
        for current_client, url in attempts:
            try:
                response = await current_client.get(url)
                response.raise_for_status()
                if len(response.content) > _MAX_SOURCE_FILE_BYTES:
                    raise ReaderClientError("书源文件超过 25 MB")
                return self._filter_book_sources(
                    response.json(), auto_filter=auto_filter
                )
            except (httpx.HTTPError, ValueError, ReaderClientError) as exc:
                route = "系统代理" if current_client is proxy_client else "直连"
                errors.append(f"{url}（{route}）: {exc}")
        raise ReaderClientError("；".join(errors) or "书源下载失败")

    async def _apply_reader(
        self,
        entry: dict[str, Any],
        config: dict[str, Any],
        proxy: str | None = None,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        repositories = _repositories("reader", config)
        results = []
        sources: dict[str, dict[str, Any]] = {}
        client_options = {
            "timeout": httpx.Timeout(90.0, connect=20.0),
            "follow_redirects": True,
            "trust_env": False,
        }
        async with httpx.AsyncClient(**client_options) as client:
            proxy_client = (
                httpx.AsyncClient(proxy=proxy, **client_options) if proxy else None
            )
            try:
                for item_id in config["selected_ids"]:
                    repository = repositories[item_id]
                    try:
                        downloaded = await self._fetch_book_sources(
                            client,
                            repository,
                            auto_filter=config["auto_filter"],
                            proxy_client=proxy_client,
                        )
                        sources.update(
                            {item["bookSourceUrl"]: item for item in downloaded}
                        )
                        results.append(
                            {
                                "id": item_id,
                                "name": repository["name"],
                                "status": "ok" if downloaded else "skipped",
                                "message": f"筛选后保留 {len(downloaded)} 条书源",
                            }
                        )
                    except ReaderClientError as exc:
                        results.append(
                            {
                                "id": item_id,
                                "name": repository["name"],
                                "status": "error",
                                "message": str(exc),
                            }
                        )
            finally:
                if proxy_client is not None:
                    await proxy_client.aclose()
        if not sources:
            return results, []
        async with ReaderSession(
            entry.get("url") or "",
            username=entry.get("username") or "",
            password=entry.get("password") or "",
        ) as session:
            if entry.get("_secure_key"):
                await session.update_book_source_limit(
                    entry.get("username") or "",
                    config["book_source_limit"],
                    entry["_secure_key"],
                )
            import_result = await session.save_book_sources(list(sources.values()))
            installed = await session.book_sources()
        installed_urls = {
            str(item.get("bookSourceUrl") or "") for item in installed
        }
        missing = set(sources) - installed_urls
        if missing and not import_result.get("limit_reached"):
            raise ReaderClientError(f"有 {len(missing)} 条书源未通过回读验证")
        return results, [url for url in sources if url in installed_urls]

    async def _apply_suwayomi(
        self,
        entry: dict[str, Any],
        config: dict[str, Any],
        proxy: str | None = None,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        repositories = _repositories("suwayomi", config)
        results = []
        source_ids = []
        async with SuwayomiClient(
            entry.get("url") or "",
            username=str(entry.get("username") or ""),
            password=str(entry.get("password") or ""),
        ) as client:
            await client.wait_ready()
            for item_id in config["selected_ids"]:
                repository = repositories[item_id]
                try:
                    installed = await client.install_repository(
                        repository,
                        auto_filter=config["auto_filter"],
                        proxy=proxy,
                    )
                    count = installed["extension_count"]
                    source_count = installed["source_count"]
                    status = (
                        "skipped" if not count else "ok" if source_count else "error"
                    )
                    message = (
                        f"已配置 {count} 个扩展，识别到 {source_count} 个在线图源"
                    )
                    healthy_count = installed.get("healthy_count")
                    if healthy_count is not None:
                        message += f"，可用 {healthy_count} 个"
                    results.append(
                        {
                            "id": item_id,
                            "name": repository["name"],
                            "status": status,
                            "message": message,
                            "installed_ids": installed["installed_ids"],
                        }
                    )
                    source_ids.extend(installed["source_ids"])
                except SuwayomiClientError as exc:
                    results.append(
                        {
                            "id": item_id,
                            "name": repository["name"],
                            "status": "error",
                            "message": str(exc),
                        }
                    )
        return results, list(dict.fromkeys(source_ids))

    async def apply(
        self,
        app_name: str,
        entry: dict[str, Any],
        *,
        proxy: str | None = None,
    ) -> dict[str, Any]:
        config = normalize_config(app_name, entry.get("resource_sources"))
        started_at = _now()
        if not config["enabled"]:
            return {
                "status": "skipped",
                "message": "自动装源已停用",
                "started_at": started_at,
                "finished_at": _now(),
                "items": [],
            }
        if not config["selected_ids"]:
            return {
                "status": "skipped",
                "message": f"未选择{_source_label(app_name)}",
                "started_at": started_at,
                "finished_at": _now(),
                "items": [],
            }
        if app_name == "reader":
            results, source_ids = await self._apply_reader(entry, config, proxy)
        else:
            results, source_ids = await self._apply_suwayomi(entry, config, proxy)
        errors = [item for item in results if item["status"] == "error"]
        succeeded = [item for item in results if item["status"] == "ok"]
        status = (
            "partial"
            if errors and succeeded
            else "error"
            if errors
            else "skipped"
            if not succeeded
            else "ok"
        )
        return {
            "status": status,
            "message": (
                f"成功 {len(succeeded)} 个源地址，失败 {len(errors)} 个，"
                f"识别资源 {len(source_ids)} 个"
            ),
            "started_at": started_at,
            "finished_at": _now(),
            "items": results,
            "source_ids": source_ids,
        }


resource_source_service = ResourceSourceService()
