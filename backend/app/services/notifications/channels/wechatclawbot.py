"""微信 ClawBot（iLink）渠道。

iLink 只支持文本/图片/语音/文件/视频五种消息项，没有卡片类型。
带封面时把文字与图片放进同一条 sendmessage，客户端不支持时退回分开发送。
"""

from __future__ import annotations

import base64
import hashlib
import io
import logging
import os
import random
import uuid
from typing import Any
from urllib.parse import quote

import httpx
import segno
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from app.services.notifications.assets import fetch_image
from app.services.notifications.channels.base import BaseChannel, ChannelError, ChannelMessage

DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"
CHANNEL_VERSION = "3.0.0"
BOT_AGENT = "OneTap"
ILINK_APP_ID = "bot"

logger = logging.getLogger(__name__)


def _wechat_uin() -> str:
    return base64.b64encode(str(random.getrandbits(32)).encode()).decode()


def _client_version() -> str:
    parts = [int(part) if part.isdigit() else 0 for part in CHANNEL_VERSION.split(".")]
    major, minor, patch = (parts + [0, 0, 0])[:3]
    return str(((major & 0xFF) << 16) | ((minor & 0xFF) << 8) | (patch & 0xFF))


def _app_headers() -> dict[str, str]:
    return {
        "iLink-App-Id": ILINK_APP_ID,
        "iLink-App-ClientVersion": _client_version(),
    }


def _base_info() -> dict[str, str]:
    return {"channel_version": CHANNEL_VERSION, "bot_agent": BOT_AGENT}


def _auth_headers(token: str) -> dict[str, str]:
    value = str(token or "").strip()
    if not value:
        raise ChannelError("微信 ClawBot 渠道缺少 bot_token")
    return {
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "User-Agent": "OneTap-WechatClawBot/1.0",
        "AuthorizationType": "ilink_bot_token",
        "Authorization": f"Bearer {value}",
        "X-WECHAT-UIN": _wechat_uin(),
        **_app_headers(),
    }


def qr_data_url(content: str) -> str:
    """把二维码内容渲染成 data URL，供前端直接展示。"""
    buffer = io.BytesIO()
    segno.make(content, error="m").save(buffer, kind="png", scale=6, border=2)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


async def request_login_qrcode(base_url: str = DEFAULT_BASE_URL) -> dict[str, Any]:
    """申请登录二维码，返回 qrcode 轮询令牌与二维码内容。"""
    base = str(base_url or DEFAULT_BASE_URL).strip().rstrip("/")
    headers = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "X-WECHAT-UIN": _wechat_uin(),
        **_app_headers(),
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            f"{base}/ilink/bot/get_bot_qrcode",
            params={"bot_type": 3},
            headers=headers,
            json={"local_token_list": []},
        )
        if response.status_code >= 400:
            response = await client.get(
                f"{base}/ilink/bot/get_bot_qrcode",
                params={"bot_type": 3},
                headers=_app_headers(),
            )
    payload = _json(response)
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    qrcode = str(data.get("qrcode") or "")
    content = str(data.get("qrcode_img_content") or data.get("qrcode_url") or "")
    if response.status_code >= 400 or not qrcode or not content:
        detail = str(data.get("errmsg") or data.get("message") or "") or f"HTTP {response.status_code}"
        raise ChannelError(f"获取微信登录二维码失败：{detail}")
    return {"qrcode": qrcode, "content": content, "base_url": base}


async def poll_login_qrcode(
    qrcode: str, base_url: str = DEFAULT_BASE_URL
) -> dict[str, Any]:
    """查询扫码状态；confirmed 时附带登录凭证。"""
    base = str(base_url or DEFAULT_BASE_URL).strip().rstrip("/")
    async with httpx.AsyncClient(timeout=40.0) as client:
        response = await client.get(
            f"{base}/ilink/bot/get_qrcode_status",
            params={"qrcode": qrcode},
            headers=_app_headers(),
        )
    payload = _json(response)
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    status = str(data.get("status") or "wait").strip().lower()
    return {
        "status": status,
        "bot_token": str(data.get("bot_token") or ""),
        "account_id": str(data.get("ilink_bot_id") or ""),
        "user_id": str(data.get("ilink_user_id") or ""),
        "base_url": str(data.get("baseurl") or base),
    }


