"""宿主机路径别名与 Compose 路径视图转换测试。"""

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import docker
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — 注册所有模型到 Base.metadata
from app.database import Base
from app.models.storage_directory import StorageDirectory
from app.services.docker import docker_manager
from app.services.system.host_path_aliases import (
    HostPathAliasService,
    HostPathMapping,
)


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with SessionLocal() as session:
        yield session
    await engine.dispose()


def test_to_host_path_without_mapping_keeps_path():
    service = HostPathAliasService()
    assert service.to_host_path("/data/compose/demo/docker-compose.yml") == (
        "/data/compose/demo/docker-compose.yml"
    )


def test_to_host_path_uses_longest_container_prefix():
    service = HostPathAliasService()
    service._mappings = (
        HostPathMapping("/host/data", "/data", "data-root"),
        HostPathMapping("/host/data/special", "/data/compose", "storage"),
    )
    assert service.to_host_path("/data/app-icons/a.svg") == "/host/data/app-icons/a.svg"
    assert service.to_host_path("/data/compose/demo") == "/host/data/special/demo"
    assert service.to_host_path("/opt/other/file") == "/opt/other/file"


def test_ensure_alias_creates_symlink(tmp_path):
    container = tmp_path / "container"
    container.mkdir()
    host = tmp_path / "host" / "data"
    mapping = HostPathMapping(str(host), str(container), "storage")

    service = HostPathAliasService()
    active = service._ensure_aliases_sync([mapping])

    assert active == [mapping]
    assert host.is_symlink()
    assert os.path.samefile(host, container)
    (host / "new-file").write_text("x", encoding="utf-8")
    assert (container / "new-file").read_text(encoding="utf-8") == "x"


def test_ensure_alias_accepts_existing_equivalent_symlink(tmp_path):
    container = tmp_path / "container"
    container.mkdir()
    host = tmp_path / "host"
    host.symlink_to(container, target_is_directory=True)
    mapping = HostPathMapping(str(host), str(container), "storage")

    assert HostPathAliasService()._ensure_aliases_sync([mapping]) == [mapping]


def test_ensure_alias_never_overwrites_existing_directory(tmp_path):
    container = tmp_path / "container"
    container.mkdir()
    host = tmp_path / "host"
    host.mkdir()
    mapping = HostPathMapping(str(host), str(container), "storage")

    service = HostPathAliasService()
    assert service._ensure_aliases_sync([mapping]) == []
    assert not host.is_symlink()


def test_ensure_alias_rejects_nested_and_system_paths(tmp_path):
    container = tmp_path / "container"
    container.mkdir()
    nested = HostPathMapping(str(container / "alias"), str(container), "storage")
    system = HostPathMapping("/etc/onetap-alias", str(container), "storage")

    assert HostPathAliasService()._ensure_aliases_sync([nested, system]) == []


async def test_refresh_reads_storage_mappings_and_creates_alias(
    db, tmp_path, monkeypatch
):
    mount = tmp_path / "mount"
    mount.mkdir()
    host = tmp_path / "host" / "data1"
    db.add(
        StorageDirectory(
            name="data1",
            host_path=str(host),
            mount_path=str(mount),
            enabled=True,
            is_app_data=False,
            sort_order=0,
        )
    )
    await db.flush()

    service = HostPathAliasService()
    monkeypatch.setattr(
        service, "_get_data_root_host", AsyncMock(return_value=None)
    )
    await service.refresh(db)

    assert host.is_symlink()
    assert service.to_host_path(str(mount / "app")) == str(host / "app")


async def test_refresh_can_be_disabled_for_legacy_behavior(db, monkeypatch):
    service = HostPathAliasService()
    monkeypatch.setattr(
        "app.services.system.host_path_aliases.settings.HOST_PATH_ALIAS_ENABLED",
        False,
    )
    await service.refresh(db)
    assert service.mappings == ()


def test_detect_data_root_host_from_own_container(monkeypatch):
    container = SimpleNamespace(
        attrs={
            "Mounts": [
                {
                    "Type": "bind",
                    "Source": "/volume1/docker/onetap/data",
                    "Destination": "/data",
                }
            ]
        }
    )
    client = SimpleNamespace(
        containers=SimpleNamespace(get=lambda _name: container)
    )
    monkeypatch.setattr(docker_manager, "_client_obj", client)

    service = HostPathAliasService()
    assert service._detect_data_root_host_sync("/data") == (
        "/volume1/docker/onetap/data",
        True,
    )


def test_detect_data_root_host_outside_container(monkeypatch):
    def _not_found(_name):
        raise docker.errors.NotFound("not a container")

    client = SimpleNamespace(containers=SimpleNamespace(get=_not_found))
    monkeypatch.setattr(docker_manager, "_client_obj", client)

    assert HostPathAliasService()._detect_data_root_host_sync("/data") == (None, True)


def test_find_legacy_bind_mounts(monkeypatch):
    def _container(name, project, source):
        return SimpleNamespace(
            name=name,
            attrs={
                "Config": {"Labels": {"com.docker.compose.project": project}},
                "Mounts": [
                    {"Type": "bind", "Source": source, "Destination": "/config"}
                ],
            },
        )

    containers = [
        _container("old-web-1", "old", "/data/compose/old/config"),
        _container("new-web-1", "new", "/host/data/compose/new/config"),
    ]
    client = SimpleNamespace(
        containers=SimpleNamespace(list=lambda all: containers)
    )
    monkeypatch.setattr(docker_manager, "_client_obj", client)

    legacy = HostPathAliasService()._find_legacy_bind_mounts_sync("/data")
    assert legacy == [
        {
            "project": "old",
            "container": "old-web-1",
            "source": "/data/compose/old/config",
            "destination": "/config",
        }
    ]
