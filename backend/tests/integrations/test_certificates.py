"""自动证书相关的纯函数测试。"""

import base64
from datetime import datetime, timezone

import pytest

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.x509.oid import NameOID

from app.core.exceptions import APIException
from app.services.integrations.certificates import (
    build_self_signed,
    is_internal_domain,
)
from app.services.integrations.lucky import (
    build_acme_certificate_payload,
    build_file_certificate_payload,
    certificate_remark,
)
from app.services.integrations.service import (
    _auto_certificate_mode,
    _certificate_covers,
    _ddns_dns_credentials,
    _is_transient_acme_error,
    _lucky_certificate_out,
    _validate_acme_domains,
    integration_service,
)


def test_is_internal_domain():
    assert is_internal_domain("nas.lan")
    assert is_internal_domain("app.local")
    assert is_internal_domain("home.internal")
    assert is_internal_domain("router")
    assert is_internal_domain("192.168.1.10")
    assert is_internal_domain("fd00::10")
    assert not is_internal_domain("media.example.com")
    assert not is_internal_domain("*.example.com")


def test_build_self_signed_certificate():
    cert_pem, key_pem = build_self_signed(
        ["media.lan", "192.168.1.10", "*.dev.lan"]
    )
    certificate = x509.load_pem_x509_certificate(cert_pem.encode())
    key = serialization.load_pem_private_key(key_pem.encode(), password=None)

    sans = certificate.extensions.get_extension_for_class(
        x509.SubjectAlternativeName
    ).value
    assert [str(entry.value) for entry in sans] == [
        "media.lan",
        "192.168.1.10",
        "*.dev.lan",
    ]
    assert (
        certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
        == "media.lan"
    )
    assert certificate.not_valid_after_utc > datetime.now(timezone.utc)
    assert certificate.public_key().public_numbers() == key.public_key().public_numbers()


def test_build_file_certificate_payload():
    cert_pem, key_pem = build_self_signed(["nas.lan"])
    payload = build_file_certificate_payload(
        remark="test", cert_pem=cert_pem, key_pem=key_pem
    )
    assert payload["AddFrom"] == "file"
    assert payload["Remark"] == "test"
    assert base64.b64decode(payload["CertBase64"]).decode() == cert_pem
    assert base64.b64decode(payload["KeyBase64"]).decode() == key_pem


def test_build_acme_certificate_payload():
    payload = build_acme_certificate_payload(
        domains=["video.example.com"], remark="test"
    )
    assert payload["AddFrom"] == "acme"
    params = payload["ExtParams"]
    assert params["acmeDomains"] == ["video.example.com"]
    assert params["acmeDNSServer"] == "http"
    assert params["acmeCADirURL"].startswith("https://acme-v02.api.letsencrypt.org")


def test_certificate_covers_all_domains():
    assert _certificate_covers(["media.example.com"], ["media.example.com"])
    assert _certificate_covers(
        ["*.example.com"], ["media.example.com", "video.example.com"]
    )
    assert not _certificate_covers(
        ["*.example.com"], ["example.com", "media.example.com"]
    )
    assert not _certificate_covers(["media.example.com"], ["video.example.com"])


def test_lucky_certificate_status():
    pending = _lucky_certificate_out(
        {
            "Key": "k1",
            "Remark": certificate_remark(["nas.lan"]),
            "ExtParams": {"acmeDomains": ["nas.lan"]},
            "ACMEErrMsg": "",
            "CertsInfo": None,
        }
    )
    assert pending is not None
    assert pending.status == "pending"
    assert pending.domains == ["nas.lan"]

    failed = _lucky_certificate_out(
        {
            "Key": "k2",
            "Remark": "failed",
            "ExtParams": {"acmeDomains": ["nas.lan"]},
            "ACMEErrMsg": "boom",
            "CertsInfo": None,
        }
    )
    assert failed is not None
    assert failed.status == "failed"
    assert failed.error == "boom"

    valid = _lucky_certificate_out(
        {
            "Key": "k3",
            "Remark": "valid",
            "CertsInfo": {
                "Domains": ["nas.lan"],
                "NotAfterTime": "2030-01-01",
            },
        }
    )
    assert valid is not None
    assert valid.status == "valid"
    assert valid.expires_on == "2030-01-01"


