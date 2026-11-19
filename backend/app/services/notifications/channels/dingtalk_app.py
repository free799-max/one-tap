"""钉钉企业内部应用机器人渠道。

用应用凭证换 access_token，优先投放互动卡片（封面 + 字段 + 详情按钮）；
没配卡片模板时退回机器人 ActionCard / Markdown 消息，正文排版与飞书卡片一致。
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from uuid import uuid4

import httpx

from app.services.notifications.channels.base import BaseChannel, ChannelError, ChannelMessage
from app.services.notifications.details import (
    content_text,
    detail_markdown,
    presentation_markdown,
    render_markdown,
    summary_line,
)

API_BASE = "https://api.dingtalk.com"
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}
_TOKEN_LOCK = asyncio.Lock()
_RECEIVE_ID_TYPES = {"user_id", "open_conversation_id"}
_SPACE_TYPES = {"user_id": "IM_ROBOT", "open_conversation_id": "IM_GROUP"}


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {"message": f"HTTP {response.status_code}"}
    return payload if isinstance(payload, dict) else {"message": "响应格式错误"}


def _clip(text: Any, limit: int) -> str:
    return str(text or "").strip()[:limit]


class DingTalkAppChannel(BaseChannel):
    """钉钉应用机器人（企业内部应用）。

    配置项：``app_key`` / ``robot_code`` / ``receive_id`` /
    ``receive_id_type``（user_id | open_conversation_id）/ ``card_template_id``；
    密钥：``app_secret``。
    """

    type = "dingtalk_app"

    async def _access_token(self) -> str:
        app_key = _clip(self.config.get("app_key"), 200)
        app_secret = _clip(self.secrets.get("app_secret"), 500)
        if not app_key or not app_secret:
            raise ChannelError("钉钉应用渠道缺少 AppKey 或 AppSecret")
        cache_key = f"{app_key}:{app_secret[:12]}"
        cached = _TOKEN_CACHE.get(cache_key)
        if cached and cached[1] > time.time() + 60:
            return cached[0]

        async with _TOKEN_LOCK:
            cached = _TOKEN_CACHE.get(cache_key)
            if cached and cached[1] > time.time() + 60:
                return cached[0]
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.post(
                        f"{API_BASE}/v1.0/oauth2/accessToken",
                        json={"appKey": app_key, "appSecret": app_secret},
                    )
            except httpx.HTTPError as exc:
                raise ChannelError(f"钉钉获取 access_token 失败：{exc}") from exc
            payload = _json(response)
            token = str(payload.get("accessToken") or "")
            if not token:
                message = payload.get("message") or payload.get("code") or response.status_code
                raise ChannelError(f"钉钉获取 access_token 失败：{message}")
            try:
                expire_in = int(payload.get("expireIn") or 7200)
            except (TypeError, ValueError):
                expire_in = 7200
            _TOKEN_CACHE[cache_key] = (token, time.time() + expire_in)
            return token

    def _target(self) -> tuple[str, str, str]:
        receive_id = _clip(self.config.get("receive_id"), 200)
        receive_id_type = _clip(self.config.get("receive_id_type"), 32) or "user_id"
        if receive_id_type not in _RECEIVE_ID_TYPES:
            receive_id_type = "user_id"
        if not receive_id:
            raise ChannelError("钉钉应用渠道缺少接收目标（userId 或群会话 ID）")
        return receive_id, receive_id_type, _SPACE_TYPES[receive_id_type]

    def _robot_code(self) -> str:
        robot_code = _clip(self.config.get("robot_code"), 200)
        if not robot_code:
            raise ChannelError("钉钉应用渠道缺少机器人编码 robotCode")
        return robot_code

    def _card_params(self, message: ChannelMessage) -> dict[str, str]:
        presentation = (message.payload or {}).get("presentation")
        if isinstance(presentation, dict):
            title, content = presentation_markdown(message)
            subtitle = str(presentation.get("subtitle") or "")
            fields = ""
        else:
            title = message.title.strip()
            content = content_text(message)
            subtitle = summary_line(message)
            fields = detail_markdown(message)
        return {
            "title": _clip(title, 200),
            "subtitle": _clip(subtitle, 200),
            "fields": _clip(fields, 1000),
            "content": _clip(content, 1000),
            "image": _clip(message.image_url, 1000),
            "link": _clip(message.link, 1000),
        }

    async def _send_card(
        self, client: httpx.AsyncClient, token: str, message: ChannelMessage
    ) -> str | None:
        template_id = _clip(self.config.get("card_template_id"), 200)
        if not template_id:
            return None
        receive_id, receive_id_type, space_type = self._target()
        robot_code = self._robot_code()
        out_track_id = f"onetap-{uuid4().hex}"
        body: dict[str, Any] = {
            "cardTemplateId": template_id,
            "outTrackId": out_track_id,
            "cardData": {"cardParamMap": self._card_params(message)},
            "openSpaceId": f"dtv1.card//{space_type}.{receive_id}",
            "userIdType": 1,
        }
        if receive_id_type == "open_conversation_id":
            body["imGroupOpenSpaceModel"] = {"supportForward": True}
            body["imGroupOpenDeliverModel"] = {"robotCode": robot_code}
        else:
            body["imRobotOpenSpaceModel"] = {
                "supportForward": True,
                "notification": {"alertContent": _clip(message.title, 200)},
            }
            body["imRobotOpenDeliverModel"] = {
                "spaceType": "IM_ROBOT",
                "robotCode": robot_code,
            }
        try:
            response = await client.post(
                f"{API_BASE}/v1.0/card/instances/createAndDeliver",
                headers={"x-acs-dingtalk-access-token": token},
                json=body,
            )
        except httpx.HTTPError as exc:
            raise ChannelError(f"钉钉卡片发送失败：{exc}") from exc
        payload = _json(response)
        if response.status_code >= 400 or payload.get("success") is False:
            message_text = payload.get("message") or payload.get("code") or f"HTTP {response.status_code}"
            raise ChannelError(f"钉钉卡片发送失败：{message_text}")
        result = payload.get("result") or {}
        return _clip(result.get("outTrackId"), 200) or out_track_id

    def _message_parts(self, message: ChannelMessage) -> tuple[str, str]:
        """标题 + Markdown 正文，字段排版与飞书卡片保持一致。

        钉钉 Markdown 里图片只能单独占一段、无法和字段并排，
        所以不附封面，只保留文字信息。
        """
        return render_markdown(message)

    def _message_text(self, message: ChannelMessage) -> str:
        title, body = self._message_parts(message)
        return "\n\n".join(part for part in (f"### {title}", body) if part)

    def _action_card_param(self, message: ChannelMessage) -> str:
        # ActionCard 的按钮是模板占位符，没有链接时不能发，
        # 否则卡片底部会显示字面量 #singleTitle#
        if not message.link:
            raise ChannelError("ActionCard 需要详情链接")
        param: dict[str, str] = {
            "title": _clip(message.title, 64) or "OneTap 通知",
            "text": self._message_text(message),
            "singleTitle": "查看详情",
            "singleURL": message.link,
        }
        return json.dumps(param, ensure_ascii=False)

    def _markdown_param(self, message: ChannelMessage) -> str:
        text = self._message_text(message)
        if message.link:
            text = f"{text}\n\n[查看详情]({message.link})"
        return json.dumps(
            {
                "title": _clip(message.title, 64) or "OneTap 通知",
                "text": text,
            },
            ensure_ascii=False,
        )

    async def _send_robot(
        self,
        client: httpx.AsyncClient,
        token: str,
        message: ChannelMessage,
        msg_key: str,
        msg_param: str,
    ) -> str | None:
        receive_id, receive_id_type, _ = self._target()
        common = {
            "robotCode": self._robot_code(),
            "msgKey": msg_key,
            "msgParam": msg_param,
        }
        if receive_id_type == "open_conversation_id":
            url = f"{API_BASE}/v1.0/robot/groupMessages/send"
            body = {**common, "openConversationId": receive_id}
        else:
            url = f"{API_BASE}/v1.0/robot/oToMessages/batchSend"
            body = {**common, "userIds": [receive_id]}
        try:
            response = await client.post(
                url,
                headers={"x-acs-dingtalk-access-token": token},
                json=body,
            )
        except httpx.HTTPError as exc:
            raise ChannelError(f"钉钉消息发送失败：{exc}") from exc
        payload = _json(response)
        if response.status_code >= 400 or payload.get("code"):
            message_text = payload.get("message") or payload.get("code") or f"HTTP {response.status_code}"
            raise ChannelError(f"钉钉消息发送失败：{message_text}")
        return _clip(payload.get("processQueryKey"), 200) or None

    async def _send_message(
        self, client: httpx.AsyncClient, token: str, message: ChannelMessage
    ) -> str | None:
        if not message.link:
            # 没有详情链接时按钮占位符无法替换，直接发 Markdown
            return await self._send_robot(
                client, token, message, "sampleMarkdown", self._markdown_param(message)
            )
        try:
            return await self._send_robot(
                client, token, message, "sampleActionCard", self._action_card_param(message)
            )
        except ChannelError as exc:
            # 部分会话不支持 ActionCard，退回 Markdown，内容排版保持一致
            text = str(exc)
            if "msgKey" not in text and "不支持" not in text:
                raise
            return await self._send_robot(
                client, token, message, "sampleMarkdown", self._markdown_param(message)
            )

    async def send(self, message: ChannelMessage) -> str | None:
        token = await self._access_token()
        async with httpx.AsyncClient(timeout=20.0) as client:
            message_id = await self._send_card(client, token, message)
            if message_id:
                return message_id
            return await self._send_message(client, token, message)
