"""通知中心 API Schema。"""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: str
    type: str
    severity: str
    domain: Optional[str] = None
    subject_key: Optional[str] = None
    resource_subscription_id: Optional[int] = None
    title: str
    markdown: str
    image_url: Optional[str] = None
    image_proxy_path: Optional[str] = None
    link: Optional[str] = None
    payload: dict = Field(default_factory=dict)
    read_at: Optional[datetime] = None
    created_at: datetime


class NotificationListOut(BaseModel):
    items: list[NotificationOut]
    total: int
    unread: int


class UnreadCountOut(BaseModel):
    unread: int


class ChannelConfigOut(BaseModel):
    app_id: Optional[str] = None
    receive_id: Optional[str] = None
    receive_id_type: Optional[str] = None
    base_url: Optional[str] = None
    account_id: Optional[str] = None
    target: Optional[str] = None
    to_user: Optional[str] = None


class ChannelOut(BaseModel):
    id: int
    name: str
    type: str
    enabled: bool
    config: dict
    secret_set: bool
    created_at: datetime
    updated_at: datetime


class ChannelUpsert(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    type: Literal["feishu", "dingtalk_app", "wechatclawbot", "qqbot"]
    enabled: bool = True
    app_id: Optional[str] = Field(None, max_length=200)
    app_key: Optional[str] = Field(None, max_length=200)
    app_secret: Optional[str] = Field(None, max_length=500)
    robot_code: Optional[str] = Field(None, max_length=200)
    card_template_id: Optional[str] = Field(None, max_length=200)
    receive_id: Optional[str] = Field(None, max_length=200)
    receive_id_type: Optional[str] = Field(None, max_length=32)
    base_url: Optional[str] = Field(None, max_length=500)
    bot_token: Optional[str] = Field(None, max_length=1000)
    account_id: Optional[str] = Field(None, max_length=200)
    to_user: Optional[str] = Field(None, max_length=200)
    context_token: Optional[str] = Field(None, max_length=500)
    target: Optional[str] = Field(None, max_length=200)


class ChannelTestResult(BaseModel):
    ok: bool
    message: str


class WeChatClawbotLoginStart(BaseModel):
    name: Optional[str] = Field(None, max_length=80)
    channel_id: Optional[int] = None


class WeChatClawbotLoginOut(BaseModel):
    session_id: str
    qrcode_image: str
    expires_in: int


class WeChatClawbotLoginStatusOut(BaseModel):
    status: Literal["wait", "scaned", "need_message", "expired", "done"]
    message: Optional[str] = None
    channel: Optional[ChannelOut] = None


class NotificationSettingsOut(BaseModel):
    event_types: list[str]


class NotificationSettingsUpdate(BaseModel):
    event_types: Optional[list[str]] = None


class EventTypeOut(BaseModel):
    value: str
    label: str


class SubscriptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    domain: str
    subject_key: str
    kind: str
    title: str
    year: Optional[str] = None
    poster: Optional[str] = None
    status: str
    revision: int
    notify_enabled: bool
    last_seen: dict
    created_at: datetime
    updated_at: datetime
    # subscribed / downloading / downloaded / organizing / organized /
    # completed / failed / removed
    stage: str = "subscribed"
    last_event_type: Optional[str] = None
    last_event_at: Optional[datetime] = None
