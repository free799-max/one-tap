"""从删除备份恢复编排实例组的测试。"""

import json
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.core.exceptions import APIException
from app.database import Base
from app.models.app_store import App
from app.models.orchestration import AppOrchestration
from app.services.orchestration import orchestration_service
from app.services.orchestration.backup import (
    BACKUP_DIR_NAME,
    MANIFEST_NAME,
    restore_app_data_dirs,
)
from app.services.system.storage_directory_service import storage_directory_service


@pytest_asyncio.fixture(name="db")
async def _db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    SessionLocal = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with SessionLocal() as session:
        yield session

    await engine.dispose()


@pytest_asyncio.fixture
async def orchestration(db):
    item = AppOrchestration(
        name="media-stack",
        display_name="影视媒体栈",
        category="media",
        app_composition=[
            {"app_name": "moviepilot", "relation": "required"},
            {"app_name": "jellyfin", "relation": "optional"},
        ],
        shared_config_schema={},
    )
    db.add(item)
    for name in ("moviepilot", "jellyfin"):
        db.add(
            App(
                name=name,
                display_name=name.title(),
                yaml_template=f"services:\n  app:\n    image: {name}:latest",
            )
        )
    await db.commit()
    await db.refresh(item)
    return item


def _set_app_data_root(monkeypatch, host: str | None):
    async def fake_pair(db):
        if host is None:
            return None
        return (host, host)

    monkeypatch.setattr(storage_directory_service, "get_app_data_pair", fake_pair)


