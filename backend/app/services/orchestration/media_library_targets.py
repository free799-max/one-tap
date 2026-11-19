"""根据 MoviePilot 分类策略与目录配置推导媒体库目标。"""

from __future__ import annotations

import posixpath
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

# Jellyfin/Emby 的媒体库类型标识
COLLECTION_TYPES = {
    "电影": "movies",
    "电视剧": "tvshows",
    "音乐": "music",
}

# 媒体库名称中使用更易读的类型前缀
TYPE_NAME_PREFIXES = {
    "电影": "电影",
    "电视剧": "剧集",
    "音乐": "音乐",
}

_UNSAFE_NAME_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


@dataclass(frozen=True, slots=True)
class LibraryTarget:
    """一个待确认的媒体库目标。"""

    name: str
    collection_type: str
    path: str
    media_type: str
    category_id: str | None = None


def _normalize_media_type(value: Any) -> str | None:
    """把 MoviePilot 的中文媒体类型归一化。"""
    text = str(value or "").strip()
    aliases = {
        "电影": "电影",
        "movie": "电影",
        "movies": "电影",
        "电视剧": "电视剧",
        "tv": "电视剧",
        "tvshows": "电视剧",
        "音乐": "音乐",
        "music": "音乐",
    }
    return aliases.get(text.casefold())


def _category_segments(value: Any) -> tuple[str, ...]:
    """校验并归一化分类相对路径，拒绝绝对路径和上级目录。"""
    raw_segments = value if isinstance(value, (list, tuple)) else []
    segments: list[str] = []
    for raw in raw_segments:
        segment = str(raw or "").strip()
        if not segment or segment in {".", ".."} or "/" in segment or "\\" in segment:
            return ()
        segments.append(segment)
    return tuple(segments)


def _library_root(value: Any) -> str:
    """规范化本地整理根目录。"""
    text = str(value or "").strip()
    if not text.startswith("/"):
        return ""
    path = posixpath.normpath(text)
    if not path.startswith("/") or ".." in path.split("/"):
        return ""
    return path


def _join_path(root: str, segments: list[str] | tuple[str, ...]) -> str:
    """按 POSIX 语义拼接路径。"""
    path = root
    for segment in segments:
        path = posixpath.join(path, segment)
    return posixpath.normpath(path)


def _safe_library_name(value: Any) -> str:
    """清理媒体服务器不接受的媒体库名称字符。"""
    text = _UNSAFE_NAME_RE.sub(" ", str(value or "")).strip()
    return re.sub(r"\s+", " ", text)


def _category_path_below_type(
    path: tuple[str, ...],
    media_type: str,
    *,
    type_folder_enabled: bool,
) -> tuple[str, ...]:
    """避免分类首段与媒体类型目录重复，例如 电影/电影/动作。"""
    if type_folder_enabled and path and path[0] == media_type:
        return path[1:]
    return path


def _make_target(
    *,
    name: str,
    media_type: str,
    root: str,
    type_folder: bool,
    category_path: tuple[str, ...] | None,
    category_id: str | None,
) -> LibraryTarget | None:
    """生成单个目标；路径或类型不合法时返回空。"""
    collection_type = COLLECTION_TYPES.get(media_type)
    if collection_type is None:
        return None
    segments: list[str] = []
    if type_folder:
        segments.append(media_type)
    if category_path:
        segments.extend(
            _category_path_below_type(
                category_path,
                media_type,
                type_folder_enabled=type_folder,
            )
        )
    safe_name = _safe_library_name(name)
    if not safe_name:
        return None
    return LibraryTarget(
        name=safe_name,
        collection_type=collection_type,
        path=_join_path(root, segments),
        media_type=media_type,
        category_id=category_id,
    )


def _disambiguate_names(targets: list[LibraryTarget]) -> list[LibraryTarget]:
    """跨类型重名时添加类型前缀，保持名称可预测且唯一。"""
    counts = Counter(target.name for target in targets)
    used: set[str] = set()
    result: list[LibraryTarget] = []
    for target in targets:
        name = target.name
        if counts[name] > 1:
            prefix = TYPE_NAME_PREFIXES.get(target.media_type, target.media_type)
            name = f"{prefix}{name}"
        candidate = name
        suffix = 2
        while candidate in used:
            candidate = f"{name} {suffix}"
            suffix += 1
        used.add(candidate)
        result.append(
            LibraryTarget(
                name=candidate,
                collection_type=target.collection_type,
                path=target.path,
                media_type=target.media_type,
                category_id=target.category_id,
            )
        )
    return result


def build_library_targets(
    directories: list[dict],
    policy: dict,
) -> list[LibraryTarget]:
    """按目录配置和分类策略生成去重后的媒体库目标。

    只生成整理目录的目标，不检查目录是否已存在，也不发起任何写操作。
    """
    raw_categories = policy.get("categories") if isinstance(policy, dict) else None
    categories: list[dict[str, Any]] = []
    for item in raw_categories if isinstance(raw_categories, list) else []:
        if not isinstance(item, dict) or not item.get("enabled", True):
            continue
        media_type = _normalize_media_type(item.get("media_type"))
        path = _category_segments(item.get("path"))
        name = str(item.get("name") or "").strip()
        if media_type is None or not path or not name:
            continue
        categories.append(
            {
                **item,
                "media_type": media_type,
                "path": path,
                "name": name,
            }
        )

    by_id = {
        str(item.get("id")): item
        for item in categories
        if item.get("id") is not None
    }
    targets: list[LibraryTarget] = []
    seen: set[tuple[str, str]] = set()

    for raw_directory in directories if isinstance(directories, list) else []:
        if not isinstance(raw_directory, dict):
            continue
        storage = str(raw_directory.get("library_storage") or "local")
        root = _library_root(raw_directory.get("library_path"))
        if storage != "local" or not root:
            continue

        directory_type = _normalize_media_type(raw_directory.get("media_type"))
        type_folder = bool(raw_directory.get("library_type_folder"))
        category_folder = bool(raw_directory.get("library_category_folder"))
        category_id = str(raw_directory.get("media_category_id") or "").strip() or None
        fixed_category = by_id.get(category_id) if category_id else None

        selected: list[dict[str, Any]]
        if category_id:
            if fixed_category is None:
                continue
            selected = [fixed_category]
        else:
            selected = [
                item
                for item in categories
                if directory_type is None or item["media_type"] == directory_type
            ]

        directory_targets: list[LibraryTarget] = []
        if category_folder:
            for category in selected:
                media_type = directory_type or category["media_type"]
                target = _make_target(
                    name=category["name"],
                    media_type=media_type,
                    root=root,
                    type_folder=type_folder,
                    category_path=category["path"],
                    category_id=str(category.get("id") or "") or None,
                )
                if target is not None:
                    directory_targets.append(target)
        else:
            # 没有分类目录时，MoviePilot 只会落到媒体类型目录或整理根目录。
            if directory_type is not None:
                types = [directory_type]
            elif type_folder:
                types = list(COLLECTION_TYPES)
            else:
                continue
            for media_type in types:
                target = _make_target(
                    name=(
                        TYPE_NAME_PREFIXES.get(media_type, media_type)
                        + "媒体库"
                    ),
                    media_type=media_type,
                    root=root,
                    type_folder=type_folder,
                    category_path=None,
                    category_id=fixed_category.get("id") if fixed_category else None,
                )
                if target is not None:
                    directory_targets.append(target)

        for target in directory_targets:
            key = (target.collection_type, target.path)
            if key in seen:
                continue
            seen.add(key)
            targets.append(target)

    return _disambiguate_names(targets)
