"""应用集成服务。

负责集成配置的持久化、已部署应用凭据整理，以及 Vaultwarden 认证集成
与网络集成（Lucky / Nginx Proxy Manager + DDNS-Go）的测试与同步操作。
"""

import asyncio
import logging
from datetime import datetime
from urllib.parse import urlparse, urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import APIException
from app.models.docker.compose import DockerComposeProject
from app.models.integration import IntegrationConfig
from app.schemas.integration import (
    DdnsGoConnectionOut,
    IntegrationStatusOut,
    IntegrationSyncItem,
    IntegrationSyncResult,
    LuckyConnectionOut,
    NetworkAppOut,
    NetworkCertificateOut,
    NetworkCertificateRequest,
    NetworkCertificateResult,
    NetworkConfigOut,
    NetworkDdnsFamilyOut,
    NetworkDdnsInterfaceOut,
    NetworkDdnsInterfacesOut,
    NetworkDdnsOut,
    NetworkDdnsStateOut,
    NetworkDdnsTaskOut,
    NetworkDdnsUpdate,
    NetworkProxyRequest,
    NetworkProxyResult,
    NetworkTestRequest,
    NetworkUpdate,
    NpmCertificateOut,
    NpmConnectionOut,
    VaultwardenAppOut,
    VaultwardenConfigOut,
    VaultwardenUpdate,
)
from app.services.docker import docker_manager
from app.services.integrations.certificates import (
    build_self_signed,
    is_internal_domain,
)
from app.services.integrations.discovery import (
    DeployedApp,
    get_deployed_app,
    list_deployed_apps,
)
from app.services.integrations.ddnsgo import (
    DdnsGoClient,
    build_entry as build_ddnsgo_entry,
    find_entry as find_ddnsgo_entry,
    merge_entry as merge_ddnsgo_entry,
    remove_entry as remove_ddnsgo_entry,
)
from app.services.integrations.lucky import (
    DDNS_FAMILIES,
    DEFAULT_IPV4_URLS,
    DEFAULT_IPV6_URLS,
    ONETAP_CERT_REMARK,
    LuckyClient,
    build_acme_certificate_payload,
    build_ddns_task,
    build_file_certificate_payload,
    build_proxy_rules,
    certificate_remark,
)
from app.services.integrations.npm import (
    NpmClient,
    build_proxy_host,
)
from app.services.integrations.vaultwarden import (
    VaultwardenClient,
    cipher_name,
)
from app.services.integrations.vaultwarden_deployment import (
    vaultwarden_deployment_service,
)

logger = logging.getLogger(__name__)

VAULTWARDEN = "vaultwarden"
NETWORK = "network"
LEGACY_LUCKY = "lucky"

DEFAULT_LISTEN_PORT = 443
DEFAULT_DDNS_INTERVAL = 300
DDNS_NO_ID_PROVIDERS = {
    "cloudflare",
    "duckdns",
    "freemyip",
    "dynv6",
    "namesilo",
    "desec",
    "dynadot",
}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _secret(value: object) -> str:
    """密码原样读取，不能去空白，否则会改动用户真实密码。"""
    return str(value) if value is not None else ""


def _certificate_int(value: object) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def _link_domains(link: dict) -> list[str]:
    """兼容旧格式的单域名 ``domain``，新格式使用 ``domains`` 列表。"""
    raw = link.get("domains")
    domains: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            text = _text(item).lower().rstrip(".")
            if text and text not in domains:
                domains.append(text)
        return domains
    text = _text(link.get("domain")).lower().rstrip(".")
    return [text] if text else []


def _link_network(link: dict) -> str:
    network = _text(link.get("network"))
    return network if network in ("tcp", "tcp4", "tcp6") else "tcp"


def _status_out(status: dict | None) -> IntegrationStatusOut:
    data = status or {}
    return IntegrationStatusOut(
        ok=data.get("ok"),
        message=_text(data.get("message")),
        version=_text(data.get("version")) or None,
        checked_at=data.get("checked_at"),
        last_synced_at=data.get("last_synced_at"),
    )


def _mask(value: str | None) -> bool:
    return bool(_text(value))


def _positive_int(value: object) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def _normalize_domain(value: str) -> str:
    domain = (value or "").strip().lower()
    if "://" in domain:
        domain = urlparse(domain).hostname or ""
    domain = domain.split("/")[0].split(":")[0].strip().rstrip(".")
    if not domain:
        raise APIException("请填写有效域名", 400)
    if " " in domain or len(domain) > 253:
        raise APIException("域名格式不正确", 400)
    return domain


def _domain_matches(pattern: str, domain: str) -> bool:
    """证书域名与目标域名的匹配，支持 *.example.com 形式。"""
    value = (pattern or "").strip().lower()
    target = (domain or "").strip().lower()
    if not value or not target:
        return False
    if value == target:
        return True
    if value.startswith("*."):
        return target.endswith(value[1:]) and target.count(".") == value.count(".")
    return False


def _certificate_covers(cert_domains: list[str], domains: list[str]) -> bool:
    """证书需要覆盖所有目标域名，否则 HTTPS 仍会在部分域名上失败。"""
    return bool(domains) and all(
        any(_domain_matches(pattern, domain) for pattern in cert_domains)
        for domain in domains
    )


def _certificate_domains(row: dict) -> list[str]:
    info = row.get("CertsInfo")
    info = info if isinstance(info, dict) else {}
    values = info.get("Domains")
    if not isinstance(values, list) or not values:
        ext = row.get("ExtParams")
        ext = ext if isinstance(ext, dict) else {}
        values = ext.get("acmeDomains")
    return [
        str(item).strip().lower()
        for item in (values or [])
        if str(item).strip()
    ]


def _lucky_certificate_out(row: dict) -> NetworkCertificateOut | None:
    key = _text(row.get("Key"))
    if not key:
        return None
    info = row.get("CertsInfo")
    info = info if isinstance(info, dict) else {}
    domains = _certificate_domains(row)
    error = _text(row.get("ACMEErrMsg"))
    if info:
        status = "valid"
    elif error:
        status = "failed"
    else:
        status = "pending"
    name = _text(row.get("Remark")) or (
        domains[0] if domains else "Lucky 证书"
    )
    return NetworkCertificateOut(
        id=key,
        name=name,
        domains=domains,
        expires_on=_text(info.get("NotAfterTime")) or None,
        status=status,
        error=error or None,
    )


def _auto_certificate_mode(domains: list[str], *, dns_available: bool) -> str:
    """内网域名只能自签名；通配符需要可用的 DNS 验证凭据。"""
    if any(is_internal_domain(domain) for domain in domains):
        return "selfsigned"
    if any(domain.startswith("*.") for domain in domains) and not dns_available:
        raise APIException(
            "通配符域名需要 DNS 验证，请先在 DDNS 中填写 DNS 服务商凭据",
            400,
        )
    return "acme"


def _validate_acme_domains(domains: list[str], *, dns_available: bool) -> None:
    if any(is_internal_domain(domain) for domain in domains):
        raise APIException(
            "内网域名无法用公网 ACME 签发，请改用自签名证书",
            400,
        )
    if any(domain.startswith("*.") for domain in domains) and not dns_available:
        raise APIException(
            "通配符域名需要 DNS 验证，请先在 DDNS 中填写 DNS 服务商凭据",
            400,
        )


# ACME 注册/申请时的临时网络错误，可以自动重试一次
_TRANSIENT_ACME_ERRORS = (
    "nonce",
    "context deadline exceeded",
    "request cancelled",
    "request canceled",
    "timeout",
    "timed out",
    "connection reset",
    "connection refused",
    "temporary failure",
    "network is unreachable",
    "no such host",
)


def _is_transient_acme_error(message: str) -> bool:
    lowered = message.lower()
    return any(part in lowered for part in _TRANSIENT_ACME_ERRORS)


# ACME 提交后等待 Lucky 回写结果，用于识别注册阶段的临时失败并重试
ACME_ATTEMPTS = 3
ACME_POLL_ATTEMPTS = 10
ACME_POLL_INTERVAL = 2.0


