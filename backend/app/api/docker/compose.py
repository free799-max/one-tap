"""Docker Compose 编排相关 API。"""

import asyncio
import json as _json
import logging

from fastapi import APIRouter, Depends, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.background import create_background_task
from app.core.custom_route import CustomAPIRoute
from app.core.exceptions import APIException
from app.core.security import get_current_user, get_current_user_sse, require_admin
from app.database import async_session, get_db
from app.models.docker import DockerComposeProject, DockerComposeStack, DockerComposeVersion
from app.models.user import User
from app.schemas.docker import (
    ComposeActionRequest,
    ComposeEditRequest,
    ComposeProjectCreate,
    ComposeProjectOut,
    ComposeProjectUpdate,
    ComposeStackStatusOut,
    ComposeVersionCreate,
    ComposeVersionOut,
    ContainerInfo,
)
from app.schemas.orchestration.deploy_task import ComposeDeployResponse
from app.services.compose import compose_manager
from app.services.docker import docker_manager
from app.services.orchestration.deploy_task_service import deploy_task_manager
from app.services.system.host_path_aliases import host_path_aliases

logger = logging.getLogger(__name__)
router = APIRouter(route_class=CustomAPIRoute)


def _build_version_out(version: DockerComposeVersion | None) -> ComposeVersionOut | None:
    """将版本模型转换为响应 schema。"""
    if not version:
        return None
    return ComposeVersionOut(
        id=version.id,
        version_number=version.version_number,
        content=version.content,
        comment=version.comment,
        is_current=version.is_current,
        created_by_user_id=version.created_by_user_id,
        created_at=version.created_at.isoformat() if version.created_at else "",
    )


def _build_stack_out(
    stack: DockerComposeStack | None,
    status_info: dict | None = None,
) -> ComposeStackStatusOut | None:
    """将 Stack 状态模型转换为响应 schema。"""
    if status_info is not None:
        ports = sorted(status_info.get("ports", []))
        return ComposeStackStatusOut(
            status=status_info.get("status", "unknown"),
            service_count=status_info.get("service_count", 0),
            running_count=status_info.get("running_count", 0),
            ports=ports,
            last_action=stack.last_action if stack else None,
            last_action_at=stack.last_action_at.isoformat() if stack and stack.last_action_at else None,
            updated_at=stack.updated_at.isoformat() if stack and stack.updated_at else "",
        )
    if not stack:
        return None
    ports = []
    if stack.ports:
        try:
            ports = _json.loads(stack.ports)
        except _json.JSONDecodeError:
            ports = []
    return ComposeStackStatusOut(
        status=stack.status,
        service_count=stack.service_count,
        running_count=stack.running_count,
        ports=ports,
        last_action=stack.last_action,
        last_action_at=stack.last_action_at.isoformat() if stack.last_action_at else None,
        updated_at=stack.updated_at.isoformat() if stack.updated_at else "",
    )


def _build_project_out(
    project: DockerComposeProject,
    status_info: dict | None = None,
) -> ComposeProjectOut:
    """将项目模型转换为响应 schema。"""
    current_version = None
    for version in project.versions:
        if version.is_current:
            current_version = version
            break
    config_files = []
    if project.config_files:
        try:
            config_files = _json.loads(project.config_files)
        except _json.JSONDecodeError:
            config_files = []
    host_config_files = [host_path_aliases.to_host_path(path) for path in config_files]
    host_working_dir = (
        host_path_aliases.to_host_path(project.working_dir)
        if project.working_dir
        else None
    )
    compose_available, compose_unavailable_reason = (
        compose_manager.project_availability(project)
    )
    return ComposeProjectOut(
        id=project.id,
        project_name=project.project_name,
        description=project.description,
        is_active=project.is_active,
        current_version=_build_version_out(current_version),
        stack=_build_stack_out(project.stack, status_info=status_info),
        config_files=config_files or None,
        working_dir=project.working_dir,
        host_config_files=(
            host_config_files if host_config_files != config_files else None
        ),
        host_working_dir=(
            host_working_dir if host_working_dir != project.working_dir else None
        ),
        compose_available=compose_available,
        compose_unavailable_reason=compose_unavailable_reason,
        created_at=project.created_at.isoformat() if project.created_at else "",
        updated_at=project.updated_at.isoformat() if project.updated_at else "",
    )


