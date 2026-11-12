"""应用集成 API。

提供 Vaultwarden 认证集成与网络集成（Lucky 或
Nginx Proxy Manager + DDNS-Go）的配置、连接测试、应用凭据同步、
反向代理和 DDNS 管理接口。
"""

from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.custom_route import CustomAPIRoute
from app.core.security import get_current_user, require_admin
from app.database import get_db
from app.models.user import User
from app.schemas.integration import (
    IntegrationSecretOut,
    IntegrationSummaryOut,
    IntegrationSyncResult,
    IntegrationTestResult,
    NetworkAppOut,
    NetworkCertificateOut,
    NetworkCertificateRequest,
    NetworkCertificateResult,
    NetworkConfigOut,
    NetworkDdnsStateOut,
    NetworkDdnsUpdate,
    NetworkDdnsInterfacesOut,
    NetworkHostsOut,
    NetworkLuckyDeployRequest,
    NetworkLuckyDeployTaskOut,
    NetworkLuckyRemoveOut,
    NetworkProxyRequest,
    NetworkProxyResult,
    NetworkSecretRequest,
    NetworkTestRequest,
    NetworkUpdate,
    NpmCertificateOut,
    SecretRevealRequest,
    VaultwardenAppOut,
    VaultwardenConfigOut,
    VaultwardenDeployRequest,
    VaultwardenDeployTaskOut,
    VaultwardenRemoveOut,
    VaultwardenSyncRequest,
    VaultwardenTestRequest,
    VaultwardenUpdate,
)
from app.services.integrations import integration_service
from app.services.integrations.hosts import host_candidates
from app.services.integrations.secret_reveal import authorize_secret_reveal
from app.services.integrations.vaultwarden_deployment import (
    VAULTWARDEN_PROJECT,
    vaultwarden_deployment_service,
)
from app.services.integrations.lucky_deployment import (
    LUCKY_PROJECT,
    lucky_deployment_service,
)

router = APIRouter(
    prefix="/api/integrations",
    tags=["integrations"],
    route_class=CustomAPIRoute,
)


def _request_host(request: Request) -> str:
    """从请求头解析当前访问主机，用于生成应用内网跳转地址。"""
    raw = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    host = raw.split(",")[0].strip()
    parsed = urlparse(f"//{host}")
    return parsed.hostname or "127.0.0.1"


