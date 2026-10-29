"""授权 API、证书验证、刷新与调度门禁测试。"""

import asyncio
import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.config import settings
from app.core.exceptions import APIException
from app.database import get_db
from app.models.license import LicenseState
from app.services.licensing import licensing_service
from app.services.licensing.service import (
    LicenseCertificateError,
    validate_certificate,
)
from app.services.orchestration.music_auto import music_auto_service
from app.services.orchestration.reader_auto import reader_auto_service


PRIVATE_KEY = Ed25519PrivateKey.generate()
PRIVATE_PEM = PRIVATE_KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode()
PUBLIC_PEM = PRIVATE_KEY.public_key().public_bytes(
    serialization.Encoding.PEM,
    serialization.PublicFormat.SubjectPublicKeyInfo,
).decode()
INSTALLATION_ID = "0df2f287-026b-47b2-a4f5-1b8efacdb734"
DEVICE = {
    "hardware_fingerprint": hashlib.sha256(b"docker:test-engine").hexdigest(),
    "fingerprint_source": "docker",
    "device_info": {
        "cpu_model": None,
        "cpu_cores": 4,
        "memory_total_bytes": 8192,
    },
}


@pytest.fixture(autouse=True)
def license_settings(monkeypatch):
    monkeypatch.setattr(settings, "LICENSE_PUBLIC_KEY", PUBLIC_PEM)
    monkeypatch.setattr(settings, "LICENSE_ISSUER", "onetap-manage")
    monkeypatch.setattr(settings, "MANAGE_BASE_URL", "https://manage.example")

    async def device_fingerprint(installation_id):
        return DEVICE

    monkeypatch.setattr(
        "app.services.licensing.service.get_device_fingerprint", device_fingerprint
    )


def certificate(
    installation_id: str = INSTALLATION_ID,
    *,
    expires_in: int = 3600,
    private_pem: str = PRIVATE_PEM,
    **overrides,
) -> str:
    def encode(value: dict) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    now = datetime.now(timezone.utc)
    claims = {
        "iss": "onetap-manage",
        "aud": "onetap",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in)).timestamp()),
        "installation_id_hash": hashlib.sha256(installation_id.encode()).hexdigest(),
        "perpetual": True,
    }
    claims.update(overrides)
    header = encode({"alg": "EdDSA", "typ": "JWT", "kid": "key-1"})
    payload = encode(claims)
    key = serialization.load_pem_private_key(private_pem.encode(), password=None)
    signature = base64.urlsafe_b64encode(
        key.sign(f"{header}.{payload}".encode())
    ).rstrip(b"=").decode()
    return f"{header}.{payload}.{signature}"


async def register(client, username: str) -> str:
    await client.post(
        "/api/auth/register", json={"username": username, "password": "password123"}
    )
    response = await client.post(
        "/api/auth/login", json={"username": username, "password": "password123"}
    )
    return response.json()["data"]["access_token"]


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _test_db(client):
    return client._transport.app.dependency_overrides[get_db]()


async def get_state(client) -> LicenseState:
    async for db in _test_db(client):
        return await licensing_service.get_state(db)


async def test_status_is_redacted_and_available_to_regular_user(client):
    admin = await register(client, "admin")
    user = await register(client, "reader")
    async for db in _test_db(client):
        state = await licensing_service.get_state(db)
        state.device_name = "living-room"
        state.certificate = "secret-certificate"
        state.refresh_token = "secret-refresh"
        state.license_key = "secret-license-key"
        state.license_id = "license-1"
        state.activation_id = "activation-1"
        state.public_key = "secret-public-key"
        state.key_id = "kid"
        await db.commit()

    response = await client.get("/api/license/status", headers=auth(user))
    assert response.status_code == 200
    data = response.json()["data"]
    assert set(data) == {
        "activated",
        "valid",
        "status",
        "perpetual",
        "temporary_months",
        "license_id",
        "activation_id",
        "license_key",
        "device_name",
        "expires_at",
        "last_refreshed_at",
        "last_error",
    }
    assert data["perpetual"] is False
    assert data["license_key"] is None
    assert "secret" not in response.text
    assert data["device_name"] == "living-room"

    forbidden = await client.post("/api/license/refresh", headers=auth(user))
    assert forbidden.status_code == 403
    assert admin


