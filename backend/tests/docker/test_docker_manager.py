import pytest
from unittest.mock import MagicMock, patch

from app.services.docker import DockerManager, ImagePullTaskManager


@pytest.fixture
def mock_docker_client():
    client = MagicMock()
    client.containers = MagicMock()
    client.ping = MagicMock(return_value=True)
    return client


def test_docker_manager_init():
    manager = DockerManager()
    assert manager is not None


@patch("app.services.docker.docker_common.docker")
def test_list_containers(mock_docker_module):
    mock_container = MagicMock()
    mock_container.id = "abc123def456"
    mock_container.name = "jellyfin"
    mock_container.status = "running"
    mock_container.attrs = {
        "State": {"Health": {"Status": "healthy"}},
        "NetworkSettings": {
            "Ports": {
                "8096/tcp": [{"HostIp": "0.0.0.0", "HostPort": "18096"}],
                "8920/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18920"}],
                "1900/udp": None,
            }
        },
    }
    mock_container.image.tags = ["jellyfin:latest"]

    mock_client = MagicMock()
    mock_client.containers.list.return_value = [mock_container]
    mock_docker_module.from_env.return_value = mock_client

    manager = DockerManager()
    containers = manager.list_containers()
    assert len(containers) == 1
    assert containers[0]["name"] == "jellyfin"
    assert containers[0]["status"] == "running"
    assert containers[0]["ports"] == (
        "18096:8096/tcp, 127.0.0.1:18920:8920/tcp"
    )
    assert containers[0]["port_bindings"] == [
        {
            "container_port": "8096/tcp",
            "host_ip": "0.0.0.0",
            "host_port": "18096",
        },
        {
            "container_port": "8920/tcp",
            "host_ip": "127.0.0.1",
            "host_port": "18920",
        },
    ]


@patch("app.services.docker.docker_common.docker")
def test_list_containers_without_port_bindings(mock_docker_module):
    mock_container = MagicMock()
    mock_container.id = "abc123def456"
    mock_container.name = "worker"
    mock_container.status = "exited"
    mock_container.attrs = {
        "State": {"Status": "exited"},
        "NetworkSettings": {"Ports": None},
    }
    mock_container.image.tags = []

    mock_client = MagicMock()
    mock_client.containers.list.return_value = [mock_container]
    mock_docker_module.from_env.return_value = mock_client

    containers = DockerManager().list_containers()

    assert containers[0]["ports"] == ""
    assert containers[0]["port_bindings"] == []


@patch("app.services.docker.docker_common.docker")
def test_container_action_stop(mock_docker_module):
    mock_container = MagicMock()
    mock_client = MagicMock()
    mock_client.containers.get.return_value = mock_container
    mock_docker_module.from_env.return_value = mock_client

    manager = DockerManager()
    manager.container_action("abc123", "stop")
    mock_container.stop.assert_called_once()


@patch("app.services.docker.docker_common.docker")
@patch("app.services.docker.docker_common.time")
def test_container_action_restart(mock_time, mock_docker_module):
    mock_container = MagicMock()
    mock_container.attrs = {"State": {"Status": "running", "Error": ""}}
    mock_container.status = "running"
    mock_client = MagicMock()
    mock_client.containers.get.return_value = mock_container
    mock_docker_module.from_env.return_value = mock_client

    # 避免真实等待
    mock_time.time.side_effect = [0, 1]
    mock_time.sleep = MagicMock()

    manager = DockerManager()
    result = manager.container_action("abc123", "restart")
    mock_container.restart.assert_called_once()
    assert result["status"] == "running"


