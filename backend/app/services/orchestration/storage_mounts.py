"""存储挂载：把巡影已连接的网盘一键挂载进 OpenList。

巡影把每个网盘的凭据明文保存在实例的 Postgres ``connected_storages.payload``
里（115/夸克/UC 为 cookie，光鸭/天翼/123 为 token 包）。OpenList 侧的驱动
只需要同一份凭据与一个根目录，因此这里直接读巡影的数据库，再用 OpenList
管理接口创建/更新存储，用户不用在 OpenList 里重复扫码。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.exceptions import APIException
from app.models.orchestration import AppInstance, AppOrchestrationInstance
from app.services.apps.base import AppClientError
from app.services.apps.openlist import OpenListClient
from app.services.docker import docker_manager

# 面板挂在巡影下（凭据都在巡影），实际写入的是同组的 OpenList
HOST_APP = "mediary-scout"
TARGET_APP = "openlist"

# 光鸭开放接口的 client_id（官方 web 客户端使用的公开值，与巡影内置的一致）
GUANGYA_CLIENT_ID = "aMe-8VSlkrbQXpUR"

# 巡影 provider -> OpenList 驱动与默认挂载点。
# kind: cookie = payload.cookie；token = payload 里的 accessToken/refreshToken；
# tianyi = 天翼的 accessToken/refreshToken 组合；pan123 = payload.token。
_PROVIDER_TARGETS: dict[str, dict[str, Any]] = {
    "pan115": {
        "driver": "115 Cloud",
        "label": "115网盘",
        "mount": "/115",
        "kind": "cookie",
        "root": "0",
    },
    "quark": {
        "driver": "Quark",
        "label": "夸克网盘",
        "mount": "/夸克",
        "kind": "cookie",
        "root": "0",
    },
    "uc": {
        "driver": "UC",
        "label": "UC网盘",
        "mount": "/UC",
        "kind": "cookie",
        "root": "0",
    },
    "guangya": {
        "driver": "GuangYaPan",
        "label": "光鸭云盘",
        "mount": "/光鸭",
        "kind": "guangya",
        "root": "",
    },
    "tianyi": {
        "driver": "189CloudPC",
        "label": "天翼云盘",
        "mount": "/天翼",
        "kind": "tianyi",
        "root": "-11",
    },
    "pan123": {
        "driver": "123 Open",
        "label": "123网盘",
        "mount": "/123",
        "kind": "pan123",
        "root": "0",
    },
}

# 挂载来源标记，写进 OpenList 存储备注，用于识别同一份网盘
_REMARK_PREFIX = "巡影同步"


def _mask_uid(uid: str) -> str:
    """与巡影界面一致地隐藏账号 ID。"""
    value = str(uid or "")
    if len(value) <= 8:
        return value
    return f"{value[:4]}…{value[-4:]}"


def _remark(provider_label: str, uid: str) -> str:
    return f"{_REMARK_PREFIX} · {provider_label} · {_mask_uid(uid)}"


def _payload_of(drive: dict[str, Any]) -> dict[str, Any]:
    payload = drive.get("payload")
    return payload if isinstance(payload, dict) else {}


def _root_id(provider: str, drive: dict[str, Any]) -> str:
    spec = _PROVIDER_TARGETS.get(provider) or {}
    return str(drive.get("root") or spec.get("root") or "0")


def build_addition(provider: str, drive: dict[str, Any]) -> dict[str, Any]:
    """按巡影网盘凭据构造 OpenList 驱动的 addition。"""
    spec = _PROVIDER_TARGETS.get(provider)
    if spec is None:
        raise APIException("该网盘暂不支持自动挂载", 400)
    payload = _payload_of(drive)
    root = _root_id(provider, drive)
    kind = spec["kind"]

    if kind == "cookie":
        cookie = str(payload.get("cookie") or "").strip()
        if not cookie:
            raise APIException("巡影里没有读到该网盘的登录凭据", 400)
        # 巡影的根目录就是它自己的媒体库目录，挂到这里可直接看到分类目录
        return {
            "cookie": cookie,
            "root_folder_id": root,
            "order_by": "none",
            "order_direction": "asc",
            "use_transcoding_address": False,
            "only_list_video_file": False,
        }

    if kind == "guangya":
        access = str(payload.get("accessToken") or "").strip()
        refresh = str(payload.get("refreshToken") or "").strip()
        if not access or not refresh:
            raise APIException("巡影里没有读到光鸭的 token", 400)
        addition: dict[str, Any] = {
            "access_token": access,
            "refresh_token": refresh,
            "client_id": GUANGYA_CLIENT_ID,
        }
        device_id = str(payload.get("deviceId") or "").strip()
        if device_id:
            addition["device_id"] = device_id
        return addition

    if kind == "tianyi":
        access = str(payload.get("accessToken") or "").strip()
        refresh = str(payload.get("refreshToken") or "").strip()
        if not access or not refresh:
            raise APIException("巡影里没有读到天翼的 token", 400)
        return {
            "login_type": "password",
            "access_token": access,
            "refresh_token": refresh,
            "root_folder_id": root,
            "type": "personal",
        }

    token = str(payload.get("token") or "").strip()
    if not token:
        raise APIException("巡影里没有读到 123 网盘的登录 token", 400)
    return {"AccessToken": token, "root_folder_id": root}


async def _load_group(db, group_id: int) -> AppOrchestrationInstance:
    result = await db.execute(
        select(AppOrchestrationInstance)
        .where(AppOrchestrationInstance.id == group_id)
        .options(
            selectinload(AppOrchestrationInstance.instances)
            .selectinload(AppInstance.app),
            selectinload(AppOrchestrationInstance.instances)
            .selectinload(AppInstance.project),
        )
    )
    group = result.scalar_one_or_none()
    if group is None:
        raise APIException("编排实例不存在", 404)
    return group


def _find_instance(
    group: AppOrchestrationInstance, app_name: str
) -> AppInstance | None:
    return next(
        (
            instance
            for instance in group.instances
            if instance.app and instance.app.name == app_name
        ),
        None,
    )


def _check_host_app(group: AppOrchestrationInstance) -> AppInstance:
    instance = _find_instance(group, HOST_APP)
    if instance is None:
        raise APIException("实例组里没有巡影，无法读取网盘配置", 404)
    return instance


def _project_name(instance: AppInstance) -> str | None:
    project = instance.project
    return project.project_name if project is not None else None


def _decode(output: bytes | str | None) -> str:
    if output is None:
        return ""
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return str(output)


def _parse_pg_env(container) -> tuple[str, str]:
    env = (container.attrs.get("Config") or {}).get("Env") or []
    values: dict[str, str] = {}
    for row in env:
        key, separator, value = str(row).partition("=")
        if separator:
            values[key] = value
    user = values.get("POSTGRES_USER") or "mediatrack"
    database = values.get("POSTGRES_DB") or user
    return user, database


def _find_service_container(project_name: str, service: str):
    client = docker_manager._client
    if client is None:
        raise APIException("Docker 不可用", 400)
    containers = client.containers.list(
        all=True,
        filters={
            "label": [
                f"com.docker.compose.project={project_name}",
                f"com.docker.compose.service={service}",
            ]
        },
    )
    return containers[0] if containers else None


def _read_mediary_drives(project_name: str) -> list[dict[str, Any]]:
    """读取巡影数据库里已连接的网盘（含凭据，仅在本进程内使用）。"""
    container = _find_service_container(project_name, "postgres")
    if container is None:
        raise APIException("未找到巡影的数据库容器", 400)
    user, database = _parse_pg_env(container)
    sql = (
        "select coalesce(jsonb_agg(jsonb_build_object("
        "'id', id, 'provider', provider, 'uid', provider_uid, "
        "'status', status, 'root', root_cid, 'label', label, "
        "'payload', payload)), '[]'::jsonb)::text "
        "from connected_storages;"
    )
    result = container.exec_run(
        ["psql", "-U", user, "-d", database, "-tAc", sql]
    )
    if getattr(result, "exit_code", 1) != 0:
        detail = _decode(getattr(result, "output", b"")).strip()[:200]
        raise APIException(f"读取巡影网盘失败：{detail or '未知错误'}", 400)
    text = _decode(getattr(result, "output", b"")).strip()
    if not text:
        return []
    try:
        rows = json.loads(text)
    except ValueError as exc:
        raise APIException("巡影网盘数据解析失败", 500) from exc
    return [row for row in rows if isinstance(row, dict)]


def _storage_mount_path(
    storages: list[dict[str, Any]],
    provider: str,
    remark: str,
) -> tuple[dict[str, Any] | None, str]:
    """找到该网盘已有的 OpenList 存储与要使用的挂载路径。"""
    spec = _PROVIDER_TARGETS[provider]
    default_path = str(spec["mount"])
    for storage in storages:
        if str(storage.get("remark") or "") == remark:
            return storage, str(storage.get("mount_path") or default_path)
    for storage in storages:
        if storage.get("driver") == spec["driver"] and str(
            storage.get("mount_path") or ""
        ) == default_path:
            return storage, default_path
    used = {str(storage.get("mount_path") or "") for storage in storages}
    path = default_path
    index = 2
    while path in used:
        path = f"{default_path}-{index}"
        index += 1
    return None, path


class StorageMountService:
    """巡影网盘 -> OpenList 存储挂载面板与一键挂载。"""

    async def _context(self, db, group_id: int) -> tuple[
        AppOrchestrationInstance, str, str, str
    ]:
        """返回实例组与 OpenList 的地址、账号、密码。"""
        group = await _load_group(db, group_id)
        host = _check_host_app(group)
        entry = (group.app_configs or {}).get(TARGET_APP) or {}
        url = str(entry.get("url") or "").strip()
        username = str(entry.get("username") or "").strip()
        password = str(entry.get("password") or "")
        if not url or not username or not password:
            raise APIException("OpenList 账号未配置，请先完成自动装配", 400)
        return group, url, username, password

    async def get_panel(
        self, db, group_id: int, app_name: str
    ) -> dict[str, Any]:
        if app_name != HOST_APP:
            return {"supported": False, "sources": [], "error": None}
        try:
            group, url, username, password = await self._context(db, group_id)
        except APIException as exc:
            return {
                "supported": True,
                "openlist_url": None,
                "sources": [],
                "error": exc.message,
            }

        host = _find_instance(group, HOST_APP)
        project_name = _project_name(host) if host is not None else None
        if not project_name:
            return {
                "supported": True,
                "openlist_url": url,
                "sources": [],
                "error": "巡影的 Compose 项目不存在，无法读取网盘配置",
            }

        client = OpenListClient()
        error: str | None = None
        try:
            storages = await client.list_storages(url, username, password)
        except AppClientError as exc:
            storages = []
            error = exc.message

        try:
            drives = await asyncio.to_thread(
                _read_mediary_drives, project_name
            )
        except APIException as exc:
            return {
                "supported": True,
                "openlist_url": url,
                "sources": [],
                "error": exc.message,
            }

        sources: list[dict[str, Any]] = []
        for drive in drives:
            provider = str(drive.get("provider") or "")
            spec = _PROVIDER_TARGETS.get(provider)
            provider_label = spec["label"] if spec else provider
            uid = str(drive.get("uid") or "")
            remark = _remark(provider_label, uid)
            storage, mount_path = (
                _storage_mount_path(storages, provider, remark)
                if spec
                else (None, "")
            )
            contents: list[str] = []
            contents_error: str | None = None
            if storage is not None:
                try:
                    files = await client.list_files(
                        url, username, password, mount_path, per_page=12
                    )
                    contents = [
                        str(item.get("name") or "") for item in files
                    ]
                except AppClientError as exc:
                    contents_error = exc.message
            sources.append(
                {
                    "id": str(drive.get("id") or ""),
                    "provider": provider,
                    "provider_label": provider_label,
                    "account": _mask_uid(uid),
                    "status": str(drive.get("status") or "unknown"),
                    "supported": spec is not None,
                    "driver": spec["driver"] if spec else None,
                    "mounted": storage is not None,
                    "mount_path": mount_path or None,
                    "storage_id": storage.get("id") if storage else None,
                    # OpenList 的 WebDAV 地址，容器外可以直接访问
                    "webdav_url": (
                        f"{url.rstrip('/')}/dav{mount_path}"
                        if storage is not None and mount_path
                        else None
                    ),
                    "contents": contents,
                    "contents_error": contents_error,
                }
            )

        return {
            "supported": True,
            "openlist_url": url,
            "sources": sources,
            "error": error,
        }

    async def mount(
        self,
        db,
        group_id: int,
        app_name: str,
        source_id: str,
    ) -> dict[str, Any]:
        """把巡影的一块网盘挂载到 OpenList（已存在则刷新凭据）。"""
        if app_name != HOST_APP:
            raise APIException("该应用不支持存储挂载", 400)
        group, url, username, password = await self._context(db, group_id)

        host = _find_instance(group, HOST_APP)
        project_name = _project_name(host) if host is not None else None
        if not project_name:
            raise APIException("巡影的 Compose 项目不存在，无法读取网盘配置", 400)

        drives = await asyncio.to_thread(_read_mediary_drives, project_name)
        drive = next(
            (item for item in drives if str(item.get("id")) == source_id),
            None,
        )
        if drive is None:
            raise APIException("巡影里没有找到该网盘，请刷新后重试", 404)

        provider = str(drive.get("provider") or "")
        spec = _PROVIDER_TARGETS.get(provider)
        if spec is None:
            raise APIException("该网盘暂不支持自动挂载", 400)
        addition = build_addition(provider, drive)
        remark = _remark(spec["label"], str(drive.get("uid") or ""))

        client = OpenListClient()
        created = False
        storage_id: int | None = None
        try:
            storages = await client.list_storages(url, username, password)
            storage, mount_path = _storage_mount_path(
                storages, provider, remark
            )
            storage_id = await client.save_storage(
                url,
                username,
                password,
                mount_path=mount_path,
                driver=str(spec["driver"]),
                addition=addition,
                remark=remark,
                storage_id=int(storage["id"]) if storage else None,
            )
            created = storage is None
            # 立即回读一次目录，确认凭据与根目录都能用
            files = await client.list_files(
                url, username, password, mount_path, per_page=12
            )
        except AppClientError as exc:
            if created and storage_id is not None:
                # 校验失败的新存储直接清掉，避免留下一堆打不开的挂载
                try:
                    await client.delete_storage(
                        url, username, password, storage_id
                    )
                except AppClientError:
                    pass
            raise APIException(str(exc), 400) from exc

        return {
            "ok": True,
            "message": (
                f"{spec['label']}已挂载到 {mount_path}"
                if created
                else f"{spec['label']}挂载已更新（{mount_path}）"
            ),
            "storage_id": storage_id,
            "mount_path": mount_path,
            "contents": [str(item.get("name") or "") for item in files],
        }


storage_mount_service = StorageMountService()
