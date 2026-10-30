"""应用商店服务端口顺序分配单元测试。"""

from unittest.mock import MagicMock, patch

import pytest

from app.core.exceptions import APIException
from app.services.app_store.app_service import _extract_app_ports
from app.services.system import StoragePathResolver
from app.services.system.port_allocator import (
    _parse_proc_net_tcp,
    get_used_ports,
)


class TestExtractAppPorts:
    """测试单端口字段提取。"""

    def test_extract_integer_port_fields(self):
        """提取类型为 integer 且命名包含 port 的字段名。"""
        schema = {
            "properties": {
                "moviepilot_port": {"type": "integer"},
                "api_port": {"type": "integer"},
                "ports": {"type": "array"},
                "name": {"type": "string"},
            }
        }
        assert sorted(_extract_app_ports(schema)) == [
            "api_port",
            "moviepilot_port",
        ]

    def test_return_empty_when_no_port_fields(self):
        """没有相关字段时返回空列表。"""
        assert _extract_app_ports({}) == []
        assert _extract_app_ports({"properties": {"name": {"type": "string"}}}) == []


class TestParseProcNetTcp:
    """测试 /proc/net/tcp 监听端口解析。"""

    def test_parse_listening_ports(self, tmp_path):
        """正确解析 LISTEN 状态的本地端口。"""
        proc_file = tmp_path / "tcp"
        proc_file.write_text(
            "  sl  local_address rem_address   st tx_queue:rx_queue tr:tm when retrnsmt   uid  timeout inode\n"
            "   0: 00000000:0BB8 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 12345 1 0000000000000000 100 0 0 10 0\n"
            "   1: 00000000:0BB9 00000000:0000 01 00000000:00000000 00:00000000 00000000     0        0 12346 1 0000000000000000 100 0 0 10 0\n"
            "   2: 0100007F:0BBA 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 12347 1 0000000000000000 100 0 0 10 0\n"
        )
        # 0BB8=3000, 0BBA=3002，01 状态应被忽略
        assert _parse_proc_net_tcp(str(proc_file)) == {3000, 3002}

    def test_handle_missing_file(self):
        """文件不存在时返回空集合。"""
        assert _parse_proc_net_tcp("/nonexistent/path") == set()


class TestGetUsedPorts:
    """测试占用端口集合合并。"""

    def test_merges_docker_and_proc_ports(self):
        """合并 Docker 容器映射端口与 /proc/net/tcp 监听端口。"""
        fake_container = {
            "HostConfig": {
                "PortBindings": {
                    "3000/tcp": [{"HostPort": "3000"}],
                    "3001/tcp": [{"HostPort": "3001"}],
                }
            }
        }

        from app.services.docker import docker_manager

        mock_client = MagicMock()
        mock_client.containers.list.return_value = [
            type("Container", (), {"attrs": fake_container})()
        ]

        with patch.object(docker_manager, "_client_obj", mock_client):
            with patch(
                "app.services.system.port_allocator._parse_proc_net_tcp"
            ) as mock_parse:
                mock_parse.side_effect = (
                    lambda path: {3002} if "tcp6" in path else {3003}
                )
                used = get_used_ports()

        assert used == {3000, 3001, 3002, 3003}

    def test_skips_docker_when_unavailable(self):
        """Docker 不可用时仍能从 /proc/net/tcp 读取端口。"""
        from app.services.docker import docker_manager

        mock_client = MagicMock()
        mock_client.containers.list.side_effect = Exception("no docker")

        with patch.object(docker_manager, "_client_obj", mock_client):
            with patch(
                "app.services.system.port_allocator._parse_proc_net_tcp"
            ) as mock_parse:
                mock_parse.side_effect = (
                    lambda path: {8080} if "tcp6" in path else {80}
                )
                used = get_used_ports()

        assert used == {80, 8080}


