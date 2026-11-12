"""授权证书、管理端交互与后台刷新服务。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
import json
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import APIException
from app.database import async_session
from app.models.license import LicenseState
from app.services.licensing.device import get_device_fingerprint
from app.services.manage_client import (
    ManageRequestError,
    manage_base_urls,
    with_manage_fallback,
)
from app.services.system.config_service import system_config_service

logger = logging.getLogger(__name__)

AUDIENCE = "onetap"


class LicenseCertificateError(ValueError):
    def __init__(self, reason: str, expires_at: datetime | None = None) -> None:
        self.reason = reason
        self.expires_at = expires_at
        super().__init__(reason)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: Any, claim: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LicenseCertificateError(f"invalid_{claim}")
    return int(value)


def _decode_segment(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def _certificate_parts(certificate: str) -> tuple[dict, dict, bytes, bytes]:
    try:
        encoded_header, encoded_payload, encoded_signature = certificate.split(".")
        header = json.loads(_decode_segment(encoded_header))
        payload = json.loads(_decode_segment(encoded_payload))
        if not isinstance(header, dict) or not isinstance(payload, dict):
            raise ValueError("JWT parts must be objects")
        signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
        return header, payload, signing_input, _decode_segment(encoded_signature)
    except (ValueError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
        raise LicenseCertificateError("invalid_signature") from exc


def _certificate_expiration(certificate: str) -> datetime | None:
    try:
        _, claims, _, _ = _certificate_parts(certificate)
        exp = claims.get("exp")
        if isinstance(exp, (int, float)) and not isinstance(exp, bool):
            return datetime.fromtimestamp(exp, tz=timezone.utc)
    except (LicenseCertificateError, ValueError, TypeError, OSError):
        pass
    return None


def _normalize_public_key(value: str) -> str:
    text = value.strip().replace("\\n", "\n")
    try:
        if "-----BEGIN" in text:
            key = serialization.load_pem_public_key(text.encode())
        else:
            raw = _decode_segment(text)
            key = Ed25519PublicKey.from_public_bytes(raw)
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError("not Ed25519")
        return key.public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()
    except Exception as exc:
        raise APIException("授权公钥无效", 502) from exc


def validate_certificate(
    certificate: str,
    installation_id: str,
    public_key: str,
    key_id: str | None = None,
    *,
    hardware_fingerprint: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """验证证书签名、身份绑定与全部必需声明。"""
    expires_at = _certificate_expiration(certificate)
    header, payload, signing_input, signature = _certificate_parts(certificate)
    if header.get("alg") != "EdDSA":
        raise LicenseCertificateError("invalid_algorithm", expires_at)
    if key_id is not None and header.get("kid") != key_id:
        raise LicenseCertificateError("invalid_key_id", expires_at)
    try:
        key = serialization.load_pem_public_key(
            _normalize_public_key(public_key).encode()
        )
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError("not Ed25519")
        key.verify(signature, signing_input)
    except (APIException, InvalidSignature, ValueError, TypeError) as exc:
        raise LicenseCertificateError("invalid_signature", expires_at) from exc

    if payload.get("iss") != settings.LICENSE_ISSUER:
        raise LicenseCertificateError("invalid_issuer", expires_at)
    audience = payload.get("aud")
    if not (
        audience == AUDIENCE
        or isinstance(audience, list) and AUDIENCE in audience
    ):
        raise LicenseCertificateError("invalid_audience", expires_at)

    current = now or _utcnow()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    now_ts = current.timestamp()
    exp = _timestamp(payload.get("exp"), "exp")
    if exp <= now_ts:
        raise LicenseCertificateError("expired", datetime.fromtimestamp(exp, timezone.utc))
    if "nbf" in payload and _timestamp(payload["nbf"], "nbf") > now_ts:
        raise LicenseCertificateError("not_active", expires_at)
    if "iat" in payload and _timestamp(payload["iat"], "iat") > now_ts + 60:
        raise LicenseCertificateError("invalid_iat", expires_at)
    expected_hash = hashlib.sha256(installation_id.encode()).hexdigest()
    actual_hash = payload.get("installation_id_hash")
    if not isinstance(actual_hash, str) or not hmac.compare_digest(actual_hash, expected_hash):
        raise LicenseCertificateError("device_mismatch", expires_at)
    if "hardware_fingerprint" in payload:
        certificate_fingerprint = payload["hardware_fingerprint"]
        if (
            not isinstance(certificate_fingerprint, str)
            or hardware_fingerprint is None
            or not hmac.compare_digest(certificate_fingerprint, hardware_fingerprint)
        ):
            raise LicenseCertificateError("device_mismatch", expires_at)
    return payload


class LicensingService:
    def __init__(self) -> None:
        self._scheduler_task: asyncio.Task | None = None
        self._scheduler_stop: asyncio.Event | None = None

    async def get_state(self, db: AsyncSession) -> LicenseState:
        result = await db.execute(select(LicenseState).where(LicenseState.id == 1))
        state = result.scalar_one_or_none()
        if state is None:
            state = LicenseState(id=1, installation_id=str(uuid.uuid4()))
            db.add(state)
            await db.flush()
        return state

    def _path(self, path: str) -> str:
        if not manage_base_urls():
            raise APIException("未配置授权管理端地址", 503)
        return f"/api/public/licenses/{path}"

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        try:
            data = response.json()
            detail = data.get("message") or data.get("detail")
            if isinstance(detail, str) and detail.strip():
                return detail
        except (ValueError, AttributeError):
            pass
        return f"管理端返回 HTTP {response.status_code}"

    @staticmethod
    def _response_data(response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError as exc:
            raise APIException("授权管理端响应无效", 502) from exc
        if not isinstance(data, dict):
            raise APIException("授权管理端响应无效", 502)
        if "success" in data:
            if data.get("success") is not True:
                detail = data.get("message") or data.get("detail") or "请求失败"
                raise APIException(f"授权管理端请求失败：{detail}", 502)
            data = data.get("data")
            if not isinstance(data, dict):
                raise APIException("授权管理端响应无效", 502)
        return data

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        license_path = self._path(path)
        try:
            async with httpx.AsyncClient(timeout=settings.LICENSE_HTTP_TIMEOUT_SECONDS) as client:
                response, _ = await with_manage_fallback(
                    lambda base_url: client.post(f"{base_url}{license_path}", json=body)
                )
        except APIException:
            raise
        except (ManageRequestError, httpx.HTTPError, OSError) as exc:
            raise APIException("无法连接授权管理端", 502) from exc
        if response.is_error:
            status = response.status_code if 400 <= response.status_code < 500 else 502
            raise APIException(f"授权管理端请求失败：{self._error_message(response)}", status)
        return self._response_data(response)

    async def _fetch_public_key(self, db: AsyncSession, state: LicenseState) -> tuple[str, str | None]:
        license_path = self._path("public-key")
        try:
            async with httpx.AsyncClient(timeout=settings.LICENSE_HTTP_TIMEOUT_SECONDS) as client:
                response, _ = await with_manage_fallback(
                    lambda base_url: client.get(f"{base_url}{license_path}")
                )
        except APIException:
            raise
        except (ManageRequestError, httpx.HTTPError, OSError) as exc:
            raise APIException("无法获取授权公钥", 502) from exc
        if response.is_error:
            raise APIException(f"获取授权公钥失败：{self._error_message(response)}", 502)
        try:
            data = self._response_data(response)
            if data.get("algorithm") != "EdDSA":
                raise ValueError("algorithm")
            public_key = _normalize_public_key(str(data["public_key"]))
            key_id = str(data["key_id"])
        except (ValueError, KeyError, TypeError, APIException) as exc:
            raise APIException("授权公钥响应无效", 502) from exc
        state.public_key = public_key
        state.key_id = key_id
        await db.flush()
        return public_key, key_id

    async def _public_key(
        self, db: AsyncSession, state: LicenseState
    ) -> tuple[str, str | None]:
        if settings.LICENSE_PUBLIC_KEY.strip():
            return settings.LICENSE_PUBLIC_KEY, None
        if state.public_key:
            return state.public_key, state.key_id
        return await self._fetch_public_key(db, state)

    @staticmethod
    def _normalize_id(value: Any, field: str) -> str:
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise APIException(f"授权管理端响应中的 {field} 无效", 502)
        normalized = str(value).strip()
        if not normalized:
            raise APIException(f"授权管理端响应中的 {field} 无效", 502)
        return normalized

    async def _apply_activation(
        self,
        db: AsyncSession,
        state: LicenseState,
        data: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        for field in ("certificate", "refresh_token"):
            if not isinstance(data.get(field), str) or not data[field]:
                raise APIException("授权管理端响应缺少必要字段", 502)
        identifiers = {}
        for field in ("license_id", "activation_id"):
            response_id = LicensingService._normalize_id(data.get(field), field)
            certificate_id = LicensingService._normalize_id(payload.get(field), field)
            if response_id != certificate_id:
                raise APIException(f"授权证书与响应中的 {field} 不一致", 502)
            identifiers[field] = response_id
        state.certificate = data["certificate"]
        state.refresh_token = data["refresh_token"]
        state.license_id = identifiers["license_id"]
        state.activation_id = identifiers["activation_id"]
        state.last_refreshed_at = _utcnow()
        state.last_error = None
        await self._apply_acceleration_url(db, data.get("acceleration_subscription_url"))

    async def _apply_acceleration_url(self, db: AsyncSession, url: Any) -> None:
        """保存管理端下发的加速订阅地址；未下发时保留本机已有值。"""
        if not isinstance(url, str) or not url.strip():
            return
        value = url.strip()
        if not value.lower().startswith(("http://", "https://")):
            return
        config = await system_config_service.get_or_create(db)
        config.acceleration_subscription_url = value
        await db.flush()

    async def _clear_acceleration_url(self, db: AsyncSession) -> None:
        config = await system_config_service.get_or_create(db)
        if config.acceleration_subscription_url:
            config.acceleration_subscription_url = ""
            await db.flush()

    async def activate(
        self, db: AsyncSession, license_key: str, device_name: str | None = None
    ) -> dict[str, Any]:
        state = await self.get_state(db)
        device = await get_device_fingerprint(state.installation_id)
        if device_name is not None:
            state.device_name = device_name.strip() or None
        try:
            data = await self._post(
                "activate",
                {
                    "license_key": license_key,
                    "installation_id": state.installation_id,
                    "device_name": state.device_name,
                    **device,
                },
            )
            certificate = data.get("certificate")
            if not isinstance(certificate, str):
                raise APIException("授权管理端响应缺少证书", 502)
            public_key, key_id = await self._public_key(db, state)
            try:
                payload = validate_certificate(
                    certificate,
                    state.installation_id,
                    public_key,
                    key_id,
                    hardware_fingerprint=device["hardware_fingerprint"],
                )
            except LicenseCertificateError as exc:
                raise APIException("授权证书验证失败", 502) from exc
            await self._apply_activation(db, state, data, payload)
            state.license_key = license_key.strip()
            await db.commit()
        except APIException as exc:
            state.last_error = exc.message
            await db.commit()
            raise
        return await self.get_status(db)

    @staticmethod
    def _clear_authorization(state: LicenseState, last_error: str | None) -> None:
        state.certificate = None
        state.refresh_token = None
        state.license_key = None
        state.license_id = None
        state.activation_id = None
        state.public_key = None
        state.key_id = None
        state.last_refreshed_at = None
        state.last_error = last_error

    async def refresh(self, db: AsyncSession) -> dict[str, Any]:
        state = await self.get_state(db)
        if not state.refresh_token:
            # 只丢了刷新令牌但还留着授权码（例如旧版本刷新失败时被清空）时，
            # 直接用授权码重新激活，用户不需要重新输入。
            if state.license_key:
                return await self.activate(db, state.license_key, state.device_name)
            raise APIException("本机尚未激活授权", 409)
        device = await get_device_fingerprint(state.installation_id)
        try:
            data = await self._post(
                "refresh",
                {
                    "refresh_token": state.refresh_token,
                    "installation_id": state.installation_id,
                    **device,
                },
            )
            certificate = data.get("certificate")
            if not isinstance(certificate, str):
                raise APIException("授权管理端响应缺少证书", 502)
            public_key, key_id = await self._public_key(db, state)
            try:
                payload = validate_certificate(
                    certificate,
                    state.installation_id,
                    public_key,
                    key_id,
                    hardware_fingerprint=device["hardware_fingerprint"],
                )
            except LicenseCertificateError as exc:
                raise APIException("刷新后的授权证书验证失败", 502) from exc
            await self._apply_activation(db, state, data, payload)
            await db.commit()
        except APIException as exc:
            # 刷新令牌失效（管理端重置、同机重新激活换了令牌等）时，
            # 用本机保存的授权码自动重新激活，而不是清空本地授权。
            if exc.status_code in {401, 403} and state.license_key:
                logger.info("刷新令牌失效，使用本机授权码重新激活：%s", exc.message)
                return await self.activate(db, state.license_key, state.device_name)
            state.last_error = exc.message
            await db.commit()
            raise
        return await self.get_status(db)

    async def deactivate(self, db: AsyncSession) -> dict[str, Any]:
        state = await self.get_state(db)
        device = await get_device_fingerprint(state.installation_id)
        if state.refresh_token:
            try:
                await self._post(
                    "deactivate",
                    {
                        "refresh_token": state.refresh_token,
                        "installation_id": state.installation_id,
                        **device,
                    },
                )
            except APIException as exc:
                if exc.status_code not in {401, 403}:
                    state.last_error = exc.message
                    await db.commit()
                    raise
                self._clear_authorization(state, exc.message)
                await self._clear_acceleration_url(db)
                await db.commit()
                return await self.get_status(db)
        self._clear_authorization(state, None)
        await self._clear_acceleration_url(db)
        await db.commit()
        return await self.get_status(db)

    async def get_status(
        self, db: AsyncSession, include_license_key: bool = False
    ) -> dict[str, Any]:
        """返回本机授权状态；明文授权码仅通过二次验证接口获取。"""
        state = await self.get_state(db)
        device = await get_device_fingerprint(state.installation_id)
        result: dict[str, Any] = {
            "activated": bool(state.certificate),
            "valid": False,
            "status": "inactive",
            "edition": "free",
            "perpetual": False,
            "license_id": state.license_id,
            "activation_id": state.activation_id,
            "license_key": state.license_key if include_license_key else None,
            "license_key_set": bool(state.license_key),
            "device_name": state.device_name,
            "expires_at": None,
            "last_refreshed_at": state.last_refreshed_at,
            "last_error": state.last_error,
            "purchase_url": settings.LICENSE_PURCHASE_URL or None,
        }
        if not state.certificate:
            return result
        try:
            public_key, key_id = await self._public_key(db, state)
            payload = validate_certificate(
                state.certificate,
                state.installation_id,
                public_key,
                key_id,
                hardware_fingerprint=device["hardware_fingerprint"],
            )
        except LicenseCertificateError as exc:
            result["status"] = "expired" if exc.reason == "expired" else "invalid"
            result["expires_at"] = exc.expires_at
            return result
        except APIException:
            result["status"] = "invalid"
            return result
        result["valid"] = True
        result["status"] = "active"
        result["edition"] = "licensed"
        result["perpetual"] = payload.get("perpetual") is True
        result["expires_at"] = datetime.fromtimestamp(payload["exp"], timezone.utc)
        return result

    async def require_valid(self, db: AsyncSession) -> dict[str, Any]:
        status = await self.get_status(db)
        if not status["valid"]:
            raise APIException("需要有效授权", 403)
        return status

    def start_scheduler(self) -> None:
        if self._scheduler_task is not None and not self._scheduler_task.done():
            return
        self._scheduler_stop = asyncio.Event()
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())

    async def stop_scheduler(self) -> None:
        if self._scheduler_stop is not None:
            self._scheduler_stop.set()
        task = self._scheduler_task
        self._scheduler_task = None
        self._scheduler_stop = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _scheduler_loop(self) -> None:
        while True:
            await self.refresh_tick()
            stop = self._scheduler_stop
            if stop is None:
                return
            try:
                await asyncio.wait_for(
                    stop.wait(), timeout=settings.LICENSE_REFRESH_INTERVAL_HOURS * 3600
                )
                return
            except asyncio.TimeoutError:
                pass

    async def refresh_tick(self) -> None:
        try:
            async with async_session() as db:
                state = await self.get_state(db)
                if not state.refresh_token and not state.license_key:
                    return
                await self.refresh(db)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("后台刷新授权失败：%s", exc)


licensing_service = LicensingService()
