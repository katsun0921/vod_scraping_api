from datetime import date, datetime, timezone

import pytest

from news_bot import target_month


def test_empty_target_month_uses_automatic_range():
    assert target_month.parse_target_month("") is None
    assert target_month.parse_target_month(None) is None


def test_target_month_returns_first_day():
    assert target_month.parse_target_month("2026-12") == date(2026, 12, 1)
    assert target_month.parse_target_month(" 2027-01 ") == date(2027, 1, 1)


def test_manual_target_overrides_automatic_range():
    automatic = (date(2026, 12, 1), date(2026, 12, 31))
    assert target_month.resolve_range(date(2026, 11, 1), automatic) == (
        date(2026, 11, 1),
        date(2026, 11, 30),
    )


def test_automatic_range_is_preserved_without_manual_target():
    automatic = (date(2026, 12, 1), date(2026, 12, 31))
    assert target_month.resolve_range(None, automatic) == automatic


@pytest.mark.parametrize("raw", ["2026/12", "202612", "2026-13", "2026-00", "2026-12-04"])
def test_invalid_target_month_is_rejected(raw):
    # 週次時代の YYYY-MM-DD 入力も弾く（月の取り違えに気付けるように）。
    with pytest.raises(ValueError, match="YYYY-MM"):
        target_month.parse_target_month(raw)


def test_routine_range_follows_artifact_dates_not_run_date():
    """月末に走ったルーティンのPRが翌月にマージされても、成果物の月を取り込む。"""
    automatic = (date(2027, 1, 1), date(2027, 1, 31))  # 12月にマージ → 実行日から計算すると1月
    dates = [date(2026, 12, 4)] * 5 + [date(2026, 12, 25)]
    assert target_month.routine_range(dates, automatic) == (date(2026, 12, 1), date(2026, 12, 31))


def test_routine_range_falls_back_to_automatic_when_empty():
    automatic = (date(2026, 12, 1), date(2026, 12, 31))
    assert target_month.routine_range([], automatic) == automatic


def test_routine_range_ignores_a_single_stray_date():
    automatic = (date(2026, 12, 1), date(2026, 12, 31))
    dates = [date(2025, 1, 3), date(2026, 12, 4), date(2026, 12, 11), date(2026, 12, 18)]
    assert target_month.routine_range(dates, automatic) == (date(2026, 12, 1), date(2026, 12, 31))


def test_routine_range_picks_earliest_month_on_tie():
    automatic = (date(2026, 12, 1), date(2026, 12, 31))
    dates = [date(2027, 1, 8), date(2026, 12, 25)]
    assert target_month.routine_range(dates, automatic) == (date(2026, 12, 1), date(2026, 12, 31))


def test_jst_today_crosses_utc_date_boundary(monkeypatch):
    # publishのcronは毎月1日 07:00 JST だが、これはUTCでは前月末日 22:00。GitHub Actions
    # ランナー（UTC）上でdate.today()を使うと前月を返し、丸ごと1ヶ月ずれる。
    fixed_utc = datetime(2026, 11, 30, 22, 0, 5, tzinfo=timezone.utc)

    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_utc.astimezone(tz) if tz else fixed_utc

    monkeypatch.setattr(target_month, "datetime", _FixedDateTime)
    assert target_month.jst_today() == date(2026, 12, 1)
