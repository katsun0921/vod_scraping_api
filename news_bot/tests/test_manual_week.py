from datetime import date

import pytest

from news_bot import manual_week


def test_empty_target_start_uses_automatic_range():
    assert manual_week.parse_target_start("", "theater") is None
    assert manual_week.parse_target_start(None, "vod") is None


def test_theater_target_start_accepts_friday():
    start = manual_week.parse_target_start("2026-08-14", "theater")
    assert start == date(2026, 8, 14)
    assert manual_week.target_range(start, "theater") == (date(2026, 8, 14), date(2026, 8, 20))


def test_vod_target_start_accepts_monday():
    start = manual_week.parse_target_start("2026-08-17", "vod")
    assert start == date(2026, 8, 17)
    assert manual_week.target_range(start, "vod") == (date(2026, 8, 17), date(2026, 8, 23))


def test_manual_target_overrides_automatic_range():
    automatic = (date(2026, 8, 21), date(2026, 8, 27))
    assert manual_week.resolve_range(date(2026, 8, 14), "theater", automatic) == (
        date(2026, 8, 14),
        date(2026, 8, 20),
    )


def test_automatic_range_is_preserved_without_manual_target():
    automatic = (date(2026, 8, 17), date(2026, 8, 23))
    assert manual_week.resolve_range(None, "vod", automatic) == automatic


@pytest.mark.parametrize(
    ("raw", "kind", "message"),
    [
        ("2026/08/14", "theater", "YYYY-MM-DD"),
        ("20260814", "theater", "YYYY-MM-DD"),
        ("2026-08-15", "theater", "金曜日"),
        ("2026-08-18", "vod", "月曜日"),
    ],
)
def test_invalid_target_start_is_rejected(raw, kind, message):
    with pytest.raises(ValueError, match=message):
        manual_week.parse_target_start(raw, kind)
