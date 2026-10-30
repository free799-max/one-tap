"""在 Docker 宿主机网络命名空间中执行只读探测。"""

import ipaddress
import logging
import socket

logger = logging.getLogger(__name__)

_HOST_ADDRESS_PROBE = (
    "import socket; "
    "s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); "
    "s.settimeout(0.5); "
    "s.connect(('8.8.8.8', 80)); "
    "print(s.getsockname()[0]); "
    "s.close()"
)


def run_in_host_network(client, command: list[str]) -> bytes | None:
    current = client.containers.get(socket.gethostname())
    host_config = current.attrs.get("HostConfig") or {}
    if host_config.get("NetworkMode") == "host":
        return None
    return client.containers.run(
        current.image,
        command=command,
        entrypoint=[],
        network_mode="host",
        remove=True,
        stdout=True,
        stderr=True,
    )


def _detect_local_address() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(0.5)
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "localhost"


def detect_host_address(client) -> str:
    local_address = _detect_local_address()
    if client is None:
        return local_address
    try:
        output = run_in_host_network(
            client, ["python", "-c", _HOST_ADDRESS_PROBE]
        )
        if output is None:
            return local_address
        address = output.decode("utf-8").strip()
        ipaddress.ip_address(address)
        return address
    except Exception:
        logger.warning("获取宿主机访问地址失败，使用当前网络检测结果", exc_info=True)
        return local_address