async def _live_status_or_none(project: DockerComposeProject) -> dict | None:
    """获取项目实时状态；失败且环境中无存活容器时返回停止状态。

    compose 文件缺失（如项目已随环境迁移消失）会导致 docker compose ps 报错，
    此时再按项目标签确认容器是否存活：一个都没有说明项目在当前环境已不存在，
    返回停止状态，避免调用方回退到数据库里过期的 running 缓存。
    """
    try:
        return await compose_manager.get_status(project)
    except Exception:
        pass
    if not compose_manager.docker_available:
        return None
    try:
        containers = await docker_manager.run_sync(
            docker_manager.list_containers,
            filters={"label": [f"com.docker.compose.project={project.project_name}"]},
        )
    except Exception:
        return None
    if not containers:
        return {"status": "stopped", "service_count": 0, "running_count": 0, "ports": []}
    return None


async def _get_compose_project(
    db: AsyncSession, project_id: int
) -> DockerComposeProject:
    """获取项目，不存在时抛出 404。"""
    result = await db.execute(
        select(DockerComposeProject)
        .options(
            selectinload(DockerComposeProject.versions),
            selectinload(DockerComposeProject.stack),
        )
        .where(DockerComposeProject.id == project_id)
    )
    project = result.scalar_one_or_none()
    if not project:
        raise APIException("编排项目不存在", 404)
    return project


