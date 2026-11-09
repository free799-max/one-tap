"""Docker 镜像业务服务。"""

import asyncio
import math
import re
import time
import urllib.parse
from collections.abc import Awaitable, Callable

import httpx

from app.services.docker import docker_common as common


_HUB_HOSTS = {"", "docker.io", "index.docker.io", "registry-1.docker.io"}
_MANIFEST_TYPES = (
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
)
_MANIFEST_ACCEPT = ", ".join(_MANIFEST_TYPES)
_INDEX_TYPES = {
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
}
_TAG_LIMIT = 50
_REGISTRY_TAG_LIMIT = 30
_TAG_META_CONCURRENCY = 16
_MANIFEST_BUDGET = 8
_CONFIG_BUDGET = 4
_TAG_CACHE_TTL = 300
_tag_cache: dict[str, tuple[float, list[dict]]] = {}


class ImageTagsUnavailable(RuntimeError):
    """所有标签来源（Docker Hub、registry、镜像加速）均不可用。"""


def _describe_error(exc: Exception) -> str:
    """把标签获取异常转成前端可读的简短原因。"""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return str(exc) or type(exc).__name__


def _split_image_ref(image: str) -> tuple[str, str]:
    """拆出镜像仓库域名与仓库名，未带域名时视为 Docker Hub。"""
    ref = image.strip().removeprefix("https://").removeprefix("http://")
    if "@" in ref:
        ref = ref.split("@", 1)[0]
    parts = [p for p in ref.split("/") if p]
    if not parts:
        return "", ""
    host = ""
    if len(parts) > 1 and (
        "." in parts[0] or ":" in parts[0] or parts[0] == "localhost"
    ):
        host = parts.pop(0)
    if parts and ":" in parts[-1]:
        parts[-1] = parts[-1].rsplit(":", 1)[0]
    repo = "/".join(parts)
    if host in _HUB_HOSTS:
        host = ""
        if "/" not in repo:
            repo = f"library/{repo}"
    return host, repo


def _parse_image_ref(image: str) -> tuple[str, str, str, str | None]:
    """解析镜像引用，返回 ``(源站, 仓库名, tag, digest)``。

    源站是客户端匹配加速域名用的键：Docker Hub 统一为 ``docker.io``。
    """
    ref = image.strip().removeprefix("https://").removeprefix("http://")
    digest = None
    if "@" in ref:
        ref, digest = ref.split("@", 1)
    tag = "latest"
    head, separator, tail = ref.rpartition(":")
    if separator and "/" not in tail:
        ref, tag = head, tail
    host, repo = _split_image_ref(ref)
    return host or "docker.io", repo, tag, digest


def _mirror_ref(image: str, mirror_url: str) -> str | None:
    """把镜像引用改写成加速域名下的地址。

    带 digest 的引用无法可靠改 tag，返回 ``None`` 由调用方直连。
    """
    _, repo, tag, digest = _parse_image_ref(image)
    if not repo or digest:
        return None
    host = (
        mirror_url.strip()
        .removeprefix("https://")
        .removeprefix("http://")
        .rstrip("/")
    )
    if not host:
        return None
    return f"{host}/{repo}:{tag}"


def _original_tag_target(image: str) -> tuple[str, str]:
    """加速域名拉取完成后要改回的仓库名与 tag。"""
    source, repo, tag, _ = _parse_image_ref(image)
    repository = repo if source == "docker.io" else f"{source}/{repo}"
    return repository, tag


def _select_mirror(
    image: str, acceleration: dict[str, list[str]] | None
) -> tuple[str, tuple[str, str] | None]:
    """挑选该镜像源站可用的加速地址，返回 ``(实际拉取引用, 改 tag 目标)``。"""
    if not acceleration:
        return image, None
    source, _, _, digest = _parse_image_ref(image)
    if digest:
        return image, None
    for url in acceleration.get(source) or []:
        mirrored = _mirror_ref(image, url)
        if mirrored:
            return mirrored, _original_tag_target(image)
    return image, None


