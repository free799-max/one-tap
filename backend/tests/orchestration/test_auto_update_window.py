"""自动更新夜间时段判断测试。"""

from datetime import datetime

import pytest

from app.services.orchestration.auto_update import in_update_window, window_key


@pytest.mark.parametrize(
    "hour,expected",
    [
        (1, False),
        (2, True),
        (5, True),
        (6, False),
        (12, False),
        (23, False),
    ],
)
def test_in_update_window_daytime_window(hour, expected):
    """02:00-06:00：只有 2 点到 5 点允许运行。"""
    now = datetime(2026, 11, 8, hour, 30)
    assert in_update_window(now, 2, 6) is expected


@pytest.mark.parametrize(
    "hour,expected",
    [(21, False), (22, True), (23, True), (0, True), (5, True), (6, False)],
)
def test_in_update_window_crosses_midnight(hour, expected):
    """22:00-06:00：跨零点窗口同样生效。"""
    now = datetime(2026, 11, 8, hour, 0)
    assert in_update_window(now, 22, 6) is expected


def test_in_update_window_same_hours_means_all_day():
    assert in_update_window(datetime(2026, 11, 8, 12, 0), 0, 0) is True


def test_window_key_groups_cross_midnight_into_start_day():
    """跨零点时凌晨属于前一天晚上的那一轮。"""
    assert window_key(datetime(2026, 11, 8, 23, 0), 22, 6) == "2026-11-08"
    assert window_key(datetime(2026, 11, 9, 3, 0), 22, 6) == "2026-11-08"
    assert window_key(datetime(2026, 11, 9, 23, 0), 22, 6) == "2026-11-09"


def test_window_key_same_day_for_daytime_window():
    assert window_key(datetime(2026, 11, 8, 3, 0), 2, 6) == "2026-11-08"
