"""钉钉自定义机器人渠道。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from app.services.notifications.channels.base import BaseChannel, ChannelError, ChannelMessage
from app.services.notifications.details import render_markdown


class DingTalkChannel(BaseChannel):
    type = "dingtalk"

    def _signed_url(self) -> str:
        webhook = str(self.config.get("webhook") or "").strip()
        if not webhook.startswith(("http://", "https://")):
            raise ChannelError("钉钉渠道缺少有效的 Webhook 地址")
        secret = str(self.secrets.get("secret") or "").strip()
        if not secret:
            return webhook
        timestamp = int(time.time() * 1000)
        string_to_sign = f"{timestamp}\n{secret}".encode()
        digest = hmac.new(
            secret.encode(), string_to_sign, digestmod=hashlib.sha256
        ).digest()
        signature = base64.b64encode(digest).decode()
        parsed = urlsplit(webhook)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query.update({"timestamp": str(timestamp), "sign": signature})
        return urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
        )

    async def send(self, message: ChannelMessage) -> str | None:
        title, content = render_markdown(message)
        parts = [f"### {title}", content]
        if message.link:
            parts.append(f"[查看详情]({message.link})")
        body = {
            "msgtype": "markdown",
            "markdown": {
                "title": title[:64] or "OneTap 通知",
                "text": "\n\n".join(part for part in parts if part),
            },
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(self._signed_url(), json=body)
        except httpx.HTTPError as exc:
            raise ChannelError(f"钉钉发送失败：{exc}") from exc
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.status_code >= 400:
            raise ChannelError(f"钉钉发送失败：HTTP {response.status_code}")
        if isinstance(payload, dict) and payload.get("errcode") not in (0, None):
            raise ChannelError(
                f"钉钉发送失败：{payload.get('errmsg') or payload.get('errcode')}"
            )
        return None
