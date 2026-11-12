"""应用图标公开接口与本地缓存测试。"""

import pytest

from app.config import settings
from app.services.app_store import icon_cache


@pytest.fixture(autouse=True)
def _icon_dir(tmp_path, monkeypatch):
    """把图标目录指向临时目录，避免污染真实数据。"""
    monkeypatch.setattr(settings, "APP_ICON_DIR", str(tmp_path / "app-icons"))
    return tmp_path


async def test_get_app_icon_returns_versioned_response(client):
    """带指纹的地址返回本地图标，并允许浏览器长期强缓存。"""
    digest = icon_cache.store("jellyfin", b"<svg>icon</svg>", ".svg")

    response = await client.get(f"/api/apps/jellyfin/icon?v={digest}")

    assert response.status_code == 200
    assert response.content == b"<svg>icon</svg>"
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert response.headers["etag"]


async def test_get_app_icon_supports_if_none_match(client):
    """浏览器带上 ETag 协商时返回 304，不再传输图标内容。"""
    digest = icon_cache.store("jellyfin", b"<svg>icon</svg>", ".svg")
    first = await client.get(f"/api/apps/jellyfin/icon?v={digest}")
    etag = first.headers["etag"]

    response = await client.get(
        f"/api/apps/jellyfin/icon?v={digest}",
        headers={"If-None-Match": etag},
    )

    assert response.status_code == 304
    assert response.content == b""


async def test_get_app_icon_without_version_revalidates(client):
    """不带指纹的地址要求每次协商，避免换图后读到旧缓存。"""
    icon_cache.store("jellyfin", b"<svg>icon</svg>", ".svg")

    response = await client.get("/api/apps/jellyfin/icon")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"


async def test_get_app_icon_missing_returns_404(client):
    response = await client.get("/api/apps/unknown/icon")

    assert response.status_code == 404


async def test_icon_cache_ignores_path_traversal(client):
    """应用标识会被转成安全文件名，无法借图标接口读取任意文件。"""
    icon_cache.store("../../etc/passwd", b"secret", ".svg")

    assert icon_cache.safe_name("../../etc/passwd") != "../../etc/passwd"
    assert icon_cache.find("../../etc/passwd") is not None
    assert icon_cache.find("../../etc/passwd").parent == icon_cache.cache_dir()

    response = await client.get("/api/apps/..%2F..%2Fetc/icon")
    assert response.status_code == 404


async def test_store_replaces_other_extension(tmp_path):
    """图标格式变化时删除旧扩展名文件，避免读取到过期图标。"""
    icon_cache.store("jellyfin", b"png", ".png")
    icon_cache.store("jellyfin", b"<svg/>", ".svg")

    directory = tmp_path / "app-icons" / "apps"
    assert not (directory / "jellyfin.png").exists()
    assert (directory / "jellyfin.svg").read_bytes() == b"<svg/>"
