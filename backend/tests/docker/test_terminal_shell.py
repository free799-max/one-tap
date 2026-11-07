from unittest.mock import MagicMock, call, patch

import docker
import pytest

from app.services.docker import DockerManager


@patch("app.services.docker.docker_common.docker.from_env")
def test_terminal_shell_keeps_available_selection(mock_docker_module):
    container = MagicMock()
    container.exec_run.return_value = MagicMock(exit_code=0)
    client = MagicMock()
    client.containers.get.return_value = container
    mock_docker_module.return_value = client

    shell = DockerManager().resolve_terminal_shell("lucky", "/bin/bash", user="1000:1000")

    assert shell == "/bin/bash"
    container.exec_run.assert_called_once_with(
        ["/bin/bash", "-c", "exit 0"],
        stdout=False,
        stderr=False,
        user="1000:1000",
    )


@patch("app.services.docker.docker_common.docker.from_env")
def test_terminal_shell_falls_back_when_bash_is_missing(mock_docker_module):
    container = MagicMock()
    container.exec_run.side_effect = [
        docker.errors.APIError(
            'OCI runtime exec failed: exec: "/bin/bash": stat /bin/bash: '
            "no such file or directory"
        ),
        MagicMock(exit_code=0),
    ]
    client = MagicMock()
    client.containers.get.return_value = container
    mock_docker_module.return_value = client

    shell = DockerManager().resolve_terminal_shell("lucky", "/bin/bash")

    assert shell == "/bin/sh"
    assert container.exec_run.call_args_list == [
        call(["/bin/bash", "-c", "exit 0"], stdout=False, stderr=False),
        call(["/bin/sh", "-c", "exit 0"], stdout=False, stderr=False),
    ]


@patch("app.services.docker.docker_common.docker.from_env")
def test_terminal_shell_reports_when_no_shell_is_available(mock_docker_module):
    container = MagicMock()
    container.exec_run.side_effect = docker.errors.APIError("no such file or directory")
    client = MagicMock()
    client.containers.get.return_value = container
    mock_docker_module.return_value = client

    with pytest.raises(RuntimeError, match="没有可用的 shell"):
        DockerManager().resolve_terminal_shell("lucky", "/bin/bash")


@patch("app.services.docker.docker_common.docker.from_env")
def test_terminal_shell_does_not_hide_other_docker_errors(mock_docker_module):
    container = MagicMock()
    container.exec_run.side_effect = docker.errors.APIError("Docker daemon unavailable")
    client = MagicMock()
    client.containers.get.return_value = container
    mock_docker_module.return_value = client

    with pytest.raises(docker.errors.APIError, match="Docker daemon unavailable"):
        DockerManager().resolve_terminal_shell("lucky", "/bin/bash")
