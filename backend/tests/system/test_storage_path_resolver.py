"""StoragePathResolver 单元测试。"""

import pytest

from app.core.exceptions import APIException
from app.services.system import StoragePathResolver
from app.services.system.storage import is_host_system_path


class TestStoragePathResolver:
    """测试宿主机/容器路径转换逻辑。"""

    def test_configured_property(self):
        """configured 属性反映是否已配置应用数据目录。"""
        assert StoragePathResolver(app_data=("/mnt/data/docker", "/docker")).configured is True
        assert StoragePathResolver(app_data=None).configured is False
        assert StoragePathResolver().configured is False

    def test_validate_requires_app_data(self):
        """validate 在未配置应用数据目录时应抛出异常（用于实际部署校验）。"""
        with pytest.raises(APIException) as exc_info:
            StoragePathResolver().validate()
        assert exc_info.value.status_code == 400
        assert "未配置应用数据目录" in exc_info.value.message

    def test_validate_accepts_explicit_mapping(self):
        """应用数据目录为显式映射时 validate 通过，两个 base 直接取配置值。"""
        resolver = StoragePathResolver(app_data=("/opt/docker", "/docker"))
        resolver.validate()
        assert resolver.host_mount_base == "/opt/docker"
        assert resolver.container_mount_base == "/docker"

    def test_with_defaults_uses_root_when_unconfigured(self):
        """未配置时 with_defaults 返回使用根目录恒等映射的解析器，用于预览。"""
        resolver = StoragePathResolver().with_defaults()
        assert resolver.host_mount_base == "/"
        assert resolver.container_mount_base == "/"

    def test_with_defaults_keeps_configured_resolver(self):
        """已配置时 with_defaults 返回自身。"""
        resolver = StoragePathResolver(app_data=("/opt/docker", "/docker"))
        assert resolver.with_defaults() is resolver

    def test_make_host_path(self):
        """自动生成宿主机挂载目录。"""
        resolver = StoragePathResolver(app_data=("/mnt/data/docker", "/docker"))
        assert (
            resolver.make_host_path("moviepilot", "moviepilot", "config")
            == "/mnt/data/docker/moviepilot/moviepilot/config"
        )

    def test_make_container_path(self):
        """自动生成容器内挂载目录。"""
        resolver = StoragePathResolver(app_data=("/mnt/data/docker", "/docker"))
        assert (
            resolver.make_container_path("moviepilot", "moviepilot", "config")
            == "/docker/moviepilot/moviepilot/config"
        )

    def test_to_container_path_from_app_data_host_prefix(self):
        """应用数据目录宿主机视角前缀换成容器视角前缀。"""
        resolver = StoragePathResolver(app_data=("/mnt/data/docker", "/docker"))
        assert resolver.to_container_path("/mnt/data/docker") == "/docker"
        assert resolver.to_container_path("/mnt/data/docker/app") == "/docker/app"

    def test_to_container_path_from_app_data_mount_prefix_passthrough(self):
        """已是应用数据目录容器视角的路径原样返回。"""
        resolver = StoragePathResolver(app_data=("/mnt/data/docker", "/docker"))
        assert resolver.to_container_path("/docker/app/config") == "/docker/app/config"

    def test_to_container_path_for_other_absolute(self):
        """不受管的其他绝对路径保持原样。"""
        resolver = StoragePathResolver(app_data=("/mnt/data/docker", "/docker"))
        assert resolver.to_container_path("/media") == "/media"
        assert resolver.to_container_path("/mnt/data/movies") == "/mnt/data/movies"

    def test_to_container_path_for_relative(self):
        """相对路径视为在应用数据目录下，拼到容器挂载基础路径下。"""
        resolver = StoragePathResolver(app_data=("/mnt/data/docker", "/docker"))
        assert resolver.to_container_path("movies") == "/docker/movies"
        assert (
            resolver.to_container_path("moviepilot/moviepilot/config")
            == "/docker/moviepilot/moviepilot/config"
        )

    def test_to_container_path_empty(self):
        """空字符串原样返回。"""
        resolver = StoragePathResolver(app_data=("/mnt/data/docker", "/docker"))
        assert resolver.to_container_path("") == ""

    def test_to_host_path_from_app_data_mount_prefix(self):
        """应用数据目录容器视角前缀还原为宿主机视角前缀。"""
        resolver = StoragePathResolver(app_data=("/mnt/data/docker", "/docker"))
        assert resolver.to_host_path("/docker") == "/mnt/data/docker"
        assert resolver.to_host_path("/docker/movies") == "/mnt/data/docker/movies"

    def test_to_host_path_from_app_data_host_prefix_passthrough(self):
        """已是应用数据目录宿主机视角的路径原样返回。"""
        resolver = StoragePathResolver(app_data=("/mnt/data/docker", "/docker"))
        assert resolver.to_host_path("/mnt/data/docker/app") == "/mnt/data/docker/app"

    def test_to_host_path_for_other_absolute(self):
        """其他绝对路径视为用户指定的宿主机路径。"""
        resolver = StoragePathResolver(app_data=("/mnt/data/docker", "/docker"))
        assert resolver.to_host_path("/media") == "/media"
        assert resolver.to_host_path("/mnt/data/movies") == "/mnt/data/movies"

    def test_to_host_path_for_relative(self):
        """相对路径拼接到应用数据目录（宿主机视角）。"""
        resolver = StoragePathResolver(app_data=("/mnt/data/docker", "/docker"))
        assert (
            resolver.to_host_path("movies") == "/mnt/data/docker/movies"
        )
        assert (
            resolver.to_host_path("moviepilot/moviepilot/config")
            == "/mnt/data/docker/moviepilot/moviepilot/config"
        )

    def test_identity_mapping_round_trip(self):
        """恒等映射（如 with_defaults 的根目录）下绝对路径双向原样。"""
        resolver = StoragePathResolver(app_data=("/", "/"))
        assert resolver.to_host_path("movies") == "/movies"
        assert resolver.to_container_path("movies") == "/movies"
        assert resolver.to_host_path("/opt/docker") == "/opt/docker"
        assert resolver.to_container_path("/opt/docker") == "/opt/docker"

    def test_trailing_slashes_are_normalized(self):
        """尾部斜杠应被规范化。"""
        resolver = StoragePathResolver(app_data=("/mnt/data/docker/", "/docker/"))
        resolver.validate()
        assert resolver.host_mount_base == "/mnt/data/docker"
        assert resolver.container_mount_base == "/docker"


