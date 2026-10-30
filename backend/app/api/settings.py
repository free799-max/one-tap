"""系统设置 API 模块。

提供系统全局配置的查询、更新、数据目录管理以及目录浏览/操作接口。
"""

import asyncio

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.custom_route import CustomAPIRoute
from app.core.exceptions import APIException
from app.core.security import get_current_user, require_admin
from app.database import get_db
from app.models.user import User
from app.schemas.hosts_optimizer import (
    HostsOptimizerConfig,
    HostsOptimizerConfigUpdate,
    HostsOptimizerRunOut,
    HostsOptimizerStatusOut,
    HostsOptimizerTestRequest,
    HostsOptimizerTestResult,
)
from app.schemas.system_config import (
    DockerMirrorTestRequest,
    DockerMirrorTestResult,
    ProxyTestRequest,
    ProxyTestResult,
    RemoteDockerMirrorsOut,
    SystemConfigOut,
    SystemConfigUpdate,
)
from app.schemas.storage_directory import (
    StorageDirectoryCreate,
    StorageDirectoryOut,
    StorageDirectoryUpdate,
)
from app.schemas.docker import (
    DirectoryCreateRequest,
    DirectoryDeleteRequest,
    DirectoryEntry,
    DirectoryList,
    DirectoryRenameRequest,
)
from app.services.host.filesystem_service import filesystem_service
from app.services.system.port_allocator import allocate_free_ports, get_used_ports
from app.services.system import (
    get_browse_roots,
    hosts_optimizer_service,
    proxy_test_service,
    registry_mirror_service,
    storage_directory_service,
    system_config_service,
)

router = APIRouter(prefix="/api/settings", tags=["settings"], route_class=CustomAPIRoute)