@patch("app.services.docker.docker_common.docker")
@patch("app.services.docker.docker_common.time")
def test_pull_progress_recomputes_when_new_layer_discovered(mock_time, mock_docker_module):
    """新层被发现后按真实字节重算，不能虚高卡住。"""

    # 让每次事件间隔 0.5 秒，既触发节流又触发速度计算
    fake_now = [0.0]

    def _next_time():
        fake_now[0] += 0.5
        return fake_now[0]

    mock_time.time.side_effect = _next_time

    events = [
        # 8 层均下载到 80%
        *[
            {
                "id": f"layer{i}",
                "status": "Downloading",
                "progressDetail": {"current": 80, "total": 100},
            }
            for i in range(8)
        ],
        # 清单稳定后按真实字节算出 640/800 = 80%
        *[
            {
                "id": "layer0",
                "status": "Downloading",
                "progressDetail": {"current": 80, "total": 100},
            }
            for _ in range(3)
        ],
        # 第 9 层先被发现
        {"id": "layer8", "status": "Pulling fs layer", "progressDetail": {}},
        # 第 9 层开始下载，大小 100，当前 0
        {
            "id": "layer8",
            "status": "Downloading",
            "progressDetail": {"current": 0, "total": 100},
        },
    ]

    mock_client = MagicMock()
    mock_client.api.pull.return_value = iter(events)
    mock_docker_module.from_env.return_value = mock_client

    manager = DockerManager()
    task_manager = ImagePullTaskManager()
    task_id = task_manager.create_task("test:latest")

    recorded = []
    original_update = task_manager.update_progress

    def _update_progress(tid, progress):
        recorded.append(progress["percentage"])
        original_update(tid, progress)

    task_manager.update_progress = _update_progress

    manager.pull_image_async("test:latest", task_id, task_manager)

    # 9 层都已给出大小：已下载 8×80=640，总大小 900 → 71%
    assert recorded[-1] == 71
    assert max(recorded) == 80

    # 最终完成 100%
    task = task_manager.get_task(task_id)
    assert task["status"] == "completed"
    assert task["progress"]["percentage"] == 100


@patch("app.services.docker.docker_common.docker")
@patch("app.services.docker.docker_common.time")
def test_pull_progress_byte_weighted(mock_time, mock_docker_module):
    """总进度应按字节加权，大层主导进度而不是简单按层数平均。"""

    fake_now = [0.0]

    def _next_time():
        fake_now[0] += 0.5
        return fake_now[0]

    mock_time.time.side_effect = _next_time

    gb = 1024 * 1024 * 1024
    mb = 1024 * 1024

    events = [
        # 大层 1GB，已下载 800MB
        {
            "id": "big",
            "status": "Downloading",
            "progressDetail": {"current": 800 * mb, "total": 1 * gb},
        },
        # 小层 100MB，尚未开始
        {
            "id": "small",
            "status": "Downloading",
            "progressDetail": {"current": 0, "total": 100 * mb},
        },
        # 清单稳定后再推进一帧，触发按字节计算
        *[
            {
                "id": "big",
                "status": "Downloading",
                "progressDetail": {"current": 800 * mb, "total": 1 * gb},
            }
            for _ in range(3)
        ],
    ]

    mock_client = MagicMock()
    mock_client.api.pull.return_value = iter(events)
    mock_docker_module.from_env.return_value = mock_client

    manager = DockerManager()
    task_manager = ImagePullTaskManager()
    task_id = task_manager.create_task("test:latest")

    last_percentage = [0]

    def _update_progress(tid, progress):
        last_percentage[0] = progress["percentage"]

    task_manager.update_progress = _update_progress

    manager.pull_image_async("test:latest", task_id, task_manager)

    # 层平均 = (80 + 0) / 2 = 40%
    # 字节加权 = 800MB / 1100MB ≈ 72%
    # 取 max 后应接近字节加权
    assert last_percentage[0] >= 65, (
        f"字节加权进度应接近 72%，实际 {last_percentage[0]}%"
    )
    assert last_percentage[0] <= 80


