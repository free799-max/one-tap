"""OneTap 独立通知中心。"""

from app.services.notifications.dispatcher import notification_dispatcher
from app.services.notifications.events import (
    DEFAULT_EVENT_TYPES,
    EVENT_LABELS,
    emit,
    find_subscription,
    get_setting,
    mark_removed,
    upsert_subscription,
)

__all__ = [
    "DEFAULT_EVENT_TYPES",
    "EVENT_LABELS",
    "emit",
    "find_subscription",
    "get_setting",
    "mark_removed",
    "notification_dispatcher",
    "upsert_subscription",
]