@router.get("", response_model=SystemConfigOut)
async def get_system_config(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取系统全局配置。

    首次调用时若数据库中无记录，会自动创建默认配置。
    """
    config = await system_config_service.get_or_create(db)
    return system_config_service.to_dict(config)


@router.put("", response_model=SystemConfigOut)
async def update_system_config(
    data: SystemConfigUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """更新系统全局配置。"""
    current = await system_config_service.get_or_create(db)
    if data.auto_update_enabled and not current.auto_update_enabled:
        from app.services.licensing import licensing_service

        try:
            await licensing_service.require_valid(db)
        except APIException as exc:
            raise APIException("自动更新需要授权版，请先激活授权", 403) from exc
    config = await system_config_service.update(db, data)
    return system_config_service.to_dict(config)


@router.post("/proxy/test", response_model=ProxyTestResult)
async def test_proxy(
    data: ProxyTestRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """检测常用境外站点在当前代理下的连通性。

    请求体中为 None 的字段回退到已保存配置（未启用代理时忽略已保存地址），
    空字符串表示不走代理。
    """
    config = await system_config_service.get_or_create(db)
    http_proxy = data.http_proxy
    https_proxy = data.https_proxy
    if http_proxy is None and https_proxy is None and not config.proxy_enabled:
        http_proxy = https_proxy = ""
    if http_proxy is None:
        http_proxy = config.http_proxy
    if https_proxy is None:
        https_proxy = config.https_proxy
    return await proxy_test_service.test(
        http_proxy=http_proxy,
        https_proxy=https_proxy,
    )


# ---------- 镜像加速 ----------


@router.get("/docker-mirrors/remote", response_model=RemoteDockerMirrorsOut)
async def list_remote_docker_mirrors(
    current_user: User = Depends(get_current_user),
):
    """从配套管理端获取启用中的 Docker 镜像加速地址。"""
    return await registry_mirror_service.fetch_remote()


@router.post("/docker-mirrors/test", response_model=DockerMirrorTestResult)
async def test_docker_mirrors(
    data: DockerMirrorTestRequest,
    current_user: User = Depends(get_current_user),
):
    """并发检测镜像加速地址的连通性与响应耗时。"""
    return await registry_mirror_service.test(data.urls)


# ---------- Hosts 优选 ----------


@router.get("/hosts-optimizer/config", response_model=HostsOptimizerConfig)
async def get_hosts_optimizer_config(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取 Hosts 优选配置。"""
    config = await system_config_service.get_or_create(db)
    return hosts_optimizer_service.config_from_model(config)


@router.put("/hosts-optimizer/config", response_model=HostsOptimizerConfig)
async def update_hosts_optimizer_config(
    data: HostsOptimizerConfigUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """保存 Hosts 优选配置（整份提交）。"""
    config = await hosts_optimizer_service.update_config(db, data)
    hosts_optimizer_service.wake()
    return hosts_optimizer_service.config_from_model(config)


@router.get("/hosts-optimizer/status", response_model=HostsOptimizerStatusOut)
async def get_hosts_optimizer_status(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取当前运行状态与最近一次结果。"""
    config = await system_config_service.get_or_create(db)
    return {
        "running": hosts_optimizer_service.running,
        "next_run_at": hosts_optimizer_service.next_run_at(config),
        "last_run": config.hosts_optimizer_status or None,
    }


@router.post("/hosts-optimizer/test", response_model=HostsOptimizerTestResult)
async def test_hosts_optimizer(
    data: HostsOptimizerTestRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """测试 AdGuard 管理接口连通性。"""
    config = await system_config_service.get_or_create(db)
    return await hosts_optimizer_service.test_connection(config, overrides=data)


@router.post("/hosts-optimizer/run", response_model=HostsOptimizerRunOut)
async def run_hosts_optimizer(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """立即执行一次 Hosts 优选（同步等待结果）。"""
    config = await system_config_service.get_or_create(db)
    try:
        return await asyncio.wait_for(
            hosts_optimizer_service.run_and_save(db, config, mode="manual"),
            timeout=180,
        )
    except asyncio.TimeoutError as exc:
        raise APIException("优选超时（180 秒），请检查 AdGuard 连接与网络", 504) from exc


@router.get("/port-suggestions")
async def get_port_suggestions(
    count: int = Query(..., ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取服务端口分配建议。

    从系统设置的 service_port_start 开始顺序挑选 count 个空闲端口，
    供部署表单初始化时预填，与部署时的顺序分配结果保持一致。
    """
    config = await system_config_service.get_or_create(db)
    start = config.service_port_start or 10000
    used = await asyncio.to_thread(get_used_ports)
    try:
        ports = allocate_free_ports(used, count, start)
    except ValueError as e:
        raise APIException(str(e), 400) from e
    return {"ports": ports}


@router.get("/directories", response_model=DirectoryList)
async def list_directories(
    path: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出宿主机指定路径下的目录列表。

    用于前端目录选择器浏览文件系统，与 Docker 无关。
    只能浏览可见存储目录（含应用数据目录）及其子目录。
    目标路径不存在时返回空列表，避免前端目录选择器因自动生成的父路径报错。
    """
    root_paths = await get_browse_roots(db)
    try:
        return filesystem_service.list_directories(path, root_paths=root_paths)
    except APIException as exc:
        if exc.status_code == 404 and "路径不存在" in exc.message:
            return {"path": path, "entries": [], "exists": False}
        raise


@router.post("/directories", response_model=DirectoryEntry)
async def create_directory(
    data: DirectoryCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """在指定父目录下创建子目录。"""
    root_paths = await get_browse_roots(db)
    target_path = f"{data.path.rstrip('/')}/{data.name}"
    return filesystem_service.create_directory(target_path, root_paths=root_paths)


@router.put("/directories", response_model=DirectoryEntry)
async def rename_directory(
    data: DirectoryRenameRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """重命名目录。"""
    root_paths = await get_browse_roots(db)
    return filesystem_service.rename_directory(
        data.old_path,
        data.new_name,
        root_paths=root_paths,
    )


@router.delete("/directories")
async def delete_directory(
    data: DirectoryDeleteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """删除目录（递归删除非空目录）。"""
    root_paths = await get_browse_roots(db)
    filesystem_service.delete_directory(data.path, root_paths=root_paths)
    return {"success": True}


# ---------- 数据目录管理 ----------


@router.get("/storage-directories", response_model=list[StorageDirectoryOut])
async def list_storage_directories(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出全部数据目录（按 sort_order、id 排序），附带容器可见性。"""
    directories = await storage_directory_service.list(db)
    return [storage_directory_service.to_dict(d) for d in directories]


@router.post("/storage-directories", response_model=StorageDirectoryOut)
async def create_storage_directory(
    data: StorageDirectoryCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """创建数据目录。"""
    directory = await storage_directory_service.create(db, data)
    return storage_directory_service.to_dict(directory)


@router.put("/storage-directories/{dir_id}", response_model=StorageDirectoryOut)
async def update_storage_directory(
    dir_id: int,
    data: StorageDirectoryUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """更新数据目录，不存在时返回 404。"""
    directory = await storage_directory_service.update(db, dir_id, data)
    return storage_directory_service.to_dict(directory)


@router.delete("/storage-directories/{dir_id}")
async def delete_storage_directory(
    dir_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """删除数据目录，不存在时返回 404。"""
    await storage_directory_service.delete(db, dir_id)
    return {"success": True}