def _tag_back(
    client, pulled_ref: str, repository: str, tag: str
) -> bool:
    """把加速域名下拉到的镜像改回原始仓库名，并去掉临时 tag。"""
    try:
        client.images.get(pulled_ref).tag(repository=repository, tag=tag)
    except Exception:
        common.logger.warning(
            "镜像已拉取但改 tag 失败：%s -> %s:%s", pulled_ref, repository, tag,
            exc_info=True,
        )
        return False
    try:
        client.images.remove(pulled_ref)
    except Exception:
        common.logger.debug("清理临时镜像 tag 失败：%s", pulled_ref)
    return True


_PULL_STATUS_MAP = {
    "Pulling from": "拉取中",
    "Pulling fs layer": "准备下载",
    "Waiting": "等待中",
    "Downloading": "下载中",
    "Download complete": "下载完成",
    "Verifying Checksum": "验证中",
    "Pull complete": "已完成",
    "Already exists": "已存在",
    "Layer already exists": "已存在",
    "Extracting": "解压中",
    "Pulling manifest": "获取清单",
}


def _format_bytes(size: int) -> str:
    if size == 0:
        return "0 B"
    step = 1024
    units = ["B", "KB", "MB", "GB", "TB"]
    index = min(len(units) - 1, int(math.log(size) / math.log(step)))
    return f"{size / (step ** index):.1f} {units[index]}"


def _map_pull_status(status: str) -> str:
    for key, value in _PULL_STATUS_MAP.items():
        if key in status:
            return value
    return status


def _consume_pull_stream(stream, task_manager, task_id: str) -> None:
    """消费 docker pull 流并推送分层进度。"""
    layers = {}
    last_progress_time = common.time.time()
    last_overall_percentage = 0

    for line in stream:
        if not line:
            continue

        status = line.get("status", "")
        layer_id = line.get("id", "")
        progress_detail = line.get("progressDetail", {}) or {}
        current = progress_detail.get("current", 0) or 0
        total = progress_detail.get("total", 0) or 0

        now = common.time.time()

        if layer_id:
            if layer_id not in layers:
                layers[layer_id] = {
                    "status": status,
                    "current": current,
                    "total": total,
                    "last_current": current,
                    "last_time": now,
                    "speed": 0,
                }
            else:
                old = layers[layer_id]
                time_delta = now - old["last_time"]
                if time_delta >= 0.5 and current > old["last_current"]:
                    speed = int((current - old["last_current"]) / time_delta)
                    old["speed"] = speed
                    old["last_current"] = current
                    old["last_time"] = now
                old["status"] = status
                old["current"] = current
                if total > 0:
                    old["total"] = total

        layer_progress_list = []
        total_size = 0
        downloaded_size = 0
        total_speed = 0
        downloading_count = 0
        completed_layers = 0

        for lid, ldata in layers.items():
            l_total = ldata.get("total", 0)
            l_current = ldata.get("current", 0)
            l_status = ldata.get("status", "")
            l_speed = ldata.get("speed", 0)

            completed_statuses = (
                "Pull complete",
                "Already exists",
                "Layer already exists",
                "Download complete",
            )
            l_percentage = 0
            if l_status in completed_statuses:
                l_percentage = 100
            elif l_total > 0 and l_current > 0:
                l_percentage = int((l_current / l_total) * 100)

            if l_status in completed_statuses:
                progress_text = "已完成"
            elif l_total > 0:
                progress_text = f"{_format_bytes(l_current)} / {_format_bytes(l_total)}"
            else:
                progress_text = "--"

            layer_progress_list.append({
                "id": lid,
                "status": l_status,
                "status_text": _map_pull_status(l_status),
                "current": l_current,
                "total": l_total,
                "progress_text": progress_text,
                "percentage": l_percentage,
                "speed": l_speed,
            })

            if l_status in completed_statuses or l_percentage == 100:
                completed_layers += 1

            if l_total > 0:
                total_size += l_total
                downloaded_size += min(l_current, l_total)

            if l_speed > 0:
                total_speed += l_speed
                downloading_count += 1

        total_layers = len(layers)
        byte_percentage = 0
        if total_size > 0:
            byte_percentage = int((downloaded_size / total_size) * 100)

        layer_average_percentage = 0
        if total_layers > 0:
            layer_percentage_sum = sum(
                layer["percentage"] for layer in layer_progress_list
            )
            layer_average_percentage = int(layer_percentage_sum / total_layers)

        calculated_percentage = max(byte_percentage, layer_average_percentage)
        percentage = max(calculated_percentage, last_overall_percentage)
        last_overall_percentage = percentage

        if downloading_count > 0:
            overall_status = f"下载中 {downloading_count} 个层"
        elif completed_layers < total_layers:
            overall_status = "准备下载"
        else:
            overall_status = "处理中"

        size_text = (
            f"{_format_bytes(downloaded_size)} / {_format_bytes(total_size)}"
            if total_size > 0
            else "--"
        )

        progress = {
            "total_layers": total_layers,
            "completed_layers": completed_layers,
            "current_layer": layer_id,
            "percentage": min(percentage, 99),
            "status": overall_status,
            "speed": total_speed,
            "total_size": total_size,
            "downloaded_size": downloaded_size,
            "size_text": size_text,
            "layers": layer_progress_list,
        }

        if now - last_progress_time >= 0.3:
            task_manager.update_progress(task_id, progress)
            last_progress_time = now


