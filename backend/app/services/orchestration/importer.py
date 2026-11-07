"""应用编排导入能力（扫描运行中容器并导入为编排实例）。"""

import asyncio
import logging

import docker
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import APIException
from app.models.app_store import App
from app.models.orchestration import (
    AppInstance,
    AppOrchestrationInstance,
)
from app.schemas.orchestration import (
    ContainerMatch,
    ImportCandidateApp,
)
from app.services.docker import docker_manager
from app.services.docker.host_network import detect_host_address

logger = logging.getLogger(__name__)


class ImportMixin:
    """已有 Docker 部署的扫描与导入能力。"""

    async def scan_import_candidates(
        self,
        db: AsyncSession,
        orchestration_name: str,
    ) -> list[ImportCandidateApp]:
        """扫描运行中的 Docker 容器，返回可导入的应用候选列表。

        匹配规则：容器镜像（去除 tag）包含应用商店 App 的镜像名（去除 tag）时视为命中。
        只返回 running 状态的容器，减少无关噪声。
        """
        db_orchestration = await self.get_orchestration(db, orchestration_name)
        composition = db_orchestration.app_composition or []

        app_names = [item.get("app_name") for item in composition if item.get("app_name")]
        app_map = await self._load_apps(db, app_names)

        containers = await asyncio.to_thread(self._list_running_containers)

        candidates_by_app: dict[str, list[ContainerMatch]] = {
            name: [] for name in app_names
        }
        for container in containers:
            container_id = container.id[:12]
            container_name = container.name
            image = str(container.image.tags[0]) if container.image.tags else "unknown"
            image_base = image.split(":")[0].lower()

            network_ip, container_port, host_port = self._extract_network_info(container)
            suggested_url = self._build_suggested_url(
                network_ip=network_ip,
                host_port=host_port,
                container_port=container_port,
            )

            for item in composition:
                app_name = item.get("app_name")
                app = app_map.get(app_name)
                if not app or not app.image:
                    continue
                app_image_base = app.image.split(":")[0].lower()
                if app_image_base and app_image_base in image_base:
                    candidates_by_app[app_name].append(
                        ContainerMatch(
                            container_id=container_id,
                            container_name=container_name,
                            image=image,
                            network_ip=network_ip,
                            host_port=host_port,
                            container_port=container_port,
                            suggested_url=suggested_url,
                        )
                    )

        result: list[ImportCandidateApp] = []
        for item in composition:
            app_name = item.get("app_name")
            app = app_map.get(app_name)
            candidates = candidates_by_app.get(app_name, [])
            result.append(
                ImportCandidateApp(
                    app_name=app_name,
                    display_name=app.display_name if app else app_name,
                    icon=app.icon if app else None,
                    relation=item.get("relation", "optional"),
                    group=item.get("group"),
                    matched=len(candidates) > 0,
                    candidates=candidates,
                )
            )
        return result

    async def import_orchestration(
        self,
        db: AsyncSession,
        orchestration_name: str,
        instance_name: str,
        selected_apps: list[str],
        app_configs: dict[str, dict],
        shared_config: dict,
        user_id: int | None = None,
    ) -> tuple[AppOrchestrationInstance, list[int]]:
        """导入已有 Docker 部署为编排实例。

        创建 AppOrchestrationInstance 记录，并为每个选中的应用创建 AppInstance，
        将容器运行时信息写入 AppInstance.config。返回组合部署记录及创建的应用实例 ID 列表。
        """
        db_orchestration = await self.get_orchestration(db, orchestration_name)
        composition = db_orchestration.app_composition or []

        self._validate_composition(
            composition=composition,
            selected_apps=selected_apps,
        )

        app_map = await self._load_apps(db, selected_apps)
        missing = [name for name in selected_apps if name not in app_map]
        if missing:
            raise APIException(f"应用不存在: {', '.join(missing)}", 400)

        # 重新扫描一次容器，获取选中应用对应的运行时信息
        candidates = await self.scan_import_candidates(db, orchestration_name)
        candidate_map = {item.app_name: item for item in candidates}

        group = AppOrchestrationInstance(
            orchestration_id=db_orchestration.id,
            instance_name=instance_name,
            shared_config=shared_config or {},
            app_configs=app_configs or {},
            status="running",
            # 导入时记录方案模板版本快照
            orchestration_version=db_orchestration.version,
        )
        db.add(group)
        await db.flush()
        await db.refresh(group)

        created_instance_ids: list[int] = []
        for app_name in selected_apps:
            app = app_map[app_name]
            candidate = candidate_map.get(app_name)
            app_config = app_configs.get(app_name) or {}
            selected_container_id = app_config.get("selected_container_id")

            selected_container = None
            if candidate and candidate.candidates:
                if selected_container_id:
                    selected_container = next(
                        (c for c in candidate.candidates if c.container_id == selected_container_id),
                        None,
                    )
                if selected_container is None:
                    selected_container = candidate.candidates[0]

            runtime_config: dict = {
                "imported": True,
                "source": "docker_container",
            }
            if selected_container:
                runtime_config.update({
                    "container_id": selected_container.container_id,
                    "container_name": selected_container.container_name,
                    "container_image": selected_container.image,
                    "network_ip": selected_container.network_ip,
                    "host_port": selected_container.host_port,
                    "container_port": selected_container.container_port,
                    "suggested_url": selected_container.suggested_url,
                })

            # 将用户填写的认证信息也合并到实例配置，方便后续统一读取
            runtime_config.update(app_config)

            instance = AppInstance(
                app_id=app.id,
                orchestration_id=db_orchestration.id,
                orchestration_group_id=group.id,
                instance_name=f"{instance_name}-{app_name}",
                config=runtime_config,
                orchestration_version=db_orchestration.version,
                status="running",
            )
            db.add(instance)
            await db.flush()
            await db.refresh(instance)
            created_instance_ids.append(instance.id)

        await db.commit()
        return group, created_instance_ids

    def _list_running_containers(self) -> list:
        """列出当前运行中的 Docker 容器，Docker 不可用时返回空列表。"""
        client = docker_manager._client
        if client is None:
            return []
        try:
            return client.containers.list(all=False)
        except docker.errors.DockerException:
            return []
        except Exception:
            logger.exception("列出运行中容器失败")
            return []

    def _extract_network_info(
        self,
        container,
    ) -> tuple[str | None, str | None, int | None]:
        """从容器的 attrs 中提取网络 IP、容器端口和宿主机端口。"""
        attrs = container.attrs or {}
        network_settings = attrs.get("NetworkSettings", {}) or {}
        host_config = attrs.get("HostConfig", {}) or {}

        # 1. 网络 IP：优先取第一个非空 IPAddress
        network_ip = None
        networks = network_settings.get("Networks") or {}
        for net_info in networks.values():
            ip = net_info.get("IPAddress")
            if ip:
                network_ip = ip
                break

        # 2. 容器端口：从 Config.ExposedPorts 或 PortBindings 推断主端口
        container_port = None
        exposed_ports = (attrs.get("Config", {}) or {}).get("ExposedPorts") or {}
        if exposed_ports:
            # 取第一个 TCP 端口，如 "8096/tcp"
            for port_key in exposed_ports.keys():
                if "/tcp" in port_key:
                    container_port = port_key.replace("/tcp", "")
                    break

        # 3. 宿主机端口：从 PortBindings 找主端口
        host_port = None
        port_bindings = network_settings.get("Ports") or {}
        for port_key, bindings in port_bindings.items():
            if isinstance(bindings, list) and bindings:
                hp = bindings[0].get("HostPort")
                if hp:
                    try:
                        host_port = int(hp)
                    except ValueError:
                        pass
                    if "/tcp" in port_key:
                        container_port = port_key.replace("/tcp", "")
                    break

        # 兜底：host network 模式下从 /proc/net/tcp 读取监听端口
        if host_port is None and host_config.get("NetworkMode") == "host":
            host_port = self._guess_host_network_port(container)
            if host_port and container_port is None:
                container_port = str(host_port)

        return network_ip, container_port, host_port

    def _guess_host_network_port(self, container) -> int | None:
        """host network 模式下，尝试从容器内 /proc/net/tcp 读取 LISTEN 端口。"""
        try:
            result = container.exec_run("cat /proc/net/tcp")
            output = result.output.decode("utf-8", errors="replace")
            listening: set[int] = set()
            for line in output.splitlines()[1:]:
                parts = line.strip().split()
                if len(parts) < 4:
                    continue
                if parts[3] != "0A":
                    continue
                local_addr = parts[1]
                if ":" not in local_addr:
                    continue
                _, port_hex = local_addr.rsplit(":", 1)
                try:
                    listening.add(int(port_hex, 16))
                except ValueError:
                    continue
            # 排除常见系统端口，取最小的一个作为候选
            for excluded in {22, 80, 443}:
                listening.discard(excluded)
            return min(listening) if listening else None
        except Exception:
            return None

    def _get_host_ip(self) -> str:
        """获取宿主机在内网中的本地 IP，失败时回退到 localhost。"""
        return detect_host_address(docker_manager._client)

    def _build_access_url(
        self, app: App, config: dict, host: str | None = None
    ) -> str | None:
        """根据应用的 Web 端口定义与实际分配的本地端口生成访问地址。

        优先匹配 App.default_ports 中描述含 "Web" 的容器端口对应的本地端口，
        其次取应用第一个 TCP 默认端口，最后回退到配置里的第一个 TCP 映射。
        外部服务（type=external）直接使用用户填写的 ``url``。
        """
        if str(getattr(app, "type", "") or "").lower() == "external":
            return str(config.get("url") or "").strip() or None
        ports = config.get("ports") or []
        tcp_ports = [
            p
            for p in ports
            if isinstance(p, dict) and p.get("protocol", "tcp") == "tcp"
        ]
        if not tcp_ports:
            return None

        web_container_port: int | None = None
        default_ports = [
            p
            for p in (app.default_ports or [])
            if isinstance(p, dict) and p.get("protocol", "tcp") == "tcp"
        ]
        for p in default_ports:
            if "web" in str(p.get("description", "")).lower():
                web_container_port = p.get("port")
                break
        if web_container_port is None and default_ports:
            web_container_port = default_ports[0].get("port")

        local_port: int | None = None
        if web_container_port is not None:
            for p in tcp_ports:
                if p.get("container_port") == web_container_port:
                    local_port = p.get("local_port")
                    break
        if local_port is None:
            local_port = tcp_ports[0].get("local_port")
        if not local_port:
            return None
        return f"http://{host or self._get_host_ip()}:{local_port}"

    def _fill_access_urls(
        self,
        group: AppOrchestrationInstance,
        app_map: dict[str, App],
        instances: dict[str, AppInstance],
    ) -> None:
        """部署/修复后，按实际分配的端口刷新组内各应用的访问地址。

        端口可能被重新分配，url 总是按最新配置重写；
        认证信息（auth_type/username/password/api_key）保留不动。
        """
        app_configs = dict(group.app_configs or {})
        host = self._get_host_ip()
        for app_name, instance in instances.items():
            app = app_map.get(app_name)
            url = self._build_access_url(app, instance.config, host) if app else None
            if not url:
                continue
            entry = dict(app_configs.get(app_name) or {})
            entry["url"] = url
            app_configs[app_name] = entry
        group.app_configs = app_configs