def _ddns_dns_credentials(config: dict) -> tuple[str, str, str] | None:
    """DDNS 里保存的 DNS 服务商凭据，可复用于 ACME 的 DNS 验证。"""
    ddns = config.get("ddns") or {}
    for family in DDNS_FAMILIES:
        item = _ddns_family_config(ddns, family)
        if item["secret"]:
            return item["provider"], item["access_id"], item["secret"]
    return None


def _ddns_family_default(family: str) -> dict:
    return {
        "enabled": False,
        "provider": "cloudflare",
        "access_id": "",
        "secret": "",
        "domains": [],
        "manual_domains": None,
        "auto_domains": [],
        "interval": DEFAULT_DDNS_INTERVAL,
        "get_type": "url",
        "url": DEFAULT_IPV4_URLS if family == "IPv4" else DEFAULT_IPV6_URLS,
        "net_interface": "",
        "ip_reg": "",
    }


def _ddns_family_config(ddns: dict, family: str) -> dict:
    """读取协议族配置；旧版单套结构自动映射到对应协议族。"""
    base = _ddns_family_default(family)
    raw = ddns.get(family.lower())
    if isinstance(raw, dict):
        manual_domains = raw.get("manual_domains")
        return {
            **base,
            "enabled": bool(raw.get("enabled")),
            "provider": _text(raw.get("provider")) or base["provider"],
            "access_id": _text(raw.get("access_id")),
            "secret": _text(raw.get("secret")),
            "domains": [_text(item) for item in (raw.get("domains") or [])],
            "manual_domains": (
                [_text(item) for item in manual_domains]
                if isinstance(manual_domains, list)
                else None
            ),
            "auto_domains": [
                _text(item) for item in (raw.get("auto_domains") or [])
            ],
            "interval": int(raw.get("interval") or base["interval"]),
            "get_type": _text(raw.get("get_type")) or "url",
            "url": _text(raw.get("url")),
            "net_interface": _text(raw.get("net_interface")),
            "ip_reg": _text(raw.get("ip_reg")),
        }

    legacy_type = _text(ddns.get("record_type")) or "IPv4"
    if legacy_type == family and ddns:
        base.update(
            {
                "enabled": bool(ddns.get("enabled")),
                "provider": _text(ddns.get("provider")) or "cloudflare",
                "access_id": _text(ddns.get("access_id")),
                "secret": _text(ddns.get("secret")),
                "domains": [_text(item) for item in (ddns.get("domains") or [])],
                "interval": int(ddns.get("interval") or DEFAULT_DDNS_INTERVAL),
            }
        )
    return base