def _parse_bearer_challenge(header: str) -> dict[str, str]:
    if not header or not header.lower().startswith("bearer"):
        return {}
    return {k.lower(): v for k, v in re.findall(r'(\w+)="([^"]*)"', header)}


def _next_link(header: str | None) -> str | None:
    if not header:
        return None
    match = re.search(r"<([^>]+)>\s*;\s*rel=\"?next\"?", header)
    return match.group(1) if match else None


class _RegistryClient:
    """OCI Distribution 协议客户端，匿名访问公开仓库。

    ``root`` 是仓库根地址（如 ``https://registry-1.docker.io``），
    镜像加速地址也可作为根地址，从而复用同一套 tags/list 协议。
    """

    def __init__(self, client: httpx.AsyncClient, root: str, repo: str):
        self._client = client
        self.root = root.rstrip("/")
        self.repo = repo
        self._token: str | None = None
        self._lock = asyncio.Lock()

    @property
    def _base(self) -> str:
        return f"{self.root}/v2/{self.repo}"

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": _MANIFEST_ACCEPT}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def _ensure_token(self) -> None:
        if self._token is not None:
            return
        async with self._lock:
            if self._token is not None:
                return
            resp = await self._client.get(f"{self.root}/v2/")
            challenge = _parse_bearer_challenge(
                resp.headers.get("www-authenticate", "")
            )
            realm = challenge.get("realm")
            if resp.status_code != 401 or not realm:
                self._token = ""
                return
            params = {"scope": f"repository:{self.repo}:pull"}
            if challenge.get("service"):
                params["service"] = challenge["service"]
            token_resp = await self._client.get(realm, params=params)
            token_resp.raise_for_status()
            data = token_resp.json()
            self._token = data.get("token") or data.get("access_token") or ""

    async def list_tags(self, limit: int) -> list[str]:
        await self._ensure_token()
        url: str | None = f"{self._base}/tags/list"
        params: dict | None = {"n": limit}
        names: list[str] = []
        while url and len(names) < limit:
            resp = await self._client.get(url, params=params, headers=self._headers())
            resp.raise_for_status()
            names.extend(resp.json().get("tags") or [])
            url = _next_link(resp.headers.get("link"))
            params = None
        return names[:limit]

    async def manifest(self, tag: str) -> dict:
        """取标签对应的单架构镜像清单，返回大小、摘要与配置摘要。"""
        resp = await self._client.get(
            f"{self._base}/manifests/{tag}", headers=self._headers()
        )
        resp.raise_for_status()
        digest = resp.headers.get("docker-content-digest", "")
        manifest = resp.json()
        if manifest.get("mediaType") in _INDEX_TYPES or "manifests" in manifest:
            child = self._pick_platform(manifest.get("manifests") or [])
            if child:
                sub = await self._client.get(
                    f"{self._base}/manifests/{child['digest']}",
                    headers=self._headers(),
                )
                sub.raise_for_status()
                manifest = sub.json()
        size = sum(layer.get("size", 0) for layer in manifest.get("layers") or [])
        size += (manifest.get("config") or {}).get("size", 0)
        return {
            "digest": digest,
            "size": size,
            "config_digest": (manifest.get("config") or {}).get("digest"),
        }

    async def config_created(self, config_digest: str) -> str:
        """读取镜像配置块的创建时间。"""
        resp = await self._client.get(
            f"{self._base}/blobs/{config_digest}", headers=self._headers()
        )
        resp.raise_for_status()
        return resp.json().get("created", "") or ""

    @staticmethod
    def _pick_platform(manifests: list[dict]) -> dict | None:
        candidates = [
            m
            for m in manifests
            if (m.get("platform") or {}).get("os") == "linux"
            and (m.get("platform") or {}).get("architecture") in ("amd64", "arm64")
        ]
        candidates.sort(
            key=lambda m: (m.get("platform") or {}).get("architecture") != "amd64"
        )
        return candidates[0] if candidates else None


