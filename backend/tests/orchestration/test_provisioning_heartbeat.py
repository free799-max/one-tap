"""自动配置等待进度的日志来源标注测试。"""

import asyncio

import pytest

from app.services.orchestration.provisioning import ProvisionMixin


class _Service(ProvisionMixin):
    """只用到心跳等待逻辑，缩短心跳间隔便于测试。"""

    PROVISION_HEARTBEAT_SECONDS = 0.01


@pytest.mark.asyncio
async def test_await_provision_marks_container_log_as_app_log():
    """等待服务就绪时，容器日志尾行要标注「应用日志」，避免误认为是平台操作。"""
    messages: list[str] = []

    async def slow_provision():
        await asyncio.sleep(0.05)
        return "ok"

    async def logs_reader():
        return "2026-10-29 21:17:18 | INFO | 已清理旧漫画记录 0 条"

    result = await _Service()._await_provision(
        slow_provision(),
        app_name="comic-auto",
        logs_reader=logs_reader,
        progress=messages.append,
    )

    assert result == "ok"
    assert messages
    assert messages[0].startswith("等待 comic-auto 服务就绪（已等待")
    assert "应用日志：2026-10-29" in messages[0]


@pytest.mark.asyncio
async def test_await_provision_keeps_status_reason_without_log_prefix():
    """客户端给出的等待原因（HTTP 状态码等）不加「应用日志」前缀。"""
    messages: list[str] = []

    async def slow_provision():
        await asyncio.sleep(0.05)
        return "ok"

    result = await _Service()._await_provision(
        slow_provision(),
        app_name="comic-auto",
        logs_reader=None,
        status_getter=lambda: "登录接口异常: HTTP 500",
        progress=messages.append,
    )

    assert result == "ok"
    assert messages
    assert "：登录接口异常: HTTP 500" in messages[0]
    assert "应用日志" not in messages[0]
