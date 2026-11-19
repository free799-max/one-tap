"""飞书应用机器人渠道。

使用应用凭证换取 tenant_access_token，上传封面换 image_key，
再发送一张 interactive 卡片，实现图文一条消息。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

import httpx

from app.services.notifications.assets import fetch_image
from app.services.notifications.channels.base import BaseChannel, ChannelError, ChannelMessage
from app.services.notifications.details import (
    comic_details,
    music_details,
    novel_details,
    video_details,
)
from app.services.notifications.events import EVENT_LABELS

API_BASE = "https://open.feishu.cn/open-apis"
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}
_TOKEN_LOCK = asyncio.Lock()
_ALLOWED_RECEIVE_ID_TYPES = {"chat_id", "open_id", "user_id", "union_id", "email"}
_DOMAIN_LABELS = {
    "video": "视频",
    "comic": "漫画",
    "novel": "小说",
    "music": "音乐",
}


def _channel_key(config: dict, secrets: dict) -> str:
    return f"{config.get('app_id', '')}:{secrets.get('app_secret', '')[:12]}"


class FeishuChannel(BaseChannel):
    type = "feishu"

    async def _tenant_token(self) -> str:
        app_id = str(self.config.get("app_id") or "").strip()
        app_secret = str(self.secrets.get("app_secret") or "").strip()
        if not app_id or not app_secret:
            raise ChannelError("飞书渠道缺少 app_id 或 app_secret")
        key = _channel_key(self.config, self.secrets)
        cached = _TOKEN_CACHE.get(key)
        if cached and cached[1] > time.time() + 60:
            return cached[0]

        async with _TOKEN_LOCK:
            cached = _TOKEN_CACHE.get(key)
            if cached and cached[1] > time.time() + 60:
                return cached[0]
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.post(
                        f"{API_BASE}/auth/v3/tenant_access_token/internal",
                        json={"app_id": app_id, "app_secret": app_secret},
                    )
            except httpx.HTTPError as exc:
                raise ChannelError(f"飞书鉴权请求失败：{exc}") from exc
            payload = _json(response)
            if payload.get("code") != 0:
                raise ChannelError(
                    f"飞书鉴权失败：{payload.get('msg') or response.status_code}"
                )
            token = str(payload.get("tenant_access_token") or "")
            expire = int(payload.get("expire") or 7200)
            if not token:
                raise ChannelError("飞书未返回 tenant_access_token")
            _TOKEN_CACHE[key] = (token, time.time() + expire)
            return token

    async def _upload_image(self, token: str, image_url: str) -> str | None:
        image = await fetch_image(image_url)
        if image is None:
            return None
        data, content_type = image
        suffix = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
            "image/gif": ".gif",
        }.get(content_type, ".jpg")
        filename = f"cover{suffix}"
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{API_BASE}/im/v1/images",
                    headers={"Authorization": f"Bearer {token}"},
                    data={"image_type": "message"},
                    files={"image": (filename, data, content_type)},
                )
        except httpx.HTTPError as exc:
            raise ChannelError(f"飞书上传封面失败：{exc}") from exc
        payload = _json(response)
        if payload.get("code") != 0:
            raise ChannelError(
                f"飞书上传封面失败：{payload.get('msg') or response.status_code}"
            )
        image_key = (payload.get("data") or {}).get("image_key")
        return str(image_key) if image_key else None

    async def _upload_presentation_images(
        self, token: str, items: list[dict[str, Any]]
    ) -> list[str | None]:
        """并发上传候选封面；单张失败只在那一行退化成纯文本。"""
        semaphore = asyncio.Semaphore(6)

        async def upload(item: dict[str, Any]) -> str | None:
            url = str((item or {}).get("image") or "").strip()
            if not url.startswith(("http://", "https://")):
                return None
            async with semaphore:
                try:
                    return await self._upload_image(token, url)
                except Exception:  # noqa: BLE001 - 封面缺失不该影响整张卡片
                    return None

        return list(await asyncio.gather(*(upload(item) for item in items)))

    def _presentation_card(
        self,
        message: ChannelMessage,
        presentation: dict[str, Any],
        image_keys: list[str | None],
    ) -> dict[str, Any]:
        elements: list[dict[str, Any]] = []
        subtitle = str(presentation.get("subtitle") or "").strip()
        if subtitle:
            elements.append(
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": subtitle},
                }
            )
        items = [item for item in presentation.get("items") or [] if isinstance(item, dict)]
        for index, item in enumerate(items, 1):
            title = str(item.get("title") or "").strip() or "未命名资源"
            line = f"**{index}. {title}**"
            author = str(item.get("subtitle") or "").strip()
            if author:
                line += f" — {author}"
            meta = str(item.get("meta") or "").strip()
            if meta:
                line += f"\n{meta}"
            for detail in item.get("details") or []:
                detail = str(detail).strip()
                if detail:
                    line += f"\n{detail}"
            image_key = image_keys[index - 1] if index - 1 < len(image_keys) else None
            if image_key:
                elements.append(
                    {
                        "tag": "column_set",
                        "flex_mode": "none",
                        "horizontal_spacing": "default",
                        "columns": [
                            {
                                "tag": "column",
                                "width": "64px",
                                "vertical_align": "center",
                                "elements": [
                                    {
                                        "tag": "img",
                                        "img_key": image_key,
                                        "alt": {"tag": "plain_text", "content": title},
                                    }
                                ],
                            },
                            {
                                "tag": "column",
                                "width": "weighted",
                                "weight": 5,
                                "vertical_align": "center",
                                "elements": [
                                    {
                                        "tag": "div",
                                        "text": {"tag": "lark_md", "content": line},
                                    }
                                ],
                            },
                        ],
                    }
                )
            else:
                elements.append(
                    {
                        "tag": "div",
                        "text": {"tag": "lark_md", "content": line},
                    }
                )

        note = str(presentation.get("note") or "").strip()
        if note:
            elements.append({"tag": "hr"})
            elements.append(
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": note},
                }
            )
        if message.link:
            elements.append(
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "查看详情"},
                            "type": "primary",
                            "url": message.link,
                        }
                    ],
                }
            )
        header = str(presentation.get("header") or message.title).strip() or message.title
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "blue",
                "title": {"tag": "plain_text", "content": header[:200]},
            },
            "elements": elements,
        }

    def _card(self, message: ChannelMessage, image_key: str | None) -> dict[str, Any]:
        elements: list[dict[str, Any]] = []
        title = message.title.strip()
        summary = []
        if message.event_type:
            summary.append(
                "通知汇总"
                if message.event_type == "batch"
                else EVENT_LABELS.get(message.event_type, message.event_type)
            )
        if message.domain:
            summary.append(_DOMAIN_LABELS.get(message.domain, message.domain))
        options = message.payload.get("options") or {}
        creator = str(options.get("artist") or options.get("author") or "").strip()
        if creator:
            summary.append(creator)
        title_text = f"**{title}**"
        if summary:
            title_text += f"\n{' · '.join(summary)}"
        shown_detail = ""
        if message.domain == "music":
            title_text = music_details(message)
        elif message.domain == "novel":
            title_text, shown_detail = novel_details(message)
        elif message.domain == "video":
            title_text, shown_detail = video_details(message)
        elif message.domain == "comic":
            details = comic_details(message)
            if details:
                # 漫画卡片用字段替换默认的「事件 · 类型 · 作者」摘要行
                title_text = details

        if image_key:
            elements.append(
                {
                    "tag": "column_set",
                    "flex_mode": "none",
                    "horizontal_spacing": "default",
                    "columns": [
                        {
                            "tag": "column",
                            # 固定像素宽度：桌面端和手机端封面大小一致，
                            # 用权重会在窄屏把封面压得过小
                            "width": "120px",
                            "vertical_align": "center",
                            "elements": [
                                {
                                    "tag": "img",
                                    "img_key": image_key,
                                    "alt": {"tag": "plain_text", "content": title},
                                }
                            ],
                        },
                        {
                            "tag": "column",
                            "width": "weighted",
                            "weight": 4,
                            "vertical_align": "center",
                            "elements": [
                                {
                                    "tag": "div",
                                    "text": {"tag": "lark_md", "content": title_text},
                                }
                            ],
                        },
                    ],
                }
            )
        else:
            elements.append(
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": title_text},
                }
            )

        content = (message.markdown or message.text).strip()
        for repeated_title in (f"**{title}**", title):
            if content.startswith(repeated_title):
                content = content[len(repeated_title) :].strip()
                break
        paragraphs = []
        for paragraph in content.split("\n\n"):
            paragraph = paragraph.strip()
            if not paragraph or paragraph == shown_detail:
                continue
            # 去掉「片名（年份）」这类重复标题行，字段区已经展示年份
            plain = re.sub(r"（\d{4}）$", "", paragraph).strip()
            if "\n" not in paragraph and (paragraph in title or plain in title):
                continue
            # 字段区已经有年份/类型，正文里「2007 年 · 电影」这类元信息不再重复
            if message.domain == "video" and re.fullmatch(
                r"\d{4} 年(?:\s*·\s*[^·]+)?", paragraph
            ):
                continue
            paragraphs.append(paragraph)
        content = "\n\n".join(paragraphs)
        if content:
            elements.append(
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": content},
                }
            )
        if message.link:
            elements.append(
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "查看详情"},
                            "type": "primary",
                            "url": message.link,
                        }
                    ],
                }
            )
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "blue",
                "title": {"tag": "plain_text", "content": title},
            },
            "elements": elements,
        }

    async def send(self, message: ChannelMessage) -> str | None:
        token = await self._tenant_token()
        presentation = (message.payload or {}).get("presentation")
        if isinstance(presentation, dict):
            image_keys = await self._upload_presentation_images(
                token, list(presentation.get("items") or [])
            )
            card = self._presentation_card(message, presentation, image_keys)
        else:
            image_key = None
            if message.image_url:
                try:
                    image_key = await self._upload_image(token, message.image_url)
                except ChannelError:
                    image_key = None
            card = self._card(message, image_key)

        receive_id = str(self.config.get("receive_id") or "").strip()
        receive_id_type = str(
            self.config.get("receive_id_type") or "chat_id"
        ).strip()
        if receive_id_type not in _ALLOWED_RECEIVE_ID_TYPES:
            receive_id_type = "chat_id"
        if not receive_id:
            raise ChannelError("飞书渠道缺少接收人 ID")

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(
                    f"{API_BASE}/im/v1/messages",
                    params={"receive_id_type": receive_id_type},
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "receive_id": receive_id,
                        "msg_type": "interactive",
                        "content": json.dumps(card, ensure_ascii=False),
                    },
                )
        except httpx.HTTPError as exc:
            raise ChannelError(f"飞书发送失败：{exc}") from exc
        payload = _json(response)
        if payload.get("code") != 0:
            raise ChannelError(
                f"飞书发送失败：{payload.get('msg') or response.status_code}"
            )
        return str((payload.get("data") or {}).get("message_id") or "") or None


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {"code": -1, "msg": f"HTTP {response.status_code}"}
    return payload if isinstance(payload, dict) else {"code": -1, "msg": "响应格式错误"}