async def _hub_tags(repo: str, limit: int) -> list[dict]:
    url = f"https://hub.docker.com/v2/repositories/{repo}/tags/"
    async with httpx.AsyncClient() as client:
        # Docker Hub 偶发连接重置，重试一次再放弃
        for attempt in range(2):
            try:
                resp = await client.get(url, params={"page_size": limit}, timeout=15)
                resp.raise_for_status()
                break
            except (
                httpx.ConnectError,
                httpx.ReadError,
                httpx.ReadTimeout,
                httpx.RemoteProtocolError,
            ):
                if attempt:
                    raise
    return [
        {
            "name": r.get("name", ""),
            "last_updated": r.get("last_updated", ""),
            "size": r.get("full_size", 0),
            "digest": r.get("digest", ""),
        }
        for r in resp.json().get("results", [])
    ]


async def _retry(fn, attempts: int = 2):
    """偶发连接重置 / 5xx 时重试一次。"""
    for attempt in range(attempts):
        try:
            return await fn()
        except (httpx.TransportError, httpx.HTTPStatusError):
            if attempt == attempts - 1:
                raise


async def _registry_tags(
    host: str, repo: str, limit: int, *, root: str | None = None
) -> list[dict]:
    """按 OCI Distribution 协议列出标签。

    ``root`` 给定时直接作为仓库根地址（镜像加速地址），否则用 ``https://host``。
    """
    async with httpx.AsyncClient(
        timeout=15,
        follow_redirects=True,
        limits=httpx.Limits(max_connections=32, max_keepalive_connections=32),
    ) as client:
        registry = _RegistryClient(client, root or f"https://{host}", repo)
        names = await _retry(lambda: registry.list_tags(limit))
        if not names:
            return []
        semaphore = asyncio.Semaphore(_TAG_META_CONCURRENCY)

        async def _manifest(name: str) -> dict:
            try:
                async with semaphore:
                    return {"name": name, **(await registry.manifest(name))}
            except Exception:
                common.logger.debug("获取标签清单失败: %s:%s", repo, name)
                return {"name": name, "size": 0, "digest": "", "config_digest": ""}

        # 元数据只做尽力补充，超出预算的标签仍会列出，只是没有大小与时间
        done = await _gather_bounded(
            {name: _manifest(name) for name in names}, _MANIFEST_BUDGET
        )
        entries = [
            done[name].result() if name in done else
            {"name": name, "size": 0, "digest": "", "config_digest": ""}
            for name in names
        ]

        # 多个标签常指向同一清单（如 latest 与 main），按配置块摘要去重
        config_digests = {e["config_digest"] for e in entries if e["config_digest"]}

        async def _created(digest: str) -> tuple[str, str]:
            try:
                async with semaphore:
                    return digest, await registry.config_created(digest)
            except Exception:
                common.logger.debug("获取镜像配置块失败: %s", digest)
                return digest, ""

        created_map = {
            key: task.result()[1]
            for key, task in (
                await _gather_bounded(
                    {digest: _created(digest) for digest in config_digests},
                    _CONFIG_BUDGET,
                )
            ).items()
        }

        return [
            {
                "name": e["name"],
                "last_updated": created_map.get(e["config_digest"], ""),
                "size": e["size"],
                "digest": e["digest"],
            }
            for e in entries
        ]