async def fetch_updates(
    bot_token: str,
    base_url: str = DEFAULT_BASE_URL,
    buffer: str = "",
    timeout: float = 15.0,
) -> dict[str, Any]:
    """长轮询收消息；用于扫码后捕获第一条会话消息的 context_token。"""
    base = str(base_url or DEFAULT_BASE_URL).strip().rstrip("/")
    body = {
        "get_updates_buf": buffer or "",
        "base_info": _base_info(),
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            f"{base}/ilink/bot/getupdates",
            headers=_auth_headers(bot_token),
            json=body,
        )
    payload = _json(response)
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    messages = data.get("msgs") or data.get("messages") or []
    return {
        "ret": data.get("ret", payload.get("ret", 0)),
        "errcode": data.get("errcode", payload.get("errcode")),
        "errmsg": str(data.get("errmsg") or payload.get("errmsg") or ""),
        "status_code": response.status_code,
        "messages": messages if isinstance(messages, list) else [],
        "buffer": str(data.get("get_updates_buf") or buffer or ""),
    }


async def notify_start(
    bot_token: str, base_url: str = DEFAULT_BASE_URL
) -> None:
    """告知后端机器人已上线，便于消息路由。"""
    base = str(base_url or DEFAULT_BASE_URL).strip().rstrip("/")
    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.post(
            f"{base}/ilink/bot/msg/notifystart",
            headers=_auth_headers(bot_token),
            json={"base_info": _base_info()},
        )