@patch("app.services.docker.docker_common.docker")
@patch("app.services.docker.docker_common.time")
def test_pull_progress_completes_at_100(mock_time, mock_docker_module):
    """拉取流结束后，任务应标记为完成并置进度为 100%。"""

    fake_now = [0.0]

    def _next_time():
        fake_now[0] += 0.5
        return fake_now[0]

    mock_time.time.side_effect = _next_time

    events = [
        {
            "id": "layer1",
            "status": "Pull complete",
            "progressDetail": {"current": 100, "total": 100},
        },
    ]

    mock_client = MagicMock()
    mock_client.api.pull.return_value = iter(events)
    mock_docker_module.from_env.return_value = mock_client

    manager = DockerManager()
    task_manager = ImagePullTaskManager()
    task_id = task_manager.create_task("test:latest")

    manager.pull_image_async("test:latest", task_id, task_manager)

    task = task_manager.get_task(task_id)
    assert task["status"] == "completed"
    assert task["progress"]["percentage"] == 100
    assert task["progress"]["status"] == "拉取完成"


@patch("app.services.docker.docker_common.docker")
@patch("app.services.docker.docker_common.time")
def test_pull_progress_all_completed_with_zero_byte_data(mock_time, mock_docker_module):
    """所有层已完成但无字节信息时，流内不给百分比，任务完成再置 100%。"""

    fake_now = [0.0]

    def _next_time():
        fake_now[0] += 0.5
        return fake_now[0]

    mock_time.time.side_effect = _next_time

    events = [
        # 两层都已完成，但 Docker 没有给出 progressDetail
        {"id": "layer1", "status": "Already exists", "progressDetail": {}},
        {"id": "layer2", "status": "Download complete", "progressDetail": {}},
    ]

    mock_client = MagicMock()
    mock_client.api.pull.return_value = iter(events)
    mock_docker_module.from_env.return_value = mock_client

    manager = DockerManager()
    task_manager = ImagePullTaskManager()
    task_id = task_manager.create_task("test:latest")

    recorded = []

    def _update_progress(tid, progress):
        recorded.append(progress.copy())

    task_manager.update_progress = _update_progress

    manager.pull_image_async("test:latest", task_id, task_manager)

    # 没有字节信息就不猜百分比，完成态由任务管理器置 100%
    assert recorded[-1]["percentage"] == 0
    assert recorded[-1]["determinate"] is False
    assert recorded[-1]["completed_layers"] == 2
    assert recorded[-1]["total_layers"] == 2

    task = task_manager.get_task(task_id)
    assert task["status"] == "completed"
    assert task["progress"]["percentage"] == 100


@patch("app.services.docker.docker_common.docker")
def test_prune_unused_images_counts_tags_not_layers(mock_docker_module):
    """清理未使用镜像时，deleted 应只统计镜像标签，不把层摘要计入镜像数。"""

    mock_client = MagicMock()
    mock_client.images.prune.return_value = {
        "ImagesDeleted": [
            {"Untagged": "nginx:latest"},
            {"Untagged": "redis:latest"},
            {"Deleted": "sha256:abc123"},
            {"Deleted": "sha256:def456"},
            {"Deleted": "sha256:ghi789"},
        ],
        "SpaceReclaimed": 161270000,
    }
    mock_docker_module.from_env.return_value = mock_client

    manager = DockerManager()
    result = manager.prune_unused_images()

    assert result["deleted"] == ["nginx:latest", "redis:latest"]
    assert result["space_reclaimed"] == 161270000
    # 只应返回 2 个镜像标签，而不是 5 个条目
    assert len(result["deleted"]) == 2


@patch("app.services.docker.docker_common.docker")
@patch("app.services.docker.docker_common.time")
def test_pull_progress_counts_100_percent_as_completed(mock_time, mock_docker_module):
    """层百分比达到 100 时，即使状态尚未变为 Pull complete，也应计入已完成层数。"""

    fake_now = [0.0]

    def _next_time():
        fake_now[0] += 0.5
        return fake_now[0]

    mock_time.time.side_effect = _next_time

    events = [
        # 状态仍是 Downloading，但 current == total，百分比 100
        {
            "id": "layer1",
            "status": "Downloading",
            "progressDetail": {"current": 100, "total": 100},
        },
        # 正常 Pull complete
        {"id": "layer2", "status": "Pull complete", "progressDetail": {}},
    ]

    mock_client = MagicMock()
    mock_client.api.pull.return_value = iter(events)
    mock_docker_module.from_env.return_value = mock_client

    manager = DockerManager()
    task_manager = ImagePullTaskManager()
    task_id = task_manager.create_task("test:latest")

    recorded = []

    def _update_progress(tid, progress):
        recorded.append(progress.copy())

    task_manager.update_progress = _update_progress

    manager.pull_image_async("test:latest", task_id, task_manager)

    # 两层都应计入已完成
    assert recorded[-1]["completed_layers"] == 2
    assert recorded[-1]["total_layers"] == 2


