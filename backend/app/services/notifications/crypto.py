"""通知渠道敏感字段的对称加密。"""

from __future__ import annotations

import base64
import hashlib
import json

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings


def _fernet() -> Fernet:
    digest = hashlib.sha256(
        f"notification:{settings.SECRET_KEY}".encode("utf-8")
    ).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secrets(secrets: dict) -> str | None:
    clean = {key: value for key, value in secrets.items() if value not in (None, "")}
    if not clean:
        return None
    return _fernet().encrypt(json.dumps(clean).encode("utf-8")).decode("ascii")


def decrypt_secrets(value: str | None) -> dict:
    if not value:
        return {}
    try:
        payload = _fernet().decrypt(value.encode("ascii"))
    except (InvalidToken, ValueError, UnicodeDecodeError):
        return {}
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}
