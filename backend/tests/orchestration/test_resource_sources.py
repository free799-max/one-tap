"""项目资源源目录、筛选、实例配置与装配测试。"""

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.core.exceptions import APIException
from app.database import Base
from app.models.app_store import App
from app.models.orchestration import (
    AppInstance,
    AppOrchestration,
    AppOrchestrationInstance,
)
from app.services.apps.suwayomi.client import SuwayomiClient
from app.services.orchestration import orchestration_service
from app.services.orchestration import resource_sources as module
from app.services.orchestration.resource_sources import (
    ResourceSourceService,
    load_catalog,
    normalize_config,
)


def test_catalogs_contain_source_addresses_not_site_snapshots():
    for app_name in ("reader", "suwayomi"):
        items = load_catalog(app_name)["items"]
        assert items
        for item in items:
            assert item["url"].startswith("https://")
            assert "source" not in item
            assert "apk_urls" not in item
            assert "source_names" not in item


def test_default_config_and_explicit_empty_selection():
    defaults = normalize_config("reader", None)
    assert defaults["enabled"] is True
    assert defaults["auto_filter"] is True
    assert defaults["selected_ids"] == ["xiu2"]
    assert defaults["book_source_limit"] == 200
    assert "book_source_limit" not in normalize_config("suwayomi", None)

    empty = normalize_config("reader", {"selected_ids": []})
    assert empty["selected_ids"] == []


def test_reader_simple_filter_removes_unusable_sources():
    sources = [
        {
            "bookSourceUrl": "https://ok.example",
            "bookSourceType": 0,
            "searchUrl": "https://ok.example/search",
            "enabled": True,
        },
        {
            "bookSourceUrl": "https://disabled.example",
            "bookSourceType": 0,
            "searchUrl": "https://disabled.example/search",
            "enabled": False,
        },
        {
            "bookSourceUrl": "https://captcha.example",
            "bookSourceType": 0,
            "searchUrl": "https://captcha.example/search",
            "bookSourceGroup": "人机验证",
            "enabled": True,
        },
        {
            "bookSourceUrl": "https://audio.example",
            "bookSourceType": 1,
            "searchUrl": "https://audio.example/search",
            "enabled": True,
        },
        {
            "bookSourceUrl": "not-a-url",
            "bookSourceType": 0,
            "searchUrl": "https://ok.example/search",
            "enabled": True,
        },
    ]

    filtered = ResourceSourceService._filter_book_sources(
        sources, auto_filter=True
    )
    assert [item["bookSourceUrl"] for item in filtered] == [
        "https://ok.example"
    ]

    unfiltered = ResourceSourceService._filter_book_sources(
        sources, auto_filter=False
    )
    assert {item["bookSourceUrl"] for item in unfiltered} == {
        "https://ok.example",
        "https://disabled.example",
        "https://captcha.example",
    }


@pytest.mark.asyncio
async def test_reader_apply_imports_and_verifies_remote_sources(monkeypatch):
    source = {
        "bookSourceUrl": "https://ok.example",
        "bookSourceType": 0,
        "searchUrl": "https://ok.example/search",
        "enabled": True,
    }
    service = ResourceSourceService()
    monkeypatch.setattr(
        service,
        "_fetch_book_sources",
        AsyncMock(return_value=[source]),
    )

    class FakeReaderSession:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def update_book_source_limit(self, username, limit, secure_key):
            assert username == "admin"
            assert limit == 200
            assert secure_key == "test-key"

        async def save_book_sources(self, sources):
            assert sources == [source]
            return {"limit_reached": False, "message": ""}

        async def book_sources(self):
            return [source]

    monkeypatch.setattr(module, "ReaderSession", FakeReaderSession)
    result = await service.apply(
        "reader",
        {
            "url": "http://reader.local",
            "username": "admin",
            "_secure_key": "test-key",
            "resource_sources": {"selected_ids": ["xiu2"]},
        },
    )

    assert result["status"] == "ok"
    assert result["source_ids"] == ["https://ok.example"]
    assert result["items"][0]["status"] == "ok"


@pytest.mark.asyncio
async def test_reader_source_limit_response_is_not_an_error(monkeypatch):
    session = module.ReaderSession("http://reader.local")
    post = AsyncMock(
        return_value={
            "isSuccess": False,
            "errorMsg": "新增0条书源，更新100条书源。你已达到书源数上限，请联系管理员",
        }
    )
    monkeypatch.setattr(session, "_post_json", post)

    result = await session.save_book_sources([{"bookSourceUrl": "https://ok.example"}])
    await session.aclose()

    assert result["limit_reached"] is True
    assert "更新100条书源" in result["message"]