async def test_license_key_visible_to_admin_only(client):
    admin = await register(client, "admin")
    user = await register(client, "reader")
    async for db in _test_db(client):
        state = await licensing_service.get_state(db)
        state.license_key = "ND-1234-5678-ABCD"
        await db.commit()

    admin_resp = await client.get("/api/license/status", headers=auth(admin))
    assert admin_resp.status_code == 200
    assert admin_resp.json()["data"]["license_key"] == "ND-1234-5678-ABCD"

    user_resp = await client.get("/api/license/status", headers=auth(user))
    assert user_resp.status_code == 200
    assert user_resp.json()["data"]["license_key"] is None
    assert "ND-1234-5678-ABCD" not in user_resp.text


async def test_unlicensed_orchestration_write_returns_403_but_get_is_available(client):
    token = await register(client, "admin")
    response = await client.post(
        "/api/orchestrations/example/deploy",
        headers=auth(token),
        json={"instance_name": "example", "selected_apps": ["app"]},
    )
    assert response.status_code == 403
    assert response.json()["message"] == "需要有效授权"

    get_response = await client.get("/api/orchestrations", headers=auth(token))
    assert get_response.status_code == 200


@pytest.mark.parametrize(
    ("token", "reason"),
    [
        (certificate() + "tampered", "invalid_signature"),
        (certificate(expires_in=-1), "expired"),
        (certificate(installation_id="another-device"), "device_mismatch"),
        (certificate(iss="other"), "invalid_issuer"),
        (certificate(aud="other"), "invalid_audience"),
        (certificate(perpetual=False), "invalid_license_type"),
    ],
)
def test_certificate_validation_rejects_invalid_claims(token, reason):
    with pytest.raises(LicenseCertificateError) as caught:
        validate_certificate(token, INSTALLATION_ID, PUBLIC_PEM, "key-1")
    assert caught.value.reason == reason


def test_certificate_validation_accepts_valid_ed25519_jwt():
    payload = validate_certificate(certificate(), INSTALLATION_ID, PUBLIC_PEM, "key-1")
    assert payload["iss"] == "onetap-manage"
    assert payload["perpetual"] is True


def test_certificate_validation_accepts_temporary_license():
    payload = validate_certificate(
        certificate(perpetual=False, temporary=True, temporary_months=12),
        INSTALLATION_ID,
        PUBLIC_PEM,
        "key-1",
    )
    assert payload["temporary"] is True
    assert payload["temporary_months"] == 12


def test_certificate_hardware_fingerprint_is_enforced_when_present():
    token = certificate(hardware_fingerprint=DEVICE["hardware_fingerprint"])
    payload = validate_certificate(
        token,
        INSTALLATION_ID,
        PUBLIC_PEM,
        "key-1",
        hardware_fingerprint=DEVICE["hardware_fingerprint"],
    )
    assert payload["hardware_fingerprint"] == DEVICE["hardware_fingerprint"]

    for current_fingerprint in (None, hashlib.sha256(b"installation:fallback").hexdigest()):
        with pytest.raises(LicenseCertificateError) as caught:
            validate_certificate(
                token,
                INSTALLATION_ID,
                PUBLIC_PEM,
                "key-1",
                hardware_fingerprint=current_fingerprint,
            )
        assert caught.value.reason == "device_mismatch"


async def test_docker_certificate_is_invalid_during_installation_fallback(client, monkeypatch):
    token = await register(client, "admin")
    async for db in _test_db(client):
        state = await licensing_service.get_state(db)
        state.certificate = certificate(
            state.installation_id,
            hardware_fingerprint=DEVICE["hardware_fingerprint"],
        )
        await db.commit()

        async def installation_fingerprint(installation_id):
            return {
                "hardware_fingerprint": hashlib.sha256(
                    f"installation:{installation_id}".encode()
                ).hexdigest(),
                "fingerprint_source": "installation",
                "device_info": {
                    "cpu_model": None,
                    "cpu_cores": None,
                    "memory_total_bytes": None,
                },
            }

        monkeypatch.setattr(
            "app.services.licensing.service.get_device_fingerprint",
            installation_fingerprint,
        )
        status = await licensing_service.get_status(db)
        assert status["activated"] is True
        assert status["valid"] is False
        assert status["status"] == "invalid"
        break


