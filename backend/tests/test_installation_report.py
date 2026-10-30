"""安装上报测试：免费版与授权版都会上报，一台机器只上报一个 installation_id。"""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx

from app.services import installation_report
from app.services.installation_report import installation_reporter


@asynccontextmanager
async def _fake_session():
    yield SimpleNamespace(commit=AsyncMock())


async def test_report_once_sends_installation_details(monkeypatch):
    state = SimpleNamespace(
        installation_id="0df2f287-026b-47b2-a4f5-1b8efacdb734",
        device_name="客厅 NAS",
    )

    async def get_state(_db):
        return state

    captured: dict = {}

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, json):
            captured["url"] = url
            captured["payload"] = json
            return httpx.Response(
                200, request=httpx.Request("POST", url), json={"data": {}}
            )

    monkeypatch.setattr(installation_report, "async_session", _fake_session)
    monkeypatch.setattr(installation_report.licensing_service, "get_state", get_state)
    monkeypatch.setattr(installation_report.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(installation_report.settings, "MANAGE_BASE_URL", "https://m.one-tap.vip")
    monkeypatch.setattr(installation_report.settings, "APP_VERSION", "v0.4.0")

    assert await installation_reporter.report_once() is True
    assert captured["url"] == "https://m.one-tap.vip/api/public/installations/report"
    payload = captured["payload"]
    assert payload["installation_id"] == state.installation_id
    assert payload["device_name"] == "客厅 NAS"
    assert payload["hostname"]
    assert payload["app_version"] == "v0.4.0"
    assert len(payload["platform"] or "") <= 100


async def test_report_once_skips_without_manage_url(monkeypatch):
    monkeypatch.setattr(installation_report.settings, "MANAGE_BASE_URL", "")
    assert await installation_reporter.report_once() is False


async def test_report_once_returns_false_on_all_addresses_failed(monkeypatch):
    state = SimpleNamespace(installation_id="installation-1", device_name=None)

    async def get_state(_db):
        return state

    class BrokenClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, json):
            raise httpx.ConnectError("unreachable")

    monkeypatch.setattr(installation_report, "async_session", _fake_session)
    monkeypatch.setattr(installation_report.licensing_service, "get_state", get_state)
    monkeypatch.setattr(installation_report.httpx, "AsyncClient", BrokenClient)
    monkeypatch.setattr(
        installation_report.settings,
        "MANAGE_BASE_URL",
        "https://a.example,https://b.example",
    )

    assert await installation_reporter.report_once() is False