@router.get("", response_model=list[IntegrationSummaryOut])
async def list_integrations(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取两类集成的启用与配置状态。"""
    return await integration_service.list_summaries(db)


# ---------- Vaultwarden 认证集成 ----------


@router.get("/vaultwarden", response_model=VaultwardenConfigOut)
async def get_vaultwarden(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取 Vaultwarden 集成配置。"""
    return await integration_service.get_vaultwarden(db)


@router.put("/vaultwarden", response_model=VaultwardenConfigOut)
async def update_vaultwarden(
    data: VaultwardenUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """保存 Vaultwarden 连接配置。"""
    return await integration_service.save_vaultwarden(db, data)


@router.post("/vaultwarden/deploy", response_model=VaultwardenDeployTaskOut)
async def deploy_vaultwarden(
    data: VaultwardenDeployRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """后台部署 Vaultwarden，并自动初始化账号和验证登录。"""
    task_id = await vaultwarden_deployment_service.start_deploy_managed(
        db,
        email=data.email,
        password=data.password,
        web_port=data.web_port,
        app_config=data.config,
    )
    return VaultwardenDeployTaskOut(
        task_id=task_id, instance_name=VAULTWARDEN_PROJECT
    )


@router.delete("/vaultwarden/managed", response_model=VaultwardenRemoveOut)
async def remove_vaultwarden(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """删除 OneTap 托管实例并备份其数据。"""
    return await vaultwarden_deployment_service.delete_managed(db)


@router.post("/vaultwarden/secret", response_model=IntegrationSecretOut)
async def reveal_vaultwarden_secret(
    data: SecretRevealRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """通过二次验证后返回已保存的 Vaultwarden 主密码。"""
    grant = authorize_secret_reveal(
        current_user, data.password, data.reveal_token
    )
    return IntegrationSecretOut(
        password=await integration_service.reveal_vaultwarden_password(db),
        reveal_token=grant.token,
        expires_at=grant.expires_at,
    )


@router.post("/vaultwarden/test", response_model=IntegrationTestResult)
async def test_vaultwarden(
    data: VaultwardenTestRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """测试 Vaultwarden 地址与账号密码。"""
    return await integration_service.test_vaultwarden(
        db, data.url, data.email, data.password
    )


@router.get("/vaultwarden/apps", response_model=list[VaultwardenAppOut])
async def list_vaultwarden_apps(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出已部署应用及可同步的账号密码。"""
    return await integration_service.list_vaultwarden_apps(db, _request_host(request))


@router.post("/vaultwarden/sync", response_model=IntegrationSyncResult)
async def sync_vaultwarden(
    data: VaultwardenSyncRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """把已部署应用的账号密码同步到 Vaultwarden。"""
    return await integration_service.sync_vaultwarden(
        db, data.instance_ids, _request_host(request)
    )


# ---------- 网络集成（Lucky / Nginx Proxy Manager + DDNS-Go）----------


@router.get("/network/hosts", response_model=NetworkHostsOut)
async def list_network_hosts(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """列出可作为反向代理后端的内网地址，当前访问地址排第一。"""
    host = _request_host(request)
    return NetworkHostsOut(current=host, candidates=host_candidates(host))


@router.get("/network", response_model=NetworkConfigOut)
async def get_network(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取网络集成配置与当前反代方案。"""
    return await integration_service.get_network(db)


@router.put("/network", response_model=NetworkConfigOut)
async def update_network(
    data: NetworkUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """保存网络集成配置，切换方案时保留其他方案的连接信息。"""
    return await integration_service.save_network(db, data)


@router.post("/network/lucky/deploy", response_model=NetworkLuckyDeployTaskOut)
async def deploy_network_lucky(
    data: NetworkLuckyDeployRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """后台部署 OneTap 托管的 Lucky，并自动初始化管理账号。"""
    task_id = await lucky_deployment_service.start_deploy_managed(
        db,
        username=data.username,
        password=data.password,
        web_port=data.web_port,
        config=data.config,
    )
    return NetworkLuckyDeployTaskOut(
        task_id=task_id, instance_name=LUCKY_PROJECT
    )


@router.delete(
    "/network/lucky/managed", response_model=NetworkLuckyRemoveOut
)
@router.delete(
    "/network/lucky/instance", response_model=NetworkLuckyRemoveOut
)
async def remove_network_lucky(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """删除 OneTap 托管的 Lucky，或识别到的外部 Lucky 容器。"""
    return await lucky_deployment_service.delete_instance(db)


@router.post("/network/test", response_model=IntegrationTestResult)
async def test_network(
    data: NetworkTestRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """测试 Lucky、Nginx Proxy Manager 或 DDNS-Go 连接。"""
    return await integration_service.test_network(db, data)


@router.post("/network/secret", response_model=IntegrationSecretOut)
async def reveal_network_secret(
    data: NetworkSecretRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """通过二次验证后返回已保存的连接密码明文。"""
    grant = authorize_secret_reveal(
        current_user, data.password, data.reveal_token
    )
    return IntegrationSecretOut(
        password=await integration_service.reveal_network_password(
            db, data.provider
        ),
        reveal_token=grant.token,
        expires_at=grant.expires_at,
    )


@router.get("/network/apps", response_model=list[NetworkAppOut])
async def list_network_apps(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出已部署应用及已配置的域名。"""
    return await integration_service.list_network_apps(
        db, _request_host(request)
    )


@router.put("/network/proxy", response_model=NetworkProxyResult)
async def upsert_network_proxy(
    data: NetworkProxyRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """为应用创建或更新反向代理转发。"""
    return await integration_service.upsert_network_proxy(
        db, data, _request_host(request)
    )


@router.delete("/network/proxy/{instance_id}", response_model=NetworkProxyResult)
async def delete_network_proxy(
    instance_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """移除应用的反向代理转发。"""
    return await integration_service.delete_network_proxy(db, instance_id)


@router.get(
    "/network/npm/certificates", response_model=list[NpmCertificateOut]
)
async def list_npm_certificates(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """列出 Nginx Proxy Manager 中可选的 SSL 证书。"""
    return await integration_service.list_npm_certificates(db)


@router.get(
    "/network/certificates", response_model=list[NetworkCertificateOut]
)
async def list_network_certificates(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """列出当前反代方案里可选的证书（Lucky 证书按域名自动匹配）。"""
    return await integration_service.list_network_certificates(db)


@router.post(
    "/network/certificates/auto", response_model=NetworkCertificateResult
)
async def auto_provision_certificate(
    data: NetworkCertificateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """自动添加证书：内网域名自签名，公网域名提交 ACME 申请。"""
    return await integration_service.auto_provision_certificate(db, data)


@router.get("/network/ddns", response_model=NetworkDdnsStateOut)
async def get_network_ddns(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取 DDNS 配置与外部系统中的任务列表。"""
    return await integration_service.network_ddns_state(db)


@router.get(
    "/network/ddns/interfaces", response_model=NetworkDdnsInterfacesOut
)
async def list_network_ddns_interfaces(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Lucky 所在主机的网卡，供 DDNS 直接按网卡取 IP。"""
    return await integration_service.network_ddns_interfaces(db)


@router.put("/network/ddns", response_model=NetworkDdnsStateOut)
async def save_network_ddns(
    data: NetworkDdnsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """保存 DDNS 配置并同步到当前网络方案。"""
    return await integration_service.save_network_ddns(db, data)


@router.delete("/network/ddns", response_model=NetworkDdnsStateOut)
async def delete_network_ddns(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """删除 OneTap 创建的 DDNS 任务。"""
    return await integration_service.delete_network_ddns(db)