async def test_status_reports_temporary_license_months(client, monkeypatch):
    """临时授权会带上剩余月数，前端据此展示授权时长。"""
    token = await register(client, "admin")
    await client.get("/api/license/status", headers=auth(token))
    state = await get_state(client)
    post = AsyncMock(
        return_value={
            "certificate": certificate(
                state.installation_id,
                license_id=101,
                activation_id=202,
                perpetual=False,
                temporary=True,
                temporary_months=2,
            ),
            "refresh_token": "refresh-token",
            "license_id": 101,
            "activation_id": 202,
            "expires_at": datetime.now(timezone.utc) + timedelta(days=60),
        }
    )
    monkeypatch.setattr(licensing_service, "_post", post)
    activated = await client.post(
        "/api/license/activate",
        headers=auth(token),
        json={"license_key": "license-key"},
    )
    assert activated.status_code == 200
    data = activated.json()["data"]
    assert data["valid"] is True
    assert data["perpetual"] is False
    assert data["temporary_months"] == 2


async def test_activate_refresh_failure_and_deactivate(client, monkeypatch):
    token = await register(client, "admin")
    await client.get("/api/license/status", headers=auth(token))
    state = await get_state(client)
    installation_id = state.installation_id
    activation_certificate = certificate(
        installation_id,
        license_id="license-one",
        activation_id="activation-one",
    )
    post = AsyncMock(
        return_value={
            "certificate": activation_certificate,
            "refresh_token": "refresh-one",
            "license_id": "license-one",
            "activation_id": "activation-one",
            "expires_at": datetime.now(timezone.utc) + timedelta(days=30),
        }
    )
    monkeypatch.setattr(licensing_service, "_post", post)

    activated = await client.post(
        "/api/license/activate",
        headers=auth(token),
        json={"license_key": "license-key", "device_name": "nas"},
    )
    assert activated.status_code == 200
    assert activated.json()["data"]["valid"] is True
    assert "certificate" not in activated.json()["data"]
    assert "refresh_token" not in activated.json()["data"]
    key_status = await client.get("/api/license/status", headers=auth(token))
    assert key_status.json()["data"]["license_key"] == "license-key"
    assert key_status.json()["data"]["perpetual"] is True
    assert key_status.json()["data"]["temporary_months"] is None
    post.assert_awaited_with(
        "activate",
        {
            "license_key": "license-key",
            "installation_id": installation_id,
            "device_name": "nas",
            **DEVICE,
        },
    )

    refreshed_certificate = certificate(
        installation_id,
        expires_in=7200,
        license_id="license-one",
        activation_id="activation-one",
    )
    post.return_value = {
        "certificate": refreshed_certificate,
        "refresh_token": "refresh-two",
        "license_id": "license-one",
        "activation_id": "activation-one",
        "expires_at": datetime.now(timezone.utc) + timedelta(days=30),
    }
    refreshed = await client.post("/api/license/refresh", headers=auth(token))
    assert refreshed.status_code == 200
    post.assert_awaited_with(
        "refresh",
        {
            "refresh_token": "refresh-one",
            "installation_id": installation_id,
            **DEVICE,
        },
    )
    state = await get_state(client)
    assert state.certificate == refreshed_certificate
    assert state.refresh_token == "refresh-two"

    post.side_effect = APIException("无法连接授权管理端", 502)
    failed = await client.post("/api/license/refresh", headers=auth(token))
    assert failed.status_code == 502
    status = await client.get("/api/license/status", headers=auth(token))
    assert status.json()["data"]["valid"] is True
    assert status.json()["data"]["last_error"] == "无法连接授权管理端"
    state = await get_state(client)
    assert state.certificate == refreshed_certificate

    post.side_effect = None
    post.return_value = {}
    deactivated = await client.post("/api/license/deactivate", headers=auth(token))
    assert deactivated.status_code == 200
    assert deactivated.json()["data"]["activated"] is False
    post.assert_awaited_with(
        "deactivate",
        {
            "refresh_token": "refresh-two",
            "installation_id": installation_id,
            **DEVICE,
        },
    )
    state = await get_state(client)
    assert state.installation_id == installation_id
    assert state.device_name == "nas"
    assert state.certificate is None
    assert state.refresh_token is None
    assert state.license_key is None