class TestValidateAndPreparePorts:
    """测试 _validate_and_prepare 中的端口顺序分配。"""

    @pytest.fixture
    def app_service(self):
        from app.services.app_store.app_service import AppService

        return AppService()

    @pytest.fixture
    def array_port_schema_app(self):
        return type(
            "App",
            (),
            {
                "config_schema": {
                    "type": "object",
                    "properties": {
                        "ports": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "local_port": {"type": "integer"},
                                    "container_port": {"type": "integer"},
                                    "protocol": {"type": "string"},
                                },
                            },
                        }
                    },
                },
                "yaml_template": "services:\n  app:\n    image: test",
            },
        )()

    @pytest.mark.asyncio
    async def test_allocates_array_ports_skipping_used(
        self, app_service, array_port_schema_app
    ):
        """ports 数组的 local_port 从起始端口顺序分配，跳过已占用端口。"""
        config = {
            "ports": [
                {"local_port": 3000, "container_port": 3000},
                {"local_port": 3001, "container_port": 3001},
            ]
        }
        with patch(
            "app.services.app_store.app_service.get_used_ports",
            return_value={10000},
        ):
            project_name, allocated = await app_service._validate_and_prepare(
                db_app=array_port_schema_app,
                instance_name="test",
                config=config,
            )

        assert project_name == "test"
        assert allocated == [10001, 10002]
        assert config["ports"][0]["local_port"] == 10001
        assert config["ports"][1]["local_port"] == 10002
        # 容器端口不被改动
        assert config["ports"][0]["container_port"] == 3000

    @pytest.mark.asyncio
    async def test_skips_port_check_when_check_ports_false(
        self, app_service, array_port_schema_app
    ):
        """preview 阶段 check_ports=False 时不进行端口分配。"""
        config = {
            "ports": [
                {"local_port": 3000, "container_port": 3000},
            ]
        }
        with patch(
            "app.services.app_store.app_service.get_used_ports"
        ) as mock_used:
            project_name, allocated = await app_service._validate_and_prepare(
                db_app=array_port_schema_app,
                instance_name="test",
                config=config,
                check_ports=False,
            )

        mock_used.assert_not_called()
        assert allocated == []
        assert config["ports"][0]["local_port"] == 3000

    @pytest.mark.asyncio
    async def test_fixed_ports_are_not_reallocated(self, app_service):
        """x-fixed-ports 应用保留配置中的端口，并返回预留列表。"""
        db_app = type(
            "App",
            (),
            {
                "config_schema": {
                    "type": "object",
                    "x-fixed-ports": True,
                    "properties": {
                        "dns_port": {"type": "integer"},
                    },
                },
                "yaml_template": "services:\n  app:\n    image: test",
            },
        )()
        config = {"dns_port": 53}

        with patch(
            "app.services.app_store.app_service.get_used_ports",
            return_value=set(),
        ):
            project_name, allocated = await app_service._validate_and_prepare(
                db_app=db_app,
                instance_name="test",
                config=config,
            )

        assert project_name == "test"
        assert allocated == [53]
        assert config["dns_port"] == 53

    @pytest.mark.asyncio
    async def test_fixed_ports_reject_occupied_port(self, app_service):
        """x-fixed-ports 应用在固定端口被占用时给出明确错误。"""
        db_app = type(
            "App",
            (),
            {
                "config_schema": {
                    "type": "object",
                    "x-fixed-ports": True,
                    "properties": {
                        "ports": {"type": "array"},
                    },
                },
                "yaml_template": "services:\n  app:\n    image: test",
            },
        )()
        config = {
            "ports": [
                {"local_port": 9016, "container_port": 3000, "protocol": "tcp"},
                {"local_port": 9017, "container_port": 80, "protocol": "tcp"},
            ]
        }

        with patch(
            "app.services.app_store.app_service.get_used_ports",
            return_value={9017},
        ):
            with pytest.raises(APIException) as exc_info:
                await app_service._validate_and_prepare(
                    db_app=db_app,
                    instance_name="test",
                    config=config,
                )

        assert "9017" in str(exc_info.value)
        assert config["ports"][0]["local_port"] == 9016

    @pytest.mark.asyncio
    async def test_allocates_integer_port_field(self, app_service):
        """单端口整数字段按顺序分配。"""
        db_app = type(
            "App",
            (),
            {
                "config_schema": {
                    "type": "object",
                    "properties": {
                        "moviepilot_port": {"type": "integer"}
                    },
                },
                "yaml_template": "services:\n  app:\n    image: test",
            },
        )()
        config = {"moviepilot_port": 3000}

        with patch(
            "app.services.app_store.app_service.get_used_ports",
            return_value={10000},
        ):
            project_name, allocated = await app_service._validate_and_prepare(
                db_app=db_app,
                instance_name="test",
                config=config,
                port_start=10000,
            )

        assert project_name == "test"
        assert allocated == [10001]
        assert config["moviepilot_port"] == 10001

    @pytest.mark.asyncio
    async def test_raises_400_when_no_free_ports(self, app_service):
        """起始端口之后无可用端口时抛 400。"""
        db_app = type(
            "App",
            (),
            {
                "config_schema": {
                    "type": "object",
                    "properties": {
                        "web_port": {"type": "integer"}
                    },
                },
                "yaml_template": "services:\n  app:\n    image: test",
            },
        )()

        with patch(
            "app.services.app_store.app_service.get_used_ports",
            return_value={65535},
        ):
            with pytest.raises(APIException) as exc_info:
                await app_service._validate_and_prepare(
                    db_app=db_app,
                    instance_name="test",
                    config={"web_port": 8080},
                    port_start=65535,
                )

        assert exc_info.value.status_code == 400

    @staticmethod
    def _volume_app():
        return type(
            "App",
            (),
            {
                "name": "portainer",
                "config_schema": {
                    "type": "object",
                    "properties": {
                        "volumes": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": ["host_path", "container_path", "mode"],
                                "properties": {
                                    "host_path": {"type": "string"},
                                    "container_path": {"type": "string"},
                                    "mode": {"type": "string"},
                                },
                            },
                        }
                    },
                },
                "yaml_template": "services:\n  app:\n    image: test",
            },
        )()

    @pytest.mark.asyncio
    async def test_allows_host_system_mounts(self, app_service):
        """Docker 套接字等宿主机内置文件不受数据目录限制。"""
        resolver = StoragePathResolver(app_data=("/opt/docker", "/docker"))
        config = {
            "volumes": [
                {
                    "host_path": "/var/run/docker.sock",
                    "container_path": "/var/run/docker.sock",
                    "mode": "ro",
                }
            ]
        }

        project_name, _ = await app_service._validate_and_prepare(
            db_app=self._volume_app(),
            instance_name="portainer",
            config=config,
            check_ports=False,
            resolver=resolver,
        )

        assert project_name == "portainer"

    @pytest.mark.asyncio
    async def test_rejects_unmanaged_absolute_mounts(self, app_service):
        """普通绝对路径仍必须位于受管存储目录下。"""
        resolver = StoragePathResolver(app_data=("/opt/docker", "/docker"))
        config = {
            "volumes": [
                {
                    "host_path": "/mnt/other/data",
                    "container_path": "/data",
                    "mode": "rw",
                }
            ]
        }

        with pytest.raises(APIException) as exc_info:
            await app_service._validate_and_prepare(
                db_app=self._volume_app(),
                instance_name="portainer",
                config=config,
                check_ports=False,
                resolver=resolver,
            )

        assert "不在已配置的应用数据目录" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_strips_builtin_mount_from_legacy_config(self, app_service):
        """模板已内置的系统挂载不再从旧实例配置中重复渲染。"""
        app = self._volume_app()
        app.yaml_template = (
            "services:\n  app:\n    image: test\n    volumes:\n"
            "      - /var/run/docker.sock:/var/run/docker.sock:ro\n"
            "{%- for vol in volumes %}\n"
            "      - {{ vol.host_path }}:{{ vol.container_path }}:{{ vol.mode }}\n"
            "{%- endfor %}\n"
        )
        config = {
            "volumes": [
                {
                    "host_path": "/var/run/docker.sock",
                    "container_path": "/var/run/docker.sock",
                    "mode": "ro",
                },
                {
                    "host_path": "portainer/data",
                    "container_path": "/data",
                    "mode": "rw",
                },
            ]
        }

        await app_service._validate_and_prepare(
            db_app=app,
            instance_name="portainer",
            config=config,
            check_ports=False,
            resolver=StoragePathResolver(app_data=("/opt/docker", "/docker")),
        )

        assert config["volumes"] == [
            {
                "host_path": "portainer/data",
                "container_path": "/data",
                "mode": "rw",
            }
        ]


