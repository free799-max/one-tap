"""服务概览聚合与访问地址生成测试。"""

from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — 注册所有模型到 Base.metadata
from app.database import Base
from app.models.app_store import App
from app.models.orchestration import AppInstance
from app.services.overview import (
    DockerSnapshot,
    _collect_container_info,
    _collect_metrics,
    build_app_url,
    is_loopback_host,
    normalize_host,
    overview_service,
    resolve_app_url,
    resolve_host,
)


def _make_app(
    name: str = "jellyfin",
    category: str = "movie",
    default_ports: list[dict] | None = None,
    config_schema: dict | None = None,
) -> App:
    return App(
        name=name,
        display_name=name.title(),
        description="测试应用",
        category=category,
        tags=[],
        icon=None,
        default_ports=default_ports
        or [{"port": 8096, "protocol": "tcp", "description": "Web UI"}],
        config_schema=config_schema or {},
        yaml_template="services:\n  app:\n    image: test:latest",
        version="1.0.0",
        type="compose",
        backup_paths=[],
    )


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:
        yield session
    await engine.dispose()


class FakeContainer:
    """模拟 Docker 容器对象，供详情与监控指标测试使用。"""

    id = "abcdef1234567890"
    name = "music-navidrome"
    status = "running"

    def __init__(self) -> None:
        self.attrs = {
            "Config": {
                "Image": "deluan/navidrome:0.64.2",
                "Labels": {"com.docker.compose.project": "music-navidrome"},
            },
            "State": {
                "Status": "running",
                "StartedAt": "2026-10-24T07:53:04.058227123Z",
            },
            "HostConfig": {
                "RestartPolicy": {"Name": "unless-stopped"},
                "PortBindings": {
                    "4533/tcp": [{"HostIp": "0.0.0.0", "HostPort": "9003"}]
                },
            },
            "NetworkSettings": {"Ports": {}},
        }

    def reload(self) -> None:
        return None

    def stats(self, stream: bool = False) -> dict:
        return {
            "cpu_stats": {
                "cpu_usage": {"total_usage": 200, "percpu_usage": [1, 2]},
                "system_cpu_usage": 2000,
                "online_cpus": 2,
            },
            "precpu_stats": {
                "cpu_usage": {"total_usage": 100, "percpu_usage": [1, 2]},
                "system_cpu_usage": 1000,
            },
            "memory_stats": {
                "usage": 100 * 1024 * 1024,
                "limit": 1024 * 1024 * 1024,
                "stats": {"cache": 20 * 1024 * 1024},
            },
            "networks": {
                "eth0": {"rx_bytes": 1000, "tx_bytes": 2000},
                "eth1": {"rx_bytes": 500, "tx_bytes": 600},
            },
            "blkio_stats": {
                "io_service_bytes_recursive": [
                    {"op": "Read", "value": 3000},
                    {"op": "Write", "value": 4000},
                ]
            },
            "pids_stats": {"current": 15},
        }


def test_collect_metrics_calculates_usage():
    metrics = _collect_metrics(FakeContainer())
    assert metrics.cpu_percent == 20.0
    assert metrics.memory_used == 80 * 1024 * 1024
    assert metrics.memory_limit == 1024 * 1024 * 1024
    assert metrics.memory_percent == 7.8
    assert metrics.network_rx == 1500
    assert metrics.network_tx == 2600
    assert metrics.block_read == 3000
    assert metrics.block_write == 4000
    assert metrics.pids == 15


def test_collect_container_info_falls_back_to_port_bindings():
    info = _collect_container_info(FakeContainer())
    assert info["image"] == "deluan/navidrome:0.64.2"
    assert info["container_name"] == "music-navidrome"
    assert info["container_id"] == "abcdef123456"
    assert info["project_name"] == "music-navidrome"
    assert info["restart_policy"] == "unless-stopped"
    assert info["ports"][0].host_port == "9003"
    assert info["ports"][0].container_port == "4533/tcp"
    assert info["uptime_seconds"] is not None


