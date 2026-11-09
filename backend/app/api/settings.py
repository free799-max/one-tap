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
from app.schemas.integration import IntegrationSecretOut, SecretRevealRequest
from app.schemas.hosts_optimizer import (
    AccelerationOverviewOut,
    AccelerationToggleOut,
    AccelerationToggleRequest,
    AdguardDeployRequest,
    AdguardDeployTaskOut,
    AdguardRemoveOut,
    HostsOptimizerConfig,
    HostsOptimizerConfigUpdate,
    HostsOptimizerRunOut,
    HostsOptimizerStatusOut,
    HostsOptimizerTestRequest,
    HostsOptimizerTestResult,
    ManagedDnsRulesOut,
)
from app.schemas.system_config import (
    AutoUpdateTargetsOut,
    DockerMirrorEffectivenessOut,
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
from app.services.system.port_allocator import (
    allocate_free_ports,
    get_reserved_ports,
    get_used_ports,
)
from app.services.system import (
    get_browse_roots,
    hosts_acceleration_service,
    hosts_optimizer_service,
    proxy_test_service,
    registry_mirror_service,
    storage_directory_service,
    system_config_service,
)
from app.services.system.hosts_acceleration_service import ADGUARD_PROJECT
from app.services.integrations.secret_reveal import authorize_secret_reveal
from app.services.system.hosts_optimizer_service import mask_run_ips

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
    return system_config_service.to_dict(config, include_password=False)


@router.put("", response_model=SystemConfigOut)
async def update_system_config(
    data: SystemConfigUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """更新系统全局配置。"""
    config = await system_config_service.update(db, data)
    return system_config_service.to_dict(config, include_password=False)


@router.post("/secret", response_model=IntegrationSecretOut)
async def reveal_system_admin_secret(
    data: SecretRevealRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """通过二次验证后返回统一服务账号的管理员密码。"""
    grant = authorize_secret_reveal(
        current_user, data.password, data.reveal_token
    )
    config = await system_config_service.get_or_create(db)
    password = config.default_admin_password or ""
    if not password:
        raise APIException("尚未设置统一管理员密码", 404)
    return IntegrationSecretOut(
        password=password,
        reveal_token=grant.token,
        expires_at=grant.expires_at,
    )


@router.get("/auto-update/targets", response_model=AutoUpdateTargetsOut)
async def list_auto_update_targets(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出可勾选自动更新的组合部署与单应用实例。"""
    from app.services.orchestration.auto_update_targets import (
        list_auto_update_targets as list_targets,
    )

    return {"targets": await list_targets(db)}


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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取免费镜像加速地址与本机授权专属域名。"""
    return await registry_mirror_service.fetch_all(db)


@router.get("/docker-mirrors/effectiveness", response_model=DockerMirrorEffectivenessOut)
async def get_docker_mirror_effectiveness(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """实际探测授权专属镜像源，返回加速是否可用。"""
    return await registry_mirror_service.effectiveness(db)


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
    return hosts_optimizer_service.config_from_model(config, include_password=False)


@router.put("/hosts-optimizer/config", response_model=HostsOptimizerConfig)
async def update_hosts_optimizer_config(
    data: HostsOptimizerConfigUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """保存 Hosts 优选配置（整份提交）。

    托管 AdGuard 的账号密码变更时同步写入实例配置并重启，避免界面里
    保存的密码与实际登录凭据不一致。
    """
    current = await system_config_service.get_or_create(db)
    previous = (
        current.hosts_optimizer_adguard_username,
        current.hosts_optimizer_adguard_password,
    )
    config = await hosts_optimizer_service.update_config(db, data)
    credentials_changed = previous != (
        config.hosts_optimizer_adguard_username,
        config.hosts_optimizer_adguard_password,
    )
    if config.hosts_optimizer_adguard_managed and credentials_changed:
        await hosts_acceleration_service.apply_managed_credentials(config)
    hosts_optimizer_service.wake()
    return hosts_optimizer_service.config_from_model(config, include_password=False)


@router.post("/hosts-optimizer/secret", response_model=IntegrationSecretOut)
async def reveal_hosts_optimizer_secret(
    data: SecretRevealRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """通过二次验证后返回已保存的 AdGuard 登录密码。"""
    grant = authorize_secret_reveal(
        current_user, data.password, data.reveal_token
    )
    config = await system_config_service.get_or_create(db)
    password = config.hosts_optimizer_adguard_password or ""
    if not password:
        raise APIException("尚未保存密码", 404)
    return IntegrationSecretOut(
        password=password,
        reveal_token=grant.token,
        expires_at=grant.expires_at,
    )


@router.get("/hosts-optimizer/status", response_model=HostsOptimizerStatusOut)
async def get_hosts_optimizer_status(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取当前运行状态与最近一次结果。

    结果只包含域名数量与最优延迟，具体 IP 不出接口，避免截图或抓包暴露网络环境。
    """
    config = await system_config_service.get_or_create(db)
    return {
        "running": hosts_optimizer_service.running,
        "next_run_at": hosts_optimizer_service.next_run_at(config),
        "last_run": mask_run_ips(config.hosts_optimizer_status or None),
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
    """立即执行一次 Hosts 优选（同步等待结果）。

    返回结果同样只保留数量与最优延迟，不下发具体 IP。
    """
    config = await system_config_service.get_or_create(db)
    try:
        result = await asyncio.wait_for(
            hosts_optimizer_service.run_and_save(db, config, mode="manual"),
            timeout=180,
        )
    except asyncio.TimeoutError as exc:
        raise APIException("优选超时（180 秒），请检查 AdGuard 连接与网络", 504) from exc
    return mask_run_ips(result)


# ---------- 应用级 DNS 加速 ----------


@router.get(
    "/hosts-optimizer/acceleration",
    response_model=AccelerationOverviewOut,
)
async def get_hosts_acceleration_overview(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取应用加速、AdGuard 与管理端规则状态。"""
    config = await system_config_service.get_or_create(db)
    return await hosts_acceleration_service.overview(db, config)


@router.post(
    "/hosts-optimizer/acceleration/apps/{instance_id}",
    response_model=AccelerationToggleOut,
)
async def toggle_app_acceleration(
    instance_id: int,
    data: AccelerationToggleRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """开启或关闭单个应用的 DNS 加速。

    开启时会按需自动部署 AdGuard Home、写入应用覆盖配置并重启该应用。
    """
    config = await system_config_service.get_or_create(db)
    try:
        return await asyncio.wait_for(
            hosts_acceleration_service.set_app_acceleration(
                db, config, instance_id, data.enabled
            ),
            timeout=300,
        )
    except asyncio.TimeoutError as exc:
        raise APIException(
            "应用加速操作超时（300 秒），请查看 AdGuard 或应用容器日志", 504
        ) from exc


@router.post(
    "/hosts-optimizer/acceleration/adguard/deploy",
    response_model=AdguardDeployTaskOut,
)
async def deploy_hosts_adguard(
    data: AdguardDeployRequest | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """自动部署并初始化 OneTap 专用的 AdGuard Home。

    `data` 来自应用市场的安装弹窗；不传时由后端自动选择端口和默认配置。
    接口立即返回部署任务 ID，前端通过统一进度弹窗查看部署与初始化进度。
    """
    config = await system_config_service.get_or_create(db)
    task_id = await hosts_acceleration_service.start_deploy_managed(
        db,
        config,
        app_config=data.config if data else None,
    )
    return AdguardDeployTaskOut(
        task_id=task_id, instance_name=ADGUARD_PROJECT
    )


@router.delete(
    "/hosts-optimizer/acceleration/adguard",
    response_model=AdguardRemoveOut,
)
async def remove_hosts_adguard(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """关闭所有应用加速并删除 OneTap 托管的 AdGuard Home。"""
    config = await system_config_service.get_or_create(db)
    try:
        return await asyncio.wait_for(
            hosts_acceleration_service.delete_managed(db, config),
            timeout=300,
        )
    except asyncio.TimeoutError as exc:
        raise APIException(
            "删除 AdGuard Home 超时（300 秒），请查看应用或容器日志", 504
        ) from exc


@router.post(
    "/hosts-optimizer/managed-rules/sync",
    response_model=ManagedDnsRulesOut,
)
async def sync_hosts_managed_rules(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """从配套管理端同步加速域名规则。"""
    config = await system_config_service.get_or_create(db)
    try:
        return await hosts_optimizer_service.sync_managed_rules(db, config)
    except APIException:
        raise
    except Exception as exc:
        raise APIException(f"同步管理端加速规则失败：{exc}", 502) from exc


@router.get("/port-suggestions")
async def get_port_suggestions(
    count: int = Query(..., ge=1, le=200),
    preferred: str | None = Query(
        None, max_length=200, description="优先复用且确认空闲的端口，逗号分隔"
    ),
    exclude_container: str | None = Query(
        None, max_length=100, description="忽略该容器已映射的端口"
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取服务端口分配建议。

    优先复用 ``preferred`` 中仍然空闲的端口；不足时从系统设置的
    service_port_start 开始顺序挑选，供部署表单初始化时预填。
    ``exclude_container`` 用于重建同一服务时忽略旧容器占用的端口。
    """
    config = await system_config_service.get_or_create(db)
    start = config.service_port_start or 10000
    excluded = {exclude_container} if exclude_container else None
    used = (
        await asyncio.to_thread(get_used_ports, excluded)
        if excluded
        else await asyncio.to_thread(get_used_ports)
    )
    reserved = get_reserved_ports()

    ports: list[int] = []
    for part in (preferred or "").split(","):
        try:
            port = int(part.strip())
        except ValueError:
            continue
        if port < 1 or port > 65535:
            continue
        if port in used or port in reserved or port in ports:
            continue
        ports.append(port)
        if len(ports) >= count:
            break

    if len(ports) < count:
        try:
            ports.extend(
                allocate_free_ports(
                    used | set(ports), count - len(ports), start
                )
            )
        except ValueError as e:
            raise APIException(str(e), 400) from e
    return {"ports": ports[:count]}


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