class TestValidateAndPrepareImage:
    """测试通用镜像配置的部署前校验。"""

    @pytest.mark.asyncio
    async def test_strict_schema_accepts_platform_image_config(self):
        """Schema 未声明镜像字段时，通用镜像配置仍可通过严格校验。"""
        from app.services.app_store.app_service import AppService

        db_app = type(
            "App",
            (),
            {
                "config_schema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                "yaml_template": "services:\n  app:\n    image: {{ image }}",
            },
        )()
        config = {
            "image": "registry.example.com/apps/demo:v2.1.0",
            "image_tag": "v2.1.0",
            "container_images": {
                "app": {
                    "repository": "registry.example.com/apps/demo",
                    "tag": "v2.1.0",
                }
            },
        }

        project_name, allocated = await AppService()._validate_and_prepare(
            db_app=db_app,
            instance_name="image-demo",
            config=config,
            check_ports=False,
        )

        assert project_name == "image-demo"
        assert allocated == []
        assert config["image"] == "registry.example.com/apps/demo:v2.1.0"
        assert config["image_tag"] == "v2.1.0"
        assert config["container_images"]["app"]["tag"] == "v2.1.0"


class TestContainerImages:
    """测试多容器默认镜像提取与逐服务覆盖。"""

    @pytest.fixture
    def app_service(self):
        from app.services.app_store.app_service import AppService

        return AppService()

    @staticmethod
    def _app(template: str, containers: list[str], image: str = "apps/main:v1"):
        return type(
            "App",
            (),
            {
                "name": containers[0],
                "image": image,
                "config_schema": {
                    "containers": [
                        {"name": name, "title": name, "settings": []}
                        for name in containers
                    ]
                },
                "yaml_template": template,
            },
        )()

    @staticmethod
    def _services(rendered: str) -> dict:
        import yaml

        return yaml.safe_load(rendered)["services"]

    def test_extracts_default_and_derived_container_images(self):
        """默认镜像提取支持可选容器和派生镜像表达式。"""
        from app.services.app_store.app_service import _extract_container_images

        app = self._app(
            "services:\n"
            "  beszel:\n"
            "    image: {{ image }}\n"
            "  beszel-agent:\n"
            "    image: {{ image | replace('beszel:', 'beszel-agent:') }}\n",
            ["beszel", "beszel-agent"],
            "henrygd/beszel:0.21.1",
        )

        assert _extract_container_images(app) == {
            "beszel": "henrygd/beszel:0.21.1",
            "beszel-agent": "henrygd/beszel-agent:0.21.1",
        }

    def test_overrides_main_and_dependency_images_independently(self, app_service):
        """主容器和依赖容器可以分别覆盖为不同镜像。"""
        app = self._app(
            "services:\n"
            "  moviepilot:\n"
            "    image: {{ image }}\n"
            "  redis:\n"
            "    image: redis:7-alpine\n"
            "  postgresql:\n"
            "    image: postgres:16-alpine\n",
            ["moviepilot", "redis", "postgresql"],
            "jxxghp/moviepilot-v2:latest",
        )
        rendered = app_service._render_app(
            app,
            {
                "container_images": {
                    "moviepilot": {
                        "repository": "custom/moviepilot",
                        "tag": "v3",
                    },
                    "postgresql": {
                        "repository": "custom/postgres",
                        "tag": "17",
                    },
                }
            },
            "demo",
            None,
        )

        services = self._services(rendered)
        assert services["moviepilot"]["image"] == "custom/moviepilot:v3"
        assert services["redis"]["image"] == "redis:7-alpine"
        assert services["postgresql"]["image"] == "custom/postgres:17"

    def test_main_override_does_not_replace_other_containers(self, app_service):
        """只修改主容器时，依赖容器保留各自默认镜像。"""
        app = self._app(
            "services:\n"
            "  moviepilot:\n"
            "    image: {{ image }}\n"
            "  redis:\n"
            "    image: redis:7-alpine\n",
            ["moviepilot", "redis"],
            "jxxghp/moviepilot-v2:latest",
        )
        rendered = app_service._render_app(
            app,
            {
                "container_images": {
                    "moviepilot": {
                        "repository": "custom/moviepilot",
                        "tag": "v3",
                    }
                }
            },
            "demo",
            None,
        )

        services = self._services(rendered)
        assert services["moviepilot"]["image"] == "custom/moviepilot:v3"
        assert services["redis"]["image"] == "redis:7-alpine"

    def test_main_override_does_not_cascade_through_template_variable(
        self, app_service
    ):
        """多个服务共用 image 变量时，非目标服务仍使用自己的默认值。"""
        app = self._app(
            "services:\n"
            "  hbbs:\n"
            "    image: {{ image }}\n"
            "  hbbr:\n"
            "    image: {{ image }}\n",
            ["hbbs", "hbbr"],
            "rustdesk/rustdesk-server:1.1.16",
        )
        rendered = app_service._render_app(
            app,
            {
                "container_images": {
                    "hbbs": {
                        "repository": "custom/rustdesk",
                        "tag": "v2",
                    }
                }
            },
            "demo",
            None,
        )

        services = self._services(rendered)
        assert services["hbbs"]["image"] == "custom/rustdesk:v2"
        assert services["hbbr"]["image"] == "rustdesk/rustdesk-server:1.1.16"


