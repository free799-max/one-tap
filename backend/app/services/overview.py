"""服务概览聚合服务。

扫描已部署的应用实例，按应用分类聚合，并根据端口配置自动生成
可点击的访问地址。返回结果不包含密码等敏感配置。
"""

import asyncio
import queue
import re
import socket
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import APIException
from app.models.app_store import App
from app.models.orchestration import AppInstance, AppOrchestrationInstance
from app.schemas.dashboard import (
    AppMetricsOut,
    AppPortOut,
    OverviewAppDetailOut,
    OverviewAppOut,
    OverviewCategoryOut,
    OverviewOut,
)
from app.services import catalog_sync
from app.services.compose import compose_manager
from app.services.docker import docker, docker_manager

# 分类展示顺序由管理端下发的分类字典决定；未同步时未知分类按名称排序


def _as_int(value: object) -> int | None:
    """把配置中的端口值（可能是字符串）转换为整数。"""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _is_tcp(entry: dict) -> bool:
    return str(entry.get("protocol") or "tcp").lower() == "tcp"


def _default_web_port(app: App) -> int | None:
    """读取应用默认端口中的 Web 主端口。"""
    default_ports = [
        entry
        for entry in (app.default_ports or [])
        if isinstance(entry, dict) and _is_tcp(entry)
    ]
    for entry in default_ports:
        if "web" in str(entry.get("description") or "").lower():
            return _as_int(entry.get("port"))
    if default_ports:
        return _as_int(default_ports[0].get("port"))
    return None


def _url_for_port(host: str, port: int) -> str:
    """拼装访问地址，IPv6 主机自动加方括号。"""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{port}"


def build_app_url(app: App, config: dict, host: str) -> str | None:
    """按应用配置生成访问地址。

    优先使用 ``ports`` 端口数组中 Web 容器端口对应的本地端口；
    其次兼容单端口字段（如 ``moviepilot_port``），最后返回 None。
    """
    config = config or {}
    ports = config.get("ports")
    if isinstance(ports, list) and ports:
        tcp_ports = [
            (entry, _as_int(entry.get("local_port")))
            for entry in ports
            if isinstance(entry, dict) and _is_tcp(entry)
        ]
        tcp_ports = [(entry, port) for entry, port in tcp_ports if port]
        if tcp_ports:
            web_port = _default_web_port(app)
            chosen = None
            if web_port is not None:
                for entry, local_port in tcp_ports:
                    if _as_int(entry.get("container_port")) == web_port:
                        chosen = local_port
                        break
            return _url_for_port(host, chosen or tcp_ports[0][1])

    # 兼容通过独立整数字段分配的端口（与部署时的分配逻辑一致）
    properties = (app.config_schema or {}).get("properties") or {}
    single_ports: list[tuple[str, int]] = []
    for key, prop in properties.items():
        if not isinstance(prop, dict) or prop.get("type") != "integer":
            continue
        if "port" not in key.lower():
            continue
        value = _as_int(config.get(key))
        if value:
            single_ports.append((key, value))
    if single_ports:
        # Web/HTTP 端口优先，其余按字段名稳定排序
        single_ports.sort(
            key=lambda item: (
                0 if ("web" in item[0].lower() or "http" in item[0].lower()) else 1,
                item[0],
            )
        )
        return _url_for_port(host, single_ports[0][1])
    return None


def resolve_app_url(
    app: App,
    instance: AppInstance,
    group: AppOrchestrationInstance | None,
    host: str,
) -> str | None:
    """解析应用访问地址。

    显式配置的 url 最优先；其次按当前请求的主机名和端口自动生成，
    这样从不同地址访问 OneTap 时都能得到可达的跳转链接。
    """
    config = instance.config or {}

    for key in ("url", "access_url"):
        value = str(config.get(key) or "").strip()
        if value:
            return value

    generated = build_app_url(app, config, host)
    if generated:
        return generated

    # 导入的外部容器：已有宿主机端口，直接替换为当前访问主机
    host_port = _as_int(config.get("host_port"))
    if host_port:
        return _url_for_port(host, host_port)

    # 外部容器未映射宿主机端口时，退回容器网络地址
    network_ip = str(config.get("network_ip") or "").strip()
    container_port = _as_int(config.get("container_port"))
    if network_ip and container_port:
        return _url_for_port(network_ip, container_port)

    suggested = str(config.get("suggested_url") or "").strip()
    if suggested:
        return suggested

    # 编排部署时写入实例组的访问地址，作为最后的兜底
    if group is not None:
        entry = (group.app_configs or {}).get(app.name) or {}
        value = str(entry.get("url") or "").strip()
        if value:
            return value
    return None


