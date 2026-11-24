"""Docker 宿主机信息相关 Pydantic Schema。"""

from pydantic import BaseModel


class DirectoryEntry(BaseModel):
    """目录条目信息。"""

    name: str
    path: str
    is_directory: bool


class DirectoryList(BaseModel):
    """目录列表响应。"""

    path: str
    entries: list[DirectoryEntry]
    exists: bool = True


class DirectoryCreateRequest(BaseModel):
    """创建目录请求。"""

    path: str
    name: str


class DirectoryRenameRequest(BaseModel):
    """重命名目录请求。"""

    old_path: str
    new_name: str


class DirectoryDeleteRequest(BaseModel):
    """删除目录请求。"""

    path: str


class DockerVersionInfo(BaseModel):
    """Docker 引擎版本信息。"""

    version: str
    api_version: str
    go_version: str
    os: str
    arch: str
    kernel_version: str
    build_time: str


class ResourceInfo(BaseModel):
    """宿主机资源信息（CPU、内存、磁盘）。"""

    cpu_cores: int
    cpu_usage_percent: float = 0.0
    memory_total: int  # 字节
    memory_used: int = 0  # 字节
    disk_total: int  # 字节
    disk_used: int  # 字节
    disk_free: int  # 字节
    disk_usage_percent: float


class DockerStatsInfo(BaseModel):
    """Docker 统计信息（容器、镜像数量）。"""

    containers_total: int
    containers_running: int
    containers_paused: int
    containers_stopped: int
    images: int


class NetworkInfo(BaseModel):
    """Docker 网络信息。"""

    id: str
    name: str
    driver: str
    scope: str
    # 关联容器数与其名称，用于判断网络是否可删除
    container_count: int = 0
    container_names: list[str] = []
    # 预定义网络（bridge/host/none）不可删除
    is_builtin: bool = False


class HostInfo(BaseModel):
    """Docker 宿主机综合信息。"""

    hostname: str
    os: str
    arch: str
    kernel_version: str
    uptime_seconds: int = 0
    load_average: list[float] = []
    docker_version: DockerVersionInfo
    resources: ResourceInfo
    stats: DockerStatsInfo
    storage_driver: str
    docker_root_dir: str
    networks: list[NetworkInfo]