class TestRenderAppImage:
    """测试编排组合覆盖镜像与模板 image_tag 的拼接。"""

    @pytest.fixture
    def app_service(self):
        from app.services.app_store.app_service import AppService

        return AppService()

    @staticmethod
    def _app(template: str):
        return type(
            "App",
            (),
            {
                "name": "qbittorrent",
                "image": "qbittorrentofficial/qbittorrent-nox",
                "config_schema": {},
                "yaml_template": template,
            },
        )()

    def test_full_image_override_is_not_double_tagged(self, app_service):
        """覆盖镜像已带标签时，不再追加 image_tag。"""
        app = self._app(
            "services:\n  app:\n    image: {{ image }}:{{ image_tag }}"
        )
        rendered = app_service._render_app(
            app,
            {
                "image": "linuxserver/qbittorrent:5.3.0_v2.1.2-ls484",
                "image_tag": "latest",
            },
            "demo",
            None,
        )

        assert (
            "image: linuxserver/qbittorrent:5.3.0_v2.1.2-ls484" in rendered
        )
        assert ":latest" not in rendered

    def test_repo_override_still_uses_image_tag(self, app_service):
        """覆盖镜像不带标签时，继续使用配置的 image_tag。"""
        app = self._app(
            "services:\n  app:\n    image: {{ image }}:{{ image_tag }}"
        )
        rendered = app_service._render_app(
            app,
            {
                "image": "linuxserver/qbittorrent",
                "image_tag": "5.3.0_v2.1.2-ls484",
            },
            "demo",
            None,
        )

        assert (
            "image: linuxserver/qbittorrent:5.3.0_v2.1.2-ls484" in rendered
        )

    def test_template_without_split_keeps_full_image(self, app_service):
        """模板只渲染 image 时保留完整镜像引用。"""
        app = self._app("services:\n  app:\n    image: {{ image }}")
        rendered = app_service._render_app(
            app,
            {
                "image": "adguard/adguardhome:v0.107.79",
                "image_tag": "latest",
            },
            "demo",
            None,
        )

        assert "image: adguard/adguardhome:v0.107.79" in rendered


