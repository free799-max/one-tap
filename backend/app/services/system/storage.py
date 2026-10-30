"""存储路径解析器。

负责宿主机真实路径与容器视角路径之间的互相转换：
- 应用数据目录 = (host_path, mount_path)，存应用自身持久化小数据，
  host_mount_base / container_mount_base 直接取这两个值（显式映射，不再做前缀剥离）
- 数据目录 = 启用的 (host_path, mount_path) 列表，提供额外的双向前缀映射
"""

from app.core.exceptions import APIException

# 宿主机自带文件：Docker 套接字与时区文件等由系统提供，不能也不需要
# 落在应用数据目录/数据目录下，部署校验时应直接放行。
HOST_SYSTEM_PATHS = frozenset(
    {
        "/var/run/docker.sock",
        "/run/docker.sock",
        "/etc/localtime",
        "/etc/timezone",
    }
)


def is_host_system_path(path: str | None) -> bool:
    """判断路径是否为宿主机内置文件（而非需要受管目录的普通挂载）。"""
    value = (path or "").strip().rstrip("/")
    return value in HOST_SYSTEM_PATHS


class StoragePathResolver:
    """存储路径解析器。

    负责宿主机真实路径与容器视角路径之间的互相转换：
    - 应用数据目录 = (host_path, mount_path)，
      host_mount_base / container_mount_base 直接取这两个值
    - 数据目录 = 启用的 (host_path, mount_path) 列表，提供额外的双向前缀映射
    """

    def __init__(
        self,
        app_data: tuple[str, str] | None = None,
        data_directories: list[tuple[str, str]] | None = None,
    ):
        def _normalize(path: str | None) -> str:
            if not path:
                return ""
            value = path.strip().rstrip("/")
            return value if value else "/"

        if app_data:
            self.host_mount_base = _normalize(app_data[0])
            self.container_mount_base = _normalize(app_data[1])
        else:
            self.host_mount_base = ""
            self.container_mount_base = ""
        # 启用数据目录的 (host_path, mount_path) 列表，规范化后保存
        self.data_directories: list[tuple[str, str]] = [
            (_normalize(host), _normalize(mount))
            for host, mount in (data_directories or [])
            if host and mount
        ]
        # 合并应用数据目录与数据目录为一个映射列表，按前缀长度降序，
        # 供前缀互转时优先匹配更长（更具体）的前缀
        self._mappings: list[tuple[str, str]] = []
        if self.host_mount_base and self.container_mount_base:
            self._mappings.append((self.host_mount_base, self.container_mount_base))
        self._mappings.extend(self.data_directories)
        self._mappings.sort(key=lambda pair: max(len(pair[0]), len(pair[1])), reverse=True)

    @staticmethod
    def _join(base: str, path: str) -> str:
        """拼接基础路径与后续路径，避免产生双斜杠或丢失根目录。"""
        if not base or not path:
            return base or path or ""
        path = path.lstrip("/")
        if base.endswith("/"):
            return base + path
        return f"{base}/{path}"

    @staticmethod
    def _is_under(path: str, base: str) -> bool:
        """判断 path 是否等于 base 或位于 base 之下（按路径段前缀判断）。"""
        if not path or not base:
            return False
        if base == "/":
            return path.startswith("/")
        return path == base or path.startswith(base + "/")

    @staticmethod
    def _swap_prefix(path: str, src: str, dst: str) -> str:
        """将 path 的 src 前缀替换为 dst 前缀。"""
        suffix = path[len(src) :].lstrip("/")
        return StoragePathResolver._join(dst or "/", suffix)

    @property
    def has_data_directories(self) -> bool:
        """是否配置了任一存储目录（含应用数据目录）。"""
        return bool(self._mappings)

    @property
    def configured(self) -> bool:
        """是否已配置应用数据目录。"""
        return bool(self.host_mount_base and self.container_mount_base)

    def validate(self) -> None:
        """校验应用数据目录是否已配置。"""
        if not self.configured:
            raise APIException("未配置应用数据目录，请前往系统设置-存储配置", 400)

    def with_defaults(self) -> "StoragePathResolver":
        """返回使用默认根目录的解析器，用于未配置时的预览。

        默认使用根目录 "/" 恒等映射，让 preview 仍可生成可读的 YAML，
        实际部署前仍需在系统设置中配置真实路径。
        """
        if self.configured:
            return self
        return StoragePathResolver(
            app_data=("/", "/"), data_directories=self.data_directories
        )

    def make_host_path(
        self,
        app_name: str,
        service_name: str,
        mount_name: str,
    ) -> str:
        """按规则生成宿主机上的自动挂载目录。"""
        return self._join(
            self.host_mount_base,
            f"{app_name}/{service_name}/{mount_name}",
        )

    def make_container_path(
        self,
        app_name: str,
        service_name: str,
        mount_name: str,
    ) -> str:
        """按规则生成容器内的自动挂载目录。"""
        return self._join(
            self.container_mount_base or "/",
            f"{app_name}/{service_name}/{mount_name}",
        )

    def to_container_path(self, path: str) -> str:
        """将用户填写或选择的路径转换为容器内路径。

        - 命中任一存储目录的 mount_path 前缀（已是容器视角）：原样返回。
        - 命中任一存储目录的 host_path 前缀：换成对应 mount_path 前缀
          （按前缀长度降序匹配，避免短前缀抢先）。
        - 其他绝对路径（如 /media）：保持原样。
        - 相对路径：视为在应用数据目录下，拼到容器挂载基础路径下。
        - 空字符串原样返回。
        """
        path = (path or "").strip().rstrip("/")
        if not path:
            return ""

        # 已是容器视角（mount_path 前缀）的路径原样返回
        if any(self._is_under(path, mount) for _, mount in self._mappings):
            return path

        # 宿主机视角（host_path 前缀）的路径换成 mount_path 前缀
        for host_path, mount_path in self._mappings:
            if self._is_under(path, host_path):
                return self._swap_prefix(path, host_path, mount_path)

        # 其他绝对路径保持原样
        if path.startswith("/"):
            return path

        # 相对路径视为在应用数据目录下
        return self._join(self.container_mount_base or "/", path)

    def to_host_path(self, path: str) -> str:
        """将用户填写或选择的路径还原为宿主机绝对路径。

        - 命中任一存储目录的 host_path 前缀（已是宿主机视角）：原样返回。
        - 命中任一存储目录的 mount_path 前缀：换成对应 host_path 前缀
          （按前缀长度降序匹配，避免短前缀抢先）。
        - 其他绝对路径（如 /media）：保持原样，视为用户指定的宿主机路径。
        - 相对路径：拼接到应用数据目录（宿主机视角）下。
        - 空字符串原样返回。
        """
        path = (path or "").strip().rstrip("/")
        if not path:
            return ""

        # 已是宿主机视角（host_path 前缀）的路径原样返回
        if any(self._is_under(path, host) for host, _ in self._mappings):
            return path

        # 容器视角（mount_path 前缀）的路径换成 host_path 前缀
        for host_path, mount_path in self._mappings:
            if self._is_under(path, mount_path):
                return self._swap_prefix(path, mount_path, host_path)

        # 其他绝对路径，保持原样作为宿主机路径
        if path.startswith("/"):
            return path

        # 相对路径，拼接到应用数据目录（宿主机视角）
        return self._join(self.host_mount_base or "/", path)

    def is_managed_path(self, path: str) -> bool:
        """判断路径（任一视角）是否落在受管理的存储目录下。

        受管理范围：所有存储目录记录（应用数据目录 + 数据目录）的
        host_path 与 mount_path 双视角前缀。空路径返回 False。
        """
        path = (path or "").strip().rstrip("/")
        if not path:
            return False
        for host_path, mount_path in self._mappings:
            if self._is_under(path, host_path) or self._is_under(path, mount_path):
                return True
        return False