class TestBuildAppUrl:
    def test_prefers_web_container_port(self):
        app = _make_app()
        config = {
            "ports": [
                {"container_port": 7359, "local_port": 10002, "protocol": "tcp"},
                {"container_port": 8096, "local_port": 10001, "protocol": "tcp"},
            ]
        }
        assert build_app_url(app, config, "192.168.1.10") == (
            "http://192.168.1.10:10001"
        )

    def test_falls_back_to_first_tcp_port(self):
        app = _make_app(default_ports=[{"port": 9999, "protocol": "tcp"}])
        config = {
            "ports": [
                {"container_port": 9999, "local_port": 10010, "protocol": "tcp"}
            ]
        }
        assert build_app_url(app, config, "nas.local") == "http://nas.local:10010"

    def test_single_port_field(self):
        app = _make_app(
            name="moviepilot",
            config_schema={"properties": {"moviepilot_port": {"type": "integer"}}},
            default_ports=[],
        )
        assert build_app_url(app, {"moviepilot_port": 10020}, "nas.local") == (
            "http://nas.local:10020"
        )

    def test_returns_none_without_ports(self):
        app = _make_app(default_ports=[])
        assert build_app_url(app, {}, "nas.local") is None


class TestResolveAppUrl:
    def test_explicit_url_wins(self):
        app = _make_app()
        instance = AppInstance(
            instance_name="jellyfin-manual",
            config={"url": "https://media.example.com"},
            status="running",
        )
        assert resolve_app_url(app, instance, None, "nas.local") == (
            "https://media.example.com"
        )

    def test_imported_container_host_port_uses_current_host(self):
        app = _make_app()
        instance = AppInstance(
            instance_name="jellyfin-imported",
            config={"host_port": 18096, "container_port": 8096},
            status="running",
        )
        assert resolve_app_url(app, instance, None, "192.168.1.20") == (
            "http://192.168.1.20:18096"
        )

    def test_group_url_is_last_resort(self):
        app = _make_app(default_ports=[])
        instance = AppInstance(
            instance_name="jellyfin-group",
            config={},
            status="running",
        )
        group = SimpleNamespace(app_configs={"jellyfin": {"url": "http://old:8096"}})
        assert resolve_app_url(app, instance, group, "nas.local") == (
            "http://old:8096"
        )


