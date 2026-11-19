"""Jellyfin/Emby 通用媒体库查询与创建能力。"""

from __future__ import annotations

import json
import posixpath
from dataclasses import dataclass

import httpx

TIMEOUT = 15.0


@dataclass(frozen=True, slots=True)
class MediaLibraryInfo:
    """媒体服务器中的媒体库。"""

    id: str | None
    name: str
    paths: tuple[str, ...]
    collection_type: str | None


def normalize_media_path(value: object) -> str:
    """规范化媒体服务器路径。"""
    text = str(value or "").strip()
    if not text.startswith("/"):
        return ""
    return posixpath.normpath(text)


def path_is_covered(target: str, existing_paths: list[str] | tuple[str, ...]) -> bool:
    """判断目标是否已被现有媒体库路径覆盖或反向覆盖。"""
    normalized_target = normalize_media_path(target)
    if not normalized_target:
        return False
    for existing in existing_paths:
        normalized_existing = normalize_media_path(existing)
        if not normalized_existing:
            continue
        if normalized_existing == "/":
            return True
        if (
            normalized_target == normalized_existing
            or normalized_target.startswith(normalized_existing + "/")
            or normalized_existing.startswith(normalized_target + "/")
        ):
            return True
    return False


def _headers(api_key: str) -> dict[str, str]:
    """同时兼容 Jellyfin 12 与 Emby 的 Token 认证头。"""
    return {
        "Authorization": (
            'MediaBrowser Client="OneTap", Device="NAS", '
            f'DeviceId="onetap-library", Version="1.0.0", Token="{api_key}"'
        ),
        "X-Emby-Token": api_key,
    }


def _parse_list(payload: object) -> list[dict] | None:
    """解析裸列表或 ``{"data": [...]}`` 包装。"""
    if isinstance(payload, dict):
        payload = payload.get("data")
    if not isinstance(payload, list):
        return None
    return [item for item in payload if isinstance(item, dict)]


async def _fetch_virtual_folders(
    base_url: str,
    api_key: str,
) -> tuple[bool, list[dict], str | None]:
    """读取媒体服务器虚拟媒体库原始配置。"""
    url = f"{base_url.rstrip('/')}/Library/VirtualFolders"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
            response = await client.get(url, headers=_headers(api_key))
    except httpx.RequestError as exc:
        return False, [], f"无法连接到媒体服务器: {exc}"
    if response.status_code in (401, 403):
        return False, [], "媒体服务器认证失败"
    if response.status_code >= 400:
        return False, [], f"读取媒体库失败: HTTP {response.status_code}"
    try:
        items = _parse_list(response.json())
    except json.JSONDecodeError:
        items = None
    if items is None:
        return False, [], "媒体库接口返回异常"
    return True, items, None


async def list_media_libraries(
    base_url: str,
    api_key: str,
) -> tuple[bool, list[MediaLibraryInfo], str | None]:
    """读取 Jellyfin/Emby 现有媒体库。"""
    ok, items, err = await _fetch_virtual_folders(base_url, api_key)
    if not ok:
        return False, [], err

    libraries: list[MediaLibraryInfo] = []
    for item in items:
        raw_paths = item.get("Locations")
        if not isinstance(raw_paths, list):
            raw_paths = []
        paths = tuple(
            path
            for path in (
                normalize_media_path(value) for value in raw_paths
            )
            if path
        )
        libraries.append(
            MediaLibraryInfo(
                id=(
                    str(item["ItemId"])
                    if item.get("ItemId") is not None
                    else None
                ),
                name=str(item.get("Name") or ""),
                paths=paths,
                collection_type=(
                    str(item["CollectionType"])
                    if item.get("CollectionType") is not None
                    else None
                ),
            )
        )
    return True, libraries, None


NFO_PROVIDER = "Nfo"
NFO_FETCHER_TYPES = frozenset({"Movie", "Series", "Video"})