def _category_of(app: App, instance: AppInstance) -> str:
    """按应用分类归类，缺失时回落到所属编排的分类。"""
    category = (app.category or "").strip()
    if category and category != "other":
        return category
    orchestration = instance.orchestration
    if orchestration is not None and orchestration.category:
        return orchestration.category
    return category or "other"


def normalize_host(value: str | None) -> str:
    """从请求头/地址中提取主机名，去掉端口与协议。"""
    raw = str(value or "").strip()
    if not raw:
        return "localhost"
    candidate = raw.split(",")[0].strip()
    if "://" not in candidate:
        candidate = f"//{candidate}"
    try:
        hostname = urlsplit(candidate).hostname
    except ValueError:
        return "localhost"
    return hostname or "localhost"


def is_loopback_host(host: str) -> bool:
    """判断主机名是否只能从本机访问。"""
    value = host.strip("[]").lower()
    if value in {"localhost", "localhost.localdomain", "0.0.0.0", "::", "::1"}:
        return True
    return value.startswith("127.")


def detect_host() -> str:
    """推断宿主机对内提供访问的地址。

    通过默认路由探测本机内网 IP，探测失败时回退到 localhost。
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(0.5)
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "localhost"


def resolve_host(value: str | None) -> str:
    """解析生成跳转链接所用的主机名。

    通过 localhost / 127.0.0.1 访问时，该主机名在其他设备上不可达，
    此时改用宿主机内网地址。
    """
    host = normalize_host(value)
    if is_loopback_host(host):
        detected = detect_host()
        if not is_loopback_host(detected):
            return detected
    return host


@dataclass
class DockerSnapshot:
    """一次 Docker 容器扫描结果，用于校验应用是否仍然存在。"""

    available: bool
    project_status: dict[str, str] = field(default_factory=dict)
    container_status: dict[str, str] = field(default_factory=dict)


def _container_status(state: str) -> str:
    """把 Docker 容器状态归一化为概览使用的 running/stopped。"""
    if state in {"running", "restarting"}:
        return "running"
    if state in {"exited", "dead", "paused", "created"}:
        return "stopped"
    return state


def _load_docker_snapshot_sync() -> DockerSnapshot:
    """同步扫描所有容器，按 Compose 项目和容器标识记录运行状态。"""
    client = docker_manager._client
    if client is None:
        return DockerSnapshot(available=False)
    try:
        containers = client.containers.list(all=True)
    except Exception:
        return DockerSnapshot(available=False)

    project_status: dict[str, str] = {}
    container_status: dict[str, str] = {}
    for container in containers:
        try:
            attrs = container.attrs or {}
            state = (attrs.get("State") or {}).get("Status", container.status)
            status = _container_status(state)
            labels = (attrs.get("Config") or {}).get("Labels") or {}
            project_name = labels.get("com.docker.compose.project") or labels.get(
                "onetap.compose.project"
            )
            if project_name:
                # 一个项目可能有多个容器，只要有一个在运行就视为运行中
                if project_name not in project_status or status == "running":
                    project_status[project_name] = status
            container_status[container.name] = status
            container_status[container.id[:12]] = status
            short_id = getattr(container, "short_id", None)
            if short_id:
                container_status[short_id] = status
        except Exception:
            continue
    return DockerSnapshot(
        available=True,
        project_status=project_status,
        container_status=container_status,
    )


async def load_docker_snapshot() -> DockerSnapshot:
    """在线程池中扫描 Docker 容器，避免阻塞事件循环。"""
    return await asyncio.to_thread(_load_docker_snapshot_sync)


def _resolve_instance_status(
    instance: AppInstance,
    snapshot: DockerSnapshot,
) -> str | None:
    """返回实例的实际运行状态；应用已不存在时返回 None。"""
    config = instance.config or {}

    # 平台部署的应用：按 Compose 项目容器判断是否仍然存在
    if instance.project_id:
        project = instance.project
        if project is None:
            return None
        if snapshot.available:
            status = snapshot.project_status.get(project.project_name)
            # compose down 后容器会被移除，但应用只是停止运行，
            # 仍需在概览中展示，便于用户再次启动或查看配置
            return status or "stopped"
        return instance.status

    # 导入的外部容器：按容器 ID / 名称判断
    refs = [
        str(value)
        for value in (config.get("container_id"), config.get("container_name"))
        if value
    ]
    if refs:
        if snapshot.available:
            for ref in refs:
                status = snapshot.container_status.get(ref)
                if status is not None:
                    return status
            return None
        return instance.status

    # 既没有关联项目也没有容器信息，说明是失败/未完成的部署记录
    if snapshot.available:
        return None
    if instance.status in {"error", "deploying"}:
        return None
    return instance.status


def _build_overview_app(
    instance: AppInstance,
    status: str,
    host: str,
) -> OverviewAppOut:
    """把应用实例转换为概览卡片数据。"""
    app = instance.app
    assert app is not None
    category = _category_of(app, instance)
    # 编排应用由实例组整体升级，单卡片不提供升级入口
    upgrade_supported = bool(
        instance.orchestration_group_id is None
        and instance.project_id
        and not (instance.config or {}).get("imported")
    )
    return OverviewAppOut(
        id=instance.id,
        app_name=app.name,
        display_name=app.display_name,
        description=app.description,
        icon=app.icon,
        category=category,
        status=status,
        url=resolve_app_url(app, instance, instance.orchestration_group, host),
        instance_name=instance.instance_name,
        version=instance.orchestration_version,
        latest_version=app.version,
        upgrade_supported=upgrade_supported,
        update_available=(
            upgrade_supported
            and bool(instance.orchestration_version)
            and instance.orchestration_version != app.version
        ),
    )


def _to_int(value: object) -> int:
    """安全地把 Docker 统计中的数值转为整数。"""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _parse_docker_time(value: str | None) -> datetime | None:
    """解析 Docker 时间戳；纳秒精度需要截断到微秒。"""
    raw = str(value or "").strip()
    if not raw or raw.startswith("0001-"):
        return None
    raw = re.sub(r"(\.\d{6})\d+", r"\1", raw)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _find_container_sync(instance: AppInstance):
    """根据实例关联的 Compose 项目或导入容器信息找到 Docker 容器。"""
    client = docker_manager._client
    if client is None:
        return None, "Docker 不可用，无法获取容器信息"

    config = instance.config or {}
    project = instance.project
    if project is not None:
        project_name = project.project_name
        try:
            containers = client.containers.list(
                all=True,
                filters={"label": f"com.docker.compose.project={project_name}"},
            )
        except Exception as exc:
            return None, f"查询容器失败: {exc}"
        if not containers:
            return None, "未找到该应用的容器"

        expected = {project_name}
        if config.get("container_name"):
            expected.add(str(config["container_name"]))
        for container in containers:
            if container.name in expected:
                return container, None
        for container in containers:
            if container.status == "running":
                return container, None
        return containers[0], None

    for key in ("container_id", "container_name"):
        ref = str(config.get(key) or "").strip()
        if not ref:
            continue
        try:
            return client.containers.get(ref), None
        except docker.errors.NotFound:
            continue
        except Exception as exc:
            return None, f"查询容器失败: {exc}"
    return None, "未找到该应用的容器"


def _collect_metrics(container) -> AppMetricsOut:
    """读取一次 Docker stats 快照并计算 CPU/内存/网络/磁盘指标。"""
    stats = container.stats(stream=False) or {}
    cpu_stats = stats.get("cpu_stats") or {}
    pre_stats = stats.get("precpu_stats") or {}
    cpu_usage = cpu_stats.get("cpu_usage") or {}
    pre_cpu_usage = pre_stats.get("cpu_usage") or {}
    cpu_delta = _to_int(cpu_usage.get("total_usage")) - _to_int(
        pre_cpu_usage.get("total_usage")
    )
    system_delta = _to_int(cpu_stats.get("system_cpu_usage")) - _to_int(
        pre_stats.get("system_cpu_usage")
    )
    online_cpus = _to_int(cpu_stats.get("online_cpus")) or len(
        cpu_usage.get("percpu_usage") or []
    )
    online_cpus = online_cpus or 1
    cpu_percent = 0.0
    if cpu_delta > 0 and system_delta > 0:
        cpu_percent = round(cpu_delta / system_delta * online_cpus * 100, 2)

    memory = stats.get("memory_stats") or {}
    memory_usage = _to_int(memory.get("usage"))
    memory_limit = _to_int(memory.get("limit"))
    memory_detail = memory.get("stats") or {}
    cache = _to_int(memory_detail.get("cache")) or _to_int(
        memory_detail.get("inactive_file")
    )
    memory_used = max(memory_usage - cache, 0)
    memory_percent = (
        round(memory_used / memory_limit * 100, 1) if memory_limit else 0.0
    )

    networks = stats.get("networks") or {}
    network_rx = sum(
        _to_int(value.get("rx_bytes"))
        for value in networks.values()
        if isinstance(value, dict)
    )
    network_tx = sum(
        _to_int(value.get("tx_bytes"))
        for value in networks.values()
        if isinstance(value, dict)
    )

    blkio_entries = (stats.get("blkio_stats") or {}).get(
        "io_service_bytes_recursive"
    ) or []
    block_read = sum(
        _to_int(entry.get("value"))
        for entry in blkio_entries
        if isinstance(entry, dict) and entry.get("op") == "Read"
    )
    block_write = sum(
        _to_int(entry.get("value"))
        for entry in blkio_entries
        if isinstance(entry, dict) and entry.get("op") == "Write"
    )

    return AppMetricsOut(
        cpu_percent=cpu_percent,
        memory_used=memory_used,
        memory_limit=memory_limit,
        memory_percent=memory_percent,
        network_rx=network_rx,
        network_tx=network_tx,
        block_read=block_read,
        block_write=block_write,
        pids=_to_int((stats.get("pids_stats") or {}).get("current")),
    )


def _collect_container_info(container) -> dict:
    """读取容器基本信息和端口映射。"""
    try:
        container.reload()
    except Exception:
        pass

    attrs = container.attrs or {}
    config = attrs.get("Config") or {}
    state = attrs.get("State") or {}
    host_config = attrs.get("HostConfig") or {}
    network_settings = attrs.get("NetworkSettings") or {}
    labels = config.get("Labels") or {}
    project_name = labels.get("com.docker.compose.project") or labels.get(
        "onetap.compose.project"
    )

    image = config.get("Image")
    if not image:
        try:
            tags = container.image.tags
            image = tags[0] if tags else None
        except Exception:
            image = None

    ports: list[AppPortOut] = []
    port_bindings = network_settings.get("Ports") or host_config.get(
        "PortBindings"
    ) or {}
    seen_ports: set[tuple[str, str | None]] = set()
    for container_port, bindings in port_bindings.items():
        if not bindings:
            key = (container_port, None)
            if key not in seen_ports:
                seen_ports.add(key)
                ports.append(AppPortOut(container_port=container_port))
            continue
        for binding in bindings:
            key = (container_port, binding.get("HostPort"))
            if key in seen_ports:
                continue
            seen_ports.add(key)
            ports.append(
                AppPortOut(
                    container_port=container_port,
                    host_ip=binding.get("HostIp"),
                    host_port=binding.get("HostPort"),
                )
            )

    started = _parse_docker_time(state.get("StartedAt"))
    status = state.get("Status", container.status)
    uptime = None
    if started and status in {"running", "restarting"}:
        uptime = max(
            int((datetime.now(timezone.utc) - started).total_seconds()), 0
        )

    return {
        "image": image,
        "container_id": container.id[:12],
        "container_name": container.name,
        "project_name": project_name,
        "restart_policy": (host_config.get("RestartPolicy") or {}).get("Name"),
        "started_at": started.isoformat() if started else None,
        "uptime_seconds": uptime,
        "ports": ports,
    }


class OverviewService:
    """生成服务概览数据。"""

    async def get_overview(
        self,
        db: AsyncSession,
        host: str | None = None,
        docker_snapshot: DockerSnapshot | None = None,
    ) -> OverviewOut:
        """返回按分类聚合的已部署应用及自动生成的访问地址。"""
        host = resolve_host(host)
        snapshot = docker_snapshot or await load_docker_snapshot()

        result = await db.execute(
            select(AppInstance)
            .where(AppInstance.app_id.is_not(None))
            .options(
                selectinload(AppInstance.app),
                selectinload(AppInstance.orchestration),
                selectinload(AppInstance.orchestration_group),
                selectinload(AppInstance.project),
            )
            .order_by(AppInstance.created_at.desc(), AppInstance.id.desc())
        )
        instances = list(result.scalars().all())

        grouped: dict[str, list[OverviewAppOut]] = defaultdict(list)
        running = 0
        for instance in instances:
            app = instance.app
            if app is None:
                continue
            status = _resolve_instance_status(instance, snapshot)
            if status is None:
                continue
            if status == "running":
                running += 1
            category = _category_of(app, instance)
            grouped[category].append(_build_overview_app(instance, status, host))

        rank = catalog_sync.get_category_rank()
        unknown_rank = len(rank)
        categories = [
            OverviewCategoryOut(category=category, apps=apps)
            for category, apps in sorted(
                grouped.items(),
                key=lambda item: (rank.get(item[0], unknown_rank), item[0]),
            )
        ]
        return OverviewOut(
            categories=categories,
            total=sum(len(category.apps) for category in categories),
            running=running,
        )

    async def get_app_detail(
        self,
        db: AsyncSession,
        instance_id: int,
        host: str | None = None,
    ) -> OverviewAppDetailOut:
        """返回单个应用的基本信息、容器信息和实时监控指标。"""
        host = resolve_host(host)
        result = await db.execute(
            select(AppInstance)
            .where(AppInstance.id == instance_id)
            .options(
                selectinload(AppInstance.app),
                selectinload(AppInstance.orchestration),
                selectinload(AppInstance.orchestration_group),
                selectinload(AppInstance.project),
            )
        )
        instance = result.scalar_one_or_none()
        if instance is None or instance.app is None:
            raise APIException("应用实例不存在", 404)

        snapshot = await load_docker_snapshot()
        status = _resolve_instance_status(instance, snapshot) or instance.status
        app_out = _build_overview_app(instance, status, host)

        container, error = await asyncio.to_thread(_find_container_sync, instance)
        if container is None:
            # 容器可能已被 compose down 移除，应用本身仍在，返回项目信息供再次启动
            return OverviewAppDetailOut(
                app=app_out,
                project_id=instance.project_id,
                project_name=(
                    instance.project.project_name if instance.project else None
                ),
                error=error,
            )

        def _collect():
            info = _collect_container_info(container)
            state = (container.attrs or {}).get("State") or {}
            metrics = None
            if state.get("Status", container.status) in {"running", "restarting"}:
                try:
                    metrics = _collect_metrics(container)
                except Exception:
                    metrics = None
            return info, metrics

        try:
            info, metrics = await asyncio.to_thread(_collect)
        except Exception as exc:
            return OverviewAppDetailOut(
                app=app_out,
                project_id=instance.project_id,
                error=f"获取容器信息失败: {exc}",
            )
        return OverviewAppDetailOut(
            app=app_out, project_id=instance.project_id, metrics=metrics, **info
        )

    async def open_log_stream(
        self,
        db: AsyncSession,
        instance_id: int,
        tail: int = 200,
        follow: bool = True,
    ):
        """解析应用实例的日志来源并返回异步迭代器。

        有 Compose 项目时走 ``docker compose logs``（覆盖项目内全部服务）；
        导入的外部容器没有 Compose 项目，回退到容器日志。
        数据库会话在响应开始前已用毕，迭代过程只读 Docker。
        """
        result = await db.execute(
            select(AppInstance)
            .where(AppInstance.id == instance_id)
            .options(selectinload(AppInstance.project))
        )
        instance = result.scalar_one_or_none()
        if instance is None:
            raise APIException("应用实例不存在", 404)

        if instance.project is not None:
            project = instance.project
            return compose_manager.stream_logs(
                project, tail=tail, follow=follow
            )

        container, error = await asyncio.to_thread(_find_container_sync, instance)
        if container is None:
            raise APIException(error or "未找到该应用的容器", 404)
        return self._stream_container_logs(container.id, tail=tail, follow=follow)

    async def _stream_container_logs(
        self,
        container_id: str,
        tail: int = 200,
        follow: bool = True,
    ):
        """把同步的容器日志生成器转成异步迭代器。

        Docker SDK 的日志流是同步阻塞生成器，放到线程里读、经队列转交事件循环；
        超出队列容量时反压线程，避免日志刷屏时内存膨胀。
        """
        log_queue: queue.Queue = queue.Queue(maxsize=500)
        stop_event = threading.Event()

        def _pump() -> None:
            try:
                for line in docker_manager.stream_container_logs(
                    container_id, tail=tail, follow=follow, timestamps=False
                ):
                    if stop_event.is_set():
                        break
                    log_queue.put(line)
            except Exception as exc:  # noqa: BLE001 — 错误转交主协程统一处理
                log_queue.put(exc)
            finally:
                log_queue.put(None)

        threading.Thread(target=_pump, daemon=True).start()
        try:
            while True:
                try:
                    item = await asyncio.to_thread(log_queue.get, True, 1.0)
                except queue.Empty:
                    continue
                if item is None:
                    break
                if isinstance(item, Exception):
                    raise APIException(f"日志流异常: {item}", 500)
                yield item
        finally:
            stop_event.set()


overview_service = OverviewService()
