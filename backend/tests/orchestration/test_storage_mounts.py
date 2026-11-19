"""巡影网盘 -> OpenList 存储挂载：凭据映射、路径选择与一键挂载测试。"""

from types import SimpleNamespace

import pytest

from app.core.exceptions import APIException
from app.services.orchestration import storage_mounts
from app.services.orchestration.storage_mounts import (
    StorageMountService,
    _mask_uid,
    _read_mediary_drives,
    _storage_mount_path,
    build_addition,
)


def _group(app_configs=None):
    return SimpleNamespace(
        app_configs=app_configs
        or {
            "openlist": {
                "url": "http://openlist:5244",
                "username": "admin",
                "password": "pass",
            }
        },
        instances=[
            SimpleNamespace(
                app=SimpleNamespace(name="mediary-scout"),
                project=SimpleNamespace(project_name="movie-cloud-mediary-scout"),
            ),
            SimpleNamespace(
                app=SimpleNamespace(name="openlist"),
                project=SimpleNamespace(project_name="movie-cloud-openlist"),
            ),
        ],
    )


def _drive(**overrides):
    drive = {
        "id": "cs_quark_AARqyWwHjBfIR0NACb5LNE7U",
        "provider": "quark",
        "uid": "AARqyWwHjBfIR0NACb5LNE7U",
        "status": "active",
        "root": "9e0a3274883e4fc4b1cc61b71c334aab",
        "label": None,
        "payload": {"cookie": "__pus=abc; __kps=def"},
    }
    drive.update(overrides)
    return drive


def test_build_addition_uses_quark_root():
    addition = build_addition("quark", _drive())

    assert addition["cookie"] == "__pus=abc; __kps=def"
    assert addition["root_folder_id"] == "9e0a3274883e4fc4b1cc61b71c334aab"


def test_build_addition_uc_uses_cookie():
    addition = build_addition("uc", _drive(provider="uc"))

    assert addition["cookie"] == "__pus=abc; __kps=def"


def test_build_addition_115_uses_cookie():
    addition = build_addition("pan115", _drive(provider="pan115", root="0"))

    assert addition["root_folder_id"] == "0"
    assert addition["cookie"].startswith("__pus")


def test_build_addition_guangya_tokens():
    drive = _drive(
        provider="guangya",
        root="",
        payload={"accessToken": "at", "refreshToken": "gy.rt", "deviceId": "dev1"},
    )

    addition = build_addition("guangya", drive)

    assert addition["access_token"] == "at"
    assert addition["refresh_token"] == "gy.rt"
    assert addition["client_id"] == storage_mounts.GUANGYA_CLIENT_ID
    assert addition["device_id"] == "dev1"


def test_build_addition_tianyi_defaults_to_personal_root():
    drive = _drive(
        provider="tianyi",
        root=None,
        payload={"sessionKey": "s", "accessToken": "at", "refreshToken": "rt"},
    )

    addition = build_addition("tianyi", drive)

    assert addition["root_folder_id"] == "-11"
    assert addition["access_token"] == "at"
    assert addition["refresh_token"] == "rt"
    assert addition["type"] == "personal"


def test_build_addition_pan123_token():
    drive = _drive(
        provider="pan123", root="0", payload={"token": "eyJtoken"}
    )

    addition = build_addition("pan123", drive)

    assert addition["AccessToken"] == "eyJtoken"
    assert addition["root_folder_id"] == "0"


def test_build_addition_rejects_unknown_provider():
    with pytest.raises(APIException):
        build_addition("unknown", _drive(provider="unknown"))


def test_build_addition_requires_credential():
    with pytest.raises(APIException):
        build_addition("quark", _drive(payload={}))
    with pytest.raises(APIException):
        build_addition("guangya", _drive(provider="guangya", payload={}))


def test_mask_uid_keeps_short_value():
    assert _mask_uid("AARqyWwHjBfIR0NACb5LNE7U") == "AARq…NE7U"
    assert _mask_uid("short") == "short"


