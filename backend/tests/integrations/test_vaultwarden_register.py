"""Vaultwarden 首次账号注册协议测试。"""

import base64
import hashlib
import json

import httpx
import pytest
from cryptography.hazmat.primitives import serialization

from app.services.integrations.vaultwarden import (
    VaultwardenClient,
    VaultwardenCrypto,
    VaultwardenError,
)


@pytest.mark.asyncio
async def test_register_builds_decryptable_bitwarden_payload():
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/send-verification-email"):
            return httpx.Response(200, json="register-token")
        return httpx.Response(200)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = VaultwardenClient("http://vaultwarden.local/")
        client._client = http_client
        result = await client.register(" User@Example.com ", "correct horse")

    assert result is None
    assert client._session is None
    assert len(requests) == 2
    assert requests[0].url.path == (
        "/identity/accounts/register/send-verification-email"
    )
    request = requests[1]
    assert request.method == "POST"
    assert request.url.path == "/identity/accounts/register/finish"

    payload = json.loads(request.content)
    assert set(payload) == {
        "email",
        "masterPasswordHash",
        "masterPasswordHint",
        "emailVerificationToken",
        "key",
        "kdf",
        "kdfIterations",
        "keys",
    }
    assert payload["emailVerificationToken"] == "register-token"
    assert payload["email"] == "User@Example.com"
    assert payload["masterPasswordHint"] == ""
    assert payload["kdf"] == 0
    assert payload["kdfIterations"] == 600000

    master_key = hashlib.pbkdf2_hmac(
        "sha256", b"correct horse", b"user@example.com", 600000, dklen=32
    )
    expected_hash = base64.b64encode(
        hashlib.pbkdf2_hmac(
            "sha256", master_key, b"correct horse", 1, dklen=32
        )
    ).decode("ascii")
    assert payload["masterPasswordHash"] == expected_hash

    user_key = VaultwardenCrypto.decrypt_key(payload["key"], master_key)
    assert len(user_key) == 64
    assert payload["key"].startswith("2.")

    assert set(payload["keys"]) == {"publicKey", "encryptedPrivateKey"}
    encrypted_private_key = payload["keys"]["encryptedPrivateKey"]
    assert encrypted_private_key.startswith("2.")
    user_enc_key, user_mac_key = VaultwardenCrypto.split_user_key(user_key)
    private_key_der = VaultwardenCrypto._decrypt_enc_string(
        encrypted_private_key, user_enc_key, user_mac_key
    )
    private_key = serialization.load_der_private_key(private_key_der, password=None)
    assert private_key.key_size == 2048
    assert private_key_der == private_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )

    public_key_der = base64.b64decode(payload["keys"]["publicKey"])
    public_key = serialization.load_der_public_key(public_key_der)
    assert public_key.key_size == 2048
    assert public_key.public_numbers() == private_key.public_key().public_numbers()
    assert public_key_der == public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


@pytest.mark.asyncio
async def test_register_surfaces_readable_http_error():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"ErrorModel": {"Message": "Email already registered"}}
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = VaultwardenClient("http://vaultwarden.local")
        client._client = http_client
        with pytest.raises(VaultwardenError) as exc_info:
            await client.register("user@example.com", "correct horse")

    assert str(exc_info.value) == "Email already registered"
    assert exc_info.value.status_code == 502
    assert client._session is None


@pytest.mark.asyncio
async def test_register_falls_back_to_legacy_endpoint():
    paths = []

    async def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/send-verification-email"):
            return httpx.Response(404)
        return httpx.Response(200)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = VaultwardenClient("http://vaultwarden.local")
        client._client = http_client
        await client.register("user@example.com", "correct horse")

    assert paths == [
        "/identity/accounts/register/send-verification-email",
        "/api/accounts/register",
    ]