@pytest.mark.asyncio
async def test_reader_limit_keeps_user_sources_and_skips_legacy_limit_update(
    monkeypatch,
):
    selected = {
        "bookSourceUrl": "https://managed.example",
        "bookSourceType": 0,
        "searchUrl": "https://managed.example/search",
        "enabled": True,
    }
    user_source = {
        "bookSourceUrl": "https://user-added.example",
        "bookSourceType": 0,
        "searchUrl": "https://user-added.example/search",
        "enabled": True,
    }
    service = ResourceSourceService()
    monkeypatch.setattr(
        service,
        "_fetch_book_sources",
        AsyncMock(return_value=[selected]),
    )

    class FakeReaderSession:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def update_book_source_limit(self, *_args):
            raise AssertionError("旧实例缺少管理密码时不应调用上限接口")

        async def save_book_sources(self, sources):
            assert sources == [selected]
            return {
                "limit_reached": True,
                "message": "新增0条书源，更新100条书源。你已达到书源数上限",
            }

        async def book_sources(self):
            return [user_source]

    monkeypatch.setattr(module, "ReaderSession", FakeReaderSession)
    result = await service.apply(
        "reader",
        {
            "url": "http://reader.local",
            "username": "admin",
            "resource_sources": {"selected_ids": ["xiu2"]},
        },
    )

    assert result["status"] == "ok"
    assert result["source_ids"] == []


@pytest.mark.asyncio
async def test_suwayomi_filters_extensions_and_skips_installed(monkeypatch):
    client = SuwayomiClient("http://suwayomi.local")
    monkeypatch.setattr(
        client, "ensure_store", AsyncMock(return_value="https://store/repo.json")
    )
    monkeypatch.setattr(
        client,
        "fetch_extensions",
        AsyncMock(
            return_value=[
                {
                    "pkgName": "pkg.zh.new",
                    "lang": "zh",
                    "isInstalled": False,
                    "isNsfw": False,
                    "isObsolete": False,
                    "storeIndexUrl": "https://store/repo.json",
                },
                {
                    "pkgName": "pkg.zh.installed",
                    "lang": "zh-Hans",
                    "isInstalled": True,
                    "isNsfw": False,
                    "isObsolete": False,
                    "storeIndexUrl": "https://store/repo.json",
                },
                {
                    "pkgName": "pkg.en",
                    "lang": "en",
                    "isInstalled": False,
                    "isNsfw": False,
                    "isObsolete": False,
                    "storeIndexUrl": "https://store/repo.json",
                },
                {
                    "pkgName": "pkg.nsfw",
                    "lang": "zh",
                    "isInstalled": False,
                    "isNsfw": True,
                    "isObsolete": False,
                    "storeIndexUrl": "https://store/repo.json",
                },
                {
                    "pkgName": "pkg.obsolete",
                    "lang": "zh",
                    "isInstalled": False,
                    "isNsfw": False,
                    "isObsolete": True,
                    "storeIndexUrl": "https://store/repo.json",
                },
            ]
        ),
    )
    install = AsyncMock()
    monkeypatch.setattr(client, "install_extensions", install)
    monkeypatch.setattr(
        client,
        "sources",
        AsyncMock(
            return_value=[
                {"id": "0", "extension": {"pkgName": "local"}},
                {"id": "12", "extension": {"pkgName": "pkg.zh.new"}},
                {"id": "13", "extension": {"pkgName": "pkg.zh.installed"}},
            ]
        ),
    )
    monkeypatch.setattr(
        client, "probe_source", AsyncMock(side_effect=[False, True])
    )

    result = await client.install_repository(
        {
            "url": "https://store/index.min.json",
            "mirror_urls": [],
        }
    )
    await client.aclose()

    install.assert_awaited_once_with(["pkg.zh.new", "pkg.nsfw"])
    # 成人扩展保留安装，只用中文与未废弃做筛选
    assert result["installed_ids"] == [
        "pkg.zh.new",
        "pkg.zh.installed",
        "pkg.nsfw",
    ]
    assert result["source_ids"] == ["13", "12"]
    assert result["healthy_count"] == 1


@pytest_asyncio.fixture
async def session_local():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    yield factory
    await engine.dispose()


