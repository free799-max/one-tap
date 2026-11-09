"""Vaultwarden 认证集成。

通过 Bitwarden 兼容接口登录 Vaultwarden，在客户端完成主密钥派生与
条目字段加密后写入密码库，避免在服务端保存 Vaultwarden 明文条目数据。
"""

import base64
import hashlib
import hmac
import json
import os
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.exceptions import APIException


class VaultwardenError(APIException):
    """Vaultwarden 调用失败。"""


def _b64encode(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.b64decode(value)


def _jwt_subject(token: str) -> str | None:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        subject = claims.get("sub")
        return str(subject) if subject else None
    except (IndexError, ValueError, TypeError):
        return None


def _hkdf_expand(prk: bytes, info: bytes, length: int = 32) -> bytes:
    """Bitwarden 使用的 HKDF-Expand（直接以主密钥作为 PRK）。"""
    result = b""
    previous = b""
    counter = 1
    while len(result) < length:
        previous = hmac.new(
            prk, previous + info + bytes([counter]), hashlib.sha256
        ).digest()
        result += previous
        counter += 1
    return result[:length]


def _stretch_master_key(master_key: bytes) -> tuple[bytes, bytes]:
    return (
        _hkdf_expand(master_key, b"enc"),
        _hkdf_expand(master_key, b"mac"),
    )


def _pad(data: bytes) -> bytes:
    padding = 16 - len(data) % 16
    return data + bytes([padding]) * padding


def _unpad(data: bytes) -> bytes:
    if not data:
        return data
    padding = data[-1]
    if padding < 1 or padding > 16 or data[-padding:] != bytes([padding]) * padding:
        raise VaultwardenError("Vaultwarden 密钥解密失败", 400)
    return data[:-padding]


def _aes_encrypt(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return encryptor.update(_pad(plaintext)) + encryptor.finalize()


def _aes_decrypt(key: bytes, iv: bytes, ciphertext: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    return _unpad(decryptor.update(ciphertext) + decryptor.finalize())


@dataclass
class VaultwardenSession:
    """一次登录会话。"""

    token: str
    user_key: bytes
    version: str | None = None
    user_id: str | None = None


class VaultwardenCrypto:
    """Bitwarden/Vaultwarden 客户端加密工具。"""

    @staticmethod
    def derive_master_key(
        password: str,
        email: str,
        kdf: int,
        iterations: int,
    ) -> bytes:
        if int(kdf) != 0:
            raise VaultwardenError(
                "当前 Vaultwarden 账号使用 Argon2id KDF，暂不支持自动同步，"
                "请在 Vaultwarden 设置中改为 PBKDF2 后重试",
                400,
            )
        if iterations <= 0:
            raise VaultwardenError("Vaultwarden 返回的 KDF 迭代次数无效", 400)
        salt = email.strip().lower().encode("utf-8")
        return hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, iterations, dklen=32
        )

    @staticmethod
    def master_password_hash(master_key: bytes, password: str) -> str:
        digest = hashlib.pbkdf2_hmac(
            "sha256", master_key, password.encode("utf-8"), 1, dklen=32
        )
        return _b64encode(digest)

    @staticmethod
    def decrypt_key(encrypted_key: str, master_key: bytes) -> bytes:
        """解密登录响应中的用户对称密钥。"""
        enc_key, mac_key = _stretch_master_key(master_key)
        return VaultwardenCrypto._decrypt_enc_string(encrypted_key, enc_key, mac_key)

    @staticmethod
    def split_user_key(user_key: bytes) -> tuple[bytes, bytes]:
        if len(user_key) < 64:
            raise VaultwardenError("Vaultwarden 用户密钥长度异常", 400)
        return user_key[:32], user_key[32:64]

    @staticmethod
    def encrypt_field(value: str | None, user_key: bytes) -> str | None:
        if value is None or value == "":
            return None
        enc_key, mac_key = VaultwardenCrypto.split_user_key(user_key)
        return VaultwardenCrypto._encrypt_enc_string(
            value.encode("utf-8"), enc_key, mac_key
        )

    @staticmethod
    def _encrypt_enc_string(value: bytes, enc_key: bytes, mac_key: bytes) -> str:
        iv = os.urandom(16)
        ciphertext = _aes_encrypt(enc_key, iv, value)
        mac = hmac.new(mac_key, iv + ciphertext, hashlib.sha256).digest()
        return f"2.{_b64encode(iv)}|{_b64encode(ciphertext)}|{_b64encode(mac)}"

    @staticmethod
    def _decrypt_enc_string(value: str, enc_key: bytes, mac_key: bytes) -> bytes:
        try:
            type_part, payload = value.split(".", 1)
            parts = payload.split("|")
            iv = _b64decode(parts[0])
            ciphertext = _b64decode(parts[1])
            if type_part == "2":
                expected = _b64decode(parts[2])
                actual = hmac.new(mac_key, iv + ciphertext, hashlib.sha256).digest()
                if not hmac.compare_digest(expected, actual):
                    raise VaultwardenError("Vaultwarden 密钥校验失败", 400)
            elif type_part != "0":
                raise VaultwardenError(
                    f"不支持的 Vaultwarden 加密类型：{type_part}", 400
                )
            return _aes_decrypt(enc_key, iv, ciphertext)
        except VaultwardenError:
            raise
        except Exception as exc:  # noqa: BLE001 - 统一转换为可读错误
            raise VaultwardenError("Vaultwarden 密钥解析失败", 400) from exc


class VaultwardenClient:
    """Vaultwarden 连接与条目写入客户端。"""

    TIMEOUT = 20.0

    def __init__(self, base_url: str):
        url = (base_url or "").strip().rstrip("/")
        if not url:
            raise VaultwardenError("请填写 Vaultwarden 地址", 400)
        if not url.startswith(("http://", "https://")):
            url = f"http://{url}"
        self.base_url = url
        self._client: httpx.AsyncClient | None = None
        self._session: VaultwardenSession | None = None

    async def __aenter__(self) -> "VaultwardenClient":
        self._client = httpx.AsyncClient(
            timeout=self.TIMEOUT,
            follow_redirects=True,
            trust_env=False,
        )
        return self

    async def __aexit__(self, *_args) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("VaultwardenClient 必须在 async with 中使用")
        return self._client

    async def _post(self, path: str, **kwargs: Any) -> httpx.Response:
        try:
            return await self.client.post(f"{self.base_url}{path}", **kwargs)
        except httpx.HTTPError as exc:
            raise VaultwardenError(
                f"无法连接 Vaultwarden：{exc}", 502
            ) from exc

    async def _get(self, path: str, **kwargs: Any) -> httpx.Response:
        try:
            return await self.client.get(f"{self.base_url}{path}", **kwargs)
        except httpx.HTTPError as exc:
            raise VaultwardenError(
                f"无法连接 Vaultwarden：{exc}", 502
            ) from exc

    async def _put(self, path: str, **kwargs: Any) -> httpx.Response:
        try:
            return await self.client.put(f"{self.base_url}{path}", **kwargs)
        except httpx.HTTPError as exc:
            raise VaultwardenError(
                f"无法连接 Vaultwarden：{exc}", 502
            ) from exc

    async def prelogin(self, email: str) -> dict:
        response = await self._post(
            "/identity/accounts/prelogin", json={"email": email}
        )
        if response.status_code == 404:
            response = await self._post("/api/accounts/prelogin", json={"email": email})
        if response.status_code >= 400:
            raise VaultwardenError(
                self._error_message(response, "Vaultwarden 预登录失败"), 502
            )
        try:
            return response.json()
        except ValueError as exc:
            raise VaultwardenError("Vaultwarden 预登录返回内容异常", 502) from exc

    async def register(self, email: str, password: str) -> None:
        """创建 Bitwarden 兼容账号；成功后由调用方显式登录。"""
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        email = (email or "").strip()
        if not email or not password:
            raise VaultwardenError("请填写 Vaultwarden 邮箱和主密码", 400)

        master_key = VaultwardenCrypto.derive_master_key(
            password, email, kdf=0, iterations=600000
        )
        user_key = os.urandom(64)
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private_key_der = private_key.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        public_key_der = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        master_enc_key, master_mac_key = _stretch_master_key(master_key)
        user_enc_key, user_mac_key = VaultwardenCrypto.split_user_key(user_key)
        payload = {
            "email": email,
            "masterPasswordHash": VaultwardenCrypto.master_password_hash(
                master_key, password
            ),
            "masterPasswordHint": "",
            "key": VaultwardenCrypto._encrypt_enc_string(
                user_key, master_enc_key, master_mac_key
            ),
            "kdf": 0,
            "kdfIterations": 600000,
            "keys": {
                "publicKey": _b64encode(public_key_der),
                "encryptedPrivateKey": VaultwardenCrypto._encrypt_enc_string(
                    private_key_der, user_enc_key, user_mac_key
                ),
            },
        }
        verification = await self._post(
            "/identity/accounts/register/send-verification-email",
            json={"email": email, "name": None},
            headers={"Accept": "application/json"},
        )
        if verification.status_code == 404:
            response = await self._post("/api/accounts/register", json=payload)
        else:
            if verification.status_code == 204:
                raise VaultwardenError(
                    "Vaultwarden 要求邮箱验证，暂时无法自动创建账号", 400
                )
            if verification.status_code >= 400:
                raise VaultwardenError(
                    self._error_message(verification, "Vaultwarden 注册验证失败"),
                    502,
                )
            try:
                token = verification.json()
            except ValueError:
                token = verification.text.strip()
            if isinstance(token, dict):
                token = token.get("token") or token.get("Token")
            if not isinstance(token, str) or not token.strip():
                raise VaultwardenError("Vaultwarden 未返回注册验证令牌", 502)
            payload["emailVerificationToken"] = token.strip()
            response = await self._post(
                "/identity/accounts/register/finish", json=payload
            )
        if response.status_code >= 400:
            raise VaultwardenError(
                self._error_message(response, "Vaultwarden 注册失败"), 502
            )

    async def login(self, email: str, password: str) -> VaultwardenSession:
        email = (email or "").strip()
        if not email or not password:
            raise VaultwardenError("请填写 Vaultwarden 邮箱和主密码", 400)

        prelogin = await self.prelogin(email)
        kdf = int(prelogin.get("Kdf", prelogin.get("kdf", 0)) or 0)
        iterations = int(
            prelogin.get("KdfIterations", prelogin.get("kdfIterations", 600000))
            or 600000
        )
        master_key = VaultwardenCrypto.derive_master_key(
            password, email, kdf, iterations
        )
        password_hash = VaultwardenCrypto.master_password_hash(master_key, password)

        response = await self._post(
            "/identity/connect/token",
            data={
                "grant_type": "password",
                "username": email,
                "password": password_hash,
                "scope": "api offline_access",
                "client_id": "web",
                "deviceType": "10",
                "deviceIdentifier": str(uuid.uuid4()),
                "deviceName": "OneTap",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if response.status_code == 400:
            raise VaultwardenError(
                "Vaultwarden 登录失败，请检查邮箱和主密码", 401
            )
        if response.status_code >= 400:
            raise VaultwardenError(
                self._error_message(response, "Vaultwarden 登录失败"), 502
            )
        payload = response.json()
        token = payload.get("access_token") or payload.get("accessToken")
        encrypted_key = payload.get("Key") or payload.get("key")
        if not token or not encrypted_key:
            raise VaultwardenError(
                "Vaultwarden 登录响应缺少密钥，请确认账号已初始化", 502
            )
        user_key = VaultwardenCrypto.decrypt_key(str(encrypted_key), master_key)
        self._session = VaultwardenSession(
            token=str(token),
            user_key=user_key,
            version=await self.get_version(),
            user_id=(
                str(payload.get("UserId") or payload.get("userId") or "")
                or _jwt_subject(str(token))
            ),
        )
        return self._session

    async def get_version(self) -> str | None:
        try:
            response = await self._get("/api/config")
            if response.status_code < 400:
                data = response.json()
                return str(data.get("version") or data.get("Version") or "") or None
        except (httpx.HTTPError, ValueError):
            pass
        return None

    def _auth_headers(self) -> dict[str, str]:
        if self._session is None:
            raise VaultwardenError("尚未登录 Vaultwarden", 401)
        return {"Authorization": f"Bearer {self._session.token}"}

    async def create_cipher(self, payload: dict) -> str:
        response = await self._post(
            "/api/ciphers", json=payload, headers=self._auth_headers()
        )
        if response.status_code >= 400:
            raise VaultwardenError(
                self._error_message(response, "写入 Vaultwarden 条目失败"), 502
            )
        data = response.json()
        cipher_id = data.get("Id") or data.get("id")
        if not cipher_id:
            raise VaultwardenError("Vaultwarden 未返回新条目 ID", 502)
        return str(cipher_id)

    async def update_cipher(self, cipher_id: str, payload: dict) -> bool:
        response = await self._put(
            f"/api/ciphers/{cipher_id}",
            json=payload,
            headers=self._auth_headers(),
        )
        if response.status_code == 404:
            return False
        if response.status_code >= 400:
            raise VaultwardenError(
                self._error_message(response, "更新 Vaultwarden 条目失败"), 502
            )
        return True

    def build_login_cipher(
        self,
        *,
        name: str,
        username: str | None,
        password: str | None,
        url: str | None,
        notes: str | None = None,
    ) -> dict:
        if self._session is None:
            raise VaultwardenError("尚未登录 Vaultwarden", 401)
        user_key = self._session.user_key
        payload = {
            "type": 1,
            "name": VaultwardenCrypto.encrypt_field(name, user_key),
            "notes": VaultwardenCrypto.encrypt_field(notes, user_key),
            "favorite": False,
            "folderId": None,
            "organizationId": None,
            "login": {
                "username": VaultwardenCrypto.encrypt_field(username, user_key),
                "password": VaultwardenCrypto.encrypt_field(password, user_key),
                "totp": None,
                "uris": (
                    [{"uri": VaultwardenCrypto.encrypt_field(url, user_key)}]
                    if url
                    else []
                ),
            },
        }
        if self._session.user_id:
            payload["encryptedFor"] = self._session.user_id
        return payload

    @staticmethod
    def _error_message(response: httpx.Response, fallback: str) -> str:
        try:
            data = response.json()
        except ValueError:
            return fallback
        if isinstance(data, dict):
            for key in ("error_description", "error", "message", "msg"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            errors = data.get("ErrorModel") or data.get("errorModel")
            if isinstance(errors, dict):
                message = errors.get("Message") or errors.get("message")
                if message:
                    return str(message)
        return fallback


def cipher_name(app_name: str, instance_name: str) -> str:
    """生成稳定且可读的条目名称。"""
    instance_name = (instance_name or "").strip()
    if instance_name and instance_name != app_name:
        return f"OneTap · {app_name}（{instance_name}）"
    return f"OneTap · {app_name}"
