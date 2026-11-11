"""应用编排业务服务。"""

import asyncio
import ipaddress
import logging
import socket
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import APIException
from app.models.orchestration import AppOrchestration
from app.services.apps import AuthVerifyResult, get_client
from app.services.orchestration.deploy import DeployMixin
from app.services.orchestration.importer import ImportMixin
from app.services.orchestration.inspection import InspectionMixin
from app.services.orchestration.instances import InstanceMixin
from app.services.orchestration.provisioning import ProvisionMixin
from app.services.orchestration.restore import RestoreMixin
from app.services.orchestration.upgrade import UpgradeMixin
from app.services.orchestration.wiring import WiringMixin

logger = logging.getLogger(__name__)


class OrchestrationService(
    DeployMixin,
    UpgradeMixin,
    RestoreMixin,
    ImportMixin,
    ProvisionMixin,
    WiringMixin,
    InspectionMixin,
    InstanceMixin,
):
    """应用编排服务。

    负责自动化分类组合模板的查询与组合部署。
    一个编排对应一组应用商店 App 的组合关系，部署时拆分为多个独立的
    Docker Compose 项目。
    """

    async def list_orchestrations(
        self,
        db: AsyncSession,
        category: str | None = None,
    ) -> list[AppOrchestration]:
        """列出应用编排，支持分类筛选。"""
        stmt = select(AppOrchestration).where(AppOrchestration.is_listed.is_(True))
        if category:
            stmt = stmt.where(AppOrchestration.category == category)
        stmt = stmt.order_by(AppOrchestration.sort_order, AppOrchestration.id)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def get_orchestration(self, db: AsyncSession, name: str) -> AppOrchestration:
        """获取单个编排详情。"""
        result = await db.execute(
            select(AppOrchestration).where(AppOrchestration.name == name)
        )
        orchestration = result.scalar_one_or_none()
        if orchestration is None:
            raise APIException("编排不存在", 404)
        return orchestration

    async def verify_app_auth(
        self,
        app_name: str,
        config: dict,
    ) -> AuthVerifyResult:
        """调用应用专属客户端验证认证信息（带 SSRF 防护）。"""
        rejection = await self._check_verify_url(config.get("url"))
        if rejection:
            return AuthVerifyResult(valid=False, message=rejection)
        client_cls = get_client(app_name)
        client = client_cls()
        return await client.verify_auth(config)

    @staticmethod
    async def _check_verify_url(url: object) -> str | None:
        """校验待探测的应用访问地址，阻断 SSRF 高风险目标。

        阻断 link-local / 云元数据地址（169.254.0.0/16、fe80::/10）、
        多播与保留地址；放行回环地址与内网（含 Docker 网段）地址，
        因为应用通常部署在宿主机或内网中。

        Returns:
            拒绝原因；允许访问时返回 None
        """
        raw = str(url or "").strip()
        if not raw:
            return None  # 空地址由应用客户端自行提示
        parsed = urlparse(raw if "://" in raw else f"http://{raw}")
        host = parsed.hostname
        if not host:
            return "访问地址无效，无法解析主机名"

        try:
            addrinfos = await asyncio.to_thread(socket.getaddrinfo, host, None)
        except socket.gaierror:
            return f"无法解析访问地址的主机名: {host}"
        except Exception:
            logger.exception("解析访问地址主机名失败: %s", host)
            return f"无法解析访问地址的主机名: {host}"

        for info in addrinfos:
            try:
                ip = ipaddress.ip_address(info[4][0])
            except ValueError:
                continue
            if ip.is_link_local:
                return f"目标地址 {ip} 属于 link-local/元数据网段，禁止访问"
            if ip.is_multicast or ip.is_unspecified or ip.is_reserved:
                return f"目标地址 {ip} 属于受限网段，禁止访问"
        return None


# 全局单例
orchestration_service = OrchestrationService()
