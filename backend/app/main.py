"""OneTap 应用入口模块。

负责创建 FastAPI 应用实例、注册中间件和路由、
管理应用生命周期（数据库初始化、插件发现、定时调度）。
"""

from contextlib import asynccontextmanager

import logging
import secrets

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.exceptions import APIException
from app.schemas.response import StandardResponse
from app.core.custom_route import CustomAPIRoute

from app.config import DEFAULT_SECRET_KEY, settings
from app.api.auth import router as auth_router
from app.api.orchestration import deploy_tasks_router, orchestrations_router
from app.api.app_store import router as apps_router
from app.api.dashboard import router as dashboard_router
from app.api.settings import router as settings_router
from app.api.license import router as license_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理：启动时初始化资源，关闭时释放资源。"""
    # SECRET_KEY 仍为默认占位值时，生成随机密钥并打印醒目警告；
    # 不硬失败以免打断开发，但重启后旧令牌会全部失效
    if settings.SECRET_KEY == DEFAULT_SECRET_KEY:
        settings.SECRET_KEY = secrets.token_urlsafe(48)
        warning = (
            "检测到 SECRET_KEY 使用默认值，已自动生成随机密钥。"
            "生产环境请通过环境变量 SECRET_KEY 配置固定密钥，"
            "否则每次重启后所有登录状态都会失效！"
        )
        logger.warning(warning)
        print(f"\n*** WARNING: {warning} ***\n", flush=True)

    # 执行数据库迁移（包含 schema 与内置数据初始化）
    import asyncio
    from pathlib import Path
    from alembic.config import Config
    from alembic import command

    backend_dir = Path(__file__).resolve().parent.parent
    alembic_cfg = Config(str(backend_dir / "alembic.ini"))
    # alembic env 内部使用 asyncio.run，必须在独立线程执行以避免与当前事件循环冲突
    await asyncio.to_thread(command.upgrade, alembic_cfg, "head")

    # 启动 reconciliation：进程重启后旧的部署协程已消亡，
    # 残留的 deploying 实例/实例组不可能仍在部署，统一置为 error 以便用户重试
    from sqlalchemy import update

    from app.database import async_session
    from app.models.orchestration import AppInstance, AppOrchestrationInstance

    async with async_session() as session:
        result = await session.execute(
            update(AppInstance)
            .where(AppInstance.status == "deploying")
            .values(status="error")
        )
        group_result = await session.execute(
            update(AppOrchestrationInstance)
            .where(AppOrchestrationInstance.status == "deploying")
            .values(status="error")
        )
        await session.commit()
        if result.rowcount:
            logger.warning(
                "启动 reconciliation：%s 个卡在 deploying 的实例已置为 error",
                result.rowcount,
            )
        if group_result.rowcount:
            logger.warning(
                "启动 reconciliation：%s 个卡在 deploying 的实例组已置为 error",
                group_result.rowcount,
            )

    # 目录同步：配置管理端地址后，启动时同步一次并按间隔循环同步，
    # 失败仅告警不阻断启动；服务关闭时取消后台同步任务
    sync_task = None
    from app.services.manage_client import manage_base_urls

    if manage_base_urls():
        from app.services import catalog_sync

        async def _sync_catalog(stage: str) -> None:
            """依次同步分类字典、应用市场与编排方案，单类失败不影响其他。"""
            for label, sync in (
                ("分类字典", catalog_sync.sync_categories),
                ("应用市场", catalog_sync.sync_apps),
                ("编排方案", catalog_sync.sync_orchestrations),
            ):
                try:
                    await sync()
                except Exception as exc:
                    logger.warning("%s同步%s失败：%s", stage, label, exc)

        if settings.MANAGE_SYNC_ON_STARTUP:
            await _sync_catalog("启动时")

        async def _sync_loop():
            """按配置间隔循环同步应用市场与编排方案，异常仅记录告警。"""
            while True:
                await asyncio.sleep(settings.MANAGE_SYNC_INTERVAL_MINUTES * 60)
                await _sync_catalog("定时")

        sync_task = asyncio.create_task(_sync_loop())

    # 音乐自动整理：按实例组应用配置中的间隔定时扫描并调用
    # Music Tag Web 完成刮削/整理，不依赖改造镜像。
    from app.services.orchestration.music_auto import music_auto_service

    music_auto_service.start_scheduler()

    # Reader 自动同步：定时导出书架 EPUB 到二毛书库并触发扫描。
    from app.services.orchestration.reader_auto import reader_auto_service

    reader_auto_service.start_scheduler()

    # Hosts 优选：按系统设置中的模式定时维护 AdGuard DNS 重写。
    from app.services.system.hosts_optimizer_service import hosts_optimizer_service

    hosts_optimizer_service.start_scheduler()

    # 授权证书启动时刷新一次，之后按配置周期刷新。
    from app.services.licensing import licensing_service

    licensing_service.start_scheduler()

    yield

    await music_auto_service.stop_scheduler()
    await reader_auto_service.stop_scheduler()
    await hosts_optimizer_service.stop_scheduler()
    await licensing_service.stop_scheduler()

    if sync_task is not None:
        sync_task.cancel()
        try:
            await sync_task
        except asyncio.CancelledError:
            pass


# 创建 FastAPI 应用实例
app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)
app.router.route_class = CustomAPIRoute


@app.exception_handler(APIException)
async def api_exception_handler(request: Request, exc: APIException):
    """捕获业务 API 异常，返回统一格式。"""
    return JSONResponse(
        status_code=exc.status_code,
        content=StandardResponse.fail(exc.message).model_dump(),
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """捕获 FastAPI HTTPException，统一包装为标准格式。"""
    message = exc.detail if isinstance(exc.detail, str) else "请求失败"
    return JSONResponse(
        status_code=exc.status_code,
        content=StandardResponse.fail(message).model_dump(),
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """捕获未预料异常，返回 500 统一格式。"""
    logger.exception("未处理异常: %s", exc)
    return JSONResponse(
        status_code=500,
        content=StandardResponse.fail("服务器内部错误").model_dump(),
    )

# 配置 CORS 中间件，允许的来源由 CORS_ORIGINS 配置（逗号分隔）
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list(),
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册所有 API 路由
app.include_router(auth_router)
app.include_router(orchestrations_router)
app.include_router(apps_router)
app.include_router(dashboard_router)
app.include_router(deploy_tasks_router, prefix="/api")
app.include_router(settings_router)
app.include_router(license_router)

# Docker 管理路由（依赖可选的 Docker 环境）
from app.api.docker import router as docker_router
app.include_router(docker_router)


@app.get("/api/health")
async def health_check():
    """健康检查端点，用于监控应用运行状态。"""
    return {"status": "ok", "app": settings.APP_NAME}