def test_storage_mount_path_matches_remark():
    storage = {
        "id": 3,
        "driver": "Quark",
        "mount_path": "/云盘",
        "remark": "巡影同步 · 夸克网盘 · AARq…NE7U",
    }

    found, path = _storage_mount_path(
        [storage], "quark", "巡影同步 · 夸克网盘 · AARq…NE7U"
    )

    assert found is storage
    assert path == "/云盘"


def test_storage_mount_path_matches_default_driver_path():
    storage = {"id": 4, "driver": "Quark", "mount_path": "/夸克", "remark": ""}

    found, path = _storage_mount_path(
        [storage], "quark", "巡影同步 · 夸克网盘 · AARq…NE7U"
    )

    assert found is storage
    assert path == "/夸克"


def test_storage_mount_path_avoids_conflict():
    storages = [
        {"id": 1, "driver": "Local", "mount_path": "/夸克", "remark": ""},
        {"id": 2, "driver": "Local", "mount_path": "/夸克-2", "remark": ""},
    ]

    found, path = _storage_mount_path(
        storages, "quark", "巡影同步 · 夸克网盘 · AARq…NE7U"
    )

    assert found is None
    assert path == "/夸克-3"


def test_read_mediary_drives_parses_json(monkeypatch):
    calls = {}

    class FakeResult:
        exit_code = 0
        output = b'[{"id": "cs_quark_1", "provider": "quark", "uid": "u1", "status": "active", "root": "r1", "label": null, "payload": {"cookie": "c1"}}]'

    class FakeContainer:
        attrs = {
            "Config": {
                "Env": [
                    "POSTGRES_USER=mediatrack",
                    "POSTGRES_DB=mediatrack",
                ]
            }
        }

        def exec_run(self, cmd):
            calls["cmd"] = cmd
            return FakeResult()

    class FakeContainers:
        def list(self, **kwargs):
            calls["filters"] = kwargs.get("filters")
            return [FakeContainer()]

    monkeypatch.setattr(
        storage_mounts,
        "docker_manager",
        SimpleNamespace(_client=SimpleNamespace(containers=FakeContainers())),
    )

    drives = _read_mediary_drives("movie-cloud-mediary-scout")

    assert drives[0]["provider"] == "quark"
    assert drives[0]["payload"]["cookie"] == "c1"
    assert calls["cmd"][:4] == ["psql", "-U", "mediatrack", "-d"]
    labels = calls["filters"]["label"]
    assert "com.docker.compose.project=movie-cloud-mediary-scout" in labels


@pytest.mark.asyncio
async def test_get_panel_returns_mount_details(monkeypatch):
    group = _group()

    async def fake_load_group(db, group_id):
        return group

    monkeypatch.setattr(storage_mounts, "_load_group", fake_load_group)
    monkeypatch.setattr(
        storage_mounts,
        "_read_mediary_drives",
        lambda project: [_drive()],
    )

    async def fake_list(self, url, username, password):
        return [
            {
                "id": 9,
                "driver": "Quark",
                "mount_path": "/夸克",
                "remark": "巡影同步 · 夸克网盘 · AARq…NE7U",
                "addition": '{"root_folder_id": "9e0a3274883e4fc4b1cc61b71c334aab"}',
            }
        ]

    async def fake_files(self, url, username, password, path, per_page=10):
        return [{"name": "Movies"}, {"name": "Anime"}, {"name": "TV"}]

    monkeypatch.setattr(
        storage_mounts.OpenListClient, "list_storages", fake_list
    )
    monkeypatch.setattr(
        storage_mounts.OpenListClient, "list_files", fake_files
    )

    panel = await StorageMountService().get_panel(None, 1, "mediary-scout")

    source = panel["sources"][0]
    assert source["mounted"] is True
    assert source["mount_path"] == "/夸克"
    assert source["webdav_url"] == "http://openlist:5244/dav/夸克"
    assert source["contents"] == ["Movies", "Anime", "TV"]