async def _seed(session_local, app_configs, app_name="suwayomi"):
    async with session_local() as db:
        app = App(name=app_name, display_name=app_name, yaml_template="")
        orchestration = AppOrchestration(
            name="comic",
            display_name="漫画",
            category="comic",
            app_composition=[],
            shared_config_schema={},
        )
        db.add_all([app, orchestration])
        await db.flush()
        group = AppOrchestrationInstance(
            orchestration_id=orchestration.id,
            instance_name="comic",
            app_configs=app_configs,
        )
        db.add(group)
        await db.flush()
        db.add(
            AppInstance(
                app_id=app.id,
                orchestration_id=orchestration.id,
                orchestration_group_id=group.id,
                instance_name=f"comic-{app_name}",
            )
        )
        await db.commit()
        return group.id


@pytest.mark.asyncio
async def test_save_config_preserves_result_and_validates_ids(session_local):
    group_id = await _seed(
        session_local,
        {
            "suwayomi": {
                "resource_sources": {
                    "selected_ids": ["keiyoushi"],
                    "last_result": {"status": "ok", "source_ids": ["12"]},
                }
            }
        },
    )
    service = ResourceSourceService()
    async with session_local() as db:
        panel = await service.save_config(
            db,
            group_id,
            "suwayomi",
            {
                "enabled": False,
                "auto_filter": False,
                "selected_ids": ["copymanga-copy20"],
            },
        )
        assert panel["config"]["enabled"] is False
        assert panel["config"]["selected_ids"] == ["copymanga-copy20"]
        assert panel["last_result"]["source_ids"] == ["12"]

        with pytest.raises(APIException) as exc:
            await service.save_config(
                db,
                group_id,
                "suwayomi",
                {"selected_ids": ["unknown"]},
            )
        assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_wiring_persists_source_result_without_integrations(
    session_local, monkeypatch
):
    group_id = await _seed(
        session_local,
        {
            "suwayomi": {
                "url": "http://suwayomi.local",
                "resource_sources": {"selected_ids": ["keiyoushi"]},
            }
        },
    )
    monkeypatch.setattr(
        "app.services.orchestration.wiring.async_session", session_local
    )
    apply = AsyncMock(
        return_value={"status": "ok", "source_ids": ["12"], "items": []}
    )
    monkeypatch.setattr(
        "app.services.orchestration.wiring.resource_source_service.apply", apply
    )

    await orchestration_service._do_wire_group(group_id)

    async with session_local() as db:
        group = await db.get(AppOrchestrationInstance, group_id)
        config = group.app_configs["suwayomi"]["resource_sources"]
        assert config["selected_ids"] == ["keiyoushi"]
        assert config["last_result"]["source_ids"] == ["12"]
    apply.assert_awaited_once()


@pytest.mark.asyncio
async def test_reader_primary_uses_proxy_before_mirror():
    import httpx

    primary = "https://primary.example/sources.json"
    mirror = "https://mirror.example/sources.json"
    repository = {"url": primary, "mirror_urls": [mirror]}
    source = {
        "bookSourceUrl": "https://ok.example",
        "bookSourceType": 0,
        "searchUrl": "https://ok.example/search",
        "enabled": True,
    }
    calls = []

    class FakeClient:
        def __init__(self, name, outcomes):
            self.name = name
            self.outcomes = outcomes

        async def get(self, url):
            calls.append((self.name, url))
            outcome = self.outcomes.get(url)
            if isinstance(outcome, Exception):
                raise outcome
            if outcome is None:
                raise AssertionError(f"不应请求 {url}")
            return httpx.Response(
                200,
                json=[source],
                request=httpx.Request("GET", url),
            )

    service = ResourceSourceService()
    failure = httpx.ConnectError("offline")
    direct = FakeClient("direct", {primary: failure})
    proxied = FakeClient("proxy", {primary: True})
    result = await service._fetch_book_sources(
        direct,
        repository,
        auto_filter=True,
        proxy_client=proxied,
    )
    assert result == [source]
    assert calls == [("direct", primary), ("proxy", primary)]

    calls.clear()
    direct = FakeClient("direct", {primary: failure, mirror: True})
    proxied = FakeClient("proxy", {primary: failure})
    result = await service._fetch_book_sources(
        direct,
        repository,
        auto_filter=True,
        proxy_client=proxied,
    )
    assert result == [source]
    assert calls == [
        ("direct", primary),
        ("proxy", primary),
        ("direct", mirror),
    ]