def enable_local_nfo_options(options: dict) -> dict:
    """启用本地 NFO 读取，并停止媒体服务器回写 movie.nfo。

    Jellyfin/Emby 新建媒体库时默认只保存 NFO、不读取本地 NFO，且会把空元数据
    写成 ``movie.nfo``，反而遮蔽 MoviePilot 生成的 ``<文件名>.nfo``。
    """
    updated = dict(options)
    updated["MetadataSavers"] = [
        saver
        for saver in updated.get("MetadataSavers") or []
        if saver != NFO_PROVIDER
    ]
    type_options: list[dict] = []
    for entry in updated.get("TypeOptions") or []:
        if not isinstance(entry, dict):
            continue
        options_entry = dict(entry)
        if options_entry.get("Type") in NFO_FETCHER_TYPES:
            order = [
                value
                for value in options_entry.get("MetadataFetcherOrder") or []
                if value != NFO_PROVIDER
            ]
            fetchers = [
                value
                for value in options_entry.get("MetadataFetchers") or []
                if value != NFO_PROVIDER
            ]
            options_entry["MetadataFetcherOrder"] = [NFO_PROVIDER, *order]
            options_entry["MetadataFetchers"] = [
                NFO_PROVIDER,
                *(fetchers or order),
            ]
        type_options.append(options_entry)
    updated["TypeOptions"] = type_options
    return updated


def _local_nfo_ready(options: dict) -> bool:
    """判断媒体库是否已经启用本地 NFO 读取。"""
    if NFO_PROVIDER in (options.get("MetadataSavers") or []):
        return False
    for entry in options.get("TypeOptions") or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("Type") not in NFO_FETCHER_TYPES:
            continue
        if NFO_PROVIDER not in (entry.get("MetadataFetchers") or []):
            return False
    return True


async def ensure_local_nfo(
    base_url: str,
    api_key: str,
    *,
    name: str,
) -> tuple[bool, str | None]:
    """让指定媒体库读取本地 NFO，重复执行无副作用。"""
    ok, items, err = await _fetch_virtual_folders(base_url, api_key)
    if not ok:
        return False, err
    entry = next(
        (item for item in items if str(item.get("Name") or "") == name),
        None,
    )
    if entry is None:
        return False, "未找到目标媒体库"
    options = entry.get("LibraryOptions")
    if not isinstance(options, dict):
        return False, "媒体库缺少选项配置"
    if _local_nfo_ready(options):
        return True, None

    url = f"{base_url.rstrip('/')}/Library/VirtualFolders/LibraryOptions"
    payload = {
        "Id": entry.get("ItemId") or entry.get("Id"),
        "LibraryOptions": enable_local_nfo_options(options),
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
            response = await client.post(
                url,
                json=payload,
                headers=_headers(api_key),
            )
    except httpx.RequestError as exc:
        return False, f"无法连接到媒体服务器: {exc}"
    if response.status_code >= 400:
        detail = response.text.strip()[:300]
        suffix = f": {detail}" if detail else ""
        return False, f"更新媒体库选项失败: HTTP {response.status_code}{suffix}"
    return True, None


async def create_media_library(
    base_url: str,
    api_key: str,
    *,
    name: str,
    collection_type: str,
    path: str,
) -> tuple[bool, str | None]:
    """创建一个媒体库并回读确认目标路径。"""
    normalized_path = normalize_media_path(path)
    if not normalized_path:
        return False, "媒体库路径必须是绝对路径"
    params = [
        ("name", name),
        ("collectionType", collection_type),
        ("paths", normalized_path),
        ("refreshLibrary", "true"),
    ]
    url = f"{base_url.rstrip('/')}/Library/VirtualFolders"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
            response = await client.post(
                url,
                params=params,
                headers=_headers(api_key),
            )
    except httpx.RequestError as exc:
        return False, f"无法连接到媒体服务器: {exc}"
    if response.status_code >= 400:
        detail = response.text.strip()[:300]
        suffix = f": {detail}" if detail else ""
        return False, f"创建媒体库失败: HTTP {response.status_code}{suffix}"

    ok, libraries, err = await list_media_libraries(base_url, api_key)
    if not ok:
        return False, f"创建后回读媒体库失败: {err}"
    if any(
        path_is_covered(normalized_path, list(library.paths))
        for library in libraries
    ):
        await ensure_local_nfo(base_url, api_key, name=name)
        return True, None
    return False, "创建后回读未找到目标媒体库路径"
