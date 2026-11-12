"""应用市场图标的本地缓存。

管理端下发的图标在同步时下载到 ``APP_ICON_DIR/apps`` 下，数据库只保存
带内容指纹的本地 URL。浏览器据此长期强缓存，仅当图标内容变化（URL 中
的指纹变化）时才重新拉取。
"""

import hashlib
import os
import time
from pathlib import Path

from app.config import settings

SUBDIR = "apps"
"""应用图标在 APP_ICON_DIR 下的子目录，避免与编排方案图标混放。"""

PRUNE_MIN_AGE_SECONDS = 600
"""清理未被引用图标的最小文件年龄。

两次同步可能并发（定时 + 手动），刚写入的文件留给对应同步提交数据库，
避免清理掉另一次同步刚缓存、但尚未落库的图标。
"""

EXTENSIONS = (".svg", ".png", ".jpg", ".jpeg", ".webp")
"""允许缓存的图标扩展名，顺序即查找优先级。"""

_CONTENT_TYPE_EXT = {
    "image/svg+xml": ".svg",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
}


def cache_dir() -> Path:
    """返回应用图标缓存目录。"""
    return Path(settings.APP_ICON_DIR) / SUBDIR


def safe_name(name: str) -> str:
    """把应用标识转成安全的文件名主体，异常字符用摘要代替。"""
    is_safe = (
        bool(name)
        and name.isascii()
        and name[0].isalnum()
        and all(char.isalnum() or char in "._-" for char in name)
    )
    if is_safe:
        return name
    return hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]


def extension_for(content_type: str | None, url: str = "") -> str | None:
    """根据响应 Content-Type（其次 URL 后缀）判断图标扩展名。"""
    if content_type:
        media_type = content_type.split(";", 1)[0].strip().lower()
        ext = _CONTENT_TYPE_EXT.get(media_type)
        if ext:
            return ext
    path = url.split("?", 1)[0].lower()
    for ext in EXTENSIONS:
        if path.endswith(ext):
            return ".jpg" if ext == ".jpeg" else ext
    return None


def store(name: str, content: bytes, ext: str) -> str:
    """写入缓存并返回内容指纹；内容未变时不重复写盘。"""
    digest = hashlib.sha256(content).hexdigest()[:12]
    directory = cache_dir()
    directory.mkdir(parents=True, exist_ok=True)

    stem = safe_name(name)
    target = directory / f"{stem}{ext}"
    if target.is_file():
        try:
            if hashlib.sha256(target.read_bytes()).hexdigest()[:12] == digest:
                return digest
        except OSError:
            pass

    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    tmp.write_bytes(content)
    os.replace(tmp, target)

    for other in EXTENSIONS:
        if other != ext:
            (directory / f"{stem}{other}").unlink(missing_ok=True)
    return digest


def remove(name: str) -> None:
    """删除某个应用的本地图标（不存在的文件忽略）。"""
    directory = cache_dir()
    stem = safe_name(name)
    for ext in EXTENSIONS:
        (directory / f"{stem}{ext}").unlink(missing_ok=True)


def find(name: str) -> Path | None:
    """返回应用图标的缓存文件，未缓存时返回 None。"""
    directory = cache_dir()
    stem = safe_name(name)
    for ext in EXTENSIONS:
        path = directory / f"{stem}{ext}"
        if path.is_file():
            return path
    return None


def url(name: str, digest: str) -> str:
    """带指纹的本地访问地址；指纹变化即触发浏览器重新拉取。"""
    return f"/api/apps/{name}/icon?v={digest}"


def prune(keep: set[str]) -> None:
    """删除不再被任何应用引用、且已过保护期的缓存文件。"""
    directory = cache_dir()
    if not directory.is_dir():
        return
    cutoff = time.time() - PRUNE_MIN_AGE_SECONDS
    for path in directory.iterdir():
        if not path.is_file() or path.suffix.lower() not in EXTENSIONS:
            continue
        if path.stem in keep:
            continue
        try:
            if path.stat().st_mtime > cutoff:
                continue
        except OSError:
            continue
        path.unlink(missing_ok=True)