@router.get("/compose", response_model=list[ComposeProjectOut])
async def list_compose_projects(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取所有 Compose 项目列表。"""
    await compose_manager.discover_projects(db)

    result = await db.execute(
        select(DockerComposeProject)
        .options(
            selectinload(DockerComposeProject.versions),
            selectinload(DockerComposeProject.stack),
        )
        .order_by(DockerComposeProject.id.desc())
    )
    projects = result.scalars().all()

    async def _project_with_status(project: DockerComposeProject) -> ComposeProjectOut:
        status_info = await _live_status_or_none(project)
        return _build_project_out(project, status_info=status_info)

    return await asyncio.gather(*[_project_with_status(p) for p in projects])


@router.post("/compose", response_model=ComposeDeployResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_compose_project(
    data: ComposeProjectCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """创建 Compose 项目（异步）。"""
    project_name = data.project_name
    content = data.content
    description = data.description
    user_id = current_user.id

    try:
        task_id = deploy_task_manager.create_task(
            "compose_deploy",
            meta={
                "project_name": project_name,
                "content": content,
                "description": description,
                "user_id": user_id,
            },
        )
    except RuntimeError as e:
        raise APIException(str(e), 409) from e

    async def _task(task_id: str):
        async with async_session() as task_db:
            try:
                def _progress(stage: str, percentage: int, message: str, detail: str | None = None):
                    deploy_task_manager.update_progress(task_id, stage, percentage, message, detail)

                project = await compose_manager.create_project_async(
                    task_db,
                    project_name=project_name,
                    content=content,
                    user_id=user_id,
                    description=description,
                    progress_callback=_progress,
                )

                # 更新任务中的 project_id
                current = deploy_task_manager.get_task(task_id)
                if current:
                    current["project_id"] = project.id

                deploy_task_manager.complete_task(task_id)
            except IntegrityError:
                await task_db.rollback()
                deploy_task_manager.fail_task(task_id, "项目名已存在")
            except ValueError as e:
                deploy_task_manager.fail_task(task_id, str(e))
            except Exception as e:
                logger.exception("Compose 创建项目失败")
                deploy_task_manager.fail_task(task_id, f"创建项目失败: {e}")

    create_background_task(_task(task_id))

    return ComposeDeployResponse(
        task_id=task_id,
        project_id=0,
        action="create_deploy",
    )


@router.get("/compose/{project_id}", response_model=ComposeProjectOut)
async def get_compose_project(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取单个 Compose 项目详情。"""
    project = await _get_compose_project(db, project_id)
    status_info = await _live_status_or_none(project)
    return _build_project_out(project, status_info=status_info)


@router.put("/compose/{project_id}", response_model=ComposeProjectOut)
async def update_compose_project(
    project_id: int,
    data: ComposeProjectUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """更新 Compose 项目元数据。"""
    project = await _get_compose_project(db, project_id)
    try:
        project = await compose_manager.update_project(
            db,
            project,
            description=data.description,
            is_active=data.is_active,
        )
    except Exception as e:
        raise APIException(f"更新项目失败: {e}", 500)
    return _build_project_out(project)


@router.post("/compose/{project_id}/edit", response_model=ComposeDeployResponse)
async def edit_compose_project(
    project_id: int,
    data: ComposeEditRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """编辑 Compose 项目并自动部署（异步）。"""
    project = await _get_compose_project(db, project_id)
    if deploy_task_manager.has_active_task(project_id=project.id):
        raise APIException("该项目有进行中的部署任务，请等待完成后再试", 409)

    content = data.content
    comment = data.comment
    description = data.description
    user_id = current_user.id

    try:
        task_id = deploy_task_manager.create_task(
            "compose_deploy",
            project_id=project.id,
            meta={
                "content": content,
                "comment": comment,
                "description": description,
                "user_id": user_id,
            },
        )
    except RuntimeError as e:
        raise APIException(str(e), 409) from e

    async def _task(task_id: str):
        async with async_session() as task_db:
            try:
                def _progress(stage: str, percentage: int, message: str, detail: str | None = None):
                    deploy_task_manager.update_progress(task_id, stage, percentage, message, detail)

                result = await task_db.execute(
                    select(DockerComposeProject)
                    .where(DockerComposeProject.id == project_id)
                )
                task_project = result.scalar_one()

                await compose_manager.edit_and_deploy_async(
                    task_db,
                    task_project,
                    content=content,
                    user_id=user_id,
                    comment=comment,
                    description=description,
                    progress_callback=_progress,
                )
                deploy_task_manager.complete_task(task_id)
            except Exception as e:
                logger.exception("Compose 编辑部署失败")
                deploy_task_manager.fail_task(task_id, str(e))

    create_background_task(_task(task_id))

    return ComposeDeployResponse(
        task_id=task_id,
        project_id=project.id,
        action="edit_deploy",
    )


@router.delete("/compose/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_compose_project(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """删除 Compose 项目。"""
    project = await _get_compose_project(db, project_id)
    try:
        # 删除时先把应用数据目录移入 _backups，重新部署从空数据开始。
        await compose_manager.delete_project(db, project, backup_data=True)
    except Exception as e:
        raise APIException(f"删除项目失败: {e}", 500)


@router.get("/compose/{project_id}/versions", response_model=list[ComposeVersionOut])
async def list_compose_versions(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取项目的所有版本列表。"""
    project = await _get_compose_project(db, project_id)
    return [_build_version_out(v) for v in project.versions]


@router.post("/compose/{project_id}/versions", response_model=ComposeVersionOut)
async def create_compose_version(
    project_id: int,
    data: ComposeVersionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """为项目新增一个版本。"""
    project = await _get_compose_project(db, project_id)
    try:
        version = await compose_manager.add_version(
            db,
            project,
            content=data.content,
            user_id=current_user.id,
        )
    except ValueError as e:
        raise APIException(str(e), 400)
    except Exception as e:
        raise APIException(f"新增版本失败: {e}", 500)
    return _build_version_out(version)


@router.post("/compose/{project_id}/versions/{version_id}/rollback", response_model=ComposeVersionOut)
async def rollback_compose_version(
    project_id: int,
    version_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """切换到指定版本。"""
    project = await _get_compose_project(db, project_id)
    version = next((v for v in project.versions if v.id == version_id), None)
    if not version:
        raise APIException("版本不存在", 404)
    try:
        version = await compose_manager.rollback_version(db, project, version)
    except RuntimeError as e:
        raise APIException(str(e), 500)
    except Exception as e:
        raise APIException(f"切换版本失败: {e}", 500)
    return _build_version_out(version)


@router.post("/compose/{project_id}/action", response_model=ComposeDeployResponse)
async def compose_action(
    project_id: int,
    data: ComposeActionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """对 Compose 项目执行 up / down / restart 操作（异步）。"""
    project = await _get_compose_project(db, project_id)
    if deploy_task_manager.has_active_task(project_id=project.id):
        raise APIException("该项目有进行中的部署任务，请等待完成后再试", 409)
    action = data.action

    try:
        task_id = deploy_task_manager.create_task(
            "compose_action",
            project_id=project.id,
            action=action,
            # 前端按 operation/action 展示「启动/停止/重启」文案
            meta={
                "operation": "compose_action",
                "action": action,
                "project_name": project.project_name,
            },
        )
    except RuntimeError as e:
        raise APIException(str(e), 409) from e

    async def _task(task_id: str):
        async with async_session() as task_db:
            try:
                def _progress(stage: str, percentage: int, message: str, detail: str | None = None):
                    deploy_task_manager.update_progress(task_id, stage, percentage, message, detail)

                result = await task_db.execute(
                    select(DockerComposeProject)
                    .where(DockerComposeProject.id == project_id)
                )
                task_project = result.scalar_one()

                await compose_manager.action_async(
                    task_db,
                    task_project,
                    action=action,
                    progress_callback=_progress,
                )
                deploy_task_manager.complete_task(task_id)
            except Exception as e:
                logger.exception("Compose 操作失败")
                deploy_task_manager.fail_task(task_id, str(e))

    create_background_task(_task(task_id))

    return ComposeDeployResponse(
        task_id=task_id,
        project_id=project.id,
        action=action,
    )


@router.get("/compose/{project_id}/status", response_model=ComposeStackStatusOut)
async def compose_status(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取 Compose 项目实时 Stack 状态。"""
    project = await _get_compose_project(db, project_id)
    try:
        stack = await compose_manager.sync_stack_status(db, project)
    except Exception as e:
        raise APIException(f"获取状态失败: {e}", 500)
    return _build_stack_out(stack)


@router.get("/compose/{project_id}/containers", response_model=list[ContainerInfo])
async def list_compose_containers(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取 Compose 项目维护的容器列表。"""
    project = await _get_compose_project(db, project_id)
    if not await docker_manager.run_sync(docker_manager.check_available):
        raise APIException("Docker 不可用", 503)
    filters = {"label": [f"com.docker.compose.project={project.project_name}"]}
    return await docker_manager.run_sync(docker_manager.list_containers, filters=filters)


@router.get("/compose/{project_id}/logs")
async def compose_logs(
    project_id: int,
    tail: int = 100,
    services: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取 Compose 项目日志。"""
    project = await _get_compose_project(db, project_id)
    service_list = services.split(",") if services else None
    try:
        logs = await compose_manager.get_logs(
            project, tail=tail, services=service_list
        )
    except RuntimeError as e:
        raise APIException(str(e), 500)
    except Exception as e:
        raise APIException(f"获取日志失败: {e}", 500)
    return {"logs": logs}


@router.get("/compose/{project_id}/logs/stream")
async def compose_logs_stream(
    project_id: int,
    tail: int = 100,
    follow: bool = True,
    services: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_sse),
):
    """流式获取 Compose 项目日志（SSE）。"""
    if not await docker_manager.run_sync(docker_manager.check_available):
        raise APIException("Docker 不可用", 503)

    project = await _get_compose_project(db, project_id)
    service_list = services.split(",") if services else None

    async def event_generator():
        try:
            async for line in compose_manager.stream_logs(
                project, tail=tail, services=service_list, follow=follow
            ):
                yield f"data: {_json.dumps({'line': line}, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.exception("Compose 日志流异常")
            yield f"data: {_json.dumps({'error': f'日志流异常: {e}'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