@pytest.mark.asyncio
async def test_suwayomi_primary_proxy_covers_install_and_restores_settings(
    monkeypatch,
):
    client = SuwayomiClient("http://suwayomi.local")
    primary = "https://primary.example/index.json"
    state = {"proxy": False, "store": ""}
    calls = []
    original = {
        "socksProxyEnabled": False,
        "socksProxyVersion": 5,
        "socksProxyHost": "",
        "socksProxyPort": "",
        "socksProxyUsername": "",
        "socksProxyPassword": "",
    }

    async def get_settings():
        return dict(original)

    async def set_settings(settings):
        state["proxy"] = settings["socksProxyEnabled"]

    async def ensure_store(urls):
        calls.append(("store", urls[0], state["proxy"]))
        if not state["proxy"]:
            raise module.SuwayomiClientError("direct failed")
        state["store"] = urls[0]
        return urls[0]

    async def fetch_extensions():
        return [
            {
                "pkgName": "pkg.zh",
                "lang": "zh",
                "isInstalled": False,
                "storeIndexUrl": state["store"],
            }
        ]

    async def install_extensions(package_names):
        calls.append(("install", package_names, state["proxy"]))

    monkeypatch.setattr(client, "proxy_settings", get_settings)
    monkeypatch.setattr(client, "set_proxy_settings", set_settings)
    monkeypatch.setattr(client, "ensure_store", ensure_store)
    monkeypatch.setattr(client, "fetch_extensions", fetch_extensions)
    monkeypatch.setattr(client, "install_extensions", install_extensions)
    monkeypatch.setattr(
        client,
        "sources",
        AsyncMock(
            return_value=[
                {"id": "12", "extension": {"pkgName": "pkg.zh"}}
            ]
        ),
    )

    result = await client.install_repository(
        {"url": primary, "mirror_urls": ["https://mirror.example/index.json"]},
        auto_filter=False,
        proxy="http://user:pass@proxy.example:9002",
    )
    await client.aclose()

    assert result["extension_count"] == 1
    assert calls == [
        ("store", primary, False),
        ("store", primary, True),
        ("install", ["pkg.zh"], True),
    ]
    assert state["proxy"] is False


@pytest.mark.asyncio
async def test_suwayomi_failed_proxy_falls_back_to_mirror_and_restores(monkeypatch):
    client = SuwayomiClient("http://suwayomi.local")
    primary = "https://primary.example/index.json"
    mirror = "https://mirror.example/index.json"
    state = {"proxy": False, "store": ""}
    calls = []

    async def get_settings():
        return {"socksProxyEnabled": False}

    async def set_settings(settings):
        state["proxy"] = settings["socksProxyEnabled"]

    async def ensure_store(urls):
        calls.append(("store", urls[0], state["proxy"]))
        if urls[0] == primary:
            raise module.SuwayomiClientError("primary failed")
        state["store"] = urls[0]
        return urls[0]

    async def fetch_extensions():
        return [
            {
                "pkgName": "pkg.zh",
                "lang": "zh",
                "isInstalled": False,
                "storeIndexUrl": state["store"],
            }
        ]

    async def install_extensions(package_names):
        calls.append(("install", package_names, state["proxy"]))

    monkeypatch.setattr(client, "proxy_settings", get_settings)
    monkeypatch.setattr(client, "set_proxy_settings", set_settings)
    monkeypatch.setattr(client, "ensure_store", ensure_store)
    monkeypatch.setattr(client, "fetch_extensions", fetch_extensions)
    monkeypatch.setattr(client, "install_extensions", install_extensions)
    monkeypatch.setattr(
        client,
        "sources",
        AsyncMock(
            return_value=[
                {"id": "12", "extension": {"pkgName": "pkg.zh"}}
            ]
        ),
    )

    result = await client.install_repository(
        {"url": primary, "mirror_urls": [mirror]},
        auto_filter=False,
        proxy="http://proxy.example:9002",
    )
    await client.aclose()

    assert result["source_ids"] == ["12"]
    assert calls == [
        ("store", primary, False),
        ("store", primary, True),
        ("store", mirror, False),
        ("install", ["pkg.zh"], False),
    ]
    assert state["proxy"] is False


