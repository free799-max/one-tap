"""封面代理：为通知收件箱和外部渠道提供可长期访问的图片地址。

上游封面（豆瓣/TMDB/Suwayomi）经常带防盗链或只在内网可达，
这里用 HMAC 签名的无状态 token 生成 ``/api/assets/cover/<token>.jpg``，
后端取图时补 Referer、限制大小并落盘缓存。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from pathlib import Path

import httpx

from app.config import settings

COVER_TTL_SECONDS = 90 * 24 * 3600
MAX_IMAGE_BYTES = 10 * 1024 * 1024
_CACHE_DIR_NAME = "cover-cache"


def _key() -> bytes:
    return hashlib.sha256(f"cover:{settings.SECRET_KEY}".encode()).digest()


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _sign(message: str) -> str:
    return _b64encode(hmac.new(_key(), message.encode(), hashlib.sha256).digest())


def make_cover_token(url: str, ttl_seconds: int = COVER_TTL_SECONDS) -> str:
    """把原始图片地址编码成带过期时间的签名 token。"""
    expires = int(time.time()) + max(ttl_seconds, 60)
    encoded = _b64encode(url.encode("utf-8"))
    message = f"{encoded}.{expires}"
    return f"{message}.{_sign(message)}"


def cover_path(url: str, ttl_seconds: int = COVER_TTL_SECONDS) -> str:
    """返回可直接给浏览器/聊天客户端使用的相对路径。"""
    return f"/api/assets/cover/{make_cover_token(url, ttl_seconds)}.jpg"


def resolve_cover_token(token: str) -> str | None:
    """校验 token 并返回原始图片地址。"""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    encoded, raw_expires, signature = parts
    message = f"{encoded}.{raw_expires}"
    if not hmac.compare_digest(signature, _sign(message)):
        return None
    try:
        expires = int(raw_expires)
    except ValueError:
        return None
    if expires < int(time.time()):
        return None
    try:
        url = _b64decode(encoded).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    if not url.startswith(("http://", "https://")):
        return None
    return url


def _cache_dir() -> Path:
    path = Path(settings.DATA_ROOT) / _CACHE_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_file(url: str) -> Path:
    return _cache_dir() / hashlib.sha256(url.encode()).hexdigest()


async def fetch_image(url: str) -> tuple[bytes, str] | None:
    """下载图片；失败或非图片时返回 None。"""
    cache_path = _cache_file(url)
    if cache_path.is_file():
        data = cache_path.read_bytes()
        if data:
            return data, _guess_content_type(data)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"
        ),
        "Accept": "image/jpeg,image/png,image/*;q=0.8",
    }
    host = httpx.URL(url).host or ""
    if "douban" in host:
        headers["Referer"] = "https://movie.douban.com/"

    result = await _download(url, headers)
    if result is not None and "webp" in result[1].lower():
        variant = _jpeg_variant(url)
        if variant:
            jpeg = await _download(variant, headers)
            if jpeg is not None and "webp" not in jpeg[1].lower():
                result = jpeg
    if result is None:
        return None

    data, content_type = result
    try:
        cache_path.write_bytes(data)
    except OSError:
        pass
    return data, content_type.split(";")[0].strip() or _guess_content_type(data)


async def _download(
    url: str, headers: dict[str, str]
) -> tuple[bytes, str] | None:
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15.0), follow_redirects=True
        ) as client:
            async with client.stream("GET", url, headers=headers) as response:
                if response.status_code >= 400:
                    return None
                content_type = response.headers.get("content-type", "")
                if "image" not in content_type.lower():
                    return None
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_IMAGE_BYTES:
                        return None
                    chunks.append(chunk)
                data = b"".join(chunks)
    except (httpx.HTTPError, ValueError):
        return None
    if not data:
        return None
    return data, content_type


def _jpeg_variant(url: str) -> str | None:
    """为常见封面源找一个 JPEG 版本，方便部分聊天平台上传图片。"""
    parsed = httpx.URL(url)
    if "image.tmdb.org" in (parsed.host or ""):
        if "format" not in parsed.params:
            return str(parsed.copy_add_param("format", "jpg"))
        return None
    if parsed.path.lower().endswith(".webp"):
        path = parsed.path[: -len(".webp")] + ".jpg"
        return str(parsed.copy_with(path=path))
    return None


def _guess_content_type(data: bytes) -> str:
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"
