"""Docker Engine 硬件指纹与 installation 回退测试。"""

import hashlib
from unittest.mock import MagicMock, mock_open

import pytest

from app.services.licensing import device


INSTALLATION_ID = "0df2f287-026b-47b2-a4f5-1b8efacdb734"


async def test_docker_engine_fingerprint_and_metadata(monkeypatch):
    client = MagicMock()
    client.info.return_value = {
        "ID": "engine-id",
        "NCPU": 8,
        "MemTotal": 16 * 1024**3,
        "OperatingSystem": "Linux",
    }
    from_env = MagicMock(return_value=client)
    monkeypatch.setattr(device.docker, "from_env", from_env)
    monkeypatch.setattr(device, "_read_cpu_model", lambda: "Test CPU")

    result = await device.get_device_fingerprint(INSTALLATION_ID)

    assert result == {
        "hardware_fingerprint": hashlib.sha256(b"docker:engine-id").hexdigest(),
        "fingerprint_source": "docker",
        "device_info": {
            "cpu_model": "Test CPU",
            "cpu_cores": 8,
            "memory_total_bytes": 16 * 1024**3,
        },
    }
    from_env.assert_called_once_with()
    client.info.assert_called_once_with()
    client.close.assert_called_once_with()


@pytest.mark.parametrize("engine_info", [{"ID": ""}, {"NCPU": 2, "MemTotal": 4096}])
async def test_missing_docker_engine_id_falls_back(monkeypatch, engine_info):
    client = MagicMock()
    client.info.return_value = engine_info
    monkeypatch.setattr(device.docker, "from_env", MagicMock(return_value=client))

    result = await device.get_device_fingerprint(INSTALLATION_ID)

    assert result == {
        "hardware_fingerprint": hashlib.sha256(
            f"installation:{INSTALLATION_ID}".encode()
        ).hexdigest(),
        "fingerprint_source": "installation",
        "device_info": {
            "cpu_model": None,
            "cpu_cores": None,
            "memory_total_bytes": None,
        },
    }


async def test_docker_failure_falls_back_without_metadata(monkeypatch):
    monkeypatch.setattr(
        device.docker, "from_env", MagicMock(side_effect=OSError("Docker unavailable"))
    )

    result = await device.get_device_fingerprint(INSTALLATION_ID)

    assert result["hardware_fingerprint"] == hashlib.sha256(
        f"installation:{INSTALLATION_ID}".encode()
    ).hexdigest()
    assert result["fingerprint_source"] == "installation"
    assert result["device_info"] == {
        "cpu_model": None,
        "cpu_cores": None,
        "memory_total_bytes": None,
    }


async def test_docker_info_is_read_in_a_thread(monkeypatch):
    calls = []

    def read_info():
        calls.append("read")
        return {"ID": "engine-id", "NCPU": None, "MemTotal": None}

    async def to_thread(function):
        calls.append("thread")
        return function()

    monkeypatch.setattr(device, "_docker_engine_info", read_info)
    monkeypatch.setattr(device.asyncio, "to_thread", to_thread)

    result = await device.get_device_fingerprint(INSTALLATION_ID)

    assert calls == ["thread", "read"]
    assert result["fingerprint_source"] == "docker"


def test_read_cpu_model(monkeypatch):
    cpuinfo = "processor\t: 0\nmodel name\t: Intel(R) Xeon(R) E5-2680 v4\n"
    monkeypatch.setattr("builtins.open", mock_open(read_data=cpuinfo))

    assert device._read_cpu_model() == "Intel(R) Xeon(R) E5-2680 v4"


async def test_host_details_uses_docker_host_metadata(monkeypatch):
    client = MagicMock()
    client.info.return_value = {
        "Name": "nas-host",
        "OperatingSystem": "Debian GNU/Linux 12 (bookworm)",
        "Architecture": "x86_64",
        "NCPU": 8,
        "MemTotal": 16 * 1024**3,
    }
    monkeypatch.setattr(device.docker, "from_env", MagicMock(return_value=client))
    monkeypatch.setattr(device, "_read_cpu_model", lambda: "Test CPU")

    result = await device.get_host_details()

    assert result == {
        "hostname": "nas-host",
        "platform": "Debian GNU/Linux 12 (bookworm) · x86_64",
        "device_info": {
            "cpu_model": "Test CPU",
            "cpu_cores": 8,
            "memory_total_bytes": 16 * 1024**3,
        },
    }


async def test_host_details_keeps_kernel_when_os_is_generic(monkeypatch):
    client = MagicMock()
    client.info.return_value = {
        "Name": "nas-host",
        "OperatingSystem": "Linux",
        "KernelVersion": "6.18.18.c1186-trim",
        "Architecture": "x86_64",
        "NCPU": 8,
        "MemTotal": 16 * 1024**3,
    }
    monkeypatch.setattr(device.docker, "from_env", MagicMock(return_value=client))
    monkeypatch.setattr(device, "_read_cpu_model", lambda: "Test CPU")

    result = await device.get_host_details()

    assert result["platform"] == "Linux 6.18.18.c1186-trim · x86_64"


async def test_host_details_falls_back_without_docker(monkeypatch):
    monkeypatch.setattr(
        device.docker, "from_env", MagicMock(side_effect=OSError("Docker unavailable"))
    )
    monkeypatch.setattr(device, "_read_cpu_model", lambda: None)
    monkeypatch.setattr(device.platform, "system", lambda: "Linux")
    monkeypatch.setattr(device.platform, "release", lambda: "6.1.0")
    monkeypatch.setattr(device.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(device.socket, "gethostname", lambda: "container-id")

    result = await device.get_host_details()

    assert result == {
        "hostname": "container-id",
        "platform": "Linux 6.1.0 x86_64",
        "device_info": {
            "cpu_model": None,
            "cpu_cores": None,
            "memory_total_bytes": None,
        },
    }