@pytest.mark.parametrize("status_code", [401, 403])
async def test_refresh_auth_failure_reactivates_with_stored_key(
    client, monkeypatch, status_code
):
    """刷新令牌失效时用本机授权码自动重新激活，不再清空本地授权。"""
    token = await register(client, "admin")
    await client.get("/api/license/status", headers=auth(token))
    state = await get_state(client)
    installation_id = state.installation_id
    signed_certificate = certificate(
        installation_id,
        license_id=101,
        activation_id=202,
    )
    activation_data = {
        "certificate": signed_certificate,
        "refresh_token": "refresh-token",
        "license_id": 101,
        "activation_id": 202,
        "expires_at": datetime.now(timezone.utc) + timedelta(days=30),
    }
    post = AsyncMock(return_value=activation_data)
    monkeypatch.setattr(licensing_service, "_post", post)
    activated = await client.post(
        "/api/license/activate",
        headers=auth(token),
        json={"license_key": "license-key", "device_name": "nas"},
    )
    assert activated.status_code == 200

    post.side_effect = [
        APIException("刷新令牌或设备标识无效", status_code),
        activation_data,
    ]
    refreshed = await client.post("/api/license/refresh", headers=auth(token))
    assert refreshed.status_code == 200
    assert refreshed.json()["data"]["valid"] is True

    actions = [call.args[0] for call in post.call_args_list]
    assert actions[-2:] == ["refresh", "activate"]
    activate_body = post.call_args_list[-1].args[1]
    assert activate_body["license_key"] == "license-key"
    assert activate_body["installation_id"] == installation_id

    state = await get_state(client)
    assert state.certificate is not None
    assert state.refresh_token == "refresh-token"
    assert state.license_key == "license-key"
    assert state.device_name == "nas"
    assert state.last_error is None


@pytest.mark.parametrize("status_code", [401, 403])
async def test_refresh_auth_failure_keeps_local_authorization(
    client, monkeypatch, status_code
):
    """重新激活也失败时保留证书与授权码，离线有效期内仍可用。"""
    token = await register(client, "admin")
    await client.get("/api/license/status", headers=auth(token))
    state = await get_state(client)
    signed_certificate = certificate(
        installation_id=state.installation_id,
        license_id=101,
        activation_id=202,
    )
    post = AsyncMock(
        return_value={
            "certificate": signed_certificate,
            "refresh_token": "refresh-token",
            "license_id": 101,
            "activation_id": 202,
            "expires_at": datetime.now(timezone.utc) + timedelta(days=30),
        }
    )
    monkeypatch.setattr(licensing_service, "_post", post)
    await client.post(
        "/api/license/activate",
        headers=auth(token),
        json={"license_key": "license-key", "device_name": "nas"},
    )

    post.side_effect = APIException("刷新令牌或设备标识无效", status_code)
    rejected = await client.post("/api/license/refresh", headers=auth(token))
    assert rejected.status_code == status_code

    status = (await client.get("/api/license/status", headers=auth(token))).json()["data"]
    assert status["activated"] is True
    assert status["last_error"] == "刷新令牌或设备标识无效"

    state = await get_state(client)
    assert state.certificate is not None
    assert state.refresh_token == "refresh-token"
    assert state.license_key == "license-key"
    assert state.license_id == "101"
    assert state.activation_id == "202"


async def test_deactivate_auth_failure_also_clears_local_authorization(
    client, monkeypatch
):
    token = await register(client, "admin")
    await client.get("/api/license/status", headers=auth(token))
    state = await get_state(client)
    signed_certificate = certificate(
        state.installation_id,
        license_id=101,
        activation_id=202,
    )
    post = AsyncMock(
        return_value={
            "certificate": signed_certificate,
            "refresh_token": "refresh-token",
            "license_id": 101,
            "activation_id": 202,
            "expires_at": datetime.now(timezone.utc) + timedelta(days=30),
        }
    )
    monkeypatch.setattr(licensing_service, "_post", post)
    await client.post(
        "/api/license/activate",
        headers=auth(token),
        json={"license_key": "license-key"},
    )
    post.side_effect = APIException("刷新令牌无效", 403)
    deactivated = await client.post("/api/license/deactivate", headers=auth(token))
    assert deactivated.status_code == 200
    assert deactivated.json()["data"]["valid"] is False
    assert deactivated.json()["data"]["last_error"] == "刷新令牌无效"