class TestOverviewService:
    @pytest.mark.asyncio
    async def test_groups_by_category_and_builds_urls(
        self, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ):
        from app.services import catalog_sync

        monkeypatch.setattr(
            catalog_sync,
            "categories",
            [
                {"value": "movie", "label": "影视", "order": 0},
                {"value": "comic", "label": "漫画", "order": 1},
            ],
        )
        jellyfin = _make_app(name="jellyfin", category="movie")
        komga = _make_app(
            name="komga",
            category="comic",
            default_ports=[{"port": 25600, "protocol": "tcp", "description": "Web"}],
        )
        db_session.add_all([jellyfin, komga])
        await db_session.flush()

        db_session.add_all(
            [
                AppInstance(
                    app_id=jellyfin.id,
                    instance_name="media-jellyfin",
                    config={
                        "ports": [
                            {
                                "container_port": 8096,
                                "local_port": 10001,
                                "protocol": "tcp",
                            }
                        ]
                    },
                    status="running",
                ),
                AppInstance(
                    app_id=komga.id,
                    instance_name="comic-komga",
                    config={
                        "ports": [
                            {
                                "container_port": 25600,
                                "local_port": 10002,
                                "protocol": "tcp",
                            }
                        ]
                    },
                    status="stopped",
                ),
            ]
        )
        await db_session.commit()

        overview = await overview_service.get_overview(
            db_session,
            host="192.168.1.10:5000",
            docker_snapshot=DockerSnapshot(available=False),
        )

        assert overview.total == 2
        assert overview.running == 1
        assert [category.category for category in overview.categories] == [
            "movie",
            "comic",
        ]
        assert overview.categories[0].apps[0].url == "http://192.168.1.10:10001"
        assert overview.categories[0].apps[0].status == "running"
        assert overview.categories[1].apps[0].url == "http://192.168.1.10:10002"

    @pytest.mark.asyncio
    async def test_unknown_category_sorted_last(
        self, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ):
        from app.services import catalog_sync

        monkeypatch.setattr(
            catalog_sync,
            "categories",
            [{"value": "tool", "label": "工具", "order": 0}],
        )
        tool = _make_app(name="memos", category="tool", default_ports=[])
        other = _make_app(name="custom", category="custom", default_ports=[])
        db_session.add_all([tool, other])
        await db_session.flush()
        db_session.add_all(
            [
                AppInstance(app_id=tool.id, instance_name="memos", config={}),
                AppInstance(app_id=other.id, instance_name="custom", config={}),
            ]
        )
        await db_session.commit()

        overview = await overview_service.get_overview(
            db_session,
            host="nas.local",
            docker_snapshot=DockerSnapshot(available=False),
        )
        assert [category.category for category in overview.categories] == [
            "tool",
            "custom",
        ]

    @pytest.mark.asyncio
    async def test_hides_failed_instances_without_deployment(self, db_session: AsyncSession):
        app = _make_app(name="moviepilot", default_ports=[])
        db_session.add(app)
        await db_session.flush()
        db_session.add(
            AppInstance(
                app_id=app.id,
                instance_name="moviepilot",
                config={"ports": [{"container_port": 3000, "local_port": 3002}]},
                status="error",
            )
        )
        await db_session.commit()

        overview = await overview_service.get_overview(
            db_session,
            host="nas.local",
            docker_snapshot=DockerSnapshot(available=False),
        )
        assert overview.total == 0
        assert overview.categories == []

    @pytest.mark.asyncio
    async def test_uses_actual_project_container_status(self, db_session: AsyncSession):
        from app.models.docker import DockerComposeProject

        app = _make_app(default_ports=[])
        project = DockerComposeProject(project_name="media-jellyfin")
        db_session.add_all([app, project])
        await db_session.flush()
        db_session.add(
            AppInstance(
                app_id=app.id,
                project_id=project.id,
                instance_name="media-jellyfin",
                config={},
                status="running",
            )
        )
        await db_session.commit()

        overview = await overview_service.get_overview(
            db_session,
            host="nas.local",
            docker_snapshot=DockerSnapshot(
                available=True,
                project_status={"media-jellyfin": "stopped"},
            ),
        )
        assert overview.total == 1
        assert overview.running == 0
        assert overview.categories[0].apps[0].status == "stopped"

        stopped = await overview_service.get_overview(
            db_session,
            host="nas.local",
            docker_snapshot=DockerSnapshot(available=True),
        )
        # compose down 会移除容器，但应用记录仍在，应展示为已停止而不是消失
        assert stopped.total == 1
        assert stopped.running == 0
        assert stopped.categories[0].apps[0].status == "stopped"

    def test_normalize_host_strips_scheme_and_port(self):
        assert normalize_host("http://192.168.1.10:5000") == "192.168.1.10"
        assert normalize_host("nas.local:5000") == "nas.local"
        assert normalize_host("[::1]:5000") == "::1"
        assert normalize_host("") == "localhost"


class TestResolveHost:
    def test_loopback_hosts_are_detected(self):
        for host in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
            assert is_loopback_host(host)
        assert not is_loopback_host("192.168.1.10")
        assert not is_loopback_host("nas.local")

    def test_loopback_is_replaced_with_lan_address(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.overview.detect_host", lambda: "192.168.1.50"
        )
        assert resolve_host("localhost:5000") == "192.168.1.50"
        assert resolve_host("127.0.0.1") == "192.168.1.50"
        assert resolve_host("[::1]:5000") == "192.168.1.50"

    def test_lan_host_is_kept(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.overview.detect_host", lambda: "10.0.0.1"
        )
        assert resolve_host("192.168.1.10:5000") == "192.168.1.10"
        assert resolve_host("nas.local") == "nas.local"

    def test_unresolvable_loopback_stays_localhost(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.overview.detect_host", lambda: "localhost"
        )
        assert resolve_host("localhost") == "localhost"