class WeChatClawBotChannel(BaseChannel):
    type = "wechatclawbot"

    def _base_url(self) -> str:
        return str(self.config.get("base_url") or DEFAULT_BASE_URL).strip().rstrip("/")

    def _headers(self) -> dict[str, str]:
        return _auth_headers(str(self.secrets.get("bot_token") or ""))

    def _body(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = dict(payload)
        base_info = dict(result.get("base_info") or {})
        base_info.setdefault("channel_version", CHANNEL_VERSION)
        base_info.setdefault("bot_agent", BOT_AGENT)
        result["base_info"] = base_info
        return result

    def _account_id(self) -> str:
        value = str(self.config.get("account_id") or "").strip()
        if not value:
            raise ChannelError("微信 ClawBot 渠道缺少 account_id")
        return value

    def _to_user(self) -> str:
        value = str(self.config.get("to_user") or "").strip()
        if not value:
            raise ChannelError("微信 ClawBot 渠道缺少 to_user")
        return value

    def _message_envelope(self, item: dict[str, Any] | list[dict[str, Any]]) -> dict[str, Any]:
        items = item if isinstance(item, list) else [item]
        message = {
            "from_user_id": self._account_id(),
            "to_user_id": self._to_user(),
            "client_id": f"onetap-{uuid.uuid4()}",
            "message_type": 2,
            "message_state": 2,
            "item_list": items,
        }
        context_token = str(self.config.get("context_token") or "").strip()
        if context_token:
            message["context_token"] = context_token
        return {"msg": message}

    async def _refresh_context_token(self) -> bool:
        """尝试从最近的上行消息里取一个新的 context_token。

        iLink 的 context_token 只在用户最近一次发消息后约 1 小时内有效，
        过期时 sendmessage 会返回 ret=-2「prepare failed」。
        """
        try:
            result = await fetch_updates(
                str(self.secrets.get("bot_token") or ""),
                self._base_url(),
                "",
                timeout=10.0,
            )
        except httpx.HTTPError:
            return False
        for item in reversed(result.get("messages") or []):
            token = str(item.get("context_token") or "").strip()
            if not token:
                continue
            self.config["context_token"] = token
            from_user = str(item.get("from_user_id") or "").strip()
            if from_user:
                self.config["to_user"] = from_user
            logger.info("微信 ClawBot 已刷新 context_token")
            return True
        return False

    async def _post_send(self, body: dict[str, Any]) -> str | None:
        base = self._base_url()
        headers = self._headers()
        last_error = ""
        async with httpx.AsyncClient(timeout=25.0) as client:
            for url in (
                f"{base}/ilink/bot/sendmessage",
                f"{base}/ilink/bot/sendmessage?bot_type=3",
            ):
                try:
                    response = await client.post(url, headers=headers, json=self._body(body))
                except httpx.HTTPError as exc:
                    last_error = str(exc)
                    continue
                payload = _json(response)
                if _send_ok(response, payload):
                    return str(payload.get("msg_id") or payload.get("message_id") or "") or None
                last_error = (
                    str(payload.get("errmsg") or payload.get("message") or "")
                    or f"HTTP {response.status_code}"
                )
        raise ChannelError(f"微信 ClawBot 发送失败：{last_error}")

    @staticmethod
    def _text_item(text: str) -> dict[str, Any]:
        return {"type": 1, "text_item": {"text": text}}

    async def _upload_image(self, data: bytes, to_user: str) -> tuple[str, str, int] | None:
        aeskey = os.urandom(16)
        filekey = os.urandom(16).hex()
        rawsize = len(data)
        filesize = ((rawsize + 1 + 15) // 16) * 16
        body = self._body(
            {
                "filekey": filekey,
                "media_type": 1,
                "to_user_id": to_user,
                "rawsize": rawsize,
                "rawfilemd5": hashlib.md5(data).hexdigest(),
                "filesize": filesize,
                "no_need_thumb": True,
                "aeskey": aeskey.hex(),
            }
        )
        headers = self._headers()
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self._base_url()}/ilink/bot/getuploadurl",
                    headers=headers,
                    json=body,
                )
        except httpx.HTTPError:
            return None
        payload = _json(response)
        if response.status_code >= 400:
            return None
        upload_param = payload.get("upload_param") or payload.get("uploadParam")
        upload_full_url = payload.get("upload_full_url") or payload.get("uploadFullUrl")

        cipher = Cipher(algorithms.AES(aeskey), modes.ECB())
        padder = PKCS7(128).padder()
        padded = padder.update(data) + padder.finalize()
        encryptor = cipher.encryptor()
        ciphertext = encryptor.update(padded) + encryptor.finalize()
        if upload_full_url:
            upload_url = str(upload_full_url)
        elif upload_param:
            upload_url = (
                f"{CDN_BASE_URL}/upload?encrypted_query_param="
                f"{quote(str(upload_param), safe='')}&filekey={quote(filekey, safe='')}"
            )
        else:
            return None

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    upload_url,
                    headers={"Content-Type": "application/octet-stream"},
                    content=ciphertext,
                )
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        download_param = response.headers.get("x-encrypted-param")
        if not download_param:
            return None
        aeskey_b64 = base64.b64encode(aeskey.hex().encode()).decode()
        return str(download_param), aeskey_b64, len(ciphertext)

    async def send(self, message: ChannelMessage) -> str | None:
        title = (message.title or "").strip()
        text = (message.text or message.markdown or "").strip()
        # 微信只有一段纯文本，标题要自己带上；正文里已含标题时不重复
        if title and text and not text.startswith(title):
            text = f"{title}\n{text}"
        elif not text:
            text = title
        if message.link:
            text = f"{text}\n{message.link}"
        text_item = self._text_item(text)
        image_item: dict[str, Any] | None = None

        if message.image_url:
            image = await fetch_image(message.image_url)
            if image is not None:
                uploaded = await self._upload_image(image[0], self._to_user())
                if uploaded is not None:
                    download_param, aeskey_b64, size = uploaded
                    image_item = {
                        "type": 2,
                        "image_item": {
                            "media": {
                                "encrypt_query_param": download_param,
                                "aes_key": aeskey_b64,
                                "encrypt_type": 1,
                            },
                            "mid_size": size,
                        },
                    }

        try:
            return await self._deliver(text_item, image_item)
        except ChannelError as exc:
            if not _is_stale_session_error(str(exc)):
                raise
            if not await self._refresh_context_token():
                raise ChannelError(STALE_SESSION_HINT) from exc
            return await self._deliver(text_item, image_item)

    async def _deliver(
        self, text_item: dict[str, Any], image_item: dict[str, Any] | None
    ) -> str | None:
        if image_item is not None:
            try:
                return await self._post_send(
                    self._message_envelope([text_item, image_item])
                )
            except ChannelError as exc:
                if _is_stale_session_error(str(exc)):
                    raise
                logger.info("微信 ClawBot 图文合并发送失败，改为分开发送：%s", exc)

        message_id = await self._post_send(self._message_envelope(text_item))
        if image_item is not None:
            try:
                await self._post_send(self._message_envelope(image_item))
            except ChannelError:
                pass
        return message_id


STALE_SESSION_HINT = (
    "微信 ClawBot 拒收：会话已过期或下行配额已用尽。"
    "iLink 只在用户最近互动后的一段时间内允许有限条主动推送，"
    "请在微信里给机器人发一条消息，稍后会自动重试。"
)


def _is_stale_session_error(message: str) -> bool:
    text = (message or "").strip().lower()
    return "prepare failed" in text or "unknown error" in text


def _send_ok(response: httpx.Response, payload: dict[str, Any]) -> bool:
    if response.status_code >= 400:
        return False
    if not payload:
        return True
    for key in ("errcode", "code", "ret", "result_code", "status_code"):
        if key in payload:
            value = payload.get(key)
            return str(value).strip().lower() in {"0", "ok", "success", "succeed"}
    if payload.get("success") is False:
        return False
    if payload.get("success") is True:
        return True
    state = str(payload.get("status") or payload.get("state") or "").lower()
    if state in {"failed", "error", "denied"}:
        return False
    return True


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}
