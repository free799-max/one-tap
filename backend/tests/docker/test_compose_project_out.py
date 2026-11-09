"""Compose 项目响应中的宿主机路径视图测试。"""

from unittest.mock import MagicMock

from app.api.docker.compose import _build_project_out
from app.services.system.host_path_aliases import (
    HostPathMapping,
    host_path_aliases,
)


def _project(config_files=None, working_dir=None):
    project = MagicMock()
    project.id = 1
    project.project_name = "demo"
    project.description = None
    project.is_active = True
    project.versions = []
    project.stack = None
    project.config_files = config_files or '["/data/compose/demo/docker-compose.yml"]'
    project.working_dir = working_dir or "/data/compose/demo"
    project.created_at = None
    project.updated_at = None
    return project


def test_project_out_includes_host_paths_when_alias_active(monkeypatch):
    monkeypatch.setattr(
        host_path_aliases,
        "_mappings",
        (HostPathMapping("/volume1/docker/onetap/data", "/data", "data-root"),),
    )

    result = _build_project_out(_project())

    assert result.config_files == ["/data/compose/demo/docker-compose.yml"]
    assert result.host_config_files == [
        "/volume1/docker/onetap/data/compose/demo/docker-compose.yml"
    ]
    assert result.host_working_dir == "/volume1/docker/onetap/data/compose/demo"


def test_project_out_omits_host_paths_without_alias():
    result = _build_project_out(_project())

    assert result.host_config_files is None
    assert result.host_working_dir is None


def test_project_out_marks_missing_compose_file_unavailable():
    result = _build_project_out(_project())

    assert result.compose_available is False
    assert "Compose 文件在容器内不可见" in result.compose_unavailable_reason
    assert "/data/compose/demo/docker-compose.yml" in result.compose_unavailable_reason


def test_project_out_marks_existing_project_available(tmp_path):
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    project = _project(
        config_files=f'["{compose_file}"]',
        working_dir=str(tmp_path),
    )

    result = _build_project_out(project)

    assert result.compose_available is True
    assert result.compose_unavailable_reason is None


def test_project_out_marks_missing_working_dir_unavailable(tmp_path):
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    project = _project(
        config_files=f'["{compose_file}"]',
        working_dir=str(tmp_path / "missing"),
    )

    result = _build_project_out(project)

    assert result.compose_available is False
    assert "工作目录在容器内不可见" in result.compose_unavailable_reason
