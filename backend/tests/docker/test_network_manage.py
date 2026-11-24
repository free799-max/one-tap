"""Docker 网络管理（关联统计与删除）测试。"""

from unittest.mock import MagicMock

import pytest

from app.services.docker.host_service import HostService


def _make_service(client) -> HostService:
    service = HostService()
    service._client_obj = client
    return service


def _container(name: str, networks: list[str]):
    container = MagicMock()
    container.name = name
    container.attrs = {
        "NetworkSettings": {"Networks": {network: {} for network in networks}}
    }
    return container


def test_get_host_info_counts_network_containers():
    client = MagicMock()
    client.version.return_value = {}
    client.info.return_value = {"DockerRootDir": "/var/lib/docker"}
    client.containers.list.return_value = [
        _container("web", ["app_default"]),
        _container("db", ["app_default", "bridge"]),
    ]
    network = MagicMock()
    network.id = "abcdef1234567890"
    network.name = "app_default"
    network.attrs = {"Driver": "bridge", "Scope": "local"}
    bridge = MagicMock()
    bridge.id = "fedcba0987654321"
    bridge.name = "bridge"
    bridge.attrs = {"Driver": "bridge", "Scope": "local"}
    client.networks.list.return_value = [network, bridge]

    info = _make_service(client).get_host_info()

    by_name = {net["name"]: net for net in info["networks"]}
    assert by_name["app_default"]["container_count"] == 2
    assert by_name["app_default"]["container_names"] == ["db", "web"]
    assert by_name["app_default"]["is_builtin"] is False
    assert by_name["bridge"]["container_count"] == 1
    assert by_name["bridge"]["is_builtin"] is True


def test_remove_network_rejects_used_network():
    client = MagicMock()
    network = MagicMock()
    network.name = "app_default"
    client.networks.get.return_value = network
    client.containers.list.return_value = [_container("web", ["app_default"])]

    with pytest.raises(ValueError, match="1 个容器"):
        _make_service(client).remove_network("abc")

    network.remove.assert_not_called()


def test_remove_network_rejects_builtin_network():
    client = MagicMock()
    network = MagicMock()
    network.name = "bridge"
    client.networks.get.return_value = network

    with pytest.raises(ValueError, match="预定义"):
        _make_service(client).remove_network("bridge")

    network.remove.assert_not_called()


def test_remove_network_removes_unused_network():
    client = MagicMock()
    network = MagicMock()
    network.name = "music_default"
    client.networks.get.return_value = network
    client.containers.list.return_value = []

    _make_service(client).remove_network("abc")

    network.remove.assert_called_once()


def test_remove_network_missing_network():
    from docker.errors import NotFound

    client = MagicMock()
    client.networks.get.side_effect = NotFound("no such network")

    with pytest.raises(ValueError, match="不存在"):
        _make_service(client).remove_network("abc")
