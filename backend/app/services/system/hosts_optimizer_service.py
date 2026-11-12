"""Hosts 优选服务。

通过 AdGuard Home 的 DNS 重写能力，把配置域名的解析结果替换为
实测可用的最快 IP。支持手动触发与后台定时运行：

- DNS 解析优先使用国内可直连的 DNSPod DoH；配置了系统代理时再补充
  Cloudflare / Google DoH，避免单个上游被污染或不可用。
- 对候选 IP 做 TLS + HTTP 探测，过滤掉握手失败、超时和 403/5xx 的地址。
- 结果写入 AdGuard 的 DNS 重写；同时把最近一次运行结果保存在
  ``system_config.hosts_optimizer_status`` 供前端展示。
"""

from __future__ import annotations

import asyncio
import logging
import re
import ssl
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import APIException
from app.database import async_session
from app.models.system_config import SystemConfig
from app.schemas.hosts_optimizer import (
    DEFAULT_HOSTS_OPTIMIZER_DOMAINS,
    DEFAULT_HOSTS_OPTIMIZER_INTERVAL_MINUTES,
    DEFAULT_HOSTS_OPTIMIZER_TOP_N,
    HostsOptimizerConfigUpdate,
    HostsOptimizerTestRequest,
)
from app.services.manage_client import (
    ManageRequestError,
    manage_base_urls,
    with_manage_fallback,
)
from app.services.system.config_service import system_config_service

logger = logging.getLogger(__name__)

MAX_DOMAINS = 50
PROBE_TIMEOUT = 4.0
PROBE_CONCURRENCY = 4
PROBE_ATTEMPTS = 1
RESOLVE_CONCURRENCY = 4
DOH_TIMEOUT = 8.0

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# (DoH 地址, 是否使用系统代理)。DNSPod 在本网络可直连且返回未污染结果；
# Cloudflare / Google 仅作为配置了代理时的补充。
DOH_ENDPOINTS: list[tuple[str, bool]] = [
    ("https://doh.pub/dns-query", False),
    ("https://1.12.12.12/dns-query", False),
    ("https://1.1.1.1/dns-query", True),
    ("https://8.8.8.8/resolve", True),
]

CHECK_PATHS: dict[str, str] = {
    "api.themoviedb.org": "/3/configuration",
    "api.tmdb.org": "/3/configuration",
    "image.tmdb.org": "/",
    "www.themoviedb.org": "/",
    "themoviedb.org": "/",
    "github.com": "/",
    "api.github.com": "/",
    "objects.githubusercontent.com": "/",
    "codeload.github.com": "/",
}

# DoH 有时不会返回对当前网络可用的边缘 IP，补充已知候选，最终仍以探测结果为准。
STATIC_CANDIDATES: dict[str, list[str]] = {
    "api.themoviedb.org": ["18.244.214.44", "18.244.214.88", "99.84.215.62"],
    "www.themoviedb.org": ["3.166.244.31", "3.166.244.95"],
    "image.tmdb.org": ["169.150.207.217", "84.17.38.232", "79.127.134.229"],
    "api.tmdb.org": ["99.84.215.100", "52.85.151.115"],
    "themoviedb.org": ["3.166.244.31", "3.166.244.95"],
    "github.com": [
        "140.82.112.3",
        "140.82.113.3",
        "140.82.114.3",
        "140.82.121.3",
        "20.205.243.166",
        "20.27.177.113",
    ],
    "api.github.com": ["20.205.243.168", "20.27.177.116"],
    "objects.githubusercontent.com": [
        "185.199.108.133",
        "185.199.109.133",
        "185.199.110.133",
        "185.199.111.133",
    ],
    "codeload.github.com": ["20.205.243.165", "20.27.177.114"],
}

VALID_STATUS = set(range(200, 400)) | {401, 404}
EXTRA_VALID_STATUS = {"www.themoviedb.org": {403}}

SSL_CONTEXT = ssl.create_default_context()
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))+$"
)

MAX_MANAGED_RULES = 200
"""管理端下发的加速规则数量上限。"""

MANAGE_RULES_TIMEOUT = 10.0
"""请求管理端加速规则的超时时间（秒）。"""