def _make_backup(root: Path, name: str = "20260101-120000-test-stack") -> Path:
    backup_dir = root / BACKUP_DIR_NAME / name
    moviepilot_dir = backup_dir / "test-stack-moviepilot"
    (moviepilot_dir / "postgresql").mkdir(parents=True)
    (moviepilot_dir / "postgresql" / "PG_VERSION").write_text("16")
    jellyfin_dir = backup_dir / "test-stack-jellyfin"
    (jellyfin_dir / "config").mkdir(parents=True)
    (jellyfin_dir / "config" / "system.xml").write_text("<x/>")

    manifest = {
        "instance_name": "test-stack",
        "orchestration": "media-stack",
        "deleted_at": "2026-01-01T12:00:00",
        "app_data_root": str(root),
        "shared_config": {
            "volumes": [
                {
                    "host_path": "/opt/data/media",
                    "container_path": "/media",
                    "mode": "rw",
                }
            ]
        },
        "app_configs": {
            "moviepilot": {
                "url": "http://old:9004",
                "auth_type": "basic",
                "username": "admin",
                "password": "OldPass123",
                "api_key": "old-token",
                "provisioned": True,
                "integrations": {"wired_at": "old"},
            },
            "jellyfin": {
                "url": "http://old:9009",
                "auth_type": "basic",
                "username": "admin",
                "password": "JellyPass456",
                "provisioned": True,
            },
        },
        "apps": [
            {
                "instance_name": "test-stack-moviepilot",
                "app_name": "moviepilot",
                "project_name": "test-stack-moviepilot",
                "path": "test-stack-moviepilot",
                "config": {
                    "moviepilot_env": [
                        {"key": "SUPERUSER", "value": "admin"},
                        {"key": "SUPERUSER_PASSWORD", "value": "OldPass123"},
                        {"key": "API_TOKEN", "value": "old-token"},
                    ],
                },
            },
            {
                "instance_name": "test-stack-jellyfin",
                "app_name": "jellyfin",
                "project_name": "test-stack-jellyfin",
                "path": "test-stack-jellyfin",
                "config": {},
            },
        ],
    }
    (backup_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    return backup_dir


@pytest.mark.asyncio
async def test_restore_backup_moves_data_and_redeploys(
    db, orchestration, monkeypatch, tmp_path
):
    """恢复应把数据目录移回新项目名，并按备份配置与凭据重新部署。"""
    _set_app_data_root(monkeypatch, str(tmp_path))
    _make_backup(tmp_path)

    captured: dict = {}

    async def fake_deploy(db, **kwargs):
        captured.update(kwargs)
        return type("G", (), {"id": 99, "instance_name": kwargs["instance_name"], "status": "deploying"})(), ["t1", "t2"]

    monkeypatch.setattr(orchestration_service, "deploy", fake_deploy)

    group, task_ids = await orchestration_service.restore_backup(
        db, "20260101-120000-test-stack", "restored-stack"
    )

    assert group.id == 99
    assert task_ids == ["t1", "t2"]
    assert captured["orchestration_name"] == "media-stack"
    assert captured["instance_name"] == "restored-stack"
    assert captured["selected_apps"] == ["moviepilot", "jellyfin"]
    assert captured["shared_config"]["volumes"][0]["host_path"] == "/opt/data/media"

    # 新项目名下的数据目录已就位，旧备份目录被清理
    assert (
        tmp_path / "restored-stack-moviepilot" / "postgresql" / "PG_VERSION"
    ).read_text() == "16"
    assert (
        tmp_path / "restored-stack-jellyfin" / "config" / "system.xml"
    ).read_text() == "<x/>"
    assert not (tmp_path / "test-stack-moviepilot").exists()
    assert not (tmp_path / BACKUP_DIR_NAME / "20260101-120000-test-stack").exists()

    # 凭据沿用备份；新实例组继承认证信息（url/装配结果不继承）
    overrides = captured["credential_overrides"]
    assert overrides["moviepilot"]["password"] == "OldPass123"
    assert overrides["moviepilot"]["api_key"] == "old-token"
    assert overrides["jellyfin"]["password"] == "JellyPass456"
    initial = captured["initial_app_configs"]
    assert initial["moviepilot"]["username"] == "admin"
    assert initial["moviepilot"]["password"] == "OldPass123"
    assert "url" not in initial["moviepilot"]
    assert "provisioned" not in initial["moviepilot"]
    assert "integrations" not in initial["moviepilot"]


@pytest.mark.asyncio
async def test_restore_backup_uses_unified_password_with_previous(
    db, orchestration, monkeypatch, tmp_path
):
    """系统统一密码与备份不同时，应登录旧密码后改成统一密码。"""
    _set_app_data_root(monkeypatch, str(tmp_path))
    _make_backup(tmp_path)

    from app.services.system import system_config_service

    sys_config = await system_config_service.get_or_create(db)
    sys_config.default_admin_password = "UnifiedPass789"
    await db.commit()

    captured: dict = {}

    async def fake_deploy(db, **kwargs):
        captured.update(kwargs)
        return type("G", (), {"id": 1, "instance_name": "test-stack", "status": "deploying"})(), []

    monkeypatch.setattr(orchestration_service, "deploy", fake_deploy)

    await orchestration_service.restore_backup(
        db, "20260101-120000-test-stack", None
    )

    moviepilot = captured["credential_overrides"]["moviepilot"]
    assert moviepilot["password"] == "UnifiedPass789"
    assert moviepilot["previous_password"] == "OldPass123"
    assert moviepilot["api_key"] == "old-token"
    jellyfin = captured["credential_overrides"]["jellyfin"]
    assert jellyfin["password"] == "UnifiedPass789"
    assert jellyfin["previous_password"] == "JellyPass456"


@pytest.mark.asyncio
async def test_restore_backup_rejects_existing_target_dir(
    db, orchestration, monkeypatch, tmp_path
):
    """目标数据目录已存在时应拒绝，且不移动任何备份目录。"""
    _set_app_data_root(monkeypatch, str(tmp_path))
    _make_backup(tmp_path)
    (tmp_path / "test-stack-moviepilot").mkdir()

    with pytest.raises(APIException) as exc_info:
        await orchestration_service.restore_backup(db, "20260101-120000-test-stack")
    assert exc_info.value.status_code == 409

    assert (tmp_path / "test-stack-moviepilot").is_dir()
    assert (
        tmp_path / BACKUP_DIR_NAME / "20260101-120000-test-stack"
    ).is_dir()


@pytest.mark.asyncio
async def test_restore_backup_not_found(db, orchestration, monkeypatch, tmp_path):
    """不存在的备份返回 404。"""
    _set_app_data_root(monkeypatch, str(tmp_path))

    with pytest.raises(APIException) as exc_info:
        await orchestration_service.restore_backup(db, "missing")
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_restore_app_data_dirs_moves_instance_dir(
    db, monkeypatch, tmp_path
):
    """新布局按实例名目录移回，恢复时可按新实例名重命名。"""
    _set_app_data_root(monkeypatch, str(tmp_path))

    backup_dir = tmp_path / BACKUP_DIR_NAME / "20260101-120000-comic"
    data_dir = backup_dir / "comic" / "comic-auto" / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "PG_VERSION").write_text("16")
    manifest = {
        "instance_name": "comic",
        "orchestration": "comics-stack",
        "app_data_root": str(tmp_path),
        "instance_dir": "comic",
        "shared_config": {},
        "app_configs": {},
        "apps": [
            {
                "instance_name": "comic-comic-auto",
                "app_name": "comic-auto",
                "project_name": "comic-comic-auto",
                "path": "comic",
                "kind": "instance",
                "config": {},
            }
        ],
    }
    (backup_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )

    await restore_app_data_dirs(
        db,
        backup_dir.name,
        {"comic-auto": "comic2-comic-auto"},
        target_instance="comic2",
    )

    restored = tmp_path / "comic2" / "comic-auto" / "data" / "PG_VERSION"
    assert restored.read_text() == "16"
    assert not (tmp_path / "comic").exists()


@pytest.mark.asyncio
async def test_restore_app_data_dirs_rejects_traversal(
    db, monkeypatch, tmp_path
):
    """备份名包含路径分隔符时直接拒绝。"""
    _set_app_data_root(monkeypatch, str(tmp_path))

    with pytest.raises(APIException) as exc_info:
        await restore_app_data_dirs(db, "../evil", {})
    assert exc_info.value.status_code == 400
