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


def test_week_start_of_snaps_to_friday_for_theater():
    # 金曜はその日自身、土〜木は直前の金曜へ寄せる。
    assert manual_week.week_start_of(date(2026, 9, 18), "theater") == date(2026, 9, 18)
    assert manual_week.week_start_of(date(2026, 9, 19), "theater") == date(2026, 9, 18)
    assert manual_week.week_start_of(date(2026, 9, 24), "theater") == date(2026, 9, 18)


def test_week_start_of_snaps_to_monday_for_vod():
    assert manual_week.week_start_of(date(2026, 9, 14), "vod") == date(2026, 9, 14)
    assert manual_week.week_start_of(date(2026, 9, 20), "vod") == date(2026, 9, 14)


def test_routine_range_follows_artifact_dates_not_run_date():
    """#83 の再現。木曜マージで実行日から計算すると1週手前になり全件が期間外で落ちた。"""
    automatic = (date(2026, 9, 11), date(2026, 9, 17))
    dates = [date(2026, 9, 18)] * 6 + [date(2026, 9, 19)]
    assert manual_week.routine_range(dates, "theater", automatic) == (
        date(2026, 9, 18),
        date(2026, 9, 24),
    )


def test_routine_range_falls_back_to_automatic_when_empty():
    automatic = (date(2026, 9, 11), date(2026, 9, 17))
    assert manual_week.routine_range([], "theater", automatic) == automatic


def test_routine_range_ignores_a_single_stray_date():
    # 1件だけ日付を取り違えた行があっても、多数派の週が対象になる。
    automatic = (date(2026, 9, 11), date(2026, 9, 17))
    dates = [date(2025, 1, 3), date(2026, 9, 18), date(2026, 9, 18), date(2026, 9, 21)]
    assert manual_week.routine_range(dates, "theater", automatic) == (
        date(2026, 9, 18),
        date(2026, 9, 24),
    )


def test_routine_range_picks_earliest_week_on_tie():
    # 同数なら早い週。行の並び順で結果が変わらないようにする。
    automatic = (date(2026, 9, 14), date(2026, 9, 20))
    dates = [date(2026, 9, 24), date(2026, 9, 15)]
    assert manual_week.routine_range(dates, "vod", automatic) == (
        date(2026, 9, 14),
        date(2026, 9, 20),
    )


def test_routine_range_rejects_unknown_kind():
    with pytest.raises(ValueError, match="不明な対象種別"):
        manual_week.routine_range([date(2026, 9, 18)], "movie", (date(2026, 9, 18), date(2026, 9, 24)))