@pytest.mark.asyncio
async def test_wiring_reads_enabled_system_proxy(session_local, monkeypatch):
    from app.models.system_config import SystemConfig

    group_id = await _seed(
        session_local,
        {
            "suwayomi": {
                "url": "http://suwayomi.local",
                "resource_sources": {"selected_ids": ["keiyoushi"]},
            }
        },
    )
    async with session_local() as db:
        db.add(
            SystemConfig(
                id=1,
                proxy_enabled=True,
                https_proxy="http://proxy.example:9002",
            )
        )
        await db.commit()
    monkeypatch.setattr(
        "app.services.orchestration.wiring.async_session", session_local
    )
    apply = AsyncMock(return_value={"status": "skipped", "items": []})
    monkeypatch.setattr(
        "app.services.orchestration.wiring.resource_source_service.apply", apply
    )

    await orchestration_service._do_wire_group(group_id)

    apply.assert_awaited_once_with(
        "suwayomi",
        {
            "url": "http://suwayomi.local",
            "resource_sources": {"selected_ids": ["keiyoushi"]},
        },
        proxy="http://proxy.example:9002",
    )


@pytest.mark.asyncio
async def test_wiring_passes_reader_management_key_without_persisting_it(
    session_local, monkeypatch
):
    group_id = await _seed(
        session_local,
        {
            "reader": {
                "url": "http://reader.local",
                "username": "admin",
                "resource_sources": {"selected_ids": ["xiu2"]},
            }
        },
        app_name="reader",
    )
    async with session_local() as db:
        instance = await db.scalar(
            select(AppInstance).where(AppInstance.orchestration_group_id == group_id)
        )
        instance.config = {
            "reader_env": [
                {"key": "READER_APP_SECUREKEY", "value": "test-key"}
            ]
        }
        await db.commit()
    monkeypatch.setattr(
        "app.services.orchestration.wiring.async_session", session_local
    )
    apply = AsyncMock(return_value={"status": "skipped", "items": []})
    monkeypatch.setattr(
        "app.services.orchestration.wiring.resource_source_service.apply", apply
    )

    await orchestration_service._do_wire_group(group_id)

    entry = apply.await_args.args[1]
    assert entry["_secure_key"] == "test-key"
    async with session_local() as db:
        group = await db.get(AppOrchestrationInstance, group_id)
        assert "_secure_key" not in group.app_configs["reader"]


def test_custom_reader_sources_are_normalized_and_selected():
    custom = {
        "id": "custom-my-sources",
        "name": "我的书源",
        "url": "https://sources.example/reader.json",
    }
    config = normalize_config(
        "reader",
        {
            "custom_sources": [
                custom,
                {**custom, "name": "重复 ID"},
                {
                    "id": "custom-invalid-url",
                    "name": "无效地址",
                    "url": "ftp://sources.example/reader.json",
                },
            ],
            "selected_ids": ["xiu2", custom["id"], "custom-missing"],
        },
    )

    assert config["custom_sources"] == [custom]
    assert config["selected_ids"] == ["xiu2", custom["id"]]
    suwayomi = normalize_config("suwayomi", {"custom_sources": [custom]})
    assert suwayomi["custom_sources"] == [custom]
    assert suwayomi["selected_ids"] == [
        item["id"]
        for item in load_catalog("suwayomi")["items"]
        if item.get("default_selected", True)
    ]
    assert "book_source_limit" not in suwayomi


@pytest.mark.asyncio
async def test_save_config_manages_custom_reader_sources(session_local):
    group_id = await _seed(
        session_local,
        {"reader": {"resource_sources": {"selected_ids": ["xiu2"]}}},
        app_name="reader",
    )
    custom = {
        "id": "custom-my-sources",
        "name": "我的书源",
        "url": "https://sources.example/reader.json",
    }
    service = ResourceSourceService()
    async with session_local() as db:
        panel = await service.save_config(
            db,
            group_id,
            "reader",
            {
                "enabled": True,
                "auto_filter": True,
                "custom_sources": [custom],
                "selected_ids": ["xiu2", custom["id"]],
            },
        )
        assert panel["config"]["custom_sources"] == [custom]
        assert panel["config"]["selected_ids"] == ["xiu2", custom["id"]]
        option = next(
            item for item in panel["options"] if item["id"] == custom["id"]
        )
        assert option["custom"] is True
        assert option["description"] == custom["url"]

        with pytest.raises(APIException) as exc:
            await service.save_config(
                db,
                group_id,
                "reader",
                {
                    "custom_sources": [custom],
                    "selected_ids": ["custom-missing"],
                },
            )
        assert exc.value.status_code == 400

        panel = await service.save_config(
            db,
            group_id,
            "reader",
            {
                "enabled": True,
                "auto_filter": True,
                "custom_sources": [],
                "selected_ids": ["xiu2"],
            },
        )
        assert panel["config"]["custom_sources"] == []
        assert all(not item["custom"] for item in panel["options"])