async def _gather_bounded(coros: dict, timeout: float) -> dict:
    """并发的协程在预算内完成则返回，超时的直接放弃。"""
    if not coros:
        return {}
    tasks = {key: asyncio.create_task(coro) for key, coro in coros.items()}
    done, pending = await asyncio.wait(tasks.values(), timeout=timeout)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    return {key: task for key, task in tasks.items() if task in done}


def _sort_tags(tags: list[dict]) -> list[dict]:
    return sorted(
        tags,
        key=lambda t: t.get("last_updated") or "",
        reverse=True,
    )


class ImageService(common.BaseDockerService):
    """镜像管理服务，封装镜像列表、详情、搜索、拉取等。"""

    def list_images(self) -> list[dict]:
        """获取本地镜像列表（扁平化，每行对应一个 tag）。"""
        if not self._client:
            return []
        images = self._client.images.list()
        containers = self._client.containers.list(all=True)
        image_usage = {}
        for c in containers:
            img_id = c.image.id
            image_usage[img_id] = image_usage.get(img_id, 0) + 1

        result = []
        for img in images:
            attrs = img.attrs
            repo_tags = attrs.get("RepoTags") or []
            if not repo_tags:
                repo_tags = ["<none>:<none>"]

            full_id = img.id
            short_id = full_id.split(":")[-1][:12] if ":" in full_id else full_id[:12]
            container_count = image_usage.get(full_id, 0)

            for tag_str in repo_tags:
                if ":" in tag_str:
                    name, tag = tag_str.rsplit(":", 1)
                else:
                    name, tag = tag_str, "<none>"
                result.append({
                    "id": short_id,
                    "image_id": full_id,
                    "name": name,
                    "tag": tag,
                    "full_tag": tag_str,
                    "size": attrs.get("Size", 0),
                    "created": attrs.get("Created", ""),
                    "containers": container_count,
                })
        return result

    def get_image_detail(self, image_id: str) -> dict | None:
        """获取镜像完整元数据。"""
        if not self._client:
            return None
        try:
            image = self._client.images.get(image_id)
            attrs = image.attrs
            config = attrs.get("Config", {}) or {}
            rootfs = attrs.get("RootFS", {}) or {}
            history = attrs.get("History", []) or []

            exposed_ports = []
            ports = config.get("ExposedPorts", {})
            if ports:
                exposed_ports = list(ports.keys())

            volumes = []
            vols = config.get("Volumes", {})
            if vols:
                volumes = list(vols.keys())

            history_cmds = []
            for h in history:
                created_by = h.get("CreatedBy", "")
                if created_by:
                    history_cmds.append(created_by)

            layers = rootfs.get("Layers", []) or []

            repo_tags = attrs.get("RepoTags") or ["<none>:<none>"]
            first_tag = repo_tags[0]
            if ":" in first_tag:
                name, tag = first_tag.rsplit(":", 1)
            else:
                name, tag = first_tag, "<none>"

            parent = attrs.get("Parent") or None
            docker_version = attrs.get("DockerVersion") or None
            architecture = attrs.get("Architecture", "")
            os_value = attrs.get("Os", "")
            build = None
            if docker_version and os_value and architecture:
                build = f"Docker {docker_version} on {os_value}, {architecture}"

            layers_table = []
            try:
                for i, layer in enumerate(image.history()):
                    created_by = (layer.get("CreatedBy", "") or "").strip()
                    if not created_by:
                        continue
                    layers_table.append({
                        "order": i,
                        "size": layer.get("Size", 0) or 0,
                        "layer": created_by,
                    })
            except Exception:
                layers_table = []

            return {
                "id": attrs.get("Id", ""),
                "name": name,
                "tag": tag,
                "full_tag": first_tag,
                "size": attrs.get("Size", 0),
                "created": attrs.get("Created", ""),
                "architecture": architecture,
                "os": os_value,
                "cmd": config.get("Cmd"),
                "entrypoint": config.get("Entrypoint"),
                "env": config.get("Env"),
                "exposed_ports": exposed_ports or None,
                "volumes": volumes or None,
                "working_dir": config.get("WorkingDir") or None,
                "user": config.get("User") or None,
                "labels": config.get("Labels") or None,
                "layers": layers or None,
                "history": history_cmds or None,
                "parent": parent,
                "docker_version": docker_version,
                "build": build,
                "layers_table": layers_table or None,
            }
        except common.docker.errors.ImageNotFound:
            return None
        except Exception:
            common.logger.exception("获取镜像详情失败: %s", image_id)
            return None

    def prune_unused_images(self) -> dict:
        """移除所有未使用的镜像。"""
        if not self._client:
            raise RuntimeError("Docker not available")
        result = self._client.images.prune(filters={"dangling": False})
        deleted_tags = [
            item["Untagged"]
            for item in result.get("ImagesDeleted", [])
            if "Untagged" in item
        ]
        return {
            "deleted": deleted_tags,
            "space_reclaimed": result.get("SpaceReclaimed", 0),
        }

    def remove_image(self, image_id: str, force: bool = False):
        """删除指定镜像。"""
        if not self._client:
            raise RuntimeError("Docker not available")
        self._client.images.remove(image_id, force=force)

    def remove_images(self, image_ids: list[str], force: bool = False) -> dict:
        """批量删除镜像。"""
        if not self._client:
            raise RuntimeError("Docker not available")
        deleted = []
        failed = []
        for image_id in image_ids:
            try:
                self._client.images.remove(image_id, force=force)
                deleted.append(image_id)
            except common.docker.errors.ImageNotFound:
                failed.append({"id": image_id, "reason": "镜像不存在"})
            except common.docker.errors.APIError as e:
                failed.append({"id": image_id, "reason": str(e)})
        return {"deleted": deleted, "failed": failed}

    async def search_images(
        self,
        query: str,
        page: int = 1,
        api_url: str | None = None,
        mirror_url: str | None = None,
        username: str | None = None,
        password: str | None = None,
    ) -> dict:
        """搜索镜像。"""
        if not query.strip():
            return {"total": 0, "page": page, "page_size": 20, "results": []}

        auth = (username, password) if username and password else None
        page_size = 20

        def _parse_item(r: dict, field_map: dict[str, str]) -> dict:
            def _get(key: str, default=None):
                mapped = field_map.get(key, key)
                return r.get(mapped, r.get(key, default))

            return {
                "name": _get("name", ""),
                "description": _get("description", ""),
                "star_count": _get("star_count", 0),
                "pull_count": _get("pull_count", 0),
                "official": _get("official", False),
                "is_automated": _get("is_automated", False),
            }

        async def _do_search(url: str, is_docker_hub: bool = False) -> dict:
            try:
                common.logger.info("镜像搜索请求: %s", url)
                async with httpx.AsyncClient() as client:
                    resp = await client.get(url, timeout=10, auth=auth)
                    resp.raise_for_status()
                    data = resp.json()
                if is_docker_hub:
                    results = data.get("results", [])
                    return {
                        "total": data.get("count", len(results)),
                        "page": page,
                        "page_size": page_size,
                        "results": [
                            _parse_item(r, {
                                "name": "repo_name",
                                "description": "short_description",
                                "official": "is_official",
                            })
                            for r in results
                        ],
                    }
                if isinstance(data, list):
                    return {
                        "total": len(data),
                        "page": page,
                        "page_size": page_size,
                        "results": [_parse_item(r, {}) for r in data],
                    }
                results = data.get("results", [])
                return {
                    "total": data.get("count", data.get("total", len(results))),
                    "page": data.get("page", page),
                    "page_size": data.get("page_size", page_size),
                    "results": [_parse_item(r, {}) for r in results],
                }
            except httpx.HTTPStatusError as e:
                msg = (
                    f"镜像搜索接口返回错误: {e.response.status_code} "
                    f"{e.response.reason_phrase} ({url})"
                )
                common.logger.error(msg)
                raise RuntimeError(msg) from e
            except httpx.RequestError as e:
                msg = f"镜像搜索接口请求失败: {e.__class__.__name__}: {e} ({url})"
                common.logger.error(msg)
                raise RuntimeError(msg) from e
            except Exception as e:
                msg = f"镜像搜索接口解析失败: {e.__class__.__name__}: {e} ({url})"
                common.logger.error(msg)
                raise RuntimeError(msg) from e

        def _build_search_url(base_url: str, is_docker_hub: bool) -> str:
            base = base_url.rstrip("/")
            if is_docker_hub:
                if not base.lower().endswith("/v2/search/repositories"):
                    base += "/v2/search/repositories"
                return (
                    base
                    + "?query="
                    + urllib.parse.quote(query.strip())
                    + f"&page_size={page_size}"
                    + f"&page={page}"
                )
            return base + "?search=" + urllib.parse.quote(query.strip())

        def _is_docker_hub_format(url: str) -> bool:
            url_lower = url.lower().rstrip("/")
            return (
                url_lower == "https://hub.docker.com"
                or url_lower == "https://registry.hub.docker.com"
                or url_lower.endswith("/v2/search/repositories")
                or url_lower.startswith("https://hub.docker.com")
                or url_lower.startswith("https://registry.hub.docker.com")
            )

        def _resolve_api_url(config_url: str | None) -> str | None:
            if not config_url:
                return None
            url = config_url.strip()
            if url.rstrip("/").lower() in (
                "https://hub.docker.com",
                "https://registry.hub.docker.com",
            ):
                return "https://hub.docker.com/v2/search/repositories"
            return url

        resolved_api = _resolve_api_url(api_url)
        resolved_mirror = _resolve_api_url(mirror_url)
        errors: list[str] = []

        if resolved_api:
            is_hub = _is_docker_hub_format(resolved_api)
            url = _build_search_url(resolved_api, is_hub)
            try:
                return await _do_search(url, is_docker_hub=is_hub)
            except RuntimeError as e:
                errors.append(str(e))
            if resolved_mirror:
                is_hub = _is_docker_hub_format(resolved_mirror)
                url = _build_search_url(resolved_mirror, is_hub)
                try:
                    return await _do_search(url, is_docker_hub=is_hub)
                except RuntimeError as e:
                    errors.append(str(e))
            raise RuntimeError("镜像搜索全部失败: " + "; ".join(errors))

        default_url = "https://hub.docker.com/v2/search/repositories"
        url = _build_search_url(default_url, is_docker_hub=True)
        try:
            return await _do_search(url, is_docker_hub=True)
        except RuntimeError as e:
            raise RuntimeError("镜像搜索失败: " + str(e)) from e

    def pull_image(
        self, image: str, acceleration: dict[str, list[str]] | None = None
    ):
        """拉取指定镜像；选中加速域名时先下载再改回原始 tag。"""
        if not self._client:
            raise RuntimeError("Docker not available")
        target, tag_target = _select_mirror(image, acceleration)
        try:
            self._client.images.pull(target)
        except Exception:
            if target == image:
                raise
            common.logger.info(
                "通过加速域名 %s 拉取 %s 失败，回退直连", target, image,
                exc_info=True,
            )
            self._client.images.pull(image)
            return
        if tag_target is not None and not _tag_back(
            self._client, target, *tag_target
        ):
            raise RuntimeError("镜像已下载但改 tag 失败")

    async def get_image_tags(
        self,
        image: str,
        *,
        refresh: bool = False,
        mirrors: list[str] | None = None,
        mirror_provider: Callable[[], Awaitable[list[str]]] | None = None,
        source_mirrors: dict[str, list[str]] | None = None,
    ) -> list[dict]:
        """获取指定镜像的可用标签列表。

        支持 Docker Hub 与任意 OCI registry（ghcr.io、quay.io 等），
        按最后更新时间倒序排列，最新的标签排在最前面。

        Docker Hub 接口不可达时会依次回退到 registry-1.docker.io 与
        镜像加速地址，全部失败时抛出 ``ImageTagsUnavailable``，由接口层
        把原因返回给前端。

        Args:
            image: 镜像名，可带标签或域名
            refresh: 为 True 时跳过进程内缓存重新获取
            mirrors: Docker Hub 镜像加速地址列表，顺序即优先级
            mirror_provider: 未显式给 mirrors 时的异步备用源，仅在需要回退时调用
            source_mirrors: 按源站分组的加速地址，用于 ghcr.io 等非 Hub 仓库
        """
        host, repo = _split_image_ref(image)
        if not repo:
            return []

        cache_key = f"{host}/{repo}"
        if not refresh:
            cached = _tag_cache.get(cache_key)
            if cached and time.monotonic() - cached[0] < _TAG_CACHE_TTL:
                return cached[1]

        errors: list[str] = []

        async def _try(label: str, fetch) -> list[dict]:
            try:
                tags = await fetch()
            except Exception as exc:
                errors.append(f"{label}: {_describe_error(exc)}")
                common.logger.info(
                    "获取镜像标签失败 %s (%s): %s", image, label, exc
                )
                return []
            if not tags:
                errors.append(f"{label}: 未返回标签")
            return tags

        async def _try_mirrors(candidates: list[str] | None) -> list[dict]:
            if not candidates:
                return []
            # 加速地址并发尝试，仍按配置顺序取第一个有结果的
            attempts = [
                _try(
                    mirror,
                    lambda m=mirror: _registry_tags(
                        "", repo, _REGISTRY_TAG_LIMIT, root=m
                    ),
                )
                for mirror in candidates
            ]
            results = await asyncio.gather(*attempts)
            return next((result for result in results if result), [])

        if host:
            tags = await _try(
                host, lambda: _registry_tags(host, repo, _REGISTRY_TAG_LIMIT)
            )
            if not tags:
                tags = await _try_mirrors((source_mirrors or {}).get(host))
        else:
            tags = await _try("Docker Hub", lambda: _hub_tags(repo, _TAG_LIMIT))
            if not tags:
                tags = await _try(
                    "registry-1.docker.io",
                    lambda: _registry_tags(
                        "registry-1.docker.io", repo, _REGISTRY_TAG_LIMIT
                    ),
                )
            if not tags:
                candidates = mirrors
                if candidates is None:
                    candidates = (source_mirrors or {}).get("docker.io")
                if candidates is None and mirror_provider is not None:
                    try:
                        candidates = await mirror_provider()
                    except Exception:
                        common.logger.info("获取镜像加速地址失败", exc_info=True)
                        candidates = []
                tags = await _try_mirrors(candidates)

        if not tags:
            raise ImageTagsUnavailable("；".join(errors) or "未返回任何标签")

        tags = _sort_tags(tags)
        _tag_cache[cache_key] = (time.monotonic(), tags)
        return tags

    def pull_image_async(
        self,
        image: str,
        task_id: str,
        task_manager,
        acceleration: dict[str, list[str]] | None = None,
    ):
        """在后台线程中流式拉取指定镜像，并报告进度。

        有可用加速域名时先按加速地址下载，成功后改回原始 tag；
        加速地址失败会自动回退直连，不需要宿主机配置 daemon.json。
        """
        if not self._client:
            task_manager.fail_task(task_id, "Docker not available")
            raise RuntimeError("Docker not available")

        target, tag_target = _select_mirror(image, acceleration)

        def _pull(ref: str) -> None:
            stream = self._client.api.pull(ref, stream=True, decode=True)
            _consume_pull_stream(stream, task_manager, task_id)

        try:
            if target != image:
                try:
                    _pull(target)
                except Exception:
                    common.logger.info(
                        "通过加速域名 %s 拉取 %s 失败，回退直连",
                        target,
                        image,
                        exc_info=True,
                    )
                    _pull(image)
                else:
                    if tag_target is not None and not _tag_back(
                        self._client, target, *tag_target
                    ):
                        task_manager.fail_task(task_id, "镜像已下载但改 tag 失败")
                        return
            else:
                _pull(image)
            task_manager.complete_task(task_id)
        except common.docker.errors.ImageNotFound:
            task_manager.fail_task(task_id, "镜像不存在")
        except common.docker.errors.APIError as e:
            task_manager.fail_task(task_id, f"拉取镜像失败: {e}")
        except Exception as e:
            task_manager.fail_task(task_id, f"拉取镜像失败: {e}")