def _merge_domains(*groups: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            domain = _text(item)
            key = domain.lower()
            if not domain or key in seen:
                continue
            seen.add(key)
            result.append(domain)
    return result


def _ddns_family_out(raw: dict) -> NetworkDdnsFamilyOut:
    auto_domains = _merge_domains(list(raw.get("auto_domains") or []))
    raw_manual = raw.get("manual_domains")
    if isinstance(raw_manual, list):
        manual_domains = _merge_domains(raw_manual)
    else:
        automatic = {item.lower() for item in auto_domains}
        manual_domains = [
            item
            for item in list(raw.get("domains") or [])
            if _text(item).lower() not in automatic
        ]
    return NetworkDdnsFamilyOut(
        enabled=bool(raw.get("enabled")),
        provider=_text(raw.get("provider")) or "cloudflare",
        access_id=_text(raw.get("access_id")),
        secret_set=_mask(raw.get("secret")),
        domains=_merge_domains(manual_domains, auto_domains),
        manual_domains=manual_domains,
        auto_domains=auto_domains,
        interval=int(raw.get("interval") or DEFAULT_DDNS_INTERVAL),
        get_type=_text(raw.get("get_type")) or "url",
        url=_text(raw.get("url")),
        net_interface=_text(raw.get("net_interface")),
        ip_reg=_text(raw.get("ip_reg")),
    )


def _ddns_task_domains(task: dict) -> list[str]:
    """Lucky 任务里的域名；新版按 Records 返回，旧版是 Domains。"""
    domains: list[str] = []
    for record in task.get("Records") or []:
        if not isinstance(record, dict):
            continue
        sub = _text(record.get("SubDomain"))
        name = _text(record.get("DomainName"))
        if not name:
            continue
        full = f"{sub}.{name}" if sub and sub != "@" else name
        if full not in domains:
            domains.append(full)
    if domains:
        return domains
    values = task.get("Domains")
    return [_text(item) for item in values] if isinstance(values, list) else []


def _ddns_families_for_network(network: str) -> list[str]:
    """代理监听地址族对应的 DDNS 协议族。"""
    if network == "tcp4":
        return ["IPv4"]
    if network == "tcp6":
        return ["IPv6"]
    return list(DDNS_FAMILIES)


def _is_ddns_domain(domain: str) -> bool:
    """只有具体的公网域名能写进 DDNS；内网域名、IP 和通配符跳过。"""
    value = _text(domain).lower()
    if not value or "*" in value or "/" in value or ":" in value:
        return False
    return not is_internal_domain(value)


def _proxy_ddns_domains(links: dict[int, dict]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {family: [] for family in DDNS_FAMILIES}
    for link in links.values():
        public = [
            domain for domain in _link_domains(link) if _is_ddns_domain(domain)
        ]
        for family in _ddns_families_for_network(_link_network(link)):
            result[family] = _merge_domains(result[family], public)
    return result


_LUCKY_SYNC_STATUS = {
    "SYNC_SUCCESS": "ok",
    "SYNC_LOC_RECORD_NOCHANGE": "unchanged",
    # Lucky 的「IP 没变化」也是正常结果，不是同步失败
    "SYNC_PROVIDER_IP_SAME": "unchanged",
}

_LUCKY_SYNC_MESSAGES = {
    "SYNC_LOC_RECORD_NOCHANGE": "记录未变化",
    "SYNC_PROVIDER_IP_SAME": "IP 未变化，无需更新",
}


def _lucky_task_snapshot(task: dict) -> dict:
    """把 Lucky 任务的最近同步结果整理成前端可展示的状态。"""
    kind = _text(task.get("TaskType"))
    record = next(
        (item for item in task.get("Records") or [] if isinstance(item, dict)), {}
    )
    message = _text(record.get("Message"))
    address_error = _text(
        task.get("Ipv6AddrErrMsg") if kind == "IPv6" else task.get("Ipv4AddrErrMsg")
    )
    update = _text(record.get("UpdateStatus"))
    status: str | None = None
    if address_error:
        status = "error"
        message = address_error
    elif update:
        status = _LUCKY_SYNC_STATUS.get(update, "error")
        if not message:
            message = _LUCKY_SYNC_MESSAGES.get(update, "")
        if status == "error" and not message:
            message = update
    return {
        "status": status,
        "message": message,
        "address": _text(
            task.get("Ipv6Addr") if kind == "IPv6" else task.get("Ipv4Addr")
        ),
        "last_sync": _text(task.get("LastSyncTime")) or None,
        "next_sync": _text(task.get("NextSyncTime")) or None,
    }


def _apply_target_host(url: str | None, target_host: str) -> str | None:
    """把应用访问地址中的主机替换为配置的内网地址。"""
    host = (target_host or "").strip()
    if not url or not host:
        return url
    if "://" in host:
        host = urlparse(host).hostname or ""
    host = host.split("/")[0].strip()
    if not host:
        return url
    parsed = urlsplit(url)
    if not parsed.hostname:
        return url
    try:
        port = parsed.port
    except ValueError:
        port = None
    netloc = f"[{host}]" if ":" in host and not host.startswith("[") else host
    if port:
        netloc = f"{netloc}:{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def _ensure_network_config(raw: dict | None) -> dict:
    """补齐网络集成配置结构，并兼容早期只支持 Lucky 的扁平配置。"""
    config = dict(raw or {})
    if "provider" not in config:
        legacy = {
            key: config[key]
            for key in (
                "url",
                "username",
                "password",
                "listen_port",
                "enable_tls",
            )
            if config.get(key) is not None
        }
        config["provider"] = "lucky"
        config["lucky"] = legacy
        for key in ("url", "username", "password", "listen_port", "enable_tls"):
            config.pop(key, None)
    config.setdefault("lucky", {})
    config.setdefault("npm", {})
    config.setdefault("ddnsgo", {})
    config.setdefault("ddns", {})
    config.setdefault("proxy_links", {})
    return config


def _provider(config: dict) -> str:
    provider = _text(config.get("provider")).lower()
    return provider if provider in ("lucky", "npm") else "lucky"


def _certificate_id(value: object) -> int | str:
    if value == "new":
        return "new"
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _network_app_list(apps: list[DeployedApp]) -> list[DeployedApp]:
    """整理网络集成可选应用。

    同一应用存在多个实例记录时：
    1. 同名实例只保留最新一条（失败的重复部署会留下同名历史记录）；
    2. 只要该应用有非 error 状态的实例，就丢弃 error 状态的残留记录。
    """
    grouped: dict[str, dict[str, DeployedApp]] = {}
    for app in apps:
        instances = grouped.setdefault(app.app_name, {})
        current = instances.get(app.instance_name)
        if current is None or app.instance_id > current.instance_id:
            instances[app.instance_name] = app

    result: list[DeployedApp] = []
    for instances in grouped.values():
        usable = [item for item in instances.values() if item.status != "error"]
        result.extend(usable or list(instances.values()))
    return sorted(result, key=lambda item: item.instance_id)


async def _docker_app_keys() -> tuple[set[str], set[str]] | None:
    """读取 Docker 中现存的项目名与容器名/ID。

    Docker 不可用时返回 ``None``，调用方退回只按数据库记录展示。
    """
    try:
        available = await docker_manager.run_sync(docker_manager.check_available)
        if not available:
            return None
        containers = await docker_manager.run_sync(docker_manager.list_containers)
    except Exception:
        logger.warning("读取 Docker 容器失败，跳过部署校验", exc_info=True)
        return None

    projects: set[str] = set()
    containers_or_ids: set[str] = set()
    for item in containers:
        labels = item.get("labels") or {}
        project = labels.get("com.docker.compose.project")
        if project:
            projects.add(project)
        for key in ("name", "id"):
            value = item.get(key)
            if value:
                containers_or_ids.add(value)
    return projects, containers_or_ids


def _deployed_in_docker(
    apps: list[DeployedApp],
    *,
    project_names: set[str],
    container_keys: set[str],
    project_by_id: dict[int, str],
) -> list[DeployedApp]:
    """只保留 Docker 中仍能找到 compose 项目或容器的实例。"""
    result: list[DeployedApp] = []
    for app in apps:
        candidates = {app.instance_name}
        project_name = project_by_id.get(app.project_id or 0)
        if project_name:
            candidates.add(project_name)
        if app.container_name:
            candidates.add(app.container_name)
        if app.container_id:
            candidates.add(app.container_id[:12])
        if candidates & project_names or candidates & container_keys:
            result.append(app)
    return result


class IntegrationService:
    """应用集成业务服务。"""

    async def _get_record(
        self, db: AsyncSession, kind: str, create: bool = True
    ) -> IntegrationConfig | None:
        if kind == NETWORK:
            result = await db.execute(
                select(IntegrationConfig).where(
                    IntegrationConfig.kind.in_([NETWORK, LEGACY_LUCKY])
                )
            )
            records = result.scalars().all()
            record = next(
                (item for item in records if item.kind == NETWORK),
                records[0] if records else None,
            )
            if record is not None and record.kind != NETWORK:
                record.kind = NETWORK
                await db.commit()
            if record is None and create:
                record = IntegrationConfig(
                    kind=NETWORK, enabled=False, config={}, status={}
                )
                db.add(record)
                await db.commit()
                await db.refresh(record)
            return record

        result = await db.execute(
            select(IntegrationConfig).where(IntegrationConfig.kind == kind)
        )
        record = result.scalar_one_or_none()
        if record is None and create:
            record = IntegrationConfig(kind=kind, enabled=False, config={}, status={})
            db.add(record)
            await db.commit()
            await db.refresh(record)
        return record

    async def list_summaries(self, db: AsyncSession) -> list[dict]:
        records = {}
        result = await db.execute(select(IntegrationConfig))
        for record in result.scalars().all():
            records[record.kind] = record

        summaries = []
        for kind in (VAULTWARDEN, NETWORK):
            record = records.get(kind)
            if record is None and kind == NETWORK:
                record = records.get(LEGACY_LUCKY)
            config = record.config if record else {}
            summaries.append(
                {
                    "kind": kind,
                    "enabled": bool(record.enabled) if record else False,
                    "configured": self._is_configured(kind, config),
                    "status": _status_out(record.status if record else {}),
                }
            )
        return summaries

    def _is_configured(self, kind: str, config: dict) -> bool:
        if kind == VAULTWARDEN:
            return bool(
                _text(config.get("url"))
                and _text(config.get("email"))
                and _text(config.get("password"))
            )
        network = _ensure_network_config(config)
        return self._network_configured(_provider(network), network)

    def _network_configured(self, provider: str, config: dict) -> bool:
        if provider == "npm":
            npm = config.get("npm") or {}
            return bool(
                _text(npm.get("url"))
                and _text(npm.get("email"))
                and _text(npm.get("password"))
            )
        lucky = config.get("lucky") or {}
        return bool(
            _text(lucky.get("url"))
            and _text(lucky.get("username"))
            and _text(lucky.get("password"))
        )

    def _ddnsgo_configured(self, config: dict) -> bool:
        ddnsgo = config.get("ddnsgo") or {}
        return bool(
            _text(ddnsgo.get("url"))
            and _text(ddnsgo.get("username"))
            and _text(ddnsgo.get("password"))
        )

    async def _set_status(
        self,
        db: AsyncSession,
        record: IntegrationConfig,
        **values,
    ) -> None:
        record.status = {**(record.status or {}), **values}
        await db.commit()

    async def _vaultwarden_sync_apps(
        self, db: AsyncSession, host: str = ""
    ) -> list[DeployedApp]:
        apps = await list_deployed_apps(db, host)
        return [app for app in apps if app.password]

    async def get_vaultwarden(self, db: AsyncSession) -> VaultwardenConfigOut:
        record = await self._get_record(db, VAULTWARDEN)
        assert record is not None
        config = record.config or {}
        items = config.get("vaultwarden_items") or {}
        apps = await self._vaultwarden_sync_apps(db)
        runtime = await vaultwarden_deployment_service.runtime_status(config)
        return VaultwardenConfigOut(
            enabled=record.enabled,
            configured=self._is_configured(VAULTWARDEN, config),
            url=_text(config.get("url")),
            email=_text(config.get("email")),
            password_set=_mask(config.get("password")),
            synced_count=sum(str(app.instance_id) in items for app in apps),
            status=_status_out(record.status),
            **runtime,
        )

    async def save_vaultwarden(
        self, db: AsyncSession, data: VaultwardenUpdate
    ) -> VaultwardenConfigOut:
        record = await self._get_record(db, VAULTWARDEN)
        assert record is not None
        config = dict(record.config or {})
        if not config.get("managed"):
            config["url"] = data.url.strip().rstrip("/")
        config["email"] = data.email.strip()
        if data.password is not None and data.password.strip():
            config["password"] = data.password.strip()
        record.config = config
        record.enabled = data.enabled
        await db.commit()
        return await self.get_vaultwarden(db)

    async def reveal_vaultwarden_password(self, db: AsyncSession) -> str:
        """返回已保存的主密码；调用方需先完成二次验证。"""
        record = await self._get_record(db, VAULTWARDEN)
        assert record is not None
        password = _secret((record.config or {}).get("password"))
        if not password:
            raise APIException("尚未保存密码", 404)
        return password

    async def test_vaultwarden(
        self,
        db: AsyncSession,
        url: str | None,
        email: str | None,
        password: str | None,
    ) -> dict:
        record = await self._get_record(db, VAULTWARDEN)
        assert record is not None
        config = record.config or {}
        base_url = _text(url) or _text(config.get("url"))
        mail = _text(email) or _text(config.get("email"))
        secret = _text(password) or _text(config.get("password"))
        if not base_url or not mail or not secret:
            raise APIException("请先填写 Vaultwarden 地址、邮箱和主密码", 400)

        try:
            async with VaultwardenClient(base_url) as client:
                session = await client.login(mail, secret)
                version = session.version
            result = {
                "ok": True,
                "message": "连接成功",
                "version": version,
            }
            await self._set_status(
                db,
                record,
                ok=True,
                message="连接成功",
                version=version,
                checked_at=_now_iso(),
            )
            return result
        except APIException as exc:
            await self._set_status(
                db,
                record,
                ok=False,
                message=exc.message,
                checked_at=_now_iso(),
            )
            raise

    def _vaultwarden_items(self, config: dict) -> dict:
        items = config.get("vaultwarden_items") or {}
        return items if isinstance(items, dict) else {}

    async def list_vaultwarden_apps(
        self, db: AsyncSession, host: str
    ) -> list[VaultwardenAppOut]:
        record = await self._get_record(db, VAULTWARDEN)
        items = self._vaultwarden_items(record.config if record else {})
        apps = await self._vaultwarden_sync_apps(db, host)
        result = []
        for app in apps:
            synced = str(app.instance_id) in items
            result.append(
                VaultwardenAppOut(
                    **self._app_fields(app),
                    synced=synced,
                )
            )
        return result

    def _app_fields(self, app: DeployedApp) -> dict:
        return {
            "instance_id": app.instance_id,
            "app_name": app.app_name,
            "display_name": app.display_name,
            "instance_name": app.instance_name,
            "status": app.status,
            "url": app.url,
            "icon": app.icon,
            "username": app.username,
            "password_set": bool(app.password),
            "api_key_set": bool(app.api_key),
            "has_credentials": bool(app.password),
        }

    async def sync_vaultwarden(
        self,
        db: AsyncSession,
        instance_ids: list[int] | None,
        host: str,
    ) -> IntegrationSyncResult:
        record = await self._get_record(db, VAULTWARDEN)
        assert record is not None
        config = dict(record.config or {})
        if not record.enabled:
            raise APIException("请先启用认证集成", 400)
        if not self._is_configured(VAULTWARDEN, config):
            raise APIException("请先保存 Vaultwarden 连接配置", 400)

        targets = await self._vaultwarden_sync_apps(db, host)
        if instance_ids:
            wanted = set(instance_ids)
            targets = [app for app in targets if app.instance_id in wanted]

        if not targets:
            raise APIException("没有可同步的账号密码，请先完成应用自动配置", 400)

        items = dict(self._vaultwarden_items(config))
        results: list[IntegrationSyncItem] = []
        ok_count = 0

        async with VaultwardenClient(_text(config.get("url"))) as client:
            await client.login(
                _text(config.get("email")), _text(config.get("password"))
            )
            for app in targets:
                name = cipher_name(app.display_name, app.instance_name)
                notes = (
                    f"由 OneTap 自动同步。\n"
                    f"应用：{app.display_name}\n"
                    f"实例：{app.instance_name}"
                )
                payload = client.build_login_cipher(
                    name=name,
                    username=app.username,
                    password=app.password,
                    url=app.url,
                    notes=notes,
                )
                existing = items.get(str(app.instance_id)) or {}
                cipher_id = _text(existing.get("cipher_id"))
                try:
                    created = False
                    if cipher_id:
                        updated = await client.update_cipher(cipher_id, payload)
                        if not updated:
                            cipher_id = await client.create_cipher(payload)
                            created = True
                    else:
                        cipher_id = await client.create_cipher(payload)
                        created = True
                    items[str(app.instance_id)] = {
                        "cipher_id": cipher_id,
                        "name": name,
                        "url": app.url,
                        "synced_at": _now_iso(),
                    }
                    ok_count += 1
                    results.append(
                        IntegrationSyncItem(
                            instance_id=app.instance_id,
                            display_name=app.display_name,
                            ok=True,
                            message="已创建" if created else "已更新",
                        )
                    )
                except APIException as exc:
                    results.append(
                        IntegrationSyncItem(
                            instance_id=app.instance_id,
                            display_name=app.display_name,
                            ok=False,
                            message=exc.message,
                        )
                    )

        config["vaultwarden_items"] = items
        record.config = config
        await db.commit()
        await self._set_status(
            db,
            record,
            ok=ok_count > 0,
            message=f"已同步 {ok_count}/{len(targets)} 个应用",
            last_synced_at=_now_iso(),
            checked_at=_now_iso(),
        )
        return IntegrationSyncResult(
            ok=ok_count == len(targets),
            message=f"已同步 {ok_count}/{len(targets)} 个应用",
            items=results,
        )

    # ---------- 网络集成 ----------

    def _network_config(self, record: IntegrationConfig) -> dict:
        return _ensure_network_config(record.config if record else {})

    def _lucky_client(self, config: dict) -> LuckyClient:
        lucky = config.get("lucky") or {}
        return LuckyClient(
            _text(lucky.get("url")),
            _text(lucky.get("username")),
            _text(lucky.get("password")),
        )

    def _npm_client(self, config: dict) -> NpmClient:
        npm = config.get("npm") or {}
        return NpmClient(
            _text(npm.get("url")),
            _text(npm.get("email")),
            _text(npm.get("password")),
        )

    def _ddnsgo_client(self, config: dict) -> DdnsGoClient:
        ddnsgo = config.get("ddnsgo") or {}
        return DdnsGoClient(
            _text(ddnsgo.get("url")),
            _text(ddnsgo.get("username")),
            _text(ddnsgo.get("password")),
        )

    def _normalized_ddns_families(
        self,
        config: dict,
        previous_links: dict[int, dict] | None = None,
    ) -> dict[str, dict]:
        ddns = config.get("ddns") or {}
        automatic = _proxy_ddns_domains(self._proxy_links(config))
        previous_automatic = (
            _proxy_ddns_domains(previous_links)
            if previous_links is not None
            else None
        )
        families: dict[str, dict] = {}
        for family in DDNS_FAMILIES:
            item = _ddns_family_config(ddns, family)
            manual = item["manual_domains"]
            if manual is None:
                if previous_automatic is not None:
                    old_auto = previous_automatic[family]
                else:
                    old_auto = item["auto_domains"] or automatic[family]
                old_keys = {domain.lower() for domain in old_auto}
                manual = [
                    domain
                    for domain in item["domains"]
                    if domain.lower() not in old_keys
                ]
            item["manual_domains"] = _merge_domains(manual)
            item["auto_domains"] = automatic[family]
            item["domains"] = _merge_domains(manual, automatic[family])
            families[family] = item
        return families

    def _ddns_out(self, config: dict) -> NetworkDdnsOut:
        ddns = config.get("ddns") or {}
        families = self._normalized_ddns_families(config)
        return NetworkDdnsOut(
            enabled=bool(ddns.get("enabled")),
            ipv4=_ddns_family_out(families["IPv4"]),
            ipv6=_ddns_family_out(families["IPv6"]),
        )

    async def get_network(self, db: AsyncSession) -> NetworkConfigOut:
        record = await self._get_record(db, NETWORK)
        assert record is not None
        config = self._network_config(record)
        provider = _provider(config)
        lucky = config.get("lucky") or {}
        npm = config.get("npm") or {}
        ddnsgo = config.get("ddnsgo") or {}
        return NetworkConfigOut(
            enabled=record.enabled,
            provider=provider,
            configured=self._network_configured(provider, config),
            target_host=_text(config.get("target_host")),
            lucky=LuckyConnectionOut(
                url=_text(lucky.get("url")),
                username=_text(lucky.get("username")),
                password_set=_mask(lucky.get("password")),
                listen_port=int(lucky.get("listen_port") or DEFAULT_LISTEN_PORT),
                enable_tls=bool(lucky.get("enable_tls")),
            ),
            npm=NpmConnectionOut(
                url=_text(npm.get("url")),
                email=_text(npm.get("email")),
                password_set=_mask(npm.get("password")),
                certificate_id=_certificate_id(npm.get("certificate_id")),
                ssl_forced=bool(npm.get("ssl_forced", True)),
            ),
            ddnsgo=DdnsGoConnectionOut(
                url=_text(ddnsgo.get("url")),
                username=_text(ddnsgo.get("username")),
                password_set=_mask(ddnsgo.get("password")),
            ),
            status=_status_out(record.status),
            ddns=self._ddns_out(config),
        )

    async def save_network(
        self, db: AsyncSession, data: NetworkUpdate
    ) -> NetworkConfigOut:
        record = await self._get_record(db, NETWORK)
        assert record is not None
        config = self._network_config(record)

        config["provider"] = data.provider
        config["target_host"] = data.target_host.strip()

        lucky = dict(config.get("lucky") or {})
        lucky["url"] = data.lucky.url.strip().rstrip("/")
        lucky["username"] = data.lucky.username.strip()
        if data.lucky.password:
            lucky["password"] = data.lucky.password
        lucky["listen_port"] = data.lucky.listen_port
        lucky["enable_tls"] = data.lucky.enable_tls
        config["lucky"] = lucky

        npm = dict(config.get("npm") or {})
        npm["url"] = data.npm.url.strip().rstrip("/")
        npm["email"] = data.npm.email.strip()
        if data.npm.password:
            npm["password"] = data.npm.password
        npm["certificate_id"] = data.npm.certificate_id
        npm["ssl_forced"] = data.npm.ssl_forced
        config["npm"] = npm

        ddnsgo = dict(config.get("ddnsgo") or {})
        ddnsgo["url"] = data.ddnsgo.url.strip().rstrip("/")
        ddnsgo["username"] = data.ddnsgo.username.strip()
        if data.ddnsgo.password:
            ddnsgo["password"] = data.ddnsgo.password
        config["ddnsgo"] = ddnsgo

        record.config = config
        record.enabled = data.enabled
        await db.commit()
        return await self.get_network(db)

    async def test_network(
        self, db: AsyncSession, data: NetworkTestRequest
    ) -> dict:
        record = await self._get_record(db, NETWORK)
        assert record is not None
        config = self._network_config(record)
        provider = data.provider
        try:
            if provider == "ddnsgo":
                block = config.get("ddnsgo") or {}
                url = _text(data.url) or _text(block.get("url"))
                username = _text(data.username) or _text(block.get("username"))
                password = data.password or _secret(block.get("password"))
                if not url or not username or not password:
                    raise APIException(
                        "请先填写 DDNS-Go 地址、用户名和密码", 400
                    )
                async with DdnsGoClient(url, username, password) as client:
                    version = await client.test()
                message = "DDNS-Go 连接成功"
            elif provider == "npm":
                block = config.get("npm") or {}
                url = _text(data.url) or _text(block.get("url"))
                email = _text(data.email) or _text(block.get("email"))
                password = data.password or _secret(block.get("password"))
                if not url or not email or not password:
                    raise APIException(
                        "请先填写 Nginx Proxy Manager 地址、邮箱和密码", 400
                    )
                async with NpmClient(url, email, password) as client:
                    await client.test()
                version = None
                message = "Nginx Proxy Manager 连接成功"
            else:
                block = config.get("lucky") or {}
                url = _text(data.url) or _text(block.get("url"))
                username = _text(data.username) or _text(block.get("username"))
                password = data.password or _secret(block.get("password"))
                if not url or not username or not password:
                    raise APIException("请先填写 Lucky 地址、账号和密码", 400)
                async with LuckyClient(url, username, password) as client:
                    version = await client.test()
                message = "Lucky 连接成功"
        except APIException as exc:
            await self._set_status(
                db,
                record,
                ok=False,
                message=exc.message,
                checked_at=_now_iso(),
            )
            raise

        await self._set_status(
            db,
            record,
            ok=True,
            message=message,
            version=version,
            checked_at=_now_iso(),
        )
        return {"ok": True, "message": message, "version": version}

    async def reveal_network_password(
        self,
        db: AsyncSession,
        provider: str,
    ) -> str:
        """返回指定方案或 DDNS 已保存的密钥；调用方需先完成二次验证。"""
        record = await self._get_record(db, NETWORK)
        assert record is not None
        config = self._network_config(record)
        if provider == "ddns":
            ddns = config.get("ddns") or {}
            for family in DDNS_FAMILIES:
                secret = _secret(_ddns_family_config(ddns, family).get("secret"))
                if secret:
                    return secret
            raise APIException("尚未保存密钥", 404)
        block = config.get(provider) or {}
        password = _secret(block.get("password")) if isinstance(block, dict) else ""
        if not password:
            raise APIException("尚未保存密码", 404)
        return password

    def _proxy_links(self, config: dict) -> dict[int, dict]:
        raw = config.get("proxy_links") or {}
        links: dict[int, dict] = {}
        if not isinstance(raw, dict):
            return links
        for key, value in raw.items():
            if not isinstance(value, dict):
                continue
            try:
                links[int(key)] = value
            except (TypeError, ValueError):
                continue
        return links

    async def list_network_apps(
        self, db: AsyncSession, host: str
    ) -> list[NetworkAppOut]:
        record = await self._get_record(db, NETWORK)
        config = self._network_config(record)
        links = self._proxy_links(config)
        lucky = config.get("lucky") or {}
        npm = config.get("npm") or {}
        apps = await list_deployed_apps(db, host)
        docker_keys = await _docker_app_keys()
        if docker_keys is not None:
            project_names, container_keys = docker_keys
            rows = await db.execute(
                select(
                    DockerComposeProject.id,
                    DockerComposeProject.project_name,
                )
            )
            project_by_id = {row[0]: row[1] for row in rows.all()}
            alive = _deployed_in_docker(
                apps,
                project_names=project_names,
                container_keys=container_keys,
                project_by_id=project_by_id,
            )
            # 已配置代理的应用即使容器被关闭/删除，也要继续展示，
            # 否则规则仍留在 Lucky/NPM 中却无法编辑或清理。
            alive_ids = {app.instance_id for app in alive}
            linked_ids = {
                instance_id
                for instance_id, link in links.items()
                if instance_id >= 0 and _link_domains(link)
            }
            apps = alive + [
                app
                for app in apps
                if app.instance_id in linked_ids and app.instance_id not in alive_ids
            ]
        apps = _network_app_list(apps)
        result: list[NetworkAppOut] = []
        for app in apps:
            link = links.get(app.instance_id) or {}
            domains = _link_domains(link)
            result.append(
                NetworkAppOut(
                    **self._app_fields(app),
                    domain=domains[0] if domains else None,
                    domains=domains,
                    target_url=_text(link.get("target_url"))
                    or _apply_target_host(app.url, _text(config.get("target_host"))),
                    proxy_enabled=bool(domains and _text(link.get("target_url"))),
                    listen_port=_positive_int(link.get("listen_port")) or None,
                    enable_tls=bool(
                        link.get("enable_tls", lucky.get("enable_tls"))
                    ),
                    network=_link_network(link),
                    certificate_id=_certificate_int(
                        link.get("certificate_id", npm.get("certificate_id"))
                    ),
                    ssl_forced=bool(
                        link.get("ssl_forced", npm.get("ssl_forced", True))
                    ),
                )
            )
        for instance_id, link in sorted(links.items()):
            if instance_id >= 0:
                continue
            domains = _link_domains(link)
            target_url = _text(link.get("target_url")) or None
            result.append(
                NetworkAppOut(
                    instance_id=instance_id,
                    app_name="custom",
                    display_name=_text(link.get("remark")) or "自定义应用",
                    instance_name="自定义应用",
                    status="custom",
                    url=target_url,
                    domain=domains[0] if domains else None,
                    domains=domains,
                    target_url=target_url,
                    proxy_enabled=bool(domains and target_url),
                    is_custom=True,
                    listen_port=_positive_int(link.get("listen_port")) or None,
                    enable_tls=bool(
                        link.get("enable_tls", lucky.get("enable_tls"))
                    ),
                    network=_link_network(link),
                    certificate_id=_certificate_int(
                        link.get("certificate_id", npm.get("certificate_id"))
                    ),
                    ssl_forced=bool(
                        link.get("ssl_forced", npm.get("ssl_forced", True))
                    ),
                )
            )
        return result

    async def upsert_network_proxy(
        self,
        db: AsyncSession,
        data: NetworkProxyRequest,
        host: str,
    ) -> NetworkProxyResult:
        record = await self._get_record(db, NETWORK)
        assert record is not None
        config = self._network_config(record)
        provider = _provider(config)
        if not record.enabled:
            raise APIException("请先启用网络集成", 400)
        if not self._network_configured(provider, config):
            raise APIException("请先保存网络集成连接配置", 400)

        links = self._proxy_links(config)
        previous_links = links.copy()
        is_custom = data.app_instance_id <= 0
        if is_custom:
            existing = links.get(data.app_instance_id) or {}
            if data.app_instance_id < 0 and not existing:
                raise APIException("自定义代理不存在", 404)
            display_name = _text(data.custom_name) or _text(existing.get("remark"))
            if not display_name:
                raise APIException("请填写自定义应用名称", 400)
            app_instance_id = data.app_instance_id or (
                min((key for key in links if key < 0), default=0) - 1
            )
            target_url = _text(data.target_url)
            if not target_url:
                raise APIException("请填写自定义应用的后端地址", 400)
        else:
            app = await get_deployed_app(db, data.app_instance_id, host)
            if app is None:
                raise APIException("应用实例不存在", 404)
            app_instance_id = app.instance_id
            display_name = app.display_name
            target_url = _text(data.target_url) or _apply_target_host(
                app.url, _text(config.get("target_host"))
            )
            if not target_url:
                raise APIException("无法确定应用访问地址，请手动填写后端地址", 400)
        if not target_url.startswith(("http://", "https://")):
            target_url = f"http://{target_url}"

        domains: list[str] = []
        for raw in data.domains:
            domain = _normalize_domain(raw)
            if domain not in domains:
                domains.append(domain)
        if not domains:
            raise APIException("请至少填写一个域名", 400)

        for instance_id, link in links.items():
            if instance_id == app_instance_id:
                continue
            for existing_domain in _link_domains(link):
                if existing_domain in domains:
                    raise APIException(f"域名 {existing_domain} 已被其他应用使用", 400)

        lucky = config.get("lucky") or {}
        npm = config.get("npm") or {}
        default_port = _positive_int(lucky.get("listen_port")) or DEFAULT_LISTEN_PORT
        listen_port = data.listen_port or default_port
        links[app_instance_id] = {
            "domains": domains,
            "target_url": target_url,
            "remark": display_name,
            "listen_port": listen_port,
            "enable_tls": data.enable_tls,
            "network": data.network,
            "certificate_id": data.certificate_id,
            "ssl_forced": data.ssl_forced,
        }

        if provider == "npm":
            certificate_id = data.certificate_id or _certificate_id(
                npm.get("certificate_id")
            )
            payload = build_proxy_host(
                app_instance_id,
                domains=domains,
                target_url=target_url,
                certificate_id=certificate_id,
                ssl_forced=data.ssl_forced,
            )
            async with self._npm_client(config) as client:
                await client.login()
                await client.save_proxy_host(app_instance_id, payload)
            message = (
                f"已在 Nginx Proxy Manager 创建 {', '.join(domains)} → {target_url}"
            )
        else:
            payloads = build_proxy_rules(
                links=links,
                default_port=default_port,
                default_tls=bool(lucky.get("enable_tls")),
            )
            async with self._lucky_client(config) as client:
                await client.save_proxy_rules(payloads)
            message = (
                f"已把 {', '.join(domains)} 转发到 {target_url}"
                f"（端口 {listen_port}）"
            )

        config["proxy_links"] = {str(k): v for k, v in links.items()}

        try:
            note = await self._sync_proxy_ddns(config, previous_links)
        except APIException as exc:
            note = f"；DDNS 域名同步失败：{exc.message}"
        message += note

        record.config = config
        await db.commit()
        return NetworkProxyResult(ok=True, message=message)

    async def delete_network_proxy(
        self, db: AsyncSession, instance_id: int
    ) -> NetworkProxyResult:
        record = await self._get_record(db, NETWORK)
        assert record is not None
        config = self._network_config(record)
        provider = _provider(config)
        links = self._proxy_links(config)
        previous_links = links.copy()
        links.pop(instance_id, None)

        if self._network_configured(provider, config):
            if provider == "npm":
                async with self._npm_client(config) as client:
                    await client.login()
                    await client.delete_proxy_host(instance_id)
            else:
                async with self._lucky_client(config) as client:
                    if links:
                        lucky = config.get("lucky") or {}
                        default_port = (
                            _positive_int(lucky.get("listen_port"))
                            or DEFAULT_LISTEN_PORT
                        )
                        payloads = build_proxy_rules(
                            links=links,
                            default_port=default_port,
                            default_tls=bool(lucky.get("enable_tls")),
                        )
                        await client.save_proxy_rules(payloads)
                    else:
                        await client.delete_proxy_rules()

        config["proxy_links"] = {str(k): v for k, v in links.items()}
        message = "已移除反向代理"
        try:
            message += await self._sync_proxy_ddns(config, previous_links)
        except APIException as exc:
            message += f"；DDNS 域名同步失败：{exc.message}"
        record.config = config
        await db.commit()
        return NetworkProxyResult(ok=True, message=message)

    async def list_npm_certificates(
        self, db: AsyncSession
    ) -> list[NpmCertificateOut]:
        record = await self._get_record(db, NETWORK)
        assert record is not None
        config = self._network_config(record)
        npm = config.get("npm") or {}
        if not (
            _text(npm.get("url"))
            and _text(npm.get("email"))
            and _text(npm.get("password"))
        ):
            raise APIException("请先保存 Nginx Proxy Manager 连接配置", 400)
        async with self._npm_client(config) as client:
            await client.login()
            rows = await client.list_certificates()
        certificates = []
        for row in rows:
            try:
                cert_id = int(row.get("id"))
            except (TypeError, ValueError):
                continue
            certificates.append(
                NpmCertificateOut(
                    id=cert_id,
                    nice_name=_text(row.get("nice_name")),
                    domain_names=[
                        _text(item)
                        for item in (row.get("domain_names") or [])
                        if _text(item)
                    ],
                    expires_on=_text(row.get("expires_on")) or None,
                )
            )
        return certificates

    async def list_network_certificates(
        self, db: AsyncSession
    ) -> list[NetworkCertificateOut]:
        """当前方案下可选的证书；Lucky 的证书由规则按域名自动匹配。"""
        record = await self._get_record(db, NETWORK)
        assert record is not None
        config = self._network_config(record)
        provider = _provider(config)
        if provider == "npm":
            if not self._network_configured("npm", config):
                return []
            return [
                NetworkCertificateOut(
                    id=str(item.id),
                    name=item.nice_name or (item.domain_names[0] if item.domain_names else f"证书 {item.id}"),
                    domains=item.domain_names,
                    expires_on=item.expires_on,
                )
                for item in await self.list_npm_certificates(db)
            ]

        if not self._network_configured("lucky", config):
            return []
        async with self._lucky_client(config) as client:
            rows = await client.list_certificates()
        return [
            certificate
            for row in rows
            if (certificate := _lucky_certificate_out(row)) is not None
        ]

    async def auto_provision_certificate(
        self, db: AsyncSession, data: NetworkCertificateRequest
    ) -> NetworkCertificateResult:
        """为域名自动准备 HTTPS 证书。

        内网域名生成自签名证书并上传到 Lucky；公网域名提交 Let's Encrypt
        ACME 申请，返回 pending，由前端轮询证书列表等待签发结果。
        """
        record = await self._get_record(db, NETWORK)
        assert record is not None
        config = self._network_config(record)
        provider = _provider(config)
        if provider != "lucky":
            raise APIException("自动添加证书目前只支持 Lucky 方案", 400)
        if not record.enabled:
            raise APIException("请先启用网络集成", 400)
        if not self._network_configured("lucky", config):
            raise APIException("请先保存 Lucky 连接配置", 400)

        domains: list[str] = []
        for raw in data.domains:
            domain = _normalize_domain(raw)
            if domain not in domains:
                domains.append(domain)
        if not domains:
            raise APIException("请至少填写一个域名", 400)

        dns = _ddns_dns_credentials(config)
        mode = data.mode
        if mode == "auto":
            mode = _auto_certificate_mode(domains, dns_available=dns is not None)
        elif mode == "acme":
            _validate_acme_domains(domains, dns_available=dns is not None)

        async with self._lucky_client(config) as client:
            rows = await client.list_certificates()
            certificates = [
                certificate
                for row in rows
                if (certificate := _lucky_certificate_out(row)) is not None
            ]

            existing = next(
                (
                    item
                    for item in certificates
                    if item.status == "valid"
                    and _certificate_covers(item.domains, domains)
                ),
                None,
            )
            if existing is not None:
                return NetworkCertificateResult(
                    ok=True,
                    message=f"已有覆盖这些域名的证书：{existing.name}",
                    certificate=existing,
                )
            pending = next(
                (
                    item
                    for item in certificates
                    if item.status == "pending"
                    and _certificate_covers(item.domains, domains)
                ),
                None,
            )
            if pending is not None:
                return NetworkCertificateResult(
                    ok=True,
                    message=f"证书正在申请中：{pending.name}",
                    certificate=pending,
                )

            # 重试前清掉上次失败的同域名自动证书，避免残留
            for item in certificates:
                if (
                    item.status == "failed"
                    and item.name.startswith(ONETAP_CERT_REMARK)
                    and set(item.domains) == set(domains)
                ):
                    await client.delete_certificate(item.id)

            remark = certificate_remark(domains)
            if mode == "selfsigned":
                cert_pem, key_pem = build_self_signed(domains)
                await client.create_certificate(
                    build_file_certificate_payload(
                        remark=remark,
                        cert_pem=cert_pem,
                        key_pem=key_pem,
                    )
                )
                created = self._find_auto_certificate(
                    await client.list_certificates(), domains, remark
                )
                if created is None or created.status != "valid":
                    raise APIException(
                        "证书已提交，但 Lucky 没有返回有效证书", 502
                    )
                return NetworkCertificateResult(
                    ok=True,
                    message=(
                        "已添加自签名证书；内网域名访问时浏览器会提示证书不受信任"
                        if any(is_internal_domain(domain) for domain in domains)
                        else "已添加自签名证书"
                    ),
                    certificate=created,
                )

            payload = build_acme_certificate_payload(
                domains=domains,
                remark=remark,
                validation=dns[0] if dns else "http",
                dns_id=dns[1] if dns else "",
                dns_secret=dns[2] if dns else "",
            )
            created = await self._submit_acme_certificate(
                client, payload, domains, remark
            )
            if created is None:
                return NetworkCertificateResult(
                    ok=False,
                    message="已提交申请，但 Lucky 没有返回证书记录",
                )
            if created.status == "failed":
                hint = (
                    "（Let's Encrypt 连接超时，可稍后重试）"
                    if _is_transient_acme_error(created.error or "")
                    else ""
                )
                return NetworkCertificateResult(
                    ok=False,
                    message=f"证书申请失败：{created.error}{hint}",
                    certificate=created,
                )
            method = "DNS 验证" if dns else "HTTP 验证"
            return NetworkCertificateResult(
                ok=True,
                message=f"已提交 Let's Encrypt 申请（{method}），签发完成后会自动匹配",
                certificate=created,
            )

    async def _submit_acme_certificate(
        self,
        client: LuckyClient,
        payload: dict,
        domains: list[str],
        remark: str,
    ) -> NetworkCertificateOut | None:
        """提交 ACME 申请；遇到注册/网络类临时错误时自动重试。"""
        certificate: NetworkCertificateOut | None = None
        for attempt in range(ACME_ATTEMPTS):
            await client.create_certificate(payload)
            certificate = None
            for _ in range(ACME_POLL_ATTEMPTS):
                await asyncio.sleep(ACME_POLL_INTERVAL)
                certificate = self._find_auto_certificate(
                    await client.list_certificates(), domains, remark
                )
                if certificate is not None and certificate.status != "pending":
                    break
            if certificate is None or certificate.status != "failed":
                return certificate
            if attempt == ACME_ATTEMPTS - 1 or not _is_transient_acme_error(
                certificate.error or ""
            ):
                return certificate
            await client.delete_certificate(certificate.id)
        return certificate

    def _find_auto_certificate(
        self, rows: list[dict], domains: list[str], remark: str
    ) -> NetworkCertificateOut | None:
        """在 Lucky 列表里找回刚创建的自动证书。"""
        expected = set(domains)
        for row in rows:
            certificate = _lucky_certificate_out(row)
            if certificate is None:
                continue
            if certificate.name != remark:
                continue
            if set(certificate.domains) != expected:
                continue
            return certificate
        return None

    def _entry_domains(self, entry: dict) -> list[str]:
        domains: list[str] = []
        for key in ("Ipv4Domains", "Ipv6Domains"):
            raw = entry.get(key)
            if not isinstance(raw, str):
                continue
            for item in raw.replace(",", "\n").splitlines():
                domain = item.strip()
                if domain and domain not in domains:
                    domains.append(domain)
        return domains

    async def network_ddns_state(
        self, db: AsyncSession
    ) -> NetworkDdnsStateOut:
        record = await self._get_record(db, NETWORK)
        assert record is not None
        config = self._network_config(record)
        provider = _provider(config)
        base = self._ddns_out(config)
        state = NetworkDdnsStateOut(**base.model_dump())
        if not self._network_configured(provider, config):
            state.message = "请先保存网络集成连接配置"
            return state

        try:
            if provider == "npm":
                if not self._ddnsgo_configured(config):
                    state.message = "请先填写 DDNS-Go 连接配置"
                    return state
                async with self._ddnsgo_client(config) as client:
                    await client.login()
                    info = await client.fetch_config()
                entry = find_ddnsgo_entry(info.dns_conf)
                if entry:
                    types = []
                    if entry.get("Ipv4Enable"):
                        types.append("IPv4")
                    if entry.get("Ipv6Enable"):
                        types.append("IPv6")
                    enabled = bool(types)
                    record_type = " & ".join(types) or "IPv4"
                    state.tasks = [
                        NetworkDdnsTaskOut(
                            task_key="onetap-ddnsgo",
                            task_name="OneTap",
                            task_type=record_type,
                            domains=self._entry_domains(entry),
                            enabled=enabled,
                        )
                    ]
            else:
                async with self._lucky_client(config) as client:
                    await client.login()
                    tasks = await client.list_ddns_tasks()
                state.tasks = [
                    NetworkDdnsTaskOut(
                        task_key=_text(task.get("TaskKey")),
                        task_name=_text(task.get("TaskName")),
                        task_type=_text(task.get("TaskType")),
                        domains=_ddns_task_domains(task),
                        enabled=bool(task.get("Enable")),
                        **_lucky_task_snapshot(task),
                    )
                    for task in tasks
                ]
        except APIException as exc:
            state.message = exc.message
        return state

    async def network_ddns_interfaces(
        self, db: AsyncSession
    ) -> NetworkDdnsInterfacesOut:
        """Lucky 所在主机的网卡列表；其他方案返回空，由用户手填。"""
        record = await self._get_record(db, NETWORK)
        assert record is not None
        config = self._network_config(record)
        if _provider(config) != "lucky":
            return NetworkDdnsInterfacesOut()
        if not self._network_configured("lucky", config):
            return NetworkDdnsInterfacesOut()
        try:
            async with self._lucky_client(config) as client:
                data = await client.list_interfaces()
        except APIException:
            return NetworkDdnsInterfacesOut()

        def parse(key: str) -> list[NetworkDdnsInterfaceOut]:
            rows = data.get(key) or []
            result = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                name = _text(row.get("NetInterfaceName"))
                if not name or name.lower().startswith(("docker", "br-", "veth")):
                    continue
                addresses = [
                    _text(item)
                    for item in (row.get("AddressList") or [])
                    if _text(item)
                ]
                result.append(
                    NetworkDdnsInterfaceOut(
                        name=name,
                        hardware_addr=_text(row.get("HardwareAddr")),
                        addresses=addresses,
                    )
                )
            return result

        return NetworkDdnsInterfacesOut(
            ipv4=parse("IPv4NewInterfaces"),
            ipv6=parse("IPv6NewInterfaces"),
        )

    async def save_network_ddns(
        self, db: AsyncSession, data: NetworkDdnsUpdate
    ) -> NetworkDdnsStateOut:
        record = await self._get_record(db, NETWORK)
        assert record is not None
        config = self._network_config(record)
        provider = _provider(config)
        if not self._network_configured(provider, config):
            raise APIException("请先保存网络集成连接配置", 400)
        if data.enabled and not record.enabled:
            raise APIException("请先启用网络集成", 400)

        updates = {"IPv4": data.ipv4, "IPv6": data.ipv6}
        submitted_secret = (
            (data.ipv4.secret or "").strip()
            or (data.ipv6.secret or "").strip()
        )
        current_families = self._normalized_ddns_families(config)
        shared_secret = (
            current_families["IPv4"]["secret"]
            or current_families["IPv6"]["secret"]
        )
        families: dict[str, dict] = {}
        for family in DDNS_FAMILIES:
            item = updates[family]
            base = current_families[family]
            secret = (
                (item.secret or "").strip()
                or base["secret"]
                or submitted_secret
                or shared_secret
            )
            manual_domains: list[str] = []
            for raw in item.domains:
                if not _text(raw):
                    continue
                domain = _normalize_domain(raw)
                if domain not in manual_domains:
                    manual_domains.append(domain)
            manual_domains = _merge_domains(manual_domains)
            auto_domains = base["auto_domains"]
            domains = _merge_domains(manual_domains, auto_domains)
            enabled = bool(data.enabled and item.enabled)
            if item.enabled:
                if not domains:
                    raise APIException(f"{family} 请至少填写一个域名", 400)
                if not _text(item.provider):
                    raise APIException(f"{family} 请选择 DNS 服务商", 400)
                if not secret:
                    raise APIException(
                        f"{family} 请填写 DNS 服务商密钥", 400
                    )
                if (
                    item.provider not in DDNS_NO_ID_PROVIDERS
                    and not item.access_id.strip()
                ):
                    raise APIException(
                        f"{family} 请填写 DNS 服务商账号 ID", 400
                    )
                if item.get_type == "netInterface" and not item.net_interface.strip():
                    raise APIException(f"{family} 请选择网卡", 400)

            families[family] = {
                "enabled": enabled,
                "provider": _text(item.provider) or base["provider"],
                "access_id": item.access_id.strip(),
                "secret": secret,
                "domains": domains,
                "manual_domains": manual_domains,
                "auto_domains": auto_domains,
                "interval": item.interval,
                "get_type": item.get_type,
                "url": item.url.strip(),
                "net_interface": item.net_interface.strip(),
                "ip_reg": item.ip_reg.strip(),
            }

        await self._push_ddns_families(config, families, enabled=bool(data.enabled))
        record.config = config
        await db.commit()
        return await self.network_ddns_state(db)

    async def _push_ddns_families(
        self,
        config: dict,
        families: dict[str, dict],
        *,
        enabled: bool,
    ) -> None:
        """把两套协议族配置同步到 Lucky / DDNS-Go，成功后才写回 config。"""
        provider = _provider(config)
        if provider == "npm":
            await self._save_ddnsgo(config, families, enabled=enabled)
        else:
            async with self._lucky_client(config) as client:
                await client.ensure_ddns_enabled()
                for family in DDNS_FAMILIES:
                    item = families[family]
                    payload = build_ddns_task(
                        record_type=family,
                        enabled=item["enabled"],
                        provider=item["provider"],
                        access_id=item["access_id"],
                        secret=item["secret"],
                        domains=item["domains"],
                        interval=item["interval"],
                        get_type=item["get_type"],
                        url=item["url"],
                        net_interface=item["net_interface"],
                        ip_reg=item["ip_reg"],
                    )
                    await client.save_ddns_task(family, payload)
                # 两套任务都写成功后再清理旧版单任务，避免中途失败丢配置
                await client.delete_legacy_ddns_task()

        config["ddns"] = {
            "enabled": enabled,
            "ipv4": families["IPv4"],
            "ipv6": families["IPv6"],
        }

    async def _sync_proxy_ddns(
        self,
        config: dict,
        previous_links: dict[int, dict],
    ) -> str:
        """按当前代理重算自动域名，保留手动域名并同步已启用的任务。"""
        current_links = self._proxy_links(config)
        changed = _proxy_ddns_domains(previous_links) != _proxy_ddns_domains(
            current_links
        )
        families = self._normalized_ddns_families(config, previous_links)
        enabled = bool((config.get("ddns") or {}).get("enabled"))
        config["ddns"] = {
            "enabled": enabled,
            "ipv4": families["IPv4"],
            "ipv6": families["IPv6"],
        }
        if not changed:
            return ""
        if enabled and _ddns_dns_credentials(config):
            if _provider(config) != "npm" or self._ddnsgo_configured(config):
                await self._push_ddns_families(config, families, enabled=True)
        return "；DDNS 自动域名已更新"

    async def _save_ddnsgo(
        self,
        config: dict,
        families: dict[str, dict],
        *,
        enabled: bool,
    ) -> None:
        if not self._ddnsgo_configured(config):
            raise APIException("请先填写 DDNS-Go 连接配置", 400)

        # DDNS-Go 的 DNS 服务商和凭据是条目级共用的，优先取启用的协议族
        candidates = [
            families[family]
            for family in DDNS_FAMILIES
            if families[family]["enabled"]
        ] or [families["IPv4"], families["IPv6"]]
        shared = candidates[0]
        entry = build_ddnsgo_entry(
            enabled=enabled,
            provider=shared["provider"],
            access_id=shared["access_id"],
            secret=shared["secret"],
            ipv4=families["IPv4"],
            ipv6=families["IPv6"],
        )

        async with self._ddnsgo_client(config) as client:
            await client.login()
            info = await client.fetch_config()
            entries = merge_ddnsgo_entry(
                info.dns_conf, entry, create=enabled
            )
            if entries != info.dns_conf:
                await client.save_config(info.global_conf, entries)

    async def delete_network_ddns(
        self, db: AsyncSession
    ) -> NetworkDdnsStateOut:
        record = await self._get_record(db, NETWORK)
        assert record is not None
        config = self._network_config(record)
        provider = _provider(config)

        if self._network_configured(provider, config):
            if provider == "npm":
                if self._ddnsgo_configured(config):
                    async with self._ddnsgo_client(config) as client:
                        await client.login()
                        info = await client.fetch_config()
                        entries = remove_ddnsgo_entry(info.dns_conf)
                        if entries != info.dns_conf:
                            await client.save_config(
                                info.global_conf, entries
                            )
            else:
                async with self._lucky_client(config) as client:
                    await client.delete_ddns_tasks()

        ddns = dict(config.get("ddns") or {})
        ddns["enabled"] = False
        for family in DDNS_FAMILIES:
            raw = ddns.get(family.lower())
            if isinstance(raw, dict):
                raw["enabled"] = False
        config["ddns"] = ddns
        record.config = config
        await db.commit()
        return await self.network_ddns_state(db)

integration_service = IntegrationService()
