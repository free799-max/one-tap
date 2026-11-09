"""Docker 镜像加速源服务。

地址由配套管理端统一维护：免费线路走公开列表；授权设备再用本机刷新
令牌换取专属加速域名。拉取镜像时按源站选择加速域名下载，完成后改回
原始 tag，不需要修改宿主机的 daemon.json。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from app.core.exceptions import APIException
from app.database import async_session
from app.services.licensing import licensing_service
from app.services.licensing.device import get_device_fingerprint
from app.services.manage_client import (
    ManageRequestError,
    manage_base_urls,
    with_manage_fallback,
)
from app.services.system.config_service import normalize_docker_registry_mirrors

logger = logging.getLogger(__name__)

MANAGE_REQUEST_TIMEOUT = 10.0
"""请求管理端接口的超时时间（秒）。"""

MIRROR_CHECK_TIMEOUT = 8.0
"""检测单个镜像地址的超时时间（秒）。"""

LEASED_CACHE_SECONDS = 60.0
"""授权专属域名的进程内缓存时间；授权状态变化时会按刷新令牌失效。"""

_leased_cache: tuple[float, str, dict[str, Any]] | None = None


def _leased_items(payload: Any) -> list[dict[str, Any]] | None:
    data = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(data, dict):
        return None
    items = data.get("mirrors")
    if not isinstance(items, list):
        return None
    return [item for item in items if isinstance(item, dict)]


class RegistryMirrorService:
    """镜像加速源拉取与检测服务。"""

    async def fetch_remote(self) -> dict[str, Any]:
        """从管理端获取启用中的免费 Docker 镜像加速源。

        ``MANAGE_BASE_URL`` 未配置时返回 ``configured=False``，由前端提示；
        管理端请求失败或响应格式异常时抛出 502。
        """
        if not manage_base_urls():
            return {"configured": False, "mirrors": []}

        try:
            async with httpx.AsyncClient(
                timeout=MANAGE_REQUEST_TIMEOUT, follow_redirects=True
            ) as client:
                resp, _ = await with_manage_fallback(
                    lambda url: client.get(f"{url}/api/public/mirrors")
                )
                resp.raise_for_status()
                payload = resp.json()
        except ManageRequestError as exc:
            raise APIException(f"从管理端获取镜像加速地址失败：{exc}", 502) from exc
        except httpx.HTTPError as exc:
            raise APIException(f"从管理端获取镜像加速地址失败：{exc}", 502) from exc
        except ValueError as exc:
            raise APIException("管理端镜像加速响应不是合法 JSON", 502) from exc

        data = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(data, list):
            raise APIException("管理端镜像加速响应格式无法识别", 502)
        items = [item for item in data if isinstance(item, dict)]

        mirrors: list[dict[str, Any]] = []
        for item in items:
            if item.get("kind") != "docker_registry":
                continue
            if item.get("enabled") is False:
                continue
            if item.get("tier") == "subscription":
                # 付费线路只能通过授权接口按设备获取
                continue
            mirror_url = str(item.get("url") or "").strip()
            if not mirror_url:
                continue
            priority = item.get("priority")
            mirrors.append(
                {
                    "name": item.get("name") or mirror_url,
                    "url": mirror_url,
                    "priority": priority if isinstance(priority, int) else 100,
                    "tier": "free",
                    "source": str(item.get("source") or "docker.io"),
                    "auth_type": str(item.get("auth_type") or "none"),
                    "region": item.get("region"),
                    "description": item.get("description"),
                    "doc_url": item.get("doc_url"),
                    "last_check_ok": item.get("last_check_ok"),
                    "last_latency_ms": item.get("last_latency_ms"),
                    "last_checked_at": item.get("last_checked_at"),
                }
            )

        mirrors.sort(key=lambda m: (m["priority"], m["url"]))
        return {"configured": True, "mirrors": mirrors}

    async def fetch_leased(
        self, db, *, refresh: bool = False
    ) -> dict[str, Any]:
        """用本机授权换取专属加速域名；未激活时返回 ``licensed=False``。"""
        state = await licensing_service.get_state(db)
        token = state.refresh_token or ""
        if not token:
            return {"licensed": False, "mirrors": []}
        if not manage_base_urls():
            return {"licensed": False, "mirrors": []}

        global _leased_cache
        now = time.monotonic()
        if (
            not refresh
            and _leased_cache is not None
            and _leased_cache[1] == token
            and now - _leased_cache[0] < LEASED_CACHE_SECONDS
        ):
            return _leased_cache[2]

        device = await get_device_fingerprint(state.installation_id)
        body = {
            "installation_id": state.installation_id,
            "refresh_token": token,
            "hardware_fingerprint": device["hardware_fingerprint"],
        }
        try:
            async with httpx.AsyncClient(
                timeout=MANAGE_REQUEST_TIMEOUT, follow_redirects=True
            ) as client:
                resp, _ = await with_manage_fallback(
                    lambda url: client.post(
                        f"{url}/api/public/mirrors/leased", json=body
                    )
                )
        except ManageRequestError as exc:
            logger.info("获取授权镜像加速地址失败：%s", exc)
            return {"licensed": False, "mirrors": []}
        except httpx.HTTPError as exc:
            logger.info("获取授权镜像加速地址失败：%s", exc)
            return {"licensed": False, "mirrors": []}

        if resp.status_code in {401, 403}:
            logger.info("本机授权不可用于专属加速域名：HTTP %s", resp.status_code)
            result = {"licensed": False, "mirrors": []}
            _leased_cache = (now, token, result)
            return result
        if resp.is_error:
            logger.info("获取授权镜像加速地址失败：HTTP %s", resp.status_code)
            return {"licensed": False, "mirrors": []}

        try:
            items = _leased_items(resp.json())
        except ValueError:
            items = None
        if items is None:
            logger.info("管理端专属域名响应格式无法识别")
            return {"licensed": False, "mirrors": []}

        mirrors = []
        for item in items:
            url = str(item.get("url") or "").strip()
            source = str(item.get("source") or "").strip().lower()
            if not url or not source:
                continue
            mirrors.append(
                {
                    "name": f"专属加速 · {source}",
                    "url": url,
                    "priority": 0,
                    "tier": "subscription",
                    "source": source,
                    "auth_type": "dedicated_domain",
                    "region": None,
                    "description": "授权设备专属加速域名",
                    "doc_url": None,
                    "last_check_ok": None,
                    "last_latency_ms": None,
                    "last_checked_at": None,
                    "expires_at": item.get("expires_at"),
                }
            )

        result = {"licensed": True, "mirrors": mirrors}
        _leased_cache = (now, token, result)
        return result

    async def fetch_all(self, db) -> dict[str, Any]:
        """返回免费线路 + 授权专属域名，专属域名排在前面。"""
        free = await self.fetch_remote()
        leased = await self.fetch_leased(db)
        return {
            "configured": free["configured"],
            "licensed": leased["licensed"],
            "mirrors": leased["mirrors"] + free["mirrors"],
        }

    async def effectiveness(self, db) -> dict[str, Any]:
        """实际探测授权专属镜像源，不可用时不报告加速生效。"""
        leased = await self.fetch_leased(db)
        mirrors = leased["mirrors"] if leased["licensed"] else []
        if not mirrors:
            return {
                "effective": False,
                "available_count": 0,
                "total_count": 0,
                "message": "未获取到可用的授权专属镜像源",
            }

        result = await self.test([mirror["url"] for mirror in mirrors])
        available = result["ok_count"]
        total = result["total"]
        return {
            "effective": available > 0,
            "available_count": available,
            "total_count": total,
            "message": (
                f"{available}/{total} 个专属镜像源连通，镜像拉取已接入专属加速"
                if available
                else "专属镜像源均不可达，镜像加速未生效"
            ),
        }

    async def acceleration_map(self, db) -> dict[str, list[str]]:
        """构建源站到加速地址的映射；任何失败都退化为空映射。"""
        try:
            data = await self.fetch_all(db)
        except Exception:
            logger.info("获取镜像加速地址失败，将直连镜像仓库", exc_info=True)
            return {}

        mapping: dict[str, list[str]] = {}
        for item in data["mirrors"]:
            source = str(item.get("source") or "docker.io").strip().lower()
            url = str(item.get("url") or "").strip()
            if not source or not url:
                continue
            urls = mapping.setdefault(source, [])
            if url not in urls:
                urls.append(url)
        return mapping

    async def test(self, urls: list[str]) -> dict[str, Any]:
        """并发检测镜像地址，返回逐地址结果。

        只关心能否建立连接并拿到 5xx 以下的响应：Docker Registry 对
        未认证的根路径请求返回 401 属于正常情况。
        """
        normalized = normalize_docker_registry_mirrors(urls)
        if not normalized:
            return {"ok_count": 0, "total": 0, "results": []}

        async with httpx.AsyncClient(
            timeout=MIRROR_CHECK_TIMEOUT,
            follow_redirects=True,
            trust_env=False,
        ) as client:
            results = await asyncio.gather(
                *(self._probe(client, url) for url in normalized)
            )

        return {
            "ok_count": sum(1 for item in results if item["ok"]),
            "total": len(results),
            "results": list(results),
        }

    async def _probe(self, client: httpx.AsyncClient, url: str) -> dict[str, Any]:
        """请求镜像根地址并记录可用性、状态码与耗时。"""
        started = time.perf_counter()
        try:
            resp = await client.get(url, headers={"User-Agent": "onetap"})
            latency_ms = int((time.perf_counter() - started) * 1000)
            ok = resp.status_code < 500
            return {
                "url": url,
                "ok": ok,
                "status_code": resp.status_code,
                "latency_ms": latency_ms,
                "error": None if ok else f"HTTP {resp.status_code}",
            }
        except Exception as exc:  # noqa: BLE001 — 任何网络异常都算该地址不可用
            latency_ms = int((time.perf_counter() - started) * 1000)
            message = str(exc) or exc.__class__.__name__
            return {
                "url": url,
                "ok": False,
                "status_code": None,
                "latency_ms": latency_ms,
                "error": message[:200],
            }


async def get_acceleration_map() -> dict[str, list[str]]:
    """给无数据库会话的后台任务（compose 预拉取）使用。"""
    async with async_session() as db:
        return await registry_mirror_service.acceleration_map(db)


# 全局服务单例
registry_mirror_service = RegistryMirrorService()
