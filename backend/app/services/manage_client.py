"""配套管理端（onetap-manage）访问辅助。

``MANAGE_BASE_URL`` 支持逗号分隔的多个地址，例如
``https://m.one-tap.vip,https://m.one-tab.vip``。请求按配置顺序尝试，
网络错误或 5xx 时自动换下一个地址；最近一次成功的地址会被记住并优先
使用，避免每次请求都先打到不可用的节点。
"""

import logging
from collections.abc import Awaitable, Callable

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_preferred_base_url: str | None = None
"""最近一次请求成功的管理端地址，后续请求优先尝试。"""


def manage_base_urls() -> list[str]:
    """解析配置的管理端地址列表，未配置时为空列表。"""
    urls: list[str] = []
    for raw in settings.MANAGE_BASE_URL.split(","):
        url = raw.strip().rstrip("/")
        if url and url not in urls:
            urls.append(url)
    return urls


class ManageRequestError(RuntimeError):
    """所有管理端地址均请求失败。"""


async def with_manage_fallback(
    fetch: Callable[[str], Awaitable[httpx.Response]],
) -> tuple[httpx.Response, str]:
    """依次尝试各管理端地址，返回 ``(响应, 实际使用的地址)``。

    ``fetch`` 接收不含结尾斜杠的地址并发起请求。4xx 属于业务错误，
    不再换地址，交由调用方处理；全部地址失败时抛出 ``ManageRequestError``。
    """
    urls = manage_base_urls()
    if not urls:
        raise ManageRequestError("未配置管理端地址")

    global _preferred_base_url
    if _preferred_base_url in urls:
        urls = [_preferred_base_url] + [u for u in urls if u != _preferred_base_url]

    errors: list[str] = []
    last_exc: Exception | None = None
    for base_url in urls:
        try:
            response = await fetch(base_url)
        except (httpx.HTTPError, OSError) as exc:
            last_exc = exc
            errors.append(f"{base_url}: {exc}")
            logger.warning("管理端 %s 请求失败：%s，尝试下一个地址", base_url, exc)
            continue
        if getattr(response, "status_code", 200) >= 500:
            errors.append(f"{base_url}: HTTP {response.status_code}")
            logger.warning(
                "管理端 %s 返回 HTTP %s，尝试下一个地址",
                base_url,
                response.status_code,
            )
            continue
        if base_url != urls[0]:
            logger.info("管理端切换到 %s", base_url)
        _preferred_base_url = base_url
        return response, base_url

    raise ManageRequestError(
        "管理端全部地址请求失败：" + "; ".join(errors)
    ) from last_exc
