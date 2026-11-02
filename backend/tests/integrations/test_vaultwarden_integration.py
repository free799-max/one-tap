"""Vaultwarden 客户端加密与配置接口测试。"""

import base64
import hashlib
import hmac

import pytest

from app.services.integrations.vaultwarden import (
    VaultwardenClient,
    VaultwardenCrypto,
    VaultwardenSession,
    _hkdf_expand,
)


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register_and_login(client, username: str) -> str:
    resp = await client.post(
        "/api/auth/register", json={"username": username, "password": "pass123456"}
    )
    assert resp.status_code == 201, resp.text
    login = await client.post(
        "/api/auth/login", json={"username": username, "password": "pass123456"}
    )
    assert login.status_code == 200, login.text
    return login.json()["data"]["access_token"]


def _encrypt_raw(value: bytes, enc_key: bytes, mac_key: bytes) -> str:
    from app.services.integrations.vaultwarden import _aes_encrypt

    iv = b"0123456789abcdef"
    ciphertext = _aes_encrypt(enc_key, iv, value)
    mac = hmac.new(mac_key, iv + ciphertext, hashlib.sha256).digest()
    return "2.{}|{}|{}".format(
        base64.b64encode(iv).decode(),
        base64.b64encode(ciphertext).decode(),
        base64.b64encode(mac).decode(),
    )


def test_master_password_hash_matches_bitwarden_derivation():
    master_key = VaultwardenCrypto.derive_master_key(
        "correct horse", "User@Example.com", 0, 100000
    )
    expected_key = hashlib.pbkdf2_hmac(
        "sha256", b"correct horse", b"user@example.com", 100000, dklen=32
    )
    assert master_key == expected_key

    expected_hash = base64.b64encode(
        hashlib.pbkdf2_hmac("sha256", master_key, b"correct horse", 1, dklen=32)
    ).decode()
    assert (
        VaultwardenCrypto.master_password_hash(master_key, "correct horse")
        == expected_hash
    )


def test_decrypt_published_bitwarden_vector():
    master_key = bytes.fromhex(
        "a33fbc86187f9c7ce2f110d4ee2042de931267035c839ac5533c2118c00e5270"
        "b5bc60fcce752993518472aad6de1fc65992853ff86a95e9388ee5e098d8859c"
    )
    encrypted = (
        "2.IkWFb104bXv7Zwl7eFbsnQ==|"
        "SB42jIOvjhV32hSusW/J7WfAnQV8DKIV/CJQB7IDaiz4lQv4lIcXzWp9+IT0ncVQ|"
        "S8Tcp2klhcOOzZvoA0C9WRURaWUq+U1F9jbuBskDIz0="
    )
    assert (
        VaultwardenCrypto._decrypt_enc_string(
            encrypted, master_key[:32], master_key[32:]
        )
        == b"The quick brown fox jumps over the lazy dog"
    )


def test_decrypt_login_key_and_encrypt_fields():
    master_key = VaultwardenCrypto.derive_master_key(
        "pass", "a@b.com", 0, 100000
    )
    enc_key = _hkdf_expand(master_key, b"enc")
    mac_key = _hkdf_expand(master_key, b"mac")
    user_key = bytes(range(64))
    encrypted = _encrypt_raw(user_key, enc_key, mac_key)

    assert VaultwardenCrypto.decrypt_key(encrypted, master_key) == user_key

    client = VaultwardenClient("http://vaultwarden.local")
    client._session = VaultwardenSession(token="token", user_key=user_key)
    payload = client.build_login_cipher(
        name="OneTap · Jellyfin",
        username="admin",
        password="secret",
        url="http://192.168.1.2:8096",
    )
    item_enc_key, item_mac_key = VaultwardenCrypto.split_user_key(user_key)
    assert (
        VaultwardenCrypto._decrypt_enc_string(
            payload["name"], item_enc_key, item_mac_key
        ).decode()
        == "OneTap · Jellyfin"
    )
    assert (
        VaultwardenCrypto._decrypt_enc_string(
            payload["login"]["password"], item_enc_key, item_mac_key
        ).decode()
        == "secret"
    )
    assert (
        VaultwardenCrypto._decrypt_enc_string(
            payload["login"]["uris"][0]["uri"], item_enc_key, item_mac_key
        ).decode()
        == "http://192.168.1.2:8096"
    )


def test_argon2_is_rejected_with_readable_message():
    with pytest.raises(Exception) as exc:
        VaultwardenCrypto.derive_master_key("pass", "a@b.com", 1, 3)
    assert "Argon2" in str(exc.value)


