"""外部服务（``type=external``）在编排实例组中的登记。

飞牛影视这类跑在宿主机上的服务没有可部署的镜像，部署时只记录访问地址与
可选密钥，不创建 Compose 项目、不分配端口、不产生后台任务。
"""

from app.models.app_store import App
from app.models.orchestration import AppInstance, AppOrchestrationInstance


def is_external_app(app: App | None) -> bool:
    return str(getattr(app, "type", "") or "").lower() == "external"


async def register_external_instance(
    db,
    *,
    group: AppOrchestrationInstance,
    orchestration_id: int,
    instance_name: str,
    app: App,
    app_config: dict | None,
    version: str | None = None,
) -> AppInstance:
    """为外部服务创建一条 running 状态的 AppInstance，并写入实例组配置。"""
    config = {"external": True, **dict(app_config or {})}
    instance = AppInstance(
        app_id=app.id,
        orchestration_id=orchestration_id,
        orchestration_group_id=group.id,
        instance_name=f"{instance_name}-{app.name}",
        config=config,
        orchestration_version=version or app.version,
        status="running",
    )
    db.add(instance)
    await db.flush()
    await db.refresh(instance)

    group.app_configs = {
        **(group.app_configs or {}),
        app.name: dict(app_config or {}),
    }
    return instance