@pytest.mark.asyncio
async def test_get_panel_reports_all_supported_providers(monkeypatch):
    group = _group()
    drives = [
        _drive(id="d1", provider="pan115", payload={"cookie": "c"}),
        _drive(id="d2", provider="quark", payload={"cookie": "c"}),
        _drive(id="d3", provider="uc", payload={"cookie": "c"}),
        _drive(
            id="d4",
            provider="guangya",
            payload={"accessToken": "a", "refreshToken": "r"},
        ),
        _drive(
            id="d5",
            provider="tianyi",
            payload={"accessToken": "a", "refreshToken": "r"},
        ),
        _drive(id="d6", provider="pan123", payload={"token": "t"}),
    ]

    async def fake_load_group(db, group_id):
        return group

    async def fake_list(self, url, username, password):
        return []

    monkeypatch.setattr(storage_mounts, "_load_group", fake_load_group)
    monkeypatch.setattr(
        storage_mounts, "_read_mediary_drives", lambda project: drives
    )
    monkeypatch.setattr(
        storage_mounts.OpenListClient, "list_storages", fake_list
    )

    panel = await StorageMountService().get_panel(None, 1, "mediary-scout")

    assert [item["supported"] for item in panel["sources"]] == [True] * 6
    assert [item["driver"] for item in panel["sources"]] == [
        "115 Cloud",
        "Quark",
        "UC",
        "GuangYaPan",
        "189CloudPC",
        "123 Open",
    ]


@pytest.mark.asyncio
async def test_mount_updates_existing_storage(monkeypatch):
    group = _group()
    saved = {}

    async def fake_load_group(db, group_id):
        return group

    monkeypatch.setattr(storage_mounts, "_load_group", fake_load_group)
    monkeypatch.setattr(
        storage_mounts,
        "_read_mediary_drives",
        lambda project: [_drive()],
    )

    async def fake_list(self, url, username, password):
        return [
            {
                "id": 7,
                "driver": "Quark",
                "mount_path": "/夸克",
                "remark": "巡影同步 · 夸克网盘 · AARq…NE7U",
            }
        ]

    async def fake_save(self, url, username, password, **kwargs):
        saved.update(kwargs)
        return 7

    async def fake_files(self, url, username, password, path, per_page=10):
        return [{"name": "Movies"}, {"name": "Anime"}]

    monkeypatch.setattr(
        storage_mounts.OpenListClient, "list_storages", fake_list
    )
    monkeypatch.setattr(
        storage_mounts.OpenListClient, "save_storage", fake_save
    )
    monkeypatch.setattr(
        storage_mounts.OpenListClient, "list_files", fake_files
    )

    result = await StorageMountService().mount(
        None, 1, "mediary-scout", "cs_quark_AARqyWwHjBfIR0NACb5LNE7U"
    )

    assert result["ok"] is True
    assert result["mount_path"] == "/夸克"
    assert result["contents"] == ["Movies", "Anime"]
    assert saved["storage_id"] == 7
    assert saved["driver"] == "Quark"
    assert saved["addition"]["root_folder_id"] == "9e0a3274883e4fc4b1cc61b71c334aab"


@pytest.mark.asyncio
async def test_mount_rolls_back_failed_new_storage(monkeypatch):
    group = _group()
    deleted = {}

    async def fake_load_group(db, group_id):
        return group

    monkeypatch.setattr(storage_mounts, "_load_group", fake_load_group)
    monkeypatch.setattr(
        storage_mounts,
        "_read_mediary_drives",
        lambda project: [_drive(provider="guangya", payload={"accessToken": "a", "refreshToken": "r"})],
    )

    async def fake_list(self, url, username, password):
        return []

    async def fake_save(self, url, username, password, **kwargs):
        return 11

    async def fake_files(self, url, username, password, path, per_page=10):
        raise storage_mounts.AppClientError("token 已失效")

    async def fake_delete(self, url, username, password, storage_id):
        deleted["id"] = storage_id

    monkeypatch.setattr(
        storage_mounts.OpenListClient, "list_storages", fake_list
    )
    monkeypatch.setattr(
        storage_mounts.OpenListClient, "save_storage", fake_save
    )
    monkeypatch.setattr(
        storage_mounts.OpenListClient, "list_files", fake_files
    )
    monkeypatch.setattr(
        storage_mounts.OpenListClient, "delete_storage", fake_delete
    )

    with pytest.raises(APIException):
        await StorageMountService().mount(
            None, 1, "mediary-scout", "cs_quark_AARqyWwHjBfIR0NACb5LNE7U"
        )

    assert deleted["id"] == 11