def _probe_name(domain: str) -> str:
    """探测通配规则时使用根域名，``*.example.com`` 不能作为 SNI。"""
    return domain[2:] if domain.startswith("*.") else domain


def normalize_domains(raw: list[str]) -> list[str]:
    """校验并规范化域名列表：去空白、转小写、去重。"""
    result: list[str] = []
    for item in raw:
        domain = str(item or "").strip().lower().rstrip(".")
        if not domain:
            continue
        if not _DOMAIN_RE.match(domain):
            raise APIException(f"域名格式不正确：{domain}", 400)
        if domain not in result:
            result.append(domain)
    if not result:
        raise APIException("至少配置一个优选域名", 400)
    if len(result) > MAX_DOMAINS:
        raise APIException(f"优选域名最多 {MAX_DOMAINS} 个", 400)
    return result


def normalize_managed_rules(payload: Any) -> list[dict[str, Any]]:
    """把管理端返回的免费加速规则整理成统一结构。

    兼容字符串和对象两种形态。旧版本下发过的 ``answers`` / ``tier`` 会被
    忽略：解析地址统一由客户端本地优选，规则等级固定为 free。
    """
    items = payload
    if isinstance(payload, dict):
        for key in ("data", "dns_rules", "rules", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                items = value
                break
        else:
            items = []

    if not isinstance(items, list):
        return []

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        if isinstance(item, str):
            domain = item
            priority = 100
            description = None
        elif isinstance(item, dict):
            if item.get("enabled") is False:
                continue
            domain = (
                item.get("domain")
                or item.get("host")
                or item.get("name")
                or ""
            )
            priority = item.get("priority")
            priority = priority if isinstance(priority, int) else 100
            description = item.get("description") or item.get("note")
        else:
            continue

        normalized = str(domain or "").strip().lower().rstrip(".")
        if not normalized:
            continue
        if not _DOMAIN_RE.match(normalized.removeprefix("*.")):
            logger.warning("忽略管理端下发的非法域名：%s", normalized)
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(
            {
                "domain": normalized,
                "answers": [],
                "priority": priority,
                "description": str(description).strip() if description else None,
                "tier": "free",
            }
        )
        if len(result) >= MAX_MANAGED_RULES:
            break

    result.sort(key=lambda rule: (rule["priority"], rule["domain"]))
    return result


def normalize_adguard_url(url: str | None) -> str | None:
    """校验并规范化 AdGuard 管理地址。"""
    value = (url or "").strip().rstrip("/")
    if not value:
        return None
    if not value.lower().startswith(("http://", "https://")):
        raise APIException("AdGuard 地址必须以 http:// 或 https:// 开头", 400)
    if len(value) > 500:
        raise APIException("AdGuard 地址长度不能超过 500 个字符", 400)
    return value


def _parse_iso(value: str | None) -> datetime | None:
    """解析 ISO 时间，兼容无时区的历史数据。"""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def mask_run_ips(run: dict[str, Any] | None) -> dict[str, Any] | None:
    """去掉优选结果里的具体 IP，只保留数量与最优延迟。

    所有对外接口都返回该结果，避免截图、日志或抓包暴露当前网络能直连的
    节点地址（可反推所在地区）；完整地址只留在数据库和写 AdGuard 的流程里。
    """
    if not isinstance(run, dict):
        return run
    domains = run.get("domains")
    if not isinstance(domains, dict):
        return run

    masked: dict[str, Any] = {}
    for domain, result in domains.items():
        if not isinstance(result, dict):
            masked[domain] = result
            continue
        ips = result.get("ips") or []
        latencies = [
            item.get("latency_ms")
            for item in ips
            if isinstance(item, dict)
            and isinstance(item.get("latency_ms"), (int, float))
        ]
        masked[domain] = {
            **{key: value for key, value in result.items() if key != "ips"},
            "ip_count": len(ips),
            "best_latency_ms": min(latencies) if latencies else None,
        }
    return {**run, "domains": masked}


class HostsOptimizerService:
    """Hosts 优选业务服务。"""

    def __init__(self) -> None:
        self._running = False
        self._scheduler_task: asyncio.Task | None = None
        self._scheduler_stop: asyncio.Event | None = None
        self._scheduler_wake: asyncio.Event | None = None

    @property
    def running(self) -> bool:
        """是否正在执行优选。"""
        return self._running

    # ---------- 配置 ----------

    def config_from_model(
        self, config: SystemConfig, include_password: bool = True
    ) -> dict[str, Any]:
        """把 ORM 配置转换为前端可用的结构。"""
        domains = config.hosts_optimizer_domains or list(DEFAULT_HOSTS_OPTIMIZER_DOMAINS)
        synced_at = config.hosts_optimizer_managed_rules_synced_at
        password = config.hosts_optimizer_adguard_password or ""
        return {
            "enabled": bool(config.hosts_optimizer_enabled),
            "mode": (
                config.hosts_optimizer_mode
                if config.hosts_optimizer_mode in ("manual", "auto")
                else "manual"
            ),
            "interval_minutes": config.hosts_optimizer_interval_minutes
            or DEFAULT_HOSTS_OPTIMIZER_INTERVAL_MINUTES,
            "top_n": config.hosts_optimizer_top_n or DEFAULT_HOSTS_OPTIMIZER_TOP_N,
            "domains": list(domains),
            "adguard_url": config.hosts_optimizer_adguard_url or None,
            "adguard_username": config.hosts_optimizer_adguard_username or "admin",
            "adguard_password": password if include_password else "",
            "adguard_password_set": bool(password),
            "adguard_managed": bool(config.hosts_optimizer_adguard_managed),
            "adguard_dns_ip": config.hosts_optimizer_adguard_dns_ip or None,
            "manage_configured": bool(manage_base_urls()),
            "managed_rules": list(config.hosts_optimizer_managed_rules or []),
            "managed_rules_synced_at": synced_at.isoformat(timespec="seconds")
            if isinstance(synced_at, datetime)
            else (str(synced_at) if synced_at else None),
        }

    def _runtime_config(self, config: SystemConfig) -> dict[str, Any]:
        """在公开配置基础上补充探测所需的代理地址（仅启用代理时生效）。"""
        values = self.config_from_model(config)
        values["proxy"] = (
            (config.https_proxy or config.http_proxy or None)
            if config.proxy_enabled
            else None
        )
        values["managed_rules"] = list(config.hosts_optimizer_managed_rules or [])
        return values

    def effective_rules(self, config: SystemConfig) -> list[dict[str, Any]]:
        """返回本次应写入 AdGuard 的规则。

        管理端下发规则优先；没有管理端规则时回退到本地优选域名。
        """
        managed = normalize_managed_rules(config.hosts_optimizer_managed_rules or [])
        if managed:
            return managed
        domains = config.hosts_optimizer_domains or list(DEFAULT_HOSTS_OPTIMIZER_DOMAINS)
        return [
            {
                "domain": domain,
                "answers": [],
                "priority": 100,
                "description": None,
                "tier": "free",
            }
            for domain in domains
        ]

    async def update_config(
        self, db: AsyncSession, data: HostsOptimizerConfigUpdate
    ) -> SystemConfig:
        """保存 Hosts 优选配置（整份提交）。"""
        config = await system_config_service.get_or_create(db)
        config.hosts_optimizer_enabled = bool(data.enabled)
        # 开关即模式：UI 不再单独提供“手动/自动”，开启即定时运行。
        config.hosts_optimizer_mode = "auto" if data.enabled else "manual"
        config.hosts_optimizer_interval_minutes = max(
            5, min(10080, int(data.interval_minutes))
        )
        config.hosts_optimizer_top_n = max(1, min(5, int(data.top_n)))
        config.hosts_optimizer_domains = normalize_domains(data.domains)
        config.hosts_optimizer_adguard_url = normalize_adguard_url(data.adguard_url)
        config.hosts_optimizer_adguard_username = (
            data.adguard_username.strip() or "admin"
        )
        if data.adguard_password:
            config.hosts_optimizer_adguard_password = data.adguard_password
        await db.flush()
        await db.refresh(config)
        return config

    def next_run_at(self, config: SystemConfig) -> str | None:
        """计算下一次自动运行时间；未开启自动优选时返回 None。"""
        if not config.hosts_optimizer_enabled:
            return None
        interval = max(
            5,
            min(
                10080,
                config.hosts_optimizer_interval_minutes
                or DEFAULT_HOSTS_OPTIMIZER_INTERVAL_MINUTES,
            ),
        )
        status = config.hosts_optimizer_status or {}
        last = _parse_iso(status.get("finished_at"))
        if last is None:
            return datetime.now(timezone.utc).isoformat(timespec="seconds")
        return (last + timedelta(minutes=interval)).isoformat(timespec="seconds")

    # ---------- AdGuard ----------

    async def test_connection(
        self,
        config: SystemConfig,
        overrides: HostsOptimizerTestRequest | None = None,
    ) -> dict[str, Any]:
        """测试 AdGuard 管理接口是否可用。"""
        values = self.config_from_model(config)
        url = (
            overrides.adguard_url
            if overrides is not None and overrides.adguard_url is not None
            else values["adguard_url"]
        )
        username = (
            overrides.adguard_username
            if overrides is not None and overrides.adguard_username is not None
            else values["adguard_username"]
        )
        password = (
            overrides.adguard_password
            if overrides is not None and overrides.adguard_password is not None
            else values["adguard_password"]
        )
        try:
            normalized = normalize_adguard_url(url)
        except APIException as exc:
            return {"ok": False, "version": None, "message": exc.message}
        if not normalized:
            return {"ok": False, "version": None, "message": "请先填写 AdGuard 管理地址"}
        try:
            async with httpx.AsyncClient(
                base_url=normalized,
                auth=(username, password),
                trust_env=False,
                timeout=8.0,
            ) as client:
                resp = await client.get("/control/status")
                resp.raise_for_status()
                payload = resp.json()
            return {
                "ok": True,
                "version": payload.get("version"),
                "message": "连接成功",
            }
        except Exception as exc:
            return {"ok": False, "version": None, "message": f"连接失败：{exc}"}

    async def _sync_adguard(
        self,
        url: str,
        username: str,
        password: str,
        domains: list[str],
        best: dict[str, list[tuple[str, float]]],
        stale_domains: list[str] | None = None,
    ) -> list[str]:
        """把优选结果写入 AdGuard DNS 重写，返回逐域名错误列表。"""
        errors: list[str] = []
        async with httpx.AsyncClient(
            base_url=url,
            auth=(username, password),
            trust_env=False,
            timeout=10.0,
        ) as client:
            try:
                resp = await client.get("/control/rewrite/list")
                resp.raise_for_status()
                existing = resp.json()
            except Exception as exc:
                raise APIException(f"AdGuard 连接失败：{exc}", 502) from exc
            if not isinstance(existing, list):
                raise APIException("AdGuard 返回的 DNS 重写列表格式异常", 502)
            for domain in domains:
                desired = [ip for ip, _ in best.get(domain, [])]
                if not desired:
                    # 探测失败可能只是临时网络抖动，保留上一次的重写，
                    # 避免把已经可用的域名退回可能被污染的默认解析。
                    logger.warning("%s 没有可用 IP，保留已有 DNS 重写", domain)
                    continue
                current = [
                    item.get("answer")
                    for item in existing
                    if isinstance(item, dict) and item.get("domain") == domain
                ]
                try:
                    for answer in current:
                        if answer not in desired:
                            res = await client.post(
                                "/control/rewrite/delete",
                                json={"domain": domain, "answer": answer},
                            )
                            res.raise_for_status()
                    for answer in desired:
                        if answer not in current:
                            res = await client.post(
                                "/control/rewrite/add",
                                json={"domain": domain, "answer": answer},
                            )
                            res.raise_for_status()
                except Exception as exc:
                    errors.append(f"{domain}: {exc}")
            stale = {
                domain for domain in (stale_domains or []) if domain not in domains
            }
            for item in existing:
                if not isinstance(item, dict):
                    continue
                domain = item.get("domain")
                if domain not in stale:
                    continue
                try:
                    res = await client.post(
                        "/control/rewrite/delete",
                        json={"domain": domain, "answer": item.get("answer")},
                    )
                    res.raise_for_status()
                except Exception as exc:
                    errors.append(f"{domain}: {exc}")
        return errors

    # ---------- 管理端规则 ----------

    async def fetch_managed_rules(self) -> dict[str, Any]:
        """从配套管理端拉取加速域名规则。

        ``MANAGE_BASE_URL`` 未配置时返回 ``configured=False``；请求失败
        或响应格式异常时抛出 ``APIException``，由调用方决定是否回退缓存。
        """
        if not manage_base_urls():
            return {"configured": False, "rules": [], "synced_at": None}

        try:
            async with httpx.AsyncClient(
                timeout=MANAGE_RULES_TIMEOUT,
                follow_redirects=True,
            ) as client:
                resp, _ = await with_manage_fallback(
                    lambda url: client.get(f"{url}/api/public/dns-rules")
                )
                resp.raise_for_status()
                payload = resp.json()
        except ManageRequestError as exc:
            raise APIException(f"从管理端获取加速规则失败：{exc}", 502) from exc
        except httpx.HTTPError as exc:
            raise APIException(f"从管理端获取加速规则失败：{exc}", 502) from exc
        except ValueError as exc:
            raise APIException("管理端加速规则响应不是合法 JSON", 502) from exc

        rules = normalize_managed_rules(payload)
        return {
            "configured": True,
            "rules": rules,
            "synced_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    async def sync_managed_rules(
        self, db: AsyncSession, config: SystemConfig
    ) -> dict[str, Any]:
        """拉取并缓存管理端加速规则。"""
        result = await self.fetch_managed_rules()
        rules = normalize_managed_rules(result["rules"])
        if result["configured"]:
            config.hosts_optimizer_managed_rules = rules
            config.hosts_optimizer_managed_rules_synced_at = datetime.now(
                timezone.utc
            )
            await db.flush()
        return {
            "configured": result["configured"],
            "rules": rules,
            "synced_at": result["synced_at"],
            "error": None,
        }

    # ---------- 解析与探测 ----------

    def _make_doh_clients(
        self, proxy: str | None
    ) -> tuple[httpx.AsyncClient, httpx.AsyncClient | None]:
        headers = {"Accept": "application/dns-json"}
        direct = httpx.AsyncClient(
            trust_env=False, timeout=DOH_TIMEOUT, headers=headers
        )
        proxied = (
            httpx.AsyncClient(
                proxy=proxy, trust_env=False, timeout=DOH_TIMEOUT, headers=headers
            )
            if proxy
            else None
        )
        return direct, proxied

    async def _resolve_domain(
        self,
        direct: httpx.AsyncClient,
        proxied: httpx.AsyncClient | None,
        domain: str,
    ) -> list[str]:
        """通过多个 DoH 上游收集候选 A 记录。"""
        ips: set[str] = set(STATIC_CANDIDATES.get(domain, []))
        for url, use_proxy in DOH_ENDPOINTS:
            client = proxied if use_proxy else direct
            if client is None:
                continue
            try:
                resp = await client.get(url, params={"name": domain, "type": "A"})
                resp.raise_for_status()
                payload = resp.json()
                for answer in payload.get("Answer", []):
                    if answer.get("type") == 1 and answer.get("data"):
                        ips.add(answer["data"])
            except Exception as exc:
                logger.debug("DoH %s 解析 %s 失败：%s", url, domain, exc)
        return sorted(ips)

    async def _probe(
        self, ip: str, domain: str, semaphore: asyncio.Semaphore
    ) -> float | None:
        """对候选 IP 做一次 TLS + HTTP 探测，返回延迟（毫秒）。"""
        path = CHECK_PATHS.get(domain, "/")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {domain}\r\n"
            f"User-Agent: {BROWSER_USER_AGENT}\r\n"
            "Accept: */*\r\n"
            "Connection: close\r\n\r\n"
        ).encode()
        best: float | None = None
        for _ in range(PROBE_ATTEMPTS):
            async with semaphore:
                started = time.monotonic()
                writer = None
                try:
                    async with asyncio.timeout(PROBE_TIMEOUT):
                        reader, writer = await asyncio.open_connection(
                            ip, 443, ssl=SSL_CONTEXT, server_hostname=domain
                        )
                        writer.write(request)
                        await writer.drain()
                        data = await reader.read(256)
                    if not data:
                        continue
                    parts = data.split(b"\r\n", 1)[0].split()
                    if len(parts) < 2:
                        continue
                    status = int(parts[1])
                    if (
                        status not in VALID_STATUS
                        and status not in EXTRA_VALID_STATUS.get(domain, set())
                    ):
                        continue
                    elapsed = (time.monotonic() - started) * 1000
                    best = elapsed if best is None else min(best, elapsed)
                except Exception:
                    continue
                finally:
                    if writer is not None:
                        writer.close()
                        try:
                            await asyncio.wait_for(writer.wait_closed(), timeout=1.0)
                        except Exception:
                            pass
        return best

    async def optimize(self, config: dict[str, Any], mode: str = "manual") -> dict[str, Any]:
        """执行一次完整优选并同步到 AdGuard。"""
        url = config.get("adguard_url")
        password = config.get("adguard_password") or ""
        if not url or not password:
            raise APIException("请先配置 AdGuard 管理地址和管理员密码", 400)

        managed = normalize_managed_rules(config.get("managed_rules") or [])
        if managed:
            rules = managed
        else:
            rules = [
                {"domain": domain, "answers": []}
                for domain in (
                    config.get("domains") or DEFAULT_HOSTS_OPTIMIZER_DOMAINS
                )
            ]
        domains = [rule["domain"] for rule in rules]
        top_n = max(1, min(5, int(config.get("top_n") or DEFAULT_HOSTS_OPTIMIZER_TOP_N)))
        proxy = config.get("proxy")
        started = time.monotonic()
        started_at = datetime.now(timezone.utc)

        direct, proxied = self._make_doh_clients(proxy)
        try:
            resolve_semaphore = asyncio.Semaphore(RESOLVE_CONCURRENCY)

            async def resolve_one(domain: str) -> tuple[str, list[str]]:
                async with resolve_semaphore:
                    return domain, await self._resolve_domain(direct, proxied, domain)

            resolved = await asyncio.gather(*(resolve_one(d) for d in domains))
        finally:
            await direct.aclose()
            if proxied is not None:
                await proxied.aclose()

        probe_semaphore = asyncio.Semaphore(PROBE_CONCURRENCY)
        probe_targets: list[tuple[str, str]] = [
            (domain, ip) for domain, ips in resolved for ip in ips
        ]
        probe_jobs = [
            (
                domain,
                ip,
                asyncio.create_task(
                    self._probe(ip, _probe_name(domain), probe_semaphore)
                ),
            )
            for domain, ip in probe_targets
        ]
        if probe_jobs:
            await asyncio.gather(*(task for _, _, task in probe_jobs), return_exceptions=True)

        reachable: dict[str, list[tuple[str, float]]] = {}
        for domain, ip, task in probe_jobs:
            try:
                latency = task.result()
            except Exception:
                latency = None
            if latency is not None:
                reachable.setdefault(domain, []).append((ip, latency))

        best: dict[str, list[tuple[str, float]]] = {}
        for domain in domains:
            best[domain] = sorted(
                reachable.get(domain, []), key=lambda item: item[1]
            )[:top_n]

        adguard_errors = await self._sync_adguard(
            url,
            config.get("adguard_username") or "admin",
            password,
            domains,
            best,
            config.get("stale_managed_domains") or [],
        )
        finished_at = datetime.now(timezone.utc)
        return {
            "mode": mode,
            "started_at": started_at.isoformat(timespec="seconds"),
            "finished_at": finished_at.isoformat(timespec="seconds"),
            "duration_ms": int((time.monotonic() - started) * 1000),
            "adguard": "ok" if not adguard_errors else "error",
            "errors": adguard_errors,
            "domains": {
                domain: {
                    "status": "unreachable" if not best[domain] else "ok",
                    "source": "local",
                    "ips": [
                        {"ip": ip, "latency_ms": round(latency)}
                        for ip, latency in best[domain]
                    ],
                }
                for domain in domains
            },
        }

    async def run_and_save(
        self, db: AsyncSession, config: SystemConfig, mode: str = "manual"
    ) -> dict[str, Any]:
        """执行一次优选并把结果写回 system_config。"""
        stale_managed_domains: list[str] = []
        if manage_base_urls():
            previous = {
                rule["domain"]
                for rule in normalize_managed_rules(
                    config.hosts_optimizer_managed_rules or []
                )
            }
            try:
                await self.sync_managed_rules(db, config)
            except Exception as exc:
                # 管理端临时不可用时继续使用上一次缓存，避免加速中断。
                logger.warning("同步管理端加速规则失败，使用本地缓存：%s", exc)
            else:
                current = {
                    rule["domain"]
                    for rule in normalize_managed_rules(
                        config.hosts_optimizer_managed_rules or []
                    )
                }
                stale_managed_domains = sorted(previous - current)

        # 托管 AdGuard 时先刷新管理地址：优先使用容器 IP，宿主机 IP 变化不影响同步。
        if config.hosts_optimizer_adguard_managed:
            try:
                from app.services.system.hosts_acceleration_service import (
                    hosts_acceleration_service as acceleration_service,
                )

                await acceleration_service.refresh_managed_url(db, config)
            except Exception as exc:
                logger.warning("刷新 AdGuard 管理地址失败：%s", exc)

        runtime = self._runtime_config(config)
        runtime["stale_managed_domains"] = stale_managed_domains
        if not runtime["adguard_url"] or not runtime["adguard_password"]:
            raise APIException("请先配置 AdGuard 管理地址和管理员密码", 400)
        if self._running:
            raise APIException("网络优化正在运行，请稍后再试", 409)

        self._running = True
        started_at = datetime.now(timezone.utc)
        try:
            result = await self.optimize(runtime, mode=mode)
        except Exception as exc:
            config.hosts_optimizer_status = {
                "mode": mode,
                "started_at": started_at.isoformat(timespec="seconds"),
                "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "duration_ms": int(
                    (datetime.now(timezone.utc) - started_at).total_seconds() * 1000
                ),
                "adguard": "error",
                "errors": [str(exc)],
                "domains": {},
            }
            await db.flush()
            raise
        finally:
            self._running = False

        config.hosts_optimizer_status = result
        await db.flush()
        return result

    # ---------- 调度 ----------

    def start_scheduler(self) -> None:
        """启动后台定时循环（幂等）。"""
        if self._scheduler_task is not None and not self._scheduler_task.done():
            return
        self._scheduler_stop = asyncio.Event()
        self._scheduler_wake = asyncio.Event()
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())
        logger.info("Hosts 优选调度器已启动")

    async def stop_scheduler(self) -> None:
        """停止后台定时循环。"""
        if self._scheduler_stop is not None:
            self._scheduler_stop.set()
        task = self._scheduler_task
        self._scheduler_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def wake(self) -> None:
        """配置变更后唤醒调度循环重新判断。"""
        if self._scheduler_wake is not None:
            self._scheduler_wake.set()

    async def _scheduler_loop(self) -> None:
        try:
            # 给启动阶段的其他初始化留出时间，也避免开发热重载时立刻跑满网络
            await asyncio.sleep(15)
            while True:
                try:
                    await self._scheduler_tick()
                except Exception as exc:
                    logger.warning("Hosts 优选调度检查失败：%s", exc)
                if self._scheduler_stop is not None and self._scheduler_stop.is_set():
                    return
                try:
                    await asyncio.wait_for(self._scheduler_wake.wait(), timeout=30)
                except asyncio.TimeoutError:
                    pass
                self._scheduler_wake.clear()
        except asyncio.CancelledError:
            raise

    async def _scheduler_tick(self) -> None:
        async with async_session() as db:
            config = await system_config_service.get_or_create(db)
            if not config.hosts_optimizer_enabled:
                return
            if self._running:
                return
            next_at = self.next_run_at(config)
            due = _parse_iso(next_at)
            if due is None or datetime.now(timezone.utc) < due:
                return
            try:
                await self.run_and_save(db, config, mode="auto")
                await db.commit()
            except Exception as exc:
                await db.commit()
                logger.warning("Hosts 优选自动运行失败：%s", exc)


hosts_optimizer_service = HostsOptimizerService()