class TestQbittorrentLsioEnv:
    """测试 linuxserver/qbittorrent 环境变量名转换。"""

    @pytest.fixture
    def app_service(self):
        from app.services.app_store.app_service import AppService

        return AppService()

    @staticmethod
    def _app():
        return type(
            "App",
            (),
            {
                "name": "qbittorrent",
                "image": "qbittorrentofficial/qbittorrent-nox",
                "config_schema": {},
                "yaml_template": (
                    "services:\n"
                    "  app:\n"
                    "    image: {{ image }}:{{ image_tag }}\n"
                    "    environment:\n"
                    "{%- for item in qbittorrent_env %}\n"
                    "      - {{ item.key }}={{ item.value }}\n"
                    "{%- endfor %}"
                ),
            },
        )()

    @staticmethod
    def _config(image: str) -> dict:
        return {
            "image": image,
            "image_tag": "latest",
            "qbittorrent_env": [
                {"key": "QBT_LEGAL_NOTICE", "value": "confirm"},
                {"key": "QBT_WEBUI_PORT", "value": "8016"},
                {"key": "QBT_TORRENTING_PORT", "value": "52001"},
                {"key": "TZ", "value": "Asia/Shanghai"},
            ],
        }

    def test_translates_env_for_linuxserver_image(self, app_service):
        """linuxserver 镜像使用 WEBUI_PORT/TORRENTING_PORT。"""
        rendered = app_service._render_app(
            self._app(),
            self._config("linuxserver/qbittorrent:5.3.0"),
            "demo",
            None,
        )

        assert "WEBUI_PORT=8016" in rendered
        assert "TORRENTING_PORT=52001" in rendered
        assert "QBT_WEBUI_PORT" not in rendered
        assert "QBT_TORRENTING_PORT" not in rendered
        assert "QBT_LEGAL_NOTICE" not in rendered
        assert "TZ=Asia/Shanghai" in rendered

    def test_keeps_env_for_official_image(self, app_service):
        """官方镜像保留 QBT_* 变量名。"""
        rendered = app_service._render_app(
            self._app(),
            self._config("qbittorrentofficial/qbittorrent-nox:5.1.0"),
            "demo",
            None,
        )

        assert "QBT_WEBUI_PORT=8016" in rendered
        assert "QBT_TORRENTING_PORT=52001" in rendered
        assert "- WEBUI_PORT=8016" not in rendered

    def test_explicit_alias_wins_over_legacy_key(self, app_service):
        """用户已显式配置新变量时不再追加旧变量。"""
        config = self._config("lscr.io/linuxserver/qbittorrent:latest")
        config["qbittorrent_env"].append(
            {"key": "WEBUI_PORT", "value": "9999"}
        )
        rendered = app_service._render_app(
            self._app(),
            config,
            "demo",
            None,
        )

        assert "WEBUI_PORT=9999" in rendered
        assert "WEBUI_PORT=8016" not in rendered


