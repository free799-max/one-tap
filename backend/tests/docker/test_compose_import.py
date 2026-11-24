"""外部 Compose 项目 / 独立容器导入测试。"""

import json
from pathlib import Path

import pytest
import pytest_asyncio
import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.database import Base
from app.models.app_store import App
from app.models.docker import DockerComposeProject, DockerComposeVersion
from app.models.orchestration import AppInstance
from app.services.compose import compose_import, compose_manager


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


async def _make_project(
    db, *, working_dir: Path, compose_file: Path, source: str = "external"
):
    project = DockerComposeProject(
        project_name="demo",
        source=source,
        config_files=json.dumps([str(compose_file)], ensure_ascii=False),
        working_dir=str(working_dir),
    )
    db.add(project)
    await db.commit()
    return project


def _container(name: str, image: str, host_port: int = 9034):
    return {
        "id": "abc123456789",
        "name": name,
        "status": "running",
        "state": "运行中",
        "image": image,
        "ports": f"{host_port}:5244",
        "port_bindings": [
            {
                "container_port": "5244/tcp",
                "host_ip": "0.0.0.0",
                "host_port": str(host_port),
            }
        ],
        "labels": {"com.docker.compose.project": "demo"},
    }


def test_is_external_project_ignores_workspace_and_imported(
    tmp_path,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    managed_file = workspace / "managed" / "docker-compose.yml"
    managed = DockerComposeProject(
        project_name="managed",
        source="managed",
        config_files=json.dumps([str(managed_file)]),
        working_dir=str(managed_file.parent),
    )
    imported = DockerComposeProject(
        project_name="imported",
        source="imported",
        config_files=json.dumps([str(tmp_path / "x" / "docker-compose.yml")]),
        working_dir=str(tmp_path / "x"),
    )
    external = DockerComposeProject(
        project_name="external",
        source="external",
        config_files=json.dumps([str(tmp_path / "ext" / "docker-compose.yml")]),
        working_dir=str(tmp_path / "ext"),
    )

    original = compose_manager.workspace
    compose_manager.workspace = workspace
    try:
        assert compose_import.is_external_project(managed, False) is False
        assert compose_import.is_external_project(imported, False) is False
        assert compose_import.is_external_project(external, False) is True
        assert compose_import.is_external_project(external, True) is False
    finally:
        compose_manager.workspace = original


def test_inspect_project_reports_missing_directories(tmp_path):
    project = DockerComposeProject(
        project_name="demo",
        source="external",
        config_files=json.dumps([str(tmp_path / "missing" / "docker-compose.yml")]),
        working_dir=str(tmp_path / "missing"),
    )

    result = compose_import._inspect_project(project, [])

    assert result["ready"] is False
    assert result["reason"] == "项目目录未挂载到 OneTap 容器"
    assert str(tmp_path / "missing") in result["missing_dirs"]


@pytest.mark.asyncio
async def test_import_project_copies_canonical_config(db, tmp_path, monkeypatch):
    project_dir = tmp_path / "host" / "demo"
    project_dir.mkdir(parents=True)
    compose_file = project_dir / "docker-compose.yml"
    compose_file.write_text(
        "services:\n  web:\n    image: nginx:alpine\n    volumes:\n      - ./data:/data\n",
        encoding="utf-8",
    )
    data_dir = project_dir / "data"
    data_dir.mkdir()
    project = await _make_project(
        db, working_dir=project_dir, compose_file=compose_file
    )

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(compose_manager, "workspace", workspace)

    async def fake_run(_project, *args, **kwargs):
        assert args[0] == "config"
        return {
            "returncode": 0,
            "stdout": yaml.safe_dump(
                {
                    "name": "demo",
                    "services": {
                        "web": {
                            "image": "nginx:alpine",
                            "volumes": [
                                {
                                    "type": "bind",
                                    "source": str(data_dir),
                                    "target": "/data",
                                }
                            ],
                        }
                    },
                },
                sort_keys=False,
            ),
            "stderr": "",
        }

    async def fake_containers():
        return [_container("demo-web-1", "nginx:alpine")]

    monkeypatch.setattr(compose_manager, "_run", fake_run)
    monkeypatch.setattr(compose_import, "list_live_containers", fake_containers)

    app_name = await compose_import.import_project(db, project)

    assert app_name.startswith("imported-demo")
    assert project.source == "imported"
    copied = workspace / "demo" / "docker-compose.yml"
    assert copied.is_file()
    data = yaml.safe_load(copied.read_text(encoding="utf-8"))
    assert data["services"]["web"]["volumes"][0]["source"] == str(data_dir)
    assert "name" not in data

    version = await db.scalar(
        select(DockerComposeVersion).where(
            DockerComposeVersion.project_id == project.id
        )
    )
    assert version is not None
    assert version.is_current is True

    instance = await db.scalar(
        select(AppInstance).where(AppInstance.project_id == project.id)
    )
    assert instance is not None
    assert instance.config["imported"] is True
    assert instance.config["source"] == "external_compose"
    assert instance.config["host_port"] == 9034
    assert instance.config["container_port"] == 5244

    app = await db.scalar(select(App).where(App.name == app_name))
    assert app is not None
    assert app.is_listed is False


@pytest.mark.asyncio
async def test_import_project_requires_mounted_directory(db, tmp_path, monkeypatch):
    missing_dir = tmp_path / "missing"
    project = DockerComposeProject(
        project_name="demo",
        source="external",
        config_files=json.dumps([str(missing_dir / "docker-compose.yml")]),
        working_dir=str(missing_dir),
    )
    db.add(project)
    await db.commit()

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(compose_manager, "workspace", workspace)

    async def fake_containers():
        return []

    monkeypatch.setattr(compose_import, "list_live_containers", fake_containers)

    with pytest.raises(ValueError, match="未挂载"):
        await compose_import.import_project(db, project)

    assert project.source == "external"


@pytest.mark.asyncio
async def test_scan_candidates_skips_self_project(db, tmp_path, monkeypatch):
    import socket

    for name in ("selfproj", "otherproj"):
        db.add(
            DockerComposeProject(
                project_name=name,
                source="external",
                config_files=json.dumps(
                    [str(tmp_path / name / "docker-compose.yml")]
                ),
                working_dir=str(tmp_path / name),
            )
        )
    await db.commit()

    async def fake_discover(_db):
        return []

    async def fake_containers():
        hostname = socket.gethostname()
        return [
            {
                "id": "self00000000",
                "name": hostname,
                "status": "running",
                "state": "运行中",
                "image": "onetap:latest",
                "ports": "",
                "labels": {"com.docker.compose.project": "selfproj"},
            },
            {
                "id": "other0000000",
                "name": "other-one",
                "status": "running",
                "state": "运行中",
                "image": "nginx:alpine",
                "ports": "",
                "labels": {},
            },
        ]

    monkeypatch.setattr(compose_manager, "discover_projects", fake_discover)
    monkeypatch.setattr(compose_import, "list_live_containers", fake_containers)

    result = await compose_import.scan_candidates(db)

    names = [item["project_name"] for item in result["projects"]]
    assert names == ["otherproj"]
    assert [item["container_name"] for item in result["containers"]] == [
        "other-one"
    ]


async def _register_admin(client) -> str:
    await client.post(
        "/api/auth/register",
        json={"username": "importadmin", "password": "pass123456"},
    )
    resp = await client.post(
        "/api/auth/login",
        json={"username": "importadmin", "password": "pass123456"},
    )
    return resp.json()["data"]["access_token"]


@pytest.mark.asyncio
async def test_import_candidates_route_not_shadowed_by_project_id(client):
    resp = await client.get("/api/docker/compose/import/candidates")
    # 未登录时由鉴权拦截；关键是不能被 /compose/{project_id} 抢先匹配成 422
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_import_candidates_route_returns_payload(client, monkeypatch):
    async def fake_scan(_db):
        return {
            "projects": [
                {
                    "project_id": 1,
                    "project_name": "demo",
                    "config_files": ["/opt/demo/docker-compose.yml"],
                    "host_config_files": ["/opt/demo/docker-compose.yml"],
                    "working_dir": "/opt/demo",
                    "host_working_dir": "/opt/demo",
                    "required_dirs": ["/opt/demo"],
                    "missing_dirs": ["/opt/demo"],
                    "ready": False,
                    "reason": "项目目录未挂载到 OneTap 容器",
                    "container_count": 1,
                    "running_count": 1,
                    "images": ["nginx:alpine"],
                    "ports": ["8080:80"],
                }
            ],
            "containers": [],
        }

    monkeypatch.setattr(compose_import, "scan_candidates", fake_scan)
    token = await _register_admin(client)

    resp = await client.get(
        "/api/docker/compose/import/candidates",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    payload = resp.json()["data"]
    assert payload["projects"][0]["project_name"] == "demo"
    assert payload["projects"][0]["missing_dirs"] == ["/opt/demo"]


@pytest.mark.asyncio
async def test_import_container_creates_instance(db, tmp_path):
    container = {
        "container_id": "deadbeef1234",
        "container_name": "my-nginx",
        "image": "nginx:alpine",
        "status": "running",
        "state": "运行中",
        "ports": "8081:80",
        "port_bindings": [],
    }
    # port_bindings 为空时退回 ports 字符串解析外的默认行为
    container["port_bindings"] = [
        {
            "container_port": "80/tcp",
            "host_ip": "0.0.0.0",
            "host_port": "8081",
        }
    ]

    app_name = await compose_import.import_container(db, container)

    instance = await db.scalar(
        select(AppInstance).where(
            AppInstance.instance_name == "my-nginx"
        )
    )
    assert instance is not None
    assert instance.project_id is None
    assert instance.config["container_id"] == "deadbeef1234"
    assert instance.config["host_port"] == 8081
    assert instance.config["container_port"] == 80
    assert instance.status == "running"
    assert app_name.startswith("imported-my-nginx")


@pytest.mark.asyncio
async def test_import_container_accepts_live_docker_shape(db, tmp_path):
    """接口层传入的是 Docker 原始格式（id/name），应与候选格式等价。"""
    container = {
        "id": "cafebabe5678",
        "name": "my-redis",
        "image": "redis:7-alpine",
        "status": "running",
        "port_bindings": [
            {
                "container_port": "6379/tcp",
                "host_ip": "0.0.0.0",
                "host_port": "6379",
            }
        ],
    }

    app_name = await compose_import.import_container(db, container)

    instance = await db.scalar(
        select(AppInstance).where(AppInstance.instance_name == "my-redis")
    )
    assert instance is not None
    assert instance.config["container_id"] == "cafebabe5678"
    assert instance.config["host_port"] == 6379
    assert instance.config["container_port"] == 6379
    assert app_name.startswith("imported-my-redis")
