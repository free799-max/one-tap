"""系统级共享服务包。

包含系统全局配置、存储路径解析与端口分配等跨领域基础设施服务。
"""

from app.services.system.config_service import SystemConfigService, system_config_service
from app.services.system.hosts_acceleration_service import (
    HostsAccelerationService,
    hosts_acceleration_service,
)
from app.services.system.hosts_optimizer_service import (
    HostsOptimizerService,
    hosts_optimizer_service,
)
from app.services.system.port_allocator import (
    allocate_and_reserve,
    allocate_free_ports,
    get_reserved_ports,
    get_used_ports,
    release_ports,
    reserve_ports,
)
from app.services.system.proxy_test_service import (
    ProxyTestService,
    proxy_test_service,
)
from app.services.system.registry_mirror_service import (
    RegistryMirrorService,
    registry_mirror_service,
)
from app.services.system.storage import StoragePathResolver
from app.services.system.storage_directory_service import (
    StorageDirectoryService,
    get_browse_roots,
    storage_directory_service,
)

__all__ = [
    "StoragePathResolver",
    "StorageDirectoryService",
    "storage_directory_service",
    "get_browse_roots",
    "SystemConfigService",
    "system_config_service",
    "HostsOptimizerService",
    "hosts_optimizer_service",
    "HostsAccelerationService",
    "hosts_acceleration_service",
    "ProxyTestService",
    "proxy_test_service",
    "RegistryMirrorService",
    "registry_mirror_service",
    "allocate_and_reserve",
    "allocate_free_ports",
    "get_reserved_ports",
    "get_used_ports",
    "release_ports",
    "reserve_ports",
]