class TestTemplateImageOverride:
    """测试编排镜像覆盖对写死 image 的模板生效。"""

    @pytest.fixture
    def app_service(self):
        from app.services.app_store.app_service import AppService

        return AppService()

    @staticmethod
    def _app(template: str, image: str):
        return type(
            "App",
            (),
            {
                "name": "jellyfin",
                "image": image,
                "config_schema": {},
                "yaml_template": template,
            },
        )()

    def test_overrides_hardcoded_image(self, app_service):
        """模板未使用 {{ image }} 时替换与应用默认镜像一致的行。"""
        app = self._app(
            "services:\n  jellyfin:\n    image: jellyfin/jellyfin",
            "jellyfin/jellyfin",
        )
        rendered = app_service._render_app(
            app,
            {"image": "jellyfin/jellyfin:12.3"},
            "demo",
            None,
        )

        assert "image: jellyfin/jellyfin:12.3" in rendered

    def test_does_not_touch_dependency_images(self, app_service):
        """只替换主服务默认镜像，不影响模板中的依赖镜像。"""
        app = self._app(
            "services:\n"
            "  main:\n"
            "    image: jxxghp/moviepilot-v2:latest\n"
            "  cache:\n"
            "    image: redis\n",
            "jxxghp/moviepilot-v2:latest",
        )
        rendered = app_service._render_app(
            app,
            {"image": "jxxghp/moviepilot-v3:v3.1.9"},
            "demo",
            None,
        )

        assert "image: jxxghp/moviepilot-v3:v3.1.9" in rendered
        assert "image: redis" in rendered

    def test_placeholder_template_is_left_untouched(self, app_service):
        """已使用 {{ image }} 的模板由渲染上下文处理。"""
        app = self._app(
            "services:\n  app:\n    image: {{ image }}",
            "adguard/adguardhome",
        )
        rendered = app_service._render_app(
            app,
            {"image": "adguard/adguardhome:v0.107.79"},
            "demo",
            None,
        )

        assert "image: adguard/adguardhome:v0.107.79" in rendered