@pytest.mark.asyncio
async def test_reader_apply_downloads_selected_custom_source(monkeypatch):
    book_source = {
        "bookSourceUrl": "https://book.example",
        "bookSourceType": 0,
        "searchUrl": "https://book.example/search",
        "enabled": True,
    }
    custom = {
        "id": "custom-my-sources",
        "name": "我的书源",
        "url": "https://sources.example/reader.json",
    }
    service = ResourceSourceService()
    fetch = AsyncMock(
        side_effect=[
            [book_source],
            module.ReaderClientError("offline"),
        ]
    )
    monkeypatch.setattr(service, "_fetch_book_sources", fetch)

    class FakeReaderSession:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def save_book_sources(self, sources):
            assert sources == [book_source]
            return {"limit_reached": False, "message": ""}

        async def book_sources(self):
            return [book_source]

    monkeypatch.setattr(module, "ReaderSession", FakeReaderSession)
    result = await service.apply(
        "reader",
        {
            "url": "http://reader.local",
            "username": "admin",
            "resource_sources": {
                "custom_sources": [custom],
                "selected_ids": [custom["id"], "xiu2"],
            },
        },
    )

    assert fetch.await_args_list[0].args[1]["url"] == custom["url"]
    assert fetch.await_args_list[1].args[1]["url"] == load_catalog("reader")[
        "items"
    ][0]["url"]
    assert result["status"] == "partial"
    assert result["items"][0]["id"] == custom["id"]
    assert result["items"][0]["status"] == "ok"
    assert result["items"][1]["id"] == "xiu2"
    assert result["items"][1]["status"] == "error"
    assert result["source_ids"] == ["https://book.example"]


@pytest.mark.asyncio
async def test_save_config_manages_custom_suwayomi_sources(session_local):
    group_id = await _seed(session_local, {"suwayomi": {}})
    custom = {
        "id": "custom-my-comics",
        "name": "我的漫源",
        "url": "https://extensions.example/index.min.json",
    }
    service = ResourceSourceService()
    async with session_local() as db:
        panel = await service.save_config(
            db,
            group_id,
            "suwayomi",
            {
                "enabled": True,
                "auto_filter": True,
                "custom_sources": [custom],
                "selected_ids": [custom["id"]],
            },
        )
    assert panel["config"]["custom_sources"] == [custom]
    assert panel["config"]["selected_ids"] == [custom["id"]]
    option = next(item for item in panel["options"] if item["id"] == custom["id"])
    assert option["custom"] is True
    assert option["description"] == custom["url"]


@pytest.mark.asyncio
async def test_suwayomi_apply_installs_selected_custom_source(monkeypatch):
    custom = {
        "id": "custom-my-comics",
        "name": "我的漫源",
        "url": "https://extensions.example/index.min.json",
    }
    installed = []

    class FakeSuwayomiClient:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def wait_ready(self):
            return None

        async def install_repository(
            self, repository, *, auto_filter, proxy=None
        ):
            installed.append((repository, auto_filter, proxy))
            return {
                "installed_ids": ["pkg.demo"],
                "source_ids": ["7"],
                "extension_count": 1,
                "source_count": 1,
                "healthy_count": 1,
            }

    monkeypatch.setattr(module, "SuwayomiClient", FakeSuwayomiClient)
    result = await ResourceSourceService().apply(
        "suwayomi",
        {
            "url": "http://suwayomi.local",
            "resource_sources": {
                "custom_sources": [custom],
                "selected_ids": [custom["id"]],
            },
        },
    )

    assert installed == [(custom, True, None)]
    assert result["status"] == "ok"
    assert result["items"][0]["id"] == custom["id"]
    assert result["items"][0]["status"] == "ok"
    assert result["source_ids"] == ["7"]


def test_custom_source_input_requires_name_and_http_url():
    from pydantic import ValidationError

    from app.schemas.orchestration.resource_sources import ResourceSourceConfigIn

    valid = {
        "id": "custom-my-sources",
        "name": " 我的书源 ",
        "url": " https://sources.example/reader.json ",
    }
    config = ResourceSourceConfigIn(custom_sources=[valid])
    assert config.custom_sources[0].name == "我的书源"
    assert config.custom_sources[0].url == "https://sources.example/reader.json"

    for invalid in (
        {**valid, "id": "not-custom"},
        {**valid, "name": "  "},
        {**valid, "url": "ftp://sources.example/reader.json"},
        {**valid, "url": "not-a-url"},
    ):
        with pytest.raises(ValidationError):
            ResourceSourceConfigIn(custom_sources=[invalid])
