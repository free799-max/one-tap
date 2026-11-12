from unittest.mock import MagicMock

import pytest

from app.services.docker import image_service, mirror_pull


def test_format_pull_progress_includes_real_speed():
    progress = {
        "percentage": 50,
        "size_text": "50.0 MB / 100.0 MB",
        "speed": 25 * 1024 * 1024,
    }

    assert mirror_pull._format_pull_progress(progress) == (
        "镜像下载 50% · 50.0 MB / 100.0 MB · 25.0 MB/s"
    )


def test_format_pull_block_looks_like_compose():
    mib = 1024 * 1024
    progress = {
        "percentage": 47,
        "determinate": True,
        "size_text": "361.0 MB / 753.0 MB",
        "speed": 51 * mib,
        "completed_layers": 1,
        "total_layers": 2,
        "layers": [
            {
                "id": "ae52aad3a114",
                "status": "Pull complete",
                "status_text": "已完成",
            },
            {
                "id": "37fb86c2a767",
                "status": "Downloading",
                "status_text": "下载中",
                "current": 361 * mib,
                "total": 753 * mib,
                "percentage": 47,
                "speed": 51 * mib,
            },
        ],
    }

    assert mirror_pull._format_pull_block(
        "langflowai/langflow:latest", progress
    ) == "\n".join([
        "[+] 拉取 langflowai/langflow:latest 1/2 · 47% "
        "· 361.0 MB / 753.0 MB · 51.0 MB/s",
        "ae52aad3a114 ✔ 已完成",
        "37fb86c2a767 ⠸ 下载中 [=========           ] "
        "361.0 MB / 753.0 MB · 51.0 MB/s",
    ])


def test_format_pull_block_without_percentage():
    mib = 1024 * 1024
    progress = {
        "percentage": 0,
        "determinate": False,
        "size_text": "已下载 212.8 MB",
        "speed": 50 * mib,
        "completed_layers": 3,
        "total_layers": 14,
        "layers": [
            {
                "id": "0626d3cc316c",
                "status": "Pulling fs layer",
                "status_text": "准备下载",
            }
        ],
    }

    lines = mirror_pull._format_pull_block("app:latest", progress).splitlines()
    # 层清单还没稳定时不给百分比，只报已下载量
    assert lines[0] == (
        "[+] 拉取 app:latest 3/14 · 已下载 212.8 MB · 50.0 MB/s"
    )
    assert lines[1] == "0626d3cc316c ⠸ 准备下载"


@pytest.mark.asyncio
async def test_pull_with_mirror_consumes_docker_progress_events(monkeypatch):
    mib = 1024 * 1024
    events = [
        {
            "id": "layer1",
            "status": "Downloading",
            "progressDetail": {"current": 25 * mib, "total": 100 * mib},
        },
        {
            "id": "layer1",
            "status": "Downloading",
            "progressDetail": {"current": 50 * mib, "total": 100 * mib},
        },
    ]
    client = MagicMock()
    client.api.pull.return_value = iter(events)
    manager = MagicMock()
    manager._client = client
    monkeypatch.setattr(mirror_pull, "docker_manager", manager)
    times = iter([100.0, 100.5, 102.0])
    monkeypatch.setattr(image_service.common.time, "time", lambda: next(times))

    async def inspect_image(*args, **kwargs):
        return 1, ""

    monkeypatch.setattr(mirror_pull, "_docker", inspect_image)
    output: list[str] = []

    def on_progress(data: dict) -> None:
        output.append(mirror_pull._format_pull_progress(data))

    assert await mirror_pull.pull_with_mirror(
        "example.com/app:latest", None, progress=on_progress
    )

    assert output == [
        # 刚发现层时先不给百分比，清单稳定后按真实字节算
        "镜像下载 已下载 25.0 MB",
        "镜像下载 50% · 50.0 MB / 100.0 MB · 16.7 MB/s",
        "镜像下载 100% · 100.0 MB / 100.0 MB",
    ]
    client.api.pull.assert_called_once_with(
        "example.com/app:latest", stream=True, decode=True
    )


def test_overall_percentage_uses_real_downloaded_bytes(monkeypatch):
    """大镜像只下到一半时，不能被已完成的小层平均成 99%。"""
    mib = 1024 * 1024
    times = iter([100.0, 100.5, 101.0, 102.0, 103.5])
    monkeypatch.setattr(image_service.common.time, "time", lambda: next(times))
    output = []
    stream = [
        {
            "id": "small",
            "status": "Downloading",
            "progressDetail": {"current": 10 * mib, "total": 10 * mib},
        },
        {"id": "small", "status": "Pull complete"},
        {
            "id": "big",
            "status": "Downloading",
            "progressDetail": {"current": 348 * mib, "total": 753 * mib},
        },
        {
            "id": "big",
            "status": "Downloading",
            "progressDetail": {"current": 348 * mib, "total": 753 * mib},
        },
    ]

    image_service._consume_pull_stream(stream, output.append)

    last = output[-1]
    assert last["downloaded_size"] == 358 * mib
    assert last["total_size"] == 763 * mib
    assert last["size_text"] == "358.0 MB / 763.0 MB"
    assert last["percentage"] == 46


