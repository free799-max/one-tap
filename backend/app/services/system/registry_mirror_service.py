"""Docker 镜像加速源服务。

地址由配套管理端统一维护，本服务负责从管理端拉取启用中的
Docker Registry 镜像源，并检测地址的连通性与响应延迟。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from app.core.exceptions import APIException
from app.services.manage_client import (
    ManageRequestError,
    manage_base_urls,
    with_manage_fallback,
)
from app.services.system.config_service import normalize_docker_registry_mirrors

MANAGE_REQUEST_TIMEOUT = 10.0
"""请求管理端接口的超时时间（秒）。"""

MIRROR_CHECK_TIMEOUT = 8.0
"""检测单个镜像地址的超时时间（秒）。"""


class RegistryMirrorService:
    """镜像加速源拉取与检测服务。"""

    async def fetch_remote(self) -> dict[str, Any]:
        """从管理端获取启用中的 Docker 镜像加速源。

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

        items = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            raise APIException("管理端镜像加速响应格式无法识别", 502)

        mirrors: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("kind") != "docker_registry":
                continue
            if item.get("enabled") is False:
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


# 全局服务单例
registry_mirror_service = RegistryMirrorService()
