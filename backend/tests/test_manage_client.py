"""管理端多地址解析与故障转移测试。"""

import httpx
import pytest

from app.config import settings
from app.services import manage_client


@pytest.fixture(autouse=True)
def reset_preferred():
    manage_client._preferred_base_url = None
    yield
    manage_client._preferred_base_url = None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", []),
        ("   ", []),
        ("https://m.one-tap.vip", ["https://m.one-tap.vip"]),
        (
            " https://m.one-tap.vip/ , https://m.one-tab.vip/ ",
            ["https://m.one-tap.vip", "https://m.one-tab.vip"],
        ),
        (
            "https://m.one-tap.vip,https://m.one-tap.vip",
            ["https://m.one-tap.vip"],
        ),
    ],
)
def test_manage_base_urls_parsing(monkeypatch, raw, expected):
    monkeypatch.setattr(settings, "MANAGE_BASE_URL", raw)
    assert manage_client.manage_base_urls() == expected


@pytest.mark.asyncio
async def test_fallback_to_next_url(monkeypatch):
    monkeypatch.setattr(
        settings,
        "MANAGE_BASE_URL",
        "https://m.one-tap.vip,https://m.one-tab.vip",
    )
    calls = []

    async def fetch(base_url):
        calls.append(base_url)
        if base_url == "https://m.one-tap.vip":
            raise httpx.ConnectError("boom")
        return httpx.Response(200, json={"ok": True})

    response, used = await manage_client.with_manage_fallback(fetch)
    assert used == "https://m.one-tab.vip"
    assert response.status_code == 200
    assert calls == ["https://m.one-tap.vip", "https://m.one-tab.vip"]

    # 成功的地址会被记住，下一次直接先用它
    calls.clear()
    _, used = await manage_client.with_manage_fallback(fetch)
    assert used == "https://m.one-tab.vip"
    assert calls == ["https://m.one-tab.vip"]


@pytest.mark.asyncio
async def test_fallback_on_5xx_but_not_4xx(monkeypatch):
    monkeypatch.setattr(
        settings,
        "MANAGE_BASE_URL",
        "https://a.test,https://b.test",
    )
    calls = []

    async def fetch(base_url):
        calls.append(base_url)
        return httpx.Response(502 if base_url == "https://a.test" else 200)

    _, used = await manage_client.with_manage_fallback(fetch)
    assert used == "https://b.test"

    calls.clear()
    manage_client._preferred_base_url = None

    async def fetch_404(base_url):
        calls.append(base_url)
        return httpx.Response(404)

    response, used = await manage_client.with_manage_fallback(fetch_404)
    assert response.status_code == 404
    assert used == "https://a.test"
    assert calls == ["https://a.test"]


@pytest.mark.asyncio
async def test_all_urls_failed(monkeypatch):
    monkeypatch.setattr(settings, "MANAGE_BASE_URL", "https://a.test,https://b.test")

    async def fetch(base_url):
        raise httpx.ConnectError("boom")

    with pytest.raises(manage_client.ManageRequestError):
        await manage_client.with_manage_fallback(fetch)


@pytest.mark.asyncio
async def test_no_url_configured(monkeypatch):
    monkeypatch.setattr(settings, "MANAGE_BASE_URL", "")

    async def fetch(base_url):
        raise AssertionError("未配置地址时不应发起请求")

    with pytest.raises(manage_client.ManageRequestError):
        await manage_client.with_manage_fallback(fetch)