@pytest.mark.asyncio
async def test_vaultwarden_config_api_masks_password(client):
    token = await _register_and_login(client, "integrationadmin")
    headers = _auth_headers(token)

    resp = await client.get("/api/integrations", headers=headers)
    assert resp.status_code == 200, resp.text
    rows = resp.json()["data"]
    assert {row["kind"] for row in rows} == {"vaultwarden", "network"}

    resp = await client.put(
        "/api/integrations/vaultwarden",
        headers=headers,
        json={
            "enabled": True,
            "url": "http://vaultwarden:8080/",
            "email": "admin@example.com",
            "password": "master-pass",
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["url"] == "http://vaultwarden:8080"
    assert data["configured"] is True
    assert data["password_set"] is True
    assert "password" not in data

    # 留空密码时沿用已保存值
    resp = await client.put(
        "/api/integrations/vaultwarden",
        headers=headers,
        json={"enabled": True, "url": "http://vaultwarden:8080", "email": "admin@example.com"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["configured"] is True
    assert resp.json()["data"]["password_set"] is True

    resp = await client.get("/api/integrations/vaultwarden/apps", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == []


@pytest.mark.asyncio
async def test_network_config_api_roundtrip(client):
    token = await _register_and_login(client, "networkadmin")
    headers = _auth_headers(token)

    resp = await client.get("/api/integrations/network", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["provider"] == "lucky"
    assert resp.json()["data"]["configured"] is False

    resp = await client.put(
        "/api/integrations/network",
        headers=headers,
        json={
            "enabled": True,
            "provider": "npm",
            "target_host": "192.168.1.10",
            "lucky": {
                "url": "http://lucky:16601",
                "username": "666",
                "password": "lucky-pass",
                "listen_port": 443,
                "enable_tls": True,
            },
            "npm": {
                "url": "http://npm:81",
                "email": "admin@example.com",
                "password": "npm-pass",
                "certificate_id": 0,
                "ssl_forced": False,
            },
            "ddnsgo": {
                "url": "http://ddnsgo:9876",
                "username": "admin",
                "password": "ddns-pass",
            },
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["provider"] == "npm"
    assert data["configured"] is True
    assert data["npm"]["password_set"] is True
    assert data["ddnsgo"]["password_set"] is True
    assert data["lucky"]["password_set"] is True
    assert "npm-pass" not in resp.text
    assert "ddns-pass" not in resp.text
    assert "lucky-pass" not in resp.text

    # 切换回 Lucky 时保留 NPM / DDNS-Go 的连接信息
    resp = await client.put(
        "/api/integrations/network",
        headers=headers,
        json={
            "enabled": True,
            "provider": "lucky",
            "target_host": "192.168.1.10",
            "lucky": {
                "url": "http://lucky:16601",
                "username": "666",
                "listen_port": 443,
                "enable_tls": True,
            },
            "npm": {
                "url": "http://npm:81",
                "email": "admin@example.com",
                "certificate_id": 0,
                "ssl_forced": False,
            },
            "ddnsgo": {
                "url": "http://ddnsgo:9876",
                "username": "admin",
            },
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["provider"] == "lucky"
    assert data["npm"]["password_set"] is True
    assert data["ddnsgo"]["password_set"] is True

    resp = await client.get("/api/integrations/network/apps", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == []


@pytest.mark.asyncio
async def test_network_password_reveal_requires_login_password(client):
    token = await _register_and_login(client, "revealadmin")
    headers = _auth_headers(token)

    resp = await client.put(
        "/api/integrations/network",
        headers=headers,
        json={
            "enabled": True,
            "provider": "lucky",
            "lucky": {
                "url": "http://lucky:16601",
                "username": "666",
                "password": "lucky-pass",
            },
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["lucky"]["password_set"] is True

    resp = await client.post(
        "/api/integrations/network/secret",
        headers=headers,
        json={"provider": "lucky", "password": "wrong-pass"},
    )
    assert resp.status_code == 401, resp.text
    assert "密码" in resp.json()["message"]

    resp = await client.post(
        "/api/integrations/network/secret",
        headers=headers,
        json={"provider": "lucky", "password": "pass123456"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["password"] == "lucky-pass"

    resp = await client.post(
        "/api/integrations/network/secret",
        headers=headers,
        json={"provider": "npm", "password": "pass123456"},
    )
    assert resp.status_code == 404, resp.text

