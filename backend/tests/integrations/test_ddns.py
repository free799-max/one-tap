"""DDNS 配置映射与任务构造测试。"""

from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.exceptions import APIException
from app.core.security import hash_password
from app.database import Base
from app.models.integration import IntegrationConfig
from app.services.integrations.lucky import build_ddns_task
from app.services.integrations.service import (
    _ddns_family_config,
    _ddns_family_out,
    _ddns_task_domains,
    _lucky_task_snapshot,
    integration_service,
)


def test_legacy_ddns_config_maps_to_family():
    legacy = {
        "enabled": True,
        "provider": "alidns",
        "access_id": "key-id",
        "secret": "key-secret",
        "domains": ["home.example.com"],
        "record_type": "IPv6",
        "interval": 120,
    }
    ipv6 = _ddns_family_config(legacy, "IPv6")
    assert ipv6["enabled"] is True
    assert ipv6["provider"] == "alidns"
    assert ipv6["domains"] == ["home.example.com"]
    assert ipv6["interval"] == 120

    ipv4 = _ddns_family_config(legacy, "IPv4")
    assert ipv4["enabled"] is False
    assert ipv4["domains"] == []


def test_ddns_family_out_hides_secret():
    out = _ddns_family_out(
        _ddns_family_config(
            {
                "enabled": True,
                "provider": "cloudflare",
                "secret": "token",
                "domains": ["home.example.com"],
                "record_type": "IPv4",
            },
            "IPv4",
        )
    )
    assert out.secret_set is True
    assert out.enabled is True
    assert out.get_type == "url"
    assert out.url.startswith("https://")


def test_build_ddns_task_ipv4_net_interface():
    payload = build_ddns_task(
        record_type="IPv4",
        enabled=True,
        provider="alidns",
        access_id="id",
        secret="secret",
        domains=["home.example.com", "media.example.com"],
        interval=120,
        get_type="netInterface",
        net_interface="ens2",
    )
    assert payload["TaskName"] == "OneTap 动态域名 IPv4"
    assert payload["TaskType"] == "IPv4"
    assert payload["Intervals"] == 120
    assert payload["V4QueryIPEnable"] is True
    assert payload["V4QueryIPType"] == "netInterface"
    assert payload["V4NetInterface"] == "ens2"
    assert payload["V6QueryIPEnable"] is False
    records = payload["Records"]
    assert [item["SyncRecordData"]["fullDomainName"] for item in records] == [
        "home.example.com",
        "media.example.com",
    ]
    assert all(item["SyncRecordData"]["type"] == "A" for item in records)
    assert records[0]["SyncRecordData"]["ipv4Address"] == "{ipv4Addr}"


def test_build_ddns_task_ipv6_url():
    payload = build_ddns_task(
        record_type="IPv6",
        enabled=True,
        provider="cloudflare",
        access_id="",
        secret="token",
        domains=["home6.example.com"],
        interval=60,
        get_type="url",
        url="https://v6.example.com/ip",
    )
    assert payload["TaskName"] == "OneTap 动态域名 IPv6"
    assert payload["V6QueryIPEnable"] is True
    assert payload["V6QueryIPType"] == "url"
    assert payload["V6QueryUrl"] == "https://v6.example.com/ip"
    record = payload["Records"][0]["SyncRecordData"]
    assert record["type"] == "AAAA"
    assert record["ipv6Address"] == "{ipv6Addr}"


@pytest.mark.asyncio
async def test_reveal_ddns_secret_requires_login_password():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        async with session_factory() as db:
            db.add(
                IntegrationConfig(
                    kind="network",
                    enabled=True,
                    config={"ddns": {"ipv4": {"secret": "ddns-secret"}}},
                )
            )
            await db.commit()
            user = SimpleNamespace(hashed_password=hash_password("pass123456"))

            with pytest.raises(APIException) as wrong:
                await integration_service.reveal_network_password(
                    db, "ddns", "wrong-pass", user
                )
            assert wrong.value.status_code == 401

            secret = await integration_service.reveal_network_password(
                db, "ddns", "pass123456", user
            )
            assert secret == "ddns-secret"
    finally:
        await engine.dispose()


def test_lucky_task_snapshot_success():
    task = {
        "TaskType": "IPv4",
        "Ipv4Addr": "1.2.3.4",
        "LastSyncTime": "2026-11-01 22:26:06",
        "NextSyncTime": "2026-11-01 22:31:06",
        "Records": [{"UpdateStatus": "SYNC_SUCCESS", "Message": ""}],
    }
    assert _lucky_task_snapshot(task) == {
        "status": "ok",
        "message": "",
        "address": "1.2.3.4",
        "last_sync": "2026-11-01 22:26:06",
        "next_sync": "2026-11-01 22:31:06",
    }

    task["Records"][0]["UpdateStatus"] = "SYNC_LOC_RECORD_NOCHANGE"
    assert _lucky_task_snapshot(task)["status"] == "unchanged"


def test_lucky_task_snapshot_error():
    task = {
        "TaskType": "IPv6",
        "Ipv6Addr": "",
        "Ipv6AddrErrMsg": "获取 IPv6 地址失败",
        "Records": [{"UpdateStatus": "SYNC_FAIL", "Message": ""}],
    }
    state = _lucky_task_snapshot(task)
    assert state["status"] == "error"
    assert state["message"] == "获取 IPv6 地址失败"

    task = {
        "TaskType": "IPv4",
        "Ipv4Addr": "1.2.3.4",
        "Records": [{"UpdateStatus": "SYNC_FAIL", "Message": "记录更新失败"}],
    }
    state = _lucky_task_snapshot(task)
    assert state["status"] == "error"
    assert state["message"] == "记录更新失败"


def test_ddns_task_domains_from_records():
    task = {
        "Records": [
            {"SubDomain": "home", "DomainName": "example.com"},
            {"SubDomain": "@", "DomainName": "example.net"},
            {"SubDomain": "", "DomainName": "example.org"},
        ]
    }
    assert _ddns_task_domains(task) == [
        "home.example.com",
        "example.net",
        "example.org",
    ]
