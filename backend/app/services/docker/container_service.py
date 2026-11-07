"""Docker 容器业务服务。"""

import codecs
import shlex

from app.services.docker import docker_common as common


class ContainerService(common.BaseDockerService):
    """容器管理服务，封装容器 CRUD、操作、日志、exec 等。"""

    def list_containers(self, filters: dict | None = None) -> list[dict]:
        """获取容器列表。"""
        if not self._client:
            return []
        containers = self._client.containers.list(all=True, filters=filters)
        return [self._format_container(c) for c in containers]

    def get_container(self, container_id: str) -> dict | None:
        """根据容器 ID 获取单个容器详情。"""
        if not self._client:
            return None
        try:
            c = self._client.containers.get(container_id)
            return self._format_container(c)
        except common.docker.errors.NotFound:
            return None

    def container_action(self, container_id: str, action: str) -> dict:
        """对指定容器执行操作（启动、停止、重启、删除）。"""
        if action not in self._ALLOWED_ACTIONS:
            raise ValueError(f"Action '{action}' is not allowed")
        if not self._client:
            raise RuntimeError("Docker not available")
        container = self._client.containers.get(container_id)
        getattr(container, action)()

        if action in {"start", "restart"}:
            return self._wait_for_status(container, "running", timeout=10)

        if action == "remove":
            return {"status": "removed", "error": ""}

        container.reload()
        state = container.attrs.get("State", {}) or {}
        return {
            "status": state.get("Status", container.status),
            "error": state.get("Error", ""),
        }

    def create_container(self, request: dict) -> dict:
        """根据请求参数创建容器。"""
        if not self._client:
            raise RuntimeError("Docker not available")

        ports = {}
        for mapping in request.get("ports") or []:
            container_port = mapping.get("container", "").strip()
            host = mapping.get("host", "").strip()
            if container_port and host:
                ports[container_port] = host

        volumes = {}
        for mount in request.get("volumes") or []:
            host_path = mount.get("host", "").strip()
            container_path = mount.get("container", "").strip()
            mode = mount.get("mode", "rw")
            if host_path and container_path:
                volumes[host_path] = {"bind": container_path, "mode": mode}

        environment = [
            f"{item.get('key', '')}={item.get('value', '')}"
            for item in request.get("environment") or []
            if item.get("key") is not None
        ]

        labels = {
            item.get("key", ""): item.get("value", "")
            for item in request.get("labels") or []
            if item.get("key") is not None
        }

        command = shlex.split(request["command"]) if request.get("command") else None
        entrypoint = shlex.split(request["entrypoint"]) if request.get("entrypoint") else None

        restart_policy = {"Name": request.get("restart_policy", "no")}

        create_kwargs = {
            "image": request["image"],
            "command": command,
            "entrypoint": entrypoint,
            "ports": ports or None,
            "volumes": volumes or None,
            "environment": environment or None,
            "labels": labels or None,
            "restart_policy": restart_policy,
            "detach": True,
        }

        name = request.get("name")
        if name:
            create_kwargs["name"] = name

        network = request.get("network")
        if network:
            create_kwargs["network"] = network

        create_kwargs = {k: v for k, v in create_kwargs.items() if v is not None}

        container = self._client.containers.create(**create_kwargs)
        if request.get("auto_start", True):
            container.start()
            self._wait_for_status(container, "running", timeout=10)
        return self._format_container(container)

    def batch_container_action(self, ids: list[str], action: str) -> dict:
        """批量对容器执行操作。"""
        if action not in self._ALLOWED_ACTIONS:
            raise ValueError(f"Action '{action}' is not allowed")
        if not self._client:
            raise RuntimeError("Docker not available")

        succeeded = []
        failed = []
        for cid in ids:
            try:
                container = self._client.containers.get(cid)
                if action == "remove":
                    container.remove(force=True)
                else:
                    getattr(container, action)()
                    if action in {"start", "restart"}:
                        self._wait_for_status(container, "running", timeout=10)
                succeeded.append(cid)
            except Exception as e:
                failed.append({"id": cid, "reason": str(e)})
        return {"succeeded": succeeded, "failed": failed}

    def get_container_logs(self, container_id: str, tail: int = 100) -> str:
        """获取容器最近日志。"""
        if not self._client:
            return ""
        try:
            container = self._client.containers.get(container_id)
        except common.docker.errors.NotFound:
            return ""
        return container.logs(tail=tail).decode("utf-8", errors="replace")

    def get_container_status(self, container_id: str) -> dict | None:
        """获取容器实时状态摘要。"""
        if not self._client:
            return None
        try:
            container = self._client.containers.get(container_id)
            container.reload()
        except common.docker.errors.NotFound:
            return None

        state = container.attrs.get("State", {}) or {}
        status = state.get("Status", container.status)
        return {
            "id": container.id[:12],
            "name": container.name,
            "status": status,
            "state": self._state_summary(status),
        }

    def stream_container_logs(
        self,
        container_id: str,
        tail: int = 100,
        follow: bool = True,
        timestamps: bool = True,
    ):
        """流式获取容器日志（按完整行产出）。

        Docker SDK 返回的是任意边界的字节块而非整行：多字节 UTF-8 字符
        可能跨块被切断，直接逐块 decode 会产生 � 乱码；同时逐块产出会让
        前端把字节碎片当作日志行，行数与实际日志完全对不上。这里用增量
        解码器拼接跨块字符，再按换行切分，保证每次产出都是完整的一行。
        """
        if not self._client:
            raise RuntimeError("Docker not available")
        container = self._client.containers.get(container_id)
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        buffer = ""
        for chunk in container.logs(
            stdout=True,
            stderr=True,
            stream=True,
            follow=follow,
            tail=tail,
            timestamps=timestamps,
        ):
            buffer += decoder.decode(chunk)
            lines = buffer.split("\n")
            buffer = lines.pop()
            yield from lines
        buffer += decoder.decode(b"", final=True)
        if buffer:
            yield buffer

    def get_container_detail(self, container_id: str) -> dict | None:
        """获取容器完整详情。"""
        if not self._client:
            return None
        try:
            container = self._client.containers.get(container_id)
        except common.docker.errors.NotFound:
            return None

        attrs = container.attrs
        config = attrs.get("Config", {}) or {}
        state = attrs.get("State", {}) or {}
        host_config = attrs.get("HostConfig", {}) or {}
        network_settings = attrs.get("NetworkSettings", {}) or {}

        command = config.get("Cmd") or None
        entrypoint = config.get("Entrypoint") or None

        ports = []
        port_bindings = network_settings.get("Ports") or {}
        for container_port, bindings in port_bindings.items():
            if isinstance(bindings, list):
                for binding in bindings:
                    ports.append({
                        "container_port": container_port,
                        "host_ip": binding.get("HostIp", ""),
                        "host_port": binding.get("HostPort", ""),
                    })
            else:
                ports.append({
                    "container_port": container_port,
                    "host_ip": "",
                    "host_port": "",
                })

        mounts = []
        for mount in attrs.get("Mounts") or []:
            mounts.append({
                "type": mount.get("Type", "bind"),
                "source": mount.get("Source", ""),
                "destination": mount.get("Destination", ""),
                "mode": mount.get("Mode", "rw"),
                "rw": mount.get("RW", True),
            })

        networks = []
        network_mode = host_config.get("NetworkMode", "default")
        networks_config = network_settings.get("Networks") or {}
        for net_name, net_info in networks_config.items():
            networks.append({
                "name": net_name,
                "ip_address": net_info.get("IPAddress", ""),
                "gateway": net_info.get("Gateway", ""),
                "mac_address": net_info.get("MacAddress", ""),
            })

        restart_policy = host_config.get("RestartPolicy", {}) or {}
        restart_policy_name = restart_policy.get("Name", "no")

        status = state.get("Status", container.status)
        health = "unknown"
        if "Health" in state:
            health = state["Health"].get("Status", "unknown")

        return {
            "id": container.id[:12],
            "name": container.name,
            "image": str(container.image.tags[0]) if container.image.tags else "unknown",
            "status": status,
            "state": self._state_summary(status),
            "health": health,
            "command": command,
            "entrypoint": entrypoint,
            "env": config.get("Env") or None,
            "working_dir": config.get("WorkingDir") or None,
            "user": config.get("User") or None,
            "labels": config.get("Labels") or None,
            "ports": ports,
            "mounts": mounts,
            "networks": networks,
            "restart_policy": restart_policy_name,
            "network_mode": network_mode,
            "privileged": host_config.get("Privileged", False),
            "created": attrs.get("Created", ""),
            "started_at": state.get("StartedAt", ""),
            "finished_at": state.get("FinishedAt", ""),
            "exit_code": state.get("ExitCode", 0),
            "error": state.get("Error", ""),
        }

    def exec_container(
        self,
        container_id: str,
        command: str,
        workdir: str | None = None,
        user: str | None = None,
        environment: list[dict] | None = None,
    ) -> tuple[int, str]:
        """在容器内执行命令。"""
        if not self._client:
            raise RuntimeError("Docker not available")
        container = self._client.containers.get(container_id)
        cmd_list = shlex.split(command)
        env_list = [
            f"{item.get('key', '')}={item.get('value', '')}"
            for item in environment or []
            if item.get("key") is not None
        ]
        exec_kwargs = {
            "stdout": True,
            "stderr": True,
            "stream": True,
        }
        if workdir:
            exec_kwargs["workdir"] = workdir
        if user:
            exec_kwargs["user"] = user
        if env_list:
            exec_kwargs["environment"] = env_list

        result = container.exec_run(cmd_list, **exec_kwargs)
        output_chunks = []
        for chunk in result.output:
            output_chunks.append(chunk.decode("utf-8", errors="replace"))
        return result.exit_code, "".join(output_chunks)

    def resolve_terminal_shell(
        self,
        container_id: str,
        shell: str,
        user: str | None = None,
    ) -> str:
        """选择容器内可执行的 shell，优先保留用户选择。"""
        if not self._client:
            raise RuntimeError("Docker not available")
        container = self._client.containers.get(container_id)
        candidates = [shell]
        fallback = "/bin/bash" if shell == "/bin/sh" else "/bin/sh"
        if fallback != shell:
            candidates.append(fallback)

        for candidate in candidates:
            kwargs = {"stdout": False, "stderr": False}
            if user:
                kwargs["user"] = user
            try:
                result = container.exec_run(
                    [candidate, "-c", "exit 0"],
                    **kwargs,
                )
            except common.docker.errors.APIError as e:
                if "no such file or directory" not in str(e):
                    raise
                continue
            if result.exit_code == 0:
                return candidate

        raise RuntimeError(f"容器内没有可用的 shell：{shell} 或 {fallback}")

    def exec_container_interactive(
        self,
        container_id: str,
        command: list[str],
        workdir: str | None = None,
        user: str | None = None,
        environment: list[dict] | None = None,
        tty: bool = True,
    ) -> tuple[str, object]:
        """在容器内创建交互式 exec 会话。"""
        if not self._client:
            raise RuntimeError("Docker not available")
        container = self._client.containers.get(container_id)
        env_list = [
            f"{item.get('key', '')}={item.get('value', '')}"
            for item in environment or []
            if item.get("key") is not None
        ]
        exec_kwargs = {
            "stdin": True,
            "stdout": True,
            "stderr": True,
            "tty": tty,
        }
        if workdir:
            exec_kwargs["workdir"] = workdir
        if user:
            exec_kwargs["user"] = user
        if env_list:
            exec_kwargs["environment"] = env_list

        exec_id = self._client.api.exec_create(container.id, command, **exec_kwargs)
        socket = self._client.api.exec_start(exec_id, socket=True, tty=tty)
        return exec_id, socket
