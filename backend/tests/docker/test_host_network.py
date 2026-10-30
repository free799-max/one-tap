"""宿主机网络探测测试。"""

from unittest.mock import MagicMock, patch

from app.services.docker.host_network import detect_host_address


def test_detect_host_address_uses_host_network_from_bridge_container():
    client = MagicMock()
    current = MagicMock()
    current.attrs = {"HostConfig": {"NetworkMode": "bridge"}}
    current.image = "onetap:local"
    client.containers.get.return_value = current
    client.containers.run.return_value = b"192.168.1.50\n"

    with patch(
        "app.services.docker.host_network._detect_local_address",
        return_value="172.18.0.3",
    ):
        address = detect_host_address(client)

    assert address == "192.168.1.50"
    run_kwargs = client.containers.run.call_args.kwargs
    assert run_kwargs["entrypoint"] == []
    assert run_kwargs["network_mode"] == "host"
    assert run_kwargs["remove"] is True


def test_detect_host_address_skips_helper_in_host_network():
    client = MagicMock()
    current = MagicMock()
    current.attrs = {"HostConfig": {"NetworkMode": "host"}}
    client.containers.get.return_value = current

    with patch(
        "app.services.docker.host_network._detect_local_address",
        return_value="192.168.1.50",
    ):
        address = detect_host_address(client)

    assert address == "192.168.1.50"
    client.containers.run.assert_not_called()