def test_extraction_bytes_do_not_inflate_download_totals(monkeypatch):
    """解压阶段的字节数不能被算进下载量。"""
    mib = 1024 * 1024
    times = iter([100.0, 100.5, 101.0, 101.5])
    monkeypatch.setattr(image_service.common.time, "time", lambda: next(times))
    output = []
    stream = [
        {
            "id": "big",
            "status": "Downloading",
            "progressDetail": {"current": 348 * mib, "total": 753 * mib},
        },
        {
            "id": "big",
            "status": "Extracting",
            "progressDetail": {"current": 800 * mib, "total": 800 * mib},
        },
        {"id": "big", "status": "Pull complete"},
    ]

    image_service._consume_pull_stream(stream, output.append)

    last = output[-1]
    assert last["total_size"] == 753 * mib
    assert last["downloaded_size"] == 753 * mib
    # 流结束前最多 99%，真正的 100% 由 _docker_pull 在成功后补发
    assert last["percentage"] == 99


def test_pulling_from_line_is_not_treated_as_layer(monkeypatch):
    """「Pulling from <仓库>」带的是 tag，不能算成一个永远未完成的层。"""
    times = iter([100.0, 100.5, 101.0, 102.5])
    monkeypatch.setattr(image_service.common.time, "time", lambda: next(times))
    output = []
    stream = [
        {"id": "22.04", "status": "Pulling from library/ubuntu"},
        {
            "id": "layer1",
            "status": "Downloading",
            "progressDetail": {"current": 50, "total": 100},
        },
        {
            "id": "layer1",
            "status": "Downloading",
            "progressDetail": {"current": 50, "total": 100},
        },
    ]

    image_service._consume_pull_stream(stream, output.append)

    last = output[-1]
    assert last["total_layers"] == 1
    assert last["unknown_layers"] == 0
    assert last["percentage"] == 50


def test_unknown_layer_size_holds_percentage(monkeypatch):
    """还有层没报大小的时候，不能拿已知部分的字节算成 99%。"""
    mib = 1024 * 1024
    times = iter([100.0, 100.5, 101.0, 101.5, 102.0, 103.5])
    monkeypatch.setattr(image_service.common.time, "time", lambda: next(times))
    output = []
    stream = [
        {"id": "small", "status": "Pulling fs layer"},
        {"id": "big", "status": "Pulling fs layer"},
        {
            "id": "small",
            "status": "Downloading",
            "progressDetail": {"current": 2 * mib, "total": 2 * mib},
        },
        {
            "id": "big",
            "status": "Downloading",
            "progressDetail": {"current": 1 * mib, "total": 10 * mib},
        },
        {
            "id": "big",
            "status": "Downloading",
            "progressDetail": {"current": 2 * mib, "total": 10 * mib},
        },
    ]

    image_service._consume_pull_stream(stream, output.append)

    # 小层已下完，但大层还没报大小：不能显示 99%/100%
    held = output[2]
    assert held["determinate"] is False
    assert held["unknown_layers"] == 1
    assert held["percentage"] == 0
    assert mirror_pull._format_pull_progress(held) == (
        "镜像下载 已下载 2.0 MB · 1 层等待中"
    )

    # 大层报出大小、清单稳定后，按真实字节算：4 MB / 12 MB
    assert output[4]["determinate"] is True
    assert output[4]["percentage"] == 33


def test_completed_layer_counts_its_full_size(monkeypatch):
    times = iter([100.0, 100.5])
    monkeypatch.setattr(image_service.common.time, "time", lambda: next(times))
    output = []
    stream = [
        {
            "id": "layer1",
            "status": "Download complete",
            "progressDetail": {"current": 0, "total": 100},
        }
    ]

    image_service._consume_pull_stream(stream, output.append)

    assert output[-1]["downloaded_size"] == 100
    assert output[-1]["total_size"] == 100


@pytest.mark.asyncio
async def test_pull_tries_next_mirror_without_pulling_source(monkeypatch):
    pulled = []

    async def docker(*args, **kwargs):
        if args[:2] == ("image", "inspect"):
            return 1, ""
        return 0, ""

    async def pull(image, **kwargs):
        pulled.append(image)
        if image.startswith("dedicated.example/"):
            return 1, "403 Forbidden"
        return 0, ""

    monkeypatch.setattr(mirror_pull, "_docker", docker)
    monkeypatch.setattr(mirror_pull, "_docker_pull", pull)

    assert await mirror_pull.pull_with_mirror(
        "metacubex/mihomo:v1.19.33",
        {
            "docker.io": [
                "https://dedicated.example",
                "https://free.example",
            ]
        },
        allow_direct=False,
    )
    assert pulled == [
        "dedicated.example/metacubex/mihomo:v1.19.33",
        "free.example/metacubex/mihomo:v1.19.33",
    ]


@pytest.mark.asyncio
async def test_pull_never_falls_back_to_source_when_direct_is_disabled(monkeypatch):
    pulled = []

    async def inspect_image(*args, **kwargs):
        return 1, ""

    async def pull(image, **kwargs):
        pulled.append(image)
        return 1, "unavailable"

    monkeypatch.setattr(mirror_pull, "_docker", inspect_image)
    monkeypatch.setattr(mirror_pull, "_docker_pull", pull)

    assert not await mirror_pull.pull_with_mirror(
        "metacubex/mihomo:v1.19.33",
        {"docker.io": ["https://dedicated.example", "https://free.example"]},
        allow_direct=False,
    )
    assert pulled == [
        "dedicated.example/metacubex/mihomo:v1.19.33",
        "free.example/metacubex/mihomo:v1.19.33",
    ]
