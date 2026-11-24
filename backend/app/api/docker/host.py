"""Docker 宿主机信息相关 API。"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.custom_route import CustomAPIRoute
from app.core.exceptions import APIException
from app.core.security import get_current_user, require_admin
from app.database import get_db
from app.models.user import User
from app.schemas.docker import (
    DirectoryCreateRequest,
    DirectoryDeleteRequest,
    DirectoryEntry,
    DirectoryList,
    DirectoryRenameRequest,
    HostInfo,
)
from app.services.docker import docker_manager
from app.services.system import get_browse_roots

router = APIRouter(route_class=CustomAPIRoute)


@router.get("/host/directories", response_model=DirectoryList)
async def list_host_directories(
    path: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出宿主机指定路径下的目录列表。

    仅返回子目录，用于前端目录选择器浏览文件系统。
    只能浏览可见存储目录（含应用数据目录）及其子目录。
    目标路径不存在时返回空列表，避免前端目录选择器因自动生成的父路径报错。
    """
    root_paths = await get_browse_roots(db)
    try:
        return docker_manager.list_directories(path, root_paths=root_paths)
    except APIException as exc:
        if exc.status_code == 404 and "路径不存在" in exc.message:
            return {"path": path, "entries": [], "exists": False}
        raise


@router.post("/host/directories", response_model=DirectoryEntry)
async def create_host_directory(
    data: DirectoryCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """在指定父目录下创建子目录。"""
    root_paths = await get_browse_roots(db)
    target_path = f"{data.path.rstrip('/')}/{data.name}"
    return docker_manager.create_directory(target_path, root_paths=root_paths)


@router.put("/host/directories", response_model=DirectoryEntry)
async def rename_host_directory(
    data: DirectoryRenameRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """重命名目录。"""
    root_paths = await get_browse_roots(db)
    return docker_manager.rename_directory(
        data.old_path,
        data.new_name,
        root_paths=root_paths,
    )


@router.delete("/host/directories")
async def delete_host_directory(
    data: DirectoryDeleteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """删除目录（递归删除非空目录）。"""
    root_paths = await get_browse_roots(db)
    docker_manager.delete_directory(data.path, root_paths=root_paths)
    return {"success": True}


@router.delete("/host/networks/{network_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_host_network(
    network_id: str,
    current_user: User = Depends(require_admin),
):
    """删除未被容器使用的自定义 Docker 网络。"""
    if not await docker_manager.run_sync(docker_manager.check_available):
        raise APIException("Docker 不可用", 503)
    try:
        await docker_manager.run_sync(
            docker_manager.remove_network, network_id
        )
    except ValueError as exc:
        raise APIException(str(exc), 400) from exc
    return None


@router.get("/host/info", response_model=HostInfo)
async def get_host_info(current_user: User = Depends(get_current_user)):
    """获取 Docker 宿主机综合信息。"""
    if not await docker_manager.run_sync(docker_manager.check_available):
        raise APIException("Docker 不可用", 503)
    info = await docker_manager.run_sync(docker_manager.get_host_info)
    if not info:
        raise APIException("Docker 不可用", 503)
    return info