async def test_activate_accepts_manage_wrapper_and_integer_ids(client, monkeypatch):
    monkeypatch.setattr(settings, "LICENSE_PUBLIC_KEY", "")
    async for db in _test_db(client):
        state = await licensing_service.get_state(db)
        await db.commit()
        installation_id = state.installation_id
        signed_certificate = certificate(
            installation_id,
            license_id=101,
            activation_id=202,
        )

        class ManageClient:
            def __init__(self):
                self.requests = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def get(self, url):
                self.requests.append(("GET", url, None))
                return httpx.Response(
                    200,
                    json={
                        "success": True,
                        "data": {
                            "algorithm": "EdDSA",
                            "key_id": "key-1",
                            "public_key": PUBLIC_PEM,
                        },
                        "message": "ok",
                    },
                )

            async def post(self, url, json):
                self.requests.append(("POST", url, json))
                return httpx.Response(
                    200,
                    json={
                        "success": True,
                        "data": {
                            "certificate": signed_certificate,
                            "refresh_token": "refresh-token",
                            "license_id": 101,
                            "activation_id": 202,
                            "expires_at": "2026-11-23T00:00:00Z",
                        },
                        "message": "ok",
                    },
                )

        manage_client = ManageClient()
        monkeypatch.setattr(
            "app.services.licensing.service.httpx.AsyncClient",
            lambda timeout: manage_client,
        )
        status = await licensing_service.activate(db, "license-key", "nas")
        assert status["valid"] is True
        assert status["license_id"] == "101"
        assert status["activation_id"] == "202"
        assert state.license_id == "101"
        assert state.activation_id == "202"
        assert state.public_key == PUBLIC_PEM
        assert state.key_id == "key-1"
        assert [request[:2] for request in manage_client.requests] == [
            ("POST", "https://manage.example/api/public/licenses/activate"),
            ("GET", "https://manage.example/api/public/licenses/public-key"),
        ]
        break


def test_activation_rejects_certificate_response_id_mismatch():
    state = LicenseState(installation_id=INSTALLATION_ID)
    with pytest.raises(APIException, match="license_id 不一致"):
        licensing_service._apply_activation(
            state,
            {
                "certificate": "certificate",
                "refresh_token": "refresh",
                "license_id": 101,
                "activation_id": 202,
            },
            {"license_id": 999, "activation_id": 202},
        )
    assert state.certificate is None
    assert state.refresh_token is None


def test_certificate_uses_configured_issuer(monkeypatch):
    monkeypatch.setattr(settings, "LICENSE_ISSUER", "custom-manage")
    payload = validate_certificate(
        certificate(iss="custom-manage"), INSTALLATION_ID, PUBLIC_PEM, "key-1"
    )
    assert payload["iss"] == "custom-manage"
    with pytest.raises(LicenseCertificateError) as caught:
        validate_certificate(certificate(), INSTALLATION_ID, PUBLIC_PEM, "key-1")
    assert caught.value.reason == "invalid_issuer"


async def test_refresh_tick_and_scheduler_shutdown(monkeypatch):
    class Session:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *args):
            return None

    state = LicenseState(installation_id=INSTALLATION_ID, refresh_token="token")
    monkeypatch.setattr("app.services.licensing.service.async_session", Session)
    monkeypatch.setattr(licensing_service, "get_state", AsyncMock(return_value=state))
    refresh = AsyncMock(return_value={"valid": True})
    monkeypatch.setattr(licensing_service, "refresh", refresh)
    await licensing_service.refresh_tick()
    refresh.assert_awaited_once()

    tick = AsyncMock()
    monkeypatch.setattr(licensing_service, "refresh_tick", tick)
    licensing_service.start_scheduler()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert tick.await_count == 1
    task = licensing_service._scheduler_task
    await licensing_service.stop_scheduler()
    assert task.cancelled()
    assert licensing_service._scheduler_task is None


@pytest.mark.parametrize("service", [music_auto_service, reader_auto_service])
async def test_auto_tick_does_not_create_jobs_without_license(service, monkeypatch):
    async def deny(db):
        raise APIException("需要有效授权", 403)

    start_run = AsyncMock()
    monkeypatch.setattr(licensing_service, "require_valid", deny)
    monkeypatch.setattr(service, "start_run", start_run)
    await service.tick()
    start_run.assert_not_awaited()