@patch("app.services.docker.docker_common.docker")
@patch("app.services.docker.docker_common.time")
def test_complete_task_syncs_layer_counts(mock_time, mock_docker_module):
    """拉取完成时，completed_layers 和 total_layers 应与实际层数一致。"""

    fake_now = [0.0]

    def _next_time():
        fake_now[0] += 0.5
        return fake_now[0]

    mock_time.time.side_effect = _next_time

    events = [
        {"id": "layer1", "status": "Pull complete", "progressDetail": {}},
        {"id": "layer2", "status": "Downloading", "progressDetail": {"current": 50, "total": 100}},
    ]

    mock_client = MagicMock()
    mock_client.api.pull.return_value = iter(events)
    mock_docker_module.from_env.return_value = mock_client

    manager = DockerManager()
    task_manager = ImagePullTaskManager()
    task_id = task_manager.create_task("test:latest")

    manager.pull_image_async("test:latest", task_id, task_manager)

    task = task_manager.get_task(task_id)
    assert task["status"] == "completed"
    assert task["progress"]["percentage"] == 100
    # 完成时两层都应算已完成
    assert task["progress"]["completed_layers"] == 2
    assert task["progress"]["total_layers"] == 2


@patch("app.services.docker.docker_common.docker")
@patch("app.services.docker.docker_common.time")
def test_container_action_start_waits_for_running(mock_time, mock_docker_module):
    """start 操作应等待容器真正进入 running 状态后再返回。"""
    mock_container = MagicMock()
    mock_container.attrs = {"State": {"Status": "running", "Error": ""}}
    mock_container.status = "running"

    mock_client = MagicMock()
    mock_client.containers.get.return_value = mock_container
    mock_docker_module.from_env.return_value = mock_client

    # 模拟时间快速推进，避免真实等待
    mock_time.time.side_effect = [0, 1]
    mock_time.sleep = MagicMock()

    manager = DockerManager()
    result = manager.container_action("abc123", "start")

    mock_container.start.assert_called_once()
    assert result["status"] == "running"
    assert result["error"] == ""


@patch("app.services.docker.docker_common.docker")
@patch("app.services.docker.docker_common.time")
def test_container_action_start_fails_on_dead(mock_time, mock_docker_module):
    """start 操作若容器进入 dead 状态应抛出异常并附带错误信息。"""
    mock_container = MagicMock()
    mock_container.attrs = {
        "State": {
            "Status": "dead",
            "Error": "driver failed programming external connectivity",
        }
    }
    mock_container.status = "dead"

    mock_client = MagicMock()
    mock_client.containers.get.return_value = mock_container
    mock_docker_module.from_env.return_value = mock_client

    mock_time.time.side_effect = [0, 1]
    mock_time.sleep = MagicMock()

    manager = DockerManager()
    with pytest.raises(RuntimeError) as exc_info:
        manager.container_action("abc123", "start")

    assert "driver failed programming external connectivity" in str(exc_info.value)


@patch("app.services.docker.docker_common.docker")
@patch("app.services.docker.docker_common.time")
def test_container_action_start_times_out(mock_time, mock_docker_module):
    """start 操作超时时应抛出异常并附带当前状态。"""
    mock_container = MagicMock()
    mock_container.attrs = {"State": {"Status": "restarting", "Error": ""}}
    mock_container.status = "restarting"

    mock_client = MagicMock()
    mock_client.containers.get.return_value = mock_container
    mock_docker_module.from_env.return_value = mock_client

    # 每次调用 time.time 推进 5 秒，确保在几次轮询后超时
    call_count = 0
    def fake_time():
        nonlocal call_count
        call_count += 1
        return call_count * 5

    mock_time.time.side_effect = fake_time
    mock_time.sleep = MagicMock()

    manager = DockerManager()
    with pytest.raises(RuntimeError) as exc_info:
        manager.container_action("abc123", "start")

    assert "等待容器状态超时" in str(exc_info.value)
    assert "restarting" in str(exc_info.value)


