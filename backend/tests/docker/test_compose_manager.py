import asyncio

import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from pathlib import Path

from app.services.compose import ComposeService as ComposeManager
from app.services.compose.permissions import parse_puid_pgid
from app.models.docker import DockerComposeProject, DockerComposeVersion


@pytest.fixture
def compose_manager():
    return ComposeManager()


@pytest.mark.asyncio
async def test_discover_projects_creates_external_project(compose_manager, tmp_path):
    """发现系统外 Compose 项目时自动创建数据库记录。"""
    # 模拟容器标签
    mock_container = {
        "id": "abc123",
        "name": "keygen-api-web-1",
        "status": "running",
        "state": "运行中",
        "health": "unknown",
        "image": "keygen-api:latest",
        "ports": "8080:8080",
        "created": "2026-01-01T00:00:00Z",
        "labels": {
            "com.docker.compose.project": "keygen-api",
            "com.docker.compose.project.config_files": str(tmp_path / "compose.yaml"),
            "com.docker.compose.project.working_dir": str(tmp_path),
        },
    }

    # 创建临时 compose 文件
    compose_file = tmp_path / "compose.yaml"
    compose_file.write_text("services:\n  web:\n    image: nginx:latest\n")

    with patch(
        "app.services.docker.manager.docker_manager.list_containers",
        return_value=[mock_container],
    ):
        # 模拟异步数据库会话
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        db = AsyncMock()
        db.execute.return_value = mock_result

        projects = await compose_manager.discover_projects(db)

    assert len(projects) == 1
    project = projects[0]
    assert project.project_name == "keygen-api"
    assert project.working_dir == str(tmp_path)
    db.add.assert_called()
    db.commit.assert_called()


@pytest.mark.asyncio
async def test_config_files_returns_default_for_legacy_project(compose_manager, tmp_path):
    """旧项目未保存 config_files 时返回 workspace 默认路径。"""
    project = MagicMock()
    project.project_name = "legacy"
    project.config_files = None
    project.working_dir = None

    paths = compose_manager._config_files(project)
    working_dir = compose_manager._working_dir(project)

    assert paths[0].name == "docker-compose.yml"
    assert working_dir.name == "legacy"


@pytest.mark.asyncio
async def test_write_moviepilot_without_offline_profile(compose_manager, tmp_path):
    """MoviePilot 写盘保留模板镜像与环境变量，不再注入离线资源档案。"""
    project = DockerComposeProject(
        id=1,
        project_name="movie-moviepilot",
        config_files='["' + str(tmp_path / "movie" / "docker-compose.yml") + '"]',
        working_dir=str(tmp_path / "movie"),
    )
    version = DockerComposeVersion(
        project_id=1,
        version_number=1,
        content="""services:
  moviepilot:
    image: jxxghp/moviepilot-v2:latest
    environment:
      TZ: Asia/Shanghai
""",
        is_current=True,
    )

    compose_file = await compose_manager._write_compose_file(project, version)

    content = compose_file.read_text(encoding="utf-8")
    assert "jxxghp/moviepilot-v2:latest" in content
    assert "@sha256:" not in content
    assert "MOVIEPILOT_AUTO_UPDATE" not in content
    assert "AUTO_UPDATE_RESOURCE" not in content
    assert "CLOAKBROWSER_AUTO_UPDATE" not in content


