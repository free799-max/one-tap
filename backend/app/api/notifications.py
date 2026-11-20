"""通知中心 API：站内收件箱、渠道配置与订阅台账。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.custom_route import CustomAPIRoute
from app.core.exceptions import APIException
from app.core.security import get_current_user, require_admin
from app.database import get_db
from app.models.notification import (
    Notification,
    NotificationChannel,
    NotificationSetting,
    NotificationSyncState,
    ResourceSubscription,
)
from app.models.user import User
from app.schemas.notification import (
    ChannelOut,
    ChannelTestResult,
    ChannelUpsert,
    EventTypeOut,
    NotificationListOut,
    NotificationOut,
    NotificationSettingsOut,
    NotificationSettingsUpdate,
    SubscriptionOut,
    UnreadCountOut,
    WeChatClawbotLoginOut,
    WeChatClawbotLoginStart,
    WeChatClawbotLoginStatusOut,
)
from app.services.notifications.channels import (
    CHANNEL_LABELS,
    CHANNEL_TYPES,
    ChannelError,
    build_channel,
)
from app.services.notifications.assets import cover_path
from app.services.notifications.channels import wechatclawbot as claw
from app.services.notifications.crypto import decrypt_secrets, encrypt_secrets
from app.services.notifications.events import EVENT_LABELS, get_setting

router = APIRouter(
    prefix="/api/notifications", tags=["notifications"], route_class=CustomAPIRoute
)

CLAWBOT_LOGIN_PREFIX = "wechatclawbot.login."
CLAWBOT_LOGIN_TTL = 600


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _notification_out(record: Notification) -> NotificationOut:
    """老通知可能只落了原图地址，读的时候补一个可用的封面代理地址。"""
    item = NotificationOut.model_validate(record)
    if (
        not item.image_proxy_path
        and item.image_url
        and item.image_url.startswith(("http://", "https://"))
    ):
        item.image_proxy_path = cover_path(item.image_url)
    return item


# 订阅生命周期事件，用于推导订阅当前阶段
SUBSCRIPTION_STAGE_EVENTS = (
    "subscription.added",
    "subscription.updated",
    "download.completed",
    "organize.completed",
)


def _subscription_stage(
    record: ResourceSubscription, event_types: set[str]
) -> str:
    """按台账状态与已到达的最远生命周期阶段推导订阅阶段。"""
    if record.status in {"failed", "removed"}:
        return record.status
    if record.status in {"downloading", "organizing"}:
        return record.status
    if "organize.completed" in event_types:
        return "organized"
    if "download.completed" in event_types:
        return "downloaded"
    if record.status == "completed":
        return "completed"
    return "subscribed"


def _channel_out(record: NotificationChannel) -> ChannelOut:
    return ChannelOut(
        id=record.id,
        name=record.name,
        type=record.type,
        enabled=bool(record.enabled),
        config=dict(record.config or {}),
        secret_set=bool(record.secrets),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _build_config(data: ChannelUpsert) -> tuple[dict, dict]:
    config: dict = {}
    secrets: dict = {}
    if data.type == "feishu":
        config = {
            "app_id": (data.app_id or "").strip(),
            "receive_id": (data.receive_id or "").strip(),
            "receive_id_type": (data.receive_id_type or "chat_id").strip() or "chat_id",
        }
        secrets = {"app_secret": (data.app_secret or "").strip()}
        if not config["app_id"] or not config["receive_id"]:
            raise APIException("飞书渠道需要 app_id 和 receive_id", 400)
    elif data.type == "dingtalk_app":
        config = {
            "app_key": (data.app_key or "").strip(),
            "robot_code": (data.robot_code or "").strip(),
            "receive_id": (data.receive_id or "").strip(),
            "receive_id_type": (data.receive_id_type or "user_id").strip() or "user_id",
            "card_template_id": (data.card_template_id or "").strip(),
        }
        secrets = {"app_secret": (data.app_secret or "").strip()}
        if not config["app_key"] or not config["robot_code"] or not config["receive_id"]:
            raise APIException("钉钉应用机器人需要 AppKey、机器人编码和接收目标", 400)
    elif data.type == "wechatclawbot":
        config = {
            "base_url": (data.base_url or "https://ilinkai.weixin.qq.com").strip()
            or "https://ilinkai.weixin.qq.com",
            "account_id": (data.account_id or "").strip(),
            "to_user": (data.to_user or "").strip(),
            "context_token": (data.context_token or "").strip(),
        }
        secrets = {"bot_token": (data.bot_token or "").strip()}
        if not config["account_id"] or not config["to_user"]:
            raise APIException("微信 ClawBot 需要 account_id 和 to_user", 400)
    elif data.type == "qqbot":
        config = {
            "app_id": (data.app_id or "").strip(),
            "target": (data.target or "").strip(),
        }
        secrets = {"app_secret": (data.app_secret or "").strip()}
        if not config["app_id"] or not config["target"]:
            raise APIException("QQ Bot 需要 app_id 和 target", 400)
    else:
        raise APIException("不支持的通知渠道类型", 400)
    return config, secrets


def _required_secret(channel_type: str) -> str:
    return {
        "feishu": "app_secret",
        "dingtalk_app": "app_secret",
        "wechatclawbot": "bot_token",
        "qqbot": "app_secret",
    }.get(channel_type, "")


def _validate_events(event_types: list[str]) -> list[str]:
    unknown = [item for item in event_types if item not in EVENT_LABELS]
    if unknown:
        raise APIException(f"未知的通知事件：{', '.join(unknown)}", 400)
    return list(dict.fromkeys(event_types))


def _settings_out(record: NotificationSetting) -> NotificationSettingsOut:
    return NotificationSettingsOut(
        event_types=list(record.event_types or []),
    )


@router.get("", response_model=NotificationListOut)
async def list_notifications(
    unread: bool = False,
    domain: str | None = None,
    page: int = Query(1, ge=1),
    count: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """站内通知列表。"""
    conditions = [
        sa.or_(
            Notification.target_user_id.is_(None),
            Notification.target_user_id == current_user.id,
        )
    ]
    if unread:
        conditions.append(Notification.read_at.is_(None))
    if domain:
        conditions.append(Notification.domain == domain)

    total = (
        await db.execute(
            sa.select(sa.func.count(Notification.id)).where(*conditions)
        )
    ).scalar_one()
    unread_count = (
        await db.execute(
            sa.select(sa.func.count(Notification.id)).where(
                *conditions, Notification.read_at.is_(None)
            )
        )
    ).scalar_one()
    rows = (
        await db.execute(
            sa.select(Notification)
            .where(*conditions)
            .order_by(Notification.created_at.desc(), Notification.id.desc())
            .offset((page - 1) * count)
            .limit(count)
        )
    ).scalars()
    return NotificationListOut(
        items=[_notification_out(row) for row in rows],
        total=total,
        unread=unread_count,
    )


@router.get("/unread-count", response_model=UnreadCountOut)
async def unread_count(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    count = (
        await db.execute(
            sa.select(sa.func.count(Notification.id)).where(
                sa.or_(
                    Notification.target_user_id.is_(None),
                    Notification.target_user_id == current_user.id,
                ),
                Notification.read_at.is_(None),
            )
        )
    ).scalar_one()
    return UnreadCountOut(unread=count)


@router.post("/read-all", response_model=UnreadCountOut)
async def mark_all_read(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await db.execute(
        sa.update(Notification)
        .where(
            sa.or_(
                Notification.target_user_id.is_(None),
                Notification.target_user_id == current_user.id,
            ),
            Notification.read_at.is_(None),
        )
        .values(read_at=_now())
    )
    await db.commit()
    return UnreadCountOut(unread=0)


@router.post("/{notification_id}/read", response_model=NotificationOut)
async def mark_read(
    notification_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    record = await db.get(Notification, notification_id)
    if record is None or record.target_user_id not in (None, current_user.id):
        raise APIException("通知不存在", 404)
    if record.read_at is None:
        record.read_at = _now()
        await db.commit()
    return _notification_out(record)


@router.get("/event-types", response_model=list[EventTypeOut])
async def list_event_types(current_user: User = Depends(get_current_user)):
    return [
        EventTypeOut(value=value, label=label)
        for value, label in EVENT_LABELS.items()
    ]


@router.get("/subscriptions", response_model=list[SubscriptionOut])
async def list_subscriptions(
    domain: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    statement = sa.select(ResourceSubscription).order_by(
        ResourceSubscription.updated_at.desc()
    )
    if domain:
        statement = statement.where(ResourceSubscription.domain == domain)
    rows = list((await db.execute(statement.limit(limit))).scalars())

    latest_events: dict[int, Notification] = {}
    stage_event_types: dict[int, set[str]] = {}
    subscription_ids = [row.id for row in rows]
    if subscription_ids:
        events = (
            await db.execute(
                sa.select(Notification)
                .where(
                    Notification.resource_subscription_id.in_(subscription_ids),
                    Notification.type.in_(SUBSCRIPTION_STAGE_EVENTS),
                )
                .order_by(Notification.created_at.desc())
            )
        ).scalars()
        for event in events:
            if event.resource_subscription_id is None:
                continue
            latest_events.setdefault(event.resource_subscription_id, event)
            stage_event_types.setdefault(event.resource_subscription_id, set()).add(
                event.type
            )

    result: list[SubscriptionOut] = []
    for row in rows:
        event = latest_events.get(row.id)
        result.append(
            SubscriptionOut.model_validate(row).model_copy(
                update={
                    "stage": _subscription_stage(
                        row, stage_event_types.get(row.id, set())
                    ),
                    "last_event_type": event.type if event else None,
                    "last_event_at": event.created_at if event else None,
                }
            )
        )
    return result


def _login_key(session_id: str) -> str:
    return f"{CLAWBOT_LOGIN_PREFIX}{session_id}"


def _login_expired(value: dict) -> bool:
    try:
        created = float(value.get("created_at") or 0)
    except (TypeError, ValueError):
        return True
    return _now().timestamp() - created > CLAWBOT_LOGIN_TTL


async def _purge_stale_logins(db: AsyncSession) -> None:
    rows = (
        await db.execute(
            sa.select(NotificationSyncState).where(
                NotificationSyncState.key.like(f"{CLAWBOT_LOGIN_PREFIX}%")
            )
        )
    ).scalars()
    for row in rows:
        if _login_expired(dict(row.value or {})):
            await db.delete(row)


async def _unique_channel_name(db: AsyncSession, preferred: str) -> str:
    base = preferred or "微信 ClawBot"
    names = set(
        (await db.execute(sa.select(NotificationChannel.name))).scalars()
    )
    if base not in names:
        return base
    for index in range(2, 100):
        candidate = f"{base} {index}"
        if candidate not in names:
            return candidate
    return f"{base} {uuid.uuid4().hex[:6]}"


def _first_login_message(messages: list) -> dict | None:
    for item in messages:
        if not isinstance(item, dict):
            continue
        if item.get("context_token") and item.get("from_user_id"):
            return item
    return None


async def _save_clawbot_channel(
    db: AsyncSession, state: dict, message: dict
) -> NotificationChannel:
    credentials = decrypt_secrets(state.get("secrets"))
    if not credentials.get("bot_token"):
        raise APIException("扫码登录凭证缺失，请重新扫码", 400)
    config = {
        "base_url": str(state.get("base_url") or claw.DEFAULT_BASE_URL),
        "account_id": str(state.get("account_id") or ""),
        "to_user": str(message.get("from_user_id") or ""),
        "context_token": str(message.get("context_token") or ""),
    }
    channel_id = state.get("channel_id")
    record = None
    if isinstance(channel_id, int):
        record = await db.get(NotificationChannel, channel_id)
    if record is None:
        record = NotificationChannel(
            name=await _unique_channel_name(db, str(state.get("name") or "")),
            type="wechatclawbot",
            enabled=True,
            config=config,
            secrets=encrypt_secrets(credentials),
        )
        db.add(record)
    else:
        record.type = "wechatclawbot"
        record.enabled = True
        record.config = config
        record.secrets = encrypt_secrets(credentials)
    return record


@router.post(
    "/channels/wechatclawbot/login", response_model=WeChatClawbotLoginOut
)
async def start_wechatclawbot_login(
    data: WeChatClawbotLoginStart,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """申请微信 ClawBot 登录二维码。"""
    try:
        qr = await claw.request_login_qrcode()
        image = claw.qr_data_url(qr["content"])
    except ChannelError as exc:
        raise APIException(str(exc), 502)
    except Exception as exc:  # noqa: BLE001
        raise APIException(f"获取微信登录二维码失败：{exc}", 502)
    session_id = uuid.uuid4().hex
    await _purge_stale_logins(db)
    db.add(
        NotificationSyncState(
            key=_login_key(session_id),
            value={
                "status": "wait",
                "qrcode": qr["qrcode"],
                "base_url": qr["base_url"],
                "name": (data.name or "").strip(),
                "channel_id": data.channel_id,
                "created_at": _now().timestamp(),
            },
        )
    )
    await db.commit()
    return WeChatClawbotLoginOut(
        session_id=session_id, qrcode_image=image, expires_in=CLAWBOT_LOGIN_TTL
    )


@router.get(
    "/channels/wechatclawbot/login/{session_id}",
    response_model=WeChatClawbotLoginStatusOut,
)
async def wechatclawbot_login_status(
    session_id: str,
    skip_message: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """轮询扫码状态；确认后等待一条入站消息，自动保存渠道。"""
    row = (
        await db.execute(
            sa.select(NotificationSyncState).where(
                NotificationSyncState.key == _login_key(session_id)
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise APIException("登录会话不存在或已过期", 404)
    state = dict(row.value or {})
    if _login_expired(state):
        await db.delete(row)
        await db.commit()
        return WeChatClawbotLoginStatusOut(
            status="expired", message="二维码已过期，请重新扫码"
        )

    if state.get("status") in (None, "wait", "scaned"):
        try:
            result = await claw.poll_login_qrcode(
                str(state.get("qrcode") or ""),
                str(state.get("base_url") or claw.DEFAULT_BASE_URL),
            )
        except ChannelError as exc:
            raise APIException(str(exc), 502)
        except Exception as exc:  # noqa: BLE001
            raise APIException(f"查询扫码状态失败：{exc}", 502)
        status = result["status"]
        if status == "confirmed":
            if not result["bot_token"] or not result["account_id"]:
                await db.delete(row)
                await db.commit()
                return WeChatClawbotLoginStatusOut(
                    status="expired", message="扫码返回的凭证不完整，请重新扫码"
                )
            state.update(
                {
                    "status": "need_message",
                    "secrets": encrypt_secrets(
                        {"bot_token": result["bot_token"]}
                    ),
                    "account_id": result["account_id"],
                    "user_id": result["user_id"],
                    "base_url": result["base_url"]
                    or state.get("base_url"),
                }
            )
            try:
                await claw.notify_start(
                    result["bot_token"],
                    state.get("base_url") or claw.DEFAULT_BASE_URL,
                )
            except Exception:  # noqa: BLE001
                pass
        elif status == "expired":
            await db.delete(row)
            await db.commit()
            return WeChatClawbotLoginStatusOut(
                status="expired", message="二维码已过期，请重新扫码"
            )
        else:
            if status not in {"wait", "scaned"}:
                status = "wait"
            state["status"] = status
            row.value = state
            await db.commit()
            return WeChatClawbotLoginStatusOut(
                status=status,
                message="请使用微信扫描二维码"
                if status == "wait"
                else "已在手机端扫码，请在微信中确认",
            )

    credentials = decrypt_secrets(state.get("secrets"))
    message = None
    detail = ""
    if skip_message:
        updates = {"messages": [], "buffer": state.get("buffer") or "", "ret": 0}
    else:
        try:
            updates = await claw.fetch_updates(
                str(credentials.get("bot_token") or ""),
                str(state.get("base_url") or claw.DEFAULT_BASE_URL),
                str(state.get("buffer") or ""),
            )
        except Exception as exc:  # noqa: BLE001
            updates = {
                "ret": 0,
                "messages": [],
                "buffer": state.get("buffer") or "",
                "errmsg": f"{type(exc).__name__}: {exc}",
            }
    ret = updates.get("ret", 0)
    if str(ret) == "-14" or str(updates.get("errcode")) == "-14":
        await db.delete(row)
        await db.commit()
        return WeChatClawbotLoginStatusOut(
            status="expired", message="微信会话已失效，请重新扫码"
        )
    if str(ret) not in {"0", ""}:
        detail = f"ret={ret} {updates.get('errmsg') or ''}".strip()
    elif updates.get("errmsg"):
        detail = str(updates.get("errmsg"))
    state["buffer"] = updates.get("buffer") or state.get("buffer") or ""
    message = _first_login_message(updates.get("messages") or [])
    if message is None and not skip_message:
        row.value = state
        await db.commit()
        hint = "已扫码。请在微信里打开 ClawBot 会话，随便发一句（如「你好」）完成绑定"
        if detail:
            hint = f"{hint}（getupdates {detail}）"
        return WeChatClawbotLoginStatusOut(status="need_message", message=hint)
    if message is None:
        message = {
            "from_user_id": str(state.get("user_id") or ""),
            "context_token": "",
        }
    record = await _save_clawbot_channel(db, state, message)
    await db.delete(row)
    await db.commit()
    await db.refresh(record)
    return WeChatClawbotLoginStatusOut(
        status="done", channel=_channel_out(record)
    )


@router.get("/channels", response_model=list[ChannelOut])
async def list_channels(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    rows = (
        await db.execute(
            sa.select(NotificationChannel).order_by(NotificationChannel.id)
        )
    ).scalars()
    return [_channel_out(row) for row in rows]


@router.post("/channels", response_model=ChannelOut)
async def create_channel(
    data: ChannelUpsert,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    if data.type not in CHANNEL_TYPES:
        raise APIException("不支持的通知渠道类型", 400)
    config, secrets = _build_config(data)
    required = _required_secret(data.type)
    if required and not secrets.get(required):
        raise APIException(f"{CHANNEL_LABELS[data.type]}渠道需要 {required}", 400)
    exists = (
        await db.execute(
            sa.select(NotificationChannel.id).where(
                NotificationChannel.name == data.name.strip()
            )
        )
    ).scalar_one_or_none()
    if exists is not None:
        raise APIException("渠道名称已存在", 400)
    record = NotificationChannel(
        name=data.name.strip(),
        type=data.type,
        enabled=data.enabled,
        config=config,
        secrets=encrypt_secrets(secrets),
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return _channel_out(record)


@router.put("/channels/{channel_id}", response_model=ChannelOut)
async def update_channel(
    channel_id: int,
    data: ChannelUpsert,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    record = await db.get(NotificationChannel, channel_id)
    if record is None:
        raise APIException("通知渠道不存在", 404)
    config, new_secrets = _build_config(data)
    existing_secrets = decrypt_secrets(record.secrets)
    for key, value in new_secrets.items():
        if value:
            existing_secrets[key] = value
    required = _required_secret(data.type)
    if required and not existing_secrets.get(required):
        raise APIException(f"{CHANNEL_LABELS[data.type]}渠道需要 {required}", 400)
    duplicate = (
        await db.execute(
            sa.select(NotificationChannel.id).where(
                NotificationChannel.name == data.name.strip(),
                NotificationChannel.id != channel_id,
            )
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        raise APIException("渠道名称已存在", 400)
    record.name = data.name.strip()
    record.type = data.type
    record.enabled = data.enabled
    record.config = config
    record.secrets = encrypt_secrets(existing_secrets)
    await db.commit()
    await db.refresh(record)
    return _channel_out(record)


@router.delete("/channels/{channel_id}", response_model=ChannelOut)
async def delete_channel(
    channel_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    record = await db.get(NotificationChannel, channel_id)
    if record is None:
        raise APIException("通知渠道不存在", 404)
    result = _channel_out(record)
    await db.delete(record)
    await db.commit()
    return result


@router.post("/channels/{channel_id}/test", response_model=ChannelTestResult)
async def test_channel(
    channel_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    record = await db.get(NotificationChannel, channel_id)
    if record is None:
        raise APIException("通知渠道不存在", 404)
    try:
        await build_channel(record).test()
    except ChannelError as exc:
        return ChannelTestResult(ok=False, message=str(exc))
    except Exception as exc:  # noqa: BLE001
        return ChannelTestResult(ok=False, message=f"测试失败：{exc}")
    return ChannelTestResult(ok=True, message="测试消息已发送")


@router.get("/settings", response_model=NotificationSettingsOut)
async def get_notification_settings(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    record = await get_setting(db)
    await db.commit()
    return _settings_out(record)


@router.put("/settings", response_model=NotificationSettingsOut)
async def update_notification_settings(
    data: NotificationSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    record = await get_setting(db)
    if data.event_types is not None:
        record.event_types = _validate_events(data.event_types)
    await db.commit()
    return _settings_out(record)