@patch("app.services.docker.docker_common.docker")
def test_container_action_stop_returns_immediately(mock_docker_module):
    """stop 操作应立即返回当前状态，不需要等待 running。"""
    mock_container = MagicMock()
    mock_container.attrs = {"State": {"Status": "exited", "Error": ""}}
    mock_container.status = "exited"

    mock_client = MagicMock()
    mock_client.containers.get.return_value = mock_container
    mock_docker_module.from_env.return_value = mock_client

    manager = DockerManager()
    result = manager.container_action("abc123", "stop")

    mock_container.stop.assert_called_once()
    assert result["status"] == "exited"
    assert result["error"] == ""


@patch("app.services.docker.docker_common.docker")
def test_pull_uses_acceleration_domain_and_retags(mock_docker_module):
    mock_client = MagicMock()
    mock_client.api.pull.return_value = iter(
        [
            {"status": "Pulling from", "id": "layer1"},
            {"status": "Pull complete", "id": "layer1"},
        ]
    )
    mock_docker_module.from_env.return_value = mock_client

    manager = DockerManager()
    task_manager = ImagePullTaskManager()
    task_id = task_manager.create_task("ghcr.io/open-webui/open-webui:main")

    manager.pull_image_async(
        "ghcr.io/open-webui/open-webui:main",
        task_id,
        task_manager,
        {"ghcr.io": ["https://abc.d.1ms.run"]},
    )

    mirrored = "abc.d.1ms.run/open-webui/open-webui:main"
    mock_client.api.pull.assert_called_once_with(
        mirrored, stream=True, decode=True
    )
    mock_client.images.get.assert_called_once_with(mirrored)
    mock_client.images.get.return_value.tag.assert_called_once_with(
        repository="ghcr.io/open-webui/open-webui", tag="main"
    )
    mock_client.images.remove.assert_called_once_with(mirrored)
    assert task_manager.get_task(task_id)["status"] == "completed"


@patch("app.services.docker.docker_common.docker")
def test_pull_falls_back_to_direct_when_acceleration_fails(mock_docker_module):
    mock_client = MagicMock()
    mock_client.api.pull.side_effect = [
        RuntimeError("mirror down"),
        iter([{"status": "Pull complete", "id": "layer1"}]),
    ]
    mock_docker_module.from_env.return_value = mock_client

    manager = DockerManager()
    task_manager = ImagePullTaskManager()
    task_id = task_manager.create_task("nginx:1.27")

    manager.pull_image_async(
        "nginx:1.27",
        task_id,
        task_manager,
        {"docker.io": ["https://mirror.example.com"]},
    )

    calls = [call.args[0] for call in mock_client.api.pull.call_args_list]
    assert calls == ["mirror.example.com/library/nginx:1.27", "nginx:1.27"]
    mock_client.images.get.assert_not_called()
    assert task_manager.get_task(task_id)["status"] == "completed"


@patch("app.services.docker.docker_common.docker")
def test_pull_skips_acceleration_for_digest_reference(mock_docker_module):
    mock_client = MagicMock()
    mock_client.api.pull.return_value = iter(
        [{"status": "Pull complete", "id": "layer1"}]
    )
    mock_docker_module.from_env.return_value = mock_client

    manager = DockerManager()
    task_manager = ImagePullTaskManager()
    image = "ghcr.io/owner/app@sha256:" + "a" * 64
    task_id = task_manager.create_task(image)

    manager.pull_image_async(
        image, task_id, task_manager, {"ghcr.io": ["https://abc.d.1ms.run"]}
    )

    mock_client.api.pull.assert_called_once_with(
        image, stream=True, decode=True
    )