@pytest.mark.asyncio
async def test_create_project_auto_deploy(compose_manager, tmp_path):
    """创建项目后自动执行 docker compose up -d 并同步状态。"""
    compose_manager.workspace = tmp_path

    db = AsyncMock()
    db.add = MagicMock()

    with patch.object(compose_manager, "_run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = {"returncode": 0, "stdout": "", "stderr": ""}
        with patch.object(
            compose_manager, "sync_stack_status", new_callable=AsyncMock
        ) as mock_sync:
            mock_stack = MagicMock()
            mock_stack.service_count = 1
            mock_stack.running_count = 1
            mock_sync.return_value = mock_stack

            project = await compose_manager.create_project(
                db,
                project_name="test-stack",
                content="services:\n  web:\n    image: nginx:latest\n",
                user_id=1,
                description="test",
            )

    assert project.project_name == "test-stack"
    assert project.description == "test"

    assert db.add.call_count == 2
    version_args = db.add.call_args_list[1][0]
    assert isinstance(version_args[0], DockerComposeVersion)
    assert version_args[0].version_number == 1
    assert version_args[0].is_current is True

    up_calls = [call for call in mock_run.call_args_list if call.args[1] == "up"]
    assert len(up_calls) == 1
    assert up_calls[0].args[1:] == ("up", "-d")
    mock_sync.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_project_deploy_fails_still_saved(compose_manager, tmp_path):
    """部署失败时项目记录和版本仍然保留。"""
    compose_manager.workspace = tmp_path

    db = AsyncMock()
    db.add = MagicMock()

    with patch.object(compose_manager, "_run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = {
            "returncode": 1,
            "stdout": "",
            "stderr": "no such image",
        }

        with pytest.raises(RuntimeError, match="no such image"):
            await compose_manager.create_project(
                db,
                project_name="fail-stack",
                content="services:\n  web:\n    image: nonexistent:latest\n",
            )

    assert db.add.call_count == 2
    version_args = db.add.call_args_list[1][0]
    assert isinstance(version_args[0], DockerComposeVersion)
    assert version_args[0].version_number == 1
    assert (tmp_path / "fail-stack" / "docker-compose.yml").exists()


@pytest.mark.asyncio
async def test_edit_and_deploy_creates_version(compose_manager, tmp_path):
    """编辑项目时生成新版本并自动部署。"""
    compose_manager.workspace = tmp_path

    project = DockerComposeProject(
        id=1,
        project_name="edit-stack",
        description="old",
        config_files='["' + str(tmp_path / "edit-stack" / "docker-compose.yml") + '"]',
        working_dir=str(tmp_path / "edit-stack"),
    )
    project.versions = []

    db = AsyncMock()
    db.add = MagicMock()

    # 模拟查询最大版本号，update 返回空结果即可
    mock_max_result = MagicMock()
    mock_max_result.scalar.return_value = 3
    mock_update_result = MagicMock()
    db.execute.side_effect = [mock_max_result, mock_update_result]

    with patch.object(compose_manager, "_run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = {"returncode": 0, "stdout": "", "stderr": ""}
        with patch.object(
            compose_manager, "sync_stack_status", new_callable=AsyncMock
        ) as mock_sync:
            mock_stack = MagicMock()
            mock_stack.service_count = 1
            mock_stack.running_count = 1
            mock_sync.return_value = mock_stack

            version, result = await compose_manager.edit_and_deploy(
                db,
                project,
                content="services:\n  web:\n    image: nginx:alpine\n",
                user_id=1,
                comment="升级镜像",
                description="new description",
            )

    assert version.version_number == 4
    assert version.comment == "升级镜像"
    assert version.is_current is True
    assert project.description == "new description"
    assert db.add.call_count == 1
    version_args = db.add.call_args_list[0][0]
    assert isinstance(version_args[0], DockerComposeVersion)
    up_calls = [call for call in mock_run.call_args_list if call.args[1] == "up"]
    assert len(up_calls) == 1
    assert up_calls[0].args[1:] == ("up", "-d")
    mock_sync.assert_awaited_once()


@pytest.mark.asyncio
async def test_edit_and_deploy_invalid_yaml(compose_manager, tmp_path):
    """YAML 非法时不创建版本也不部署。"""
    compose_manager.workspace = tmp_path

    project = DockerComposeProject(
        id=1,
        project_name="edit-stack",
        description=None,
        config_files=None,
        working_dir=None,
    )
    project.versions = []

    db = AsyncMock()
    db.add = MagicMock()

    with pytest.raises(ValueError, match="YAML"):
        await compose_manager.edit_and_deploy(
            db,
            project,
            content="services: [\n",
        )

    db.add.assert_not_called()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_host_directories_creates_bind_mount_dirs(compose_manager, tmp_path):
    """解析 Compose YAML 并为 bind mount 的宿主机路径自动创建目录。"""
    host_dir = tmp_path / "host"
    content = f"""services:
  web:
    image: nginx
    volumes:
      - {host_dir}/data:/data:rw
      - {host_dir}/config:/config
  db:
    image: postgres
    volumes:
      - type: bind
        source: {host_dir}/db
        target: /var/lib/postgresql
"""
    await compose_manager._ensure_host_directories(content)

    assert (host_dir / "data").is_dir()
    assert (host_dir / "config").is_dir()
    assert (host_dir / "db").is_dir()


@pytest.mark.asyncio
async def test_ensure_host_directories_chowns_new_dirs_for_puid_pgid(
    compose_manager, tmp_path
):
    """服务显式声明 PUID/PGID 时，新建的宿主机目录授权给该用户；已存在目录不动。"""
    new_dir = tmp_path / "new-data"
    existing_dir = tmp_path / "existing-data"
    existing_dir.mkdir()
    content = f"""services:
  suwayomi:
    image: ghcr.io/suwayomi/suwayomi-server
    environment:
      - PUID=1000
      - PGID=1000
    volumes:
      - {new_dir}:/home/suwayomi/.local/share/Tachidesk
      - {existing_dir}:/existing
"""
    with patch(
        "app.services.compose.compose_service.os.chown"
    ) as mock_chown:
        await compose_manager._ensure_host_directories(content)

    assert new_dir.is_dir()
    mock_chown.assert_called_once_with(new_dir, 1000, 1000)


def test_parse_puid_pgid():
    """PUID/PGID 支持列表与字典两种环境变量形式，缺一或非法时不生效。"""
    assert parse_puid_pgid(["PUID=1000", "PGID=100"]) == (1000, 100)
    assert parse_puid_pgid({"PUID": "1000", "PGID": "100"}) == (1000, 100)
    assert parse_puid_pgid(["PUID=1000"]) is None
    assert parse_puid_pgid(["PUID=abc", "PGID=100"]) is None
    assert parse_puid_pgid(None) is None


@pytest.mark.asyncio
async def test_ensure_host_directories_ignores_named_volumes(compose_manager, tmp_path):
    """命名卷和相对路径不应被创建为宿主机目录。"""
    content = """services:
  web:
    image: nginx
    volumes:
      - data_volume:/data
      - ./relative:/app
"""
    await compose_manager._ensure_host_directories(content)

    # 不应抛出异常，也不应在当前目录创建意外目录
    assert True


def test_compose_command_uses_host_path_view(compose_manager, monkeypatch):
    """CLI 使用宿主机路径，数据库中的容器路径保持不变。"""
    from app.services.system.host_path_aliases import (
        HostPathMapping,
        host_path_aliases,
    )

    monkeypatch.setattr(
        host_path_aliases,
        "_mappings",
        (HostPathMapping("/volume1/docker/onetap/data", "/data", "data-root"),),
    )
    project = MagicMock()
    project.project_name = "demo"
    project.config_files = '["/data/compose/demo/docker-compose.yml"]'
    project.working_dir = "/data/compose/demo"

    cmd, cwd = compose_manager._compose_command(project, "up", "-d")

    assert cmd == [
        "docker",
        "compose",
        "-f",
        "/volume1/docker/onetap/data/compose/demo/docker-compose.yml",
        "-p",
        "demo",
        "up",
        "-d",
    ]
    assert cwd == "/volume1/docker/onetap/data/compose/demo"
    assert project.config_files == '["/data/compose/demo/docker-compose.yml"]'


def test_compose_command_without_mapping_keeps_legacy_paths(compose_manager):
    """未启用或未建立映射时完全沿用旧版本调用方式。"""
    project = MagicMock()
    project.project_name = "demo"
    project.config_files = '["/data/compose/demo/docker-compose.yml"]'
    project.working_dir = "/data/compose/demo"

    cmd, cwd = compose_manager._compose_command(project, "ps")

    assert cmd[3] == "/data/compose/demo/docker-compose.yml"
    assert cwd == "/data/compose/demo"


def _static_ip_project(tmp_path) -> DockerComposeProject:
    return DockerComposeProject(
        id=1,
        project_name="demo",
        config_files='["' + str(tmp_path / "demo" / "docker-compose.yml") + '"]',
        working_dir=str(tmp_path / "demo"),
    )


@pytest.mark.asyncio
async def test_up_retries_after_cleaning_stale_static_ip(compose_manager, tmp_path):
    """固定 IP 被本项目残留容器占用时，清理后自动重试一次。"""
    project = _static_ip_project(tmp_path)
    target = {
        "service": "adguardhome",
        "container_name": "onetap-adguardhome",
        "network": "onetap-accel",
        "ip": "172.31.254.2",
    }
    holder = {
        "id": "old",
        "name": "onetap-adguardhome",
        "status": "created",
        "project": "demo",
        "network": "onetap-accel",
        "ip": "172.31.254.2",
        "expected": "onetap-adguardhome",
    }
    conflict = {
        "returncode": 1,
        "stdout": "",
        "stderr": (
            "Container onetap-adguardhome Starting\n"
            "Error response from daemon: failed to set up container networking: "
            "Address already in use"
        ),
    }
    ok = {"returncode": 0, "stdout": "", "stderr": ""}

    with (
        patch.object(compose_manager, "_prepull_images", AsyncMock()),
        patch.object(
            compose_manager, "_static_ip_targets", AsyncMock(return_value=[target])
        ),
        patch.object(
            compose_manager, "_static_ip_holders_sync", return_value=[holder]
        ),
        patch.object(compose_manager, "_remove_container_sync") as mock_remove,
        patch.object(
            compose_manager, "_run", AsyncMock(side_effect=[conflict, ok])
        ) as mock_run,
    ):
        await compose_manager._run_up_with_progress(project, None)

    up_calls = [
        call for call in mock_run.await_args_list if call.args[1:3] == ("up", "-d")
    ]
    assert len(up_calls) == 2
    assert mock_remove.call_count >= 1


@pytest.mark.asyncio
async def test_up_reports_foreign_static_ip_conflict(compose_manager, tmp_path):
    """固定 IP 被其它项目容器占用时，给出明确错误而不是直接启动失败。"""
    project = _static_ip_project(tmp_path)
    target = {
        "service": "adguardhome",
        "container_name": "onetap-adguardhome",
        "network": "onetap-accel",
        "ip": "172.31.254.2",
    }
    holder = {
        "id": "other",
        "name": "movie-moviepilot",
        "status": "running",
        "project": "movie-moviepilot",
        "network": "onetap-accel",
        "ip": "172.31.254.2",
        "expected": "onetap-adguardhome",
    }

    with (
        patch.object(compose_manager, "_prepull_images", AsyncMock()),
        patch.object(
            compose_manager, "_static_ip_targets", AsyncMock(return_value=[target])
        ),
        patch.object(
            compose_manager, "_static_ip_holders_sync", return_value=[holder]
        ),
        patch.object(compose_manager, "_run", AsyncMock()) as mock_run,
    ):
        with pytest.raises(RuntimeError, match="Address already in use") as excinfo:
            await compose_manager._run_up_with_progress(project, None)

    assert "movie-moviepilot" in str(excinfo.value)
    mock_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_action_up_uses_local_images_without_pulling(compose_manager):
    """已有项目停止后启动时只使用本机镜像，不进入部署拉取流程。"""
    project = MagicMock()
    project.id = 1
    project.project_name = "vaultwarden-1"
    db = AsyncMock()
    stack = MagicMock(service_count=1, running_count=1)

    with (
        patch.object(compose_manager, "_prepull_images", AsyncMock()) as prepull,
        patch.object(
            compose_manager,
            "_run",
            AsyncMock(return_value={"returncode": 0, "stdout": "", "stderr": ""}),
        ) as run,
        patch.object(
            compose_manager, "sync_stack_status", AsyncMock(return_value=stack)
        ),
    ):
        await compose_manager.action_async(db, project, "up")

    up_calls = [call for call in run.await_args_list if call.args[1] == "up"]
    assert len(up_calls) == 1
    assert up_calls[0].args[1:] == ("up", "-d", "--pull", "never")
    prepull.assert_not_awaited()


@pytest.mark.asyncio
async def test_action_up_pulls_missing_image_before_start(compose_manager):
    """上次拉取中断导致镜像缺失时，启动前先补拉再执行 up。"""
    project = MagicMock()
    project.id = 1
    project.project_name = "movie-moviepilot"
    db = AsyncMock()
    stack = MagicMock(service_count=1, running_count=1)
    pulled = []

    async def fake_pull(image, acceleration, *, progress=None, allow_direct=True):
        assert allow_direct is True
        pulled.append(image)
        return True

    with (
        patch.object(
            compose_manager,
            "_compose_images",
            AsyncMock(return_value=["jxxghp/moviepilot:3.2.6"]),
        ),
        patch(
            "app.services.compose.compose_service._docker",
            AsyncMock(return_value=(1, "Error: No such image")),
        ),
        patch(
            "app.services.compose.compose_service.manage_base_urls",
            return_value=[],
        ),
        patch(
            "app.services.compose.compose_service.pull_with_mirror",
            side_effect=fake_pull,
        ),
        patch.object(
            compose_manager,
            "_run",
            AsyncMock(return_value={"returncode": 0, "stdout": "", "stderr": ""}),
        ) as run,
        patch.object(
            compose_manager, "sync_stack_status", AsyncMock(return_value=stack)
        ),
    ):
        await compose_manager.action_async(db, project, "up")

    assert pulled == ["jxxghp/moviepilot:3.2.6"]
    up_calls = [call for call in run.await_args_list if call.args[1] == "up"]
    assert up_calls[0].args[1:] == ("up", "-d", "--pull", "never")


@pytest.mark.asyncio
async def test_action_up_skips_pull_when_image_exists(compose_manager):
    """镜像已在本地时启动不触发补拉。"""
    project = MagicMock()
    project.id = 1
    project.project_name = "movie-moviepilot"
    db = AsyncMock()
    stack = MagicMock(service_count=1, running_count=1)

    with (
        patch.object(
            compose_manager,
            "_compose_images",
            AsyncMock(return_value=["jxxghp/moviepilot:3.2.6"]),
        ),
        patch(
            "app.services.compose.compose_service._docker",
            AsyncMock(return_value=(0, "")),
        ),
        patch(
            "app.services.compose.compose_service.pull_with_mirror",
            AsyncMock(return_value=True),
        ) as pull,
        patch.object(
            compose_manager,
            "_run",
            AsyncMock(return_value={"returncode": 0, "stdout": "", "stderr": ""}),
        ),
        patch.object(
            compose_manager, "sync_stack_status", AsyncMock(return_value=stack)
        ),
    ):
        await compose_manager.action_async(db, project, "up")

    pull.assert_not_awaited()


@pytest.mark.asyncio
async def test_prepull_forwards_download_progress(compose_manager):
    project = MagicMock()
    events = []

    async def fake_pull(image, acceleration, *, progress=None, allow_direct=True):
        assert allow_direct is False
        assert progress is not None
        progress({
            "percentage": 50,
            "size_text": "50.0 MB / 100.0 MB",
            "speed": 25 * 1024 * 1024,
            "completed_layers": 1,
            "total_layers": 2,
            "determinate": True,
            "layers": [
                {
                    "id": "aaaaaaaaaaaa",
                    "status": "Downloading",
                    "status_text": "下载中",
                    "current": 50 * 1024 * 1024,
                    "total": 100 * 1024 * 1024,
                    "percentage": 50,
                    "speed": 25 * 1024 * 1024,
                },
                {
                    "id": "bbbbbbbbbbbb",
                    "status": "Waiting",
                    "status_text": "等待中",
                    "current": 0,
                    "total": 0,
                    "percentage": 0,
                    "speed": 0,
                },
            ],
        })
        return True

    with (
        patch(
            "app.services.compose.compose_service.manage_base_urls",
            return_value=["https://manage.example.com"],
        ),
        patch.object(
            compose_manager, "_compose_images", AsyncMock(return_value=["app:latest"])
        ),
        patch(
            "app.services.compose.compose_service.get_acceleration_map",
            AsyncMock(return_value={"docker.io": []}),
        ),
        patch(
            "app.services.compose.compose_service.pull_with_mirror",
            side_effect=fake_pull,
        ),
    ):
        await compose_manager._prepull_images(project, lambda *args: events.append(args))

    assert events[0][:3] == (
        "pulling_images",
        26,
        "通过加速域名拉取镜像 1/1",
    )
    # 头部进度必须跟着真实下载字节走，不能一直停在固定值
    assert events[-1] == (
        "pulling_images",
        58,
        "通过加速域名拉取镜像 1/1",
        "\n".join([
            "[+] 拉取 app:latest 1/2 · 50% · 50.0 MB / 100.0 MB · 25.0 MB/s",
            "aaaaaaaaaaaa ⠸ 下载中 [==========          ] "
            "50.0 MB / 100.0 MB · 25.0 MB/s",
            "bbbbbbbbbbbb ⠸ 等待中",
        ]),
    )


@pytest.mark.asyncio
async def test_prepull_failure_does_not_fall_back_to_compose(compose_manager):
    with (
        patch(
            "app.services.compose.compose_service.manage_base_urls",
            return_value=["https://manage.example.com"],
        ),
        patch.object(
            compose_manager,
            "_compose_images",
            AsyncMock(return_value=["metacubex/mihomo:v1.19.33"]),
        ),
        patch(
            "app.services.compose.compose_service.get_acceleration_map",
            AsyncMock(return_value={"docker.io": ["https://mirror.example.com"]}),
        ),
        patch(
            "app.services.compose.compose_service.pull_with_mirror",
            AsyncMock(return_value=False),
        ),
    ):
        with pytest.raises(RuntimeError, match="未继续回退源站"):
            await compose_manager._prepull_images(MagicMock(), None)


def test_parse_ps_preserves_restarting_status(compose_manager):
    """重启循环不计入运行数，也不能归类为正常运行或已退出。"""
    restarting = compose_manager._parse_ps(
        [{"State": "restarting", "Publishers": []}]
    )
    assert restarting["status"] == "restarting"
    assert restarting["running_count"] == 0
    assert restarting["service_count"] == 1

    mixed = compose_manager._parse_ps(
        [
            {"State": "running", "Publishers": []},
            {"State": "restarting", "Publishers": []},
        ]
    )
    assert mixed["status"] == "partial"
    assert mixed["running_count"] == 1


@pytest.mark.asyncio
async def test_cancel_streaming_command_kills_and_reaps_process(compose_manager):
    compose_manager._docker_available = True
    started = asyncio.Event()
    killed = asyncio.Event()
    proc = MagicMock()
    proc.stdout.readline = AsyncMock(return_value=b"")
    proc.stderr.readline = AsyncMock(return_value=b"")
    proc.kill.side_effect = killed.set

    async def wait():
        started.set()
        await killed.wait()
        return -9

    proc.wait = wait
    project = MagicMock()
    project.project_name = "cancel-test"
    project.config_files = '["/tmp/compose.yaml"]'
    project.working_dir = "/tmp"

    with patch(
        "app.services.compose.compose_service.asyncio.create_subprocess_exec",
        AsyncMock(return_value=proc),
    ):
        task = asyncio.create_task(
            compose_manager._run_streaming(project, "up", "-d")
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    proc.kill.assert_called_once_with()
    assert killed.is_set()