class TestStoragePathResolverDataDirectories:
    """测试数据目录的路径转换与受管路径判断。"""

    def _resolver(self) -> StoragePathResolver:
        return StoragePathResolver(
            app_data=("/mnt/data/docker", "/docker"),
            data_directories=[("/mnt/media/", "/data/media/")],
        )

    def test_has_data_directories(self):
        """has_data_directories 反映是否配置了任一存储目录（含应用数据目录）。"""
        assert self._resolver().has_data_directories is True
        # 仅配置应用数据目录也视为已配置存储目录
        assert (
            StoragePathResolver(app_data=("/mnt/data/docker", "/docker")).has_data_directories
            is True
        )
        assert StoragePathResolver().has_data_directories is False
        # 空对的条目会被忽略
        assert (
            StoragePathResolver(data_directories=[("", "")]).has_data_directories
            is False
        )

    def test_data_directories_are_normalized(self):
        """数据目录路径尾部斜杠被规范化。"""
        resolver = self._resolver()
        assert resolver.data_directories == [("/mnt/media", "/data/media")]

    def test_to_container_path_from_host_path(self):
        """数据目录 host_path 前缀换成 mount_path 前缀。"""
        resolver = self._resolver()
        assert resolver.to_container_path("/mnt/media/movies") == "/data/media/movies"
        assert resolver.to_container_path("/mnt/media") == "/data/media"

    def test_to_container_path_from_mount_path_passthrough(self):
        """已是数据目录容器视角的路径原样返回。"""
        resolver = self._resolver()
        assert resolver.to_container_path("/data/media/movies") == "/data/media/movies"

    def test_to_container_path_prefers_longest_prefix(self):
        """前缀匹配按长度降序，避免短前缀抢先。"""
        resolver = StoragePathResolver(
            app_data=("/mnt/data", "/data"),
            data_directories=[("/mnt/data/media", "/media")],
        )
        # host_path 嵌套在应用数据目录下时，优先命中更长的数据目录前缀
        assert resolver.to_container_path("/mnt/data/media/movies") == "/media/movies"
        assert resolver.to_container_path("/mnt/data/other") == "/data/other"

    def test_to_container_path_regression_without_data_directories(self):
        """无数据目录时行为不变。"""
        resolver = StoragePathResolver(app_data=("/mnt/data/docker", "/docker"))
        assert resolver.to_container_path("/mnt/media/movies") == "/mnt/media/movies"
        assert resolver.to_container_path("/mnt/data/docker/movies") == "/docker/movies"
        assert resolver.to_container_path("movies") == "/docker/movies"

    def test_to_host_path_from_mount_path(self):
        """数据目录 mount_path 前缀还原为 host_path 前缀。"""
        resolver = self._resolver()
        assert resolver.to_host_path("/data/media/movies") == "/mnt/media/movies"
        assert resolver.to_host_path("/data/media") == "/mnt/media"

    def test_to_host_path_from_host_path_passthrough(self):
        """已是数据目录宿主机视角的路径原样返回。"""
        resolver = self._resolver()
        assert resolver.to_host_path("/mnt/media/movies") == "/mnt/media/movies"

    def test_to_host_path_regression_without_data_directories(self):
        """无数据目录时行为不变。"""
        resolver = StoragePathResolver(app_data=("/mnt/data/docker", "/docker"))
        assert resolver.to_host_path("/data/media/movies") == "/data/media/movies"
        assert resolver.to_host_path("/docker/movies") == "/mnt/data/docker/movies"
        assert resolver.to_host_path("movies") == "/mnt/data/docker/movies"

    def test_is_managed_path(self):
        """任一视角落在受管理目录下的路径返回 True。"""
        resolver = self._resolver()
        # 应用数据目录两个视角
        assert resolver.is_managed_path("/mnt/data/docker/app/config") is True
        assert resolver.is_managed_path("/docker/app/config") is True
        # 数据目录两个视角
        assert resolver.is_managed_path("/data/media/movies") is True
        assert resolver.is_managed_path("/mnt/media/movies") is True
        assert resolver.is_managed_path("/mnt/media") is True
        # 不受管理的路径
        assert resolver.is_managed_path("/etc/passwd") is False
        # 路径段边界：/mnt/media2 不属于 /mnt/media
        assert resolver.is_managed_path("/mnt/media2/x") is False
        # 空路径
        assert resolver.is_managed_path("") is False
        assert resolver.is_managed_path(None) is False

    def test_is_host_system_path(self):
        """Docker 套接字、时区文件等宿主机内置路径应被识别并放行。"""
        assert is_host_system_path("/var/run/docker.sock") is True
        assert is_host_system_path("/var/run/docker.sock/") is True
        assert is_host_system_path("/run/docker.sock") is True
        assert is_host_system_path("/etc/localtime") is True
        assert is_host_system_path("/etc/timezone") is True
        # 普通目录和空值不属于内置文件
        assert is_host_system_path("/etc/passwd") is False
        assert is_host_system_path("/mnt/media") is False
        assert is_host_system_path("") is False
        assert is_host_system_path(None) is False