def test_ddns_dns_credentials_reused_for_acme():
    config = {
        "ddns": {
            "ipv4": {
                "provider": "alidns",
                "access_id": "LTAI-test",
                "secret": "secret",
            }
        }
    }
    assert _ddns_dns_credentials(config) == (
        "alidns",
        "LTAI-test",
        "secret",
    )
    assert _ddns_dns_credentials({}) is None


def test_transient_acme_error_detection():
    assert _is_transient_acme_error(
        "failed to get nonce from HTTP HEAD: request cancelled or timed out"
    )
    assert _is_transient_acme_error("context deadline exceeded")
    assert not _is_transient_acme_error(
        "DNS problem: NXDOMAIN looking up A for gitlab.example.com"
    )
    assert not _is_transient_acme_error(
        "invalid authorization: acme: error: 400 :: urn:ietf:params:acme:error:unauthorized"
    )


def test_wildcard_requires_dns_credentials():
    with pytest.raises(APIException):
        _auto_certificate_mode(["*.example.com"], dns_available=False)
    assert _auto_certificate_mode(["*.example.com"], dns_available=True) == "acme"
    with pytest.raises(APIException):
        _validate_acme_domains(["*.example.com"], dns_available=False)
    _validate_acme_domains(["*.example.com"], dns_available=True)


@pytest.mark.asyncio
async def test_submit_acme_retries_transient_failure(monkeypatch):
    from app.services.integrations import service as service_module

    monkeypatch.setattr(service_module, "ACME_POLL_INTERVAL", 0)

    domains = ["gitlab.example.com"]
    remark = certificate_remark(domains)

    def row(key: str, error: str) -> dict:
        return {
            "Key": key,
            "Remark": remark,
            "ExtParams": {"acmeDomains": domains},
            "ACMEErrMsg": error,
            "CertsInfo": None,
        }

    class FakeClient:
        def __init__(self) -> None:
            self.created = 0
            self.deleted: list[str] = []

        async def create_certificate(self, payload: dict) -> None:
            self.created += 1

        async def list_certificates(self) -> list[dict]:
            if self.created == 1:
                return [row("k1", "failed to get nonce: context deadline exceeded")]
            return [row("k2", "")]

        async def delete_certificate(self, key: str) -> None:
            self.deleted.append(key)

    client = FakeClient()
    result = await integration_service._submit_acme_certificate(
        client, {}, domains, remark
    )
    assert client.created == 2
    assert client.deleted == ["k1"]
    assert result is not None
    assert result.status == "pending"


@pytest.mark.asyncio
async def test_submit_acme_does_not_retry_real_failure(monkeypatch):
    from app.services.integrations import service as service_module

    monkeypatch.setattr(service_module, "ACME_POLL_INTERVAL", 0)

    domains = ["gitlab.example.com"]
    remark = certificate_remark(domains)
    error = "DNS problem: NXDOMAIN looking up A for gitlab.example.com"

    class FakeClient:
        def __init__(self) -> None:
            self.created = 0
            self.deleted: list[str] = []

        async def create_certificate(self, payload: dict) -> None:
            self.created += 1

        async def list_certificates(self) -> list[dict]:
            return [
                {
                    "Key": "k1",
                    "Remark": remark,
                    "ExtParams": {"acmeDomains": domains},
                    "ACMEErrMsg": error,
                    "CertsInfo": None,
                }
            ]

        async def delete_certificate(self, key: str) -> None:
            self.deleted.append(key)

    client = FakeClient()
    result = await integration_service._submit_acme_certificate(
        client, {}, domains, remark
    )
    assert client.created == 1
    assert client.deleted == []
    assert result is not None
    assert result.status == "failed"
