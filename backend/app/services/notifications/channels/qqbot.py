"""QQ Bot 渠道。

通过 QQ 开放平台主动消息接口发送 Markdown 图文；
Markdown 权限不足时回退为纯文本。
"""

from __future__ import annotations

import asyncio
import time

import httpx

from app.services.notifications.channels.base import BaseChannel, ChannelError, ChannelMessage

TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
API_BASE = "https://api.sgroup.qq.com"
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}
_TOKEN_LOCK = asyncio.Lock()


def _token_key(app_id: str, secret: str) -> str:
    return f"{app_id}:{secret[:12]}"


class QQBotChannel(BaseChannel):
    type = "qqbot"

    async def _access_token(self) -> str:
        app_id = str(self.config.get("app_id") or "").strip()
        app_secret = str(self.secrets.get("app_secret") or "").strip()
        if not app_id or not app_secret:
            raise ChannelError("QQ Bot 渠道缺少 app_id 或 app_secret")
        key = _token_key(app_id, app_secret)
        cached = _TOKEN_CACHE.get(key)
        if cached and cached[1] > time.time() + 300:
            return cached[0]

        async with _TOKEN_LOCK:
            cached = _TOKEN_CACHE.get(key)
            if cached and cached[1] > time.time() + 300:
                return cached[0]
            try:
                async with httpx.AsyncClient(timeout=20.0) as client:
                    response = await client.post(
                        TOKEN_URL,
                        json={"appId": app_id, "clientSecret": app_secret},
                    )
            except httpx.HTTPError as exc:
                raise ChannelError(f"QQ Bot 鉴权失败：{exc}") from exc
            payload = _json(response)
            token = str(payload.get("access_token") or "")
            if response.status_code >= 400 or not token:
                raise ChannelError(
                    f"QQ Bot 鉴权失败：{payload.get('message') or response.status_code}"
                )
            expires = int(payload.get("expires_in") or 7200)
            _TOKEN_CACHE[key] = (token, time.time() + expires)
            return token

    def _resolve_target(self) -> tuple[str, bool]:
        raw = str(self.config.get("target") or "").strip()
        if not raw:
            raise ChannelError("QQ Bot 渠道缺少接收目标")
        if raw.lower().startswith("group:"):
            return raw[6:].strip(), True
        return raw, False

    async def _send(
        self, token: str, target: str, is_group: bool, body: dict
    ) -> str | None:
        path = (
            f"/v2/groups/{target}/messages"
            if is_group
            else f"/v2/users/{target}/messages"
        )
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(
                    f"{API_BASE}{path}",
                    headers={
                        "Authorization": f"QQBot {token}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
        except httpx.HTTPError as exc:
            raise ChannelError(f"QQ Bot 发送失败：{exc}") from exc
        payload = _json(response)
        if response.status_code >= 400:
            raise ChannelError(
                f"QQ Bot 发送失败：{payload.get('message') or response.status_code}"
            )
        return str(payload.get("id") or "") or None

    async def send(self, message: ChannelMessage) -> str | None:
        token = await self._access_token()
        target, is_group = self._resolve_target()
        markdown = message.markdown or message.text
        content = f"**{message.title}**\n\n{markdown}"
        if message.image_url:
            content += f"\n\n![封面]({message.image_url})"
        if message.link:
            content += f"\n\n[查看详情]({message.link})"

        try:
            return await self._send(
                token,
                target,
                is_group,
                {"markdown": {"content": content}, "msg_type": 2},
            )
        except ChannelError as exc:
            # Markdown 需要平台授权；未授权时退回纯文本。
            plain = f"{message.title}\n{markdown}"
            if message.image_url:
                plain += f"\n{message.image_url}"
            if message.link:
                plain += f"\n{message.link}"
            try:
                return await self._send(
                    token,
                    target,
                    is_group,
                    {"content": plain, "msg_type": 0},
                )
            except ChannelError:
                raise exc


def _json(response: httpx.Response) -> dict:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}
