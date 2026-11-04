"""対象期間（暦月）計算の検証。

収集（next_month_range: 隔週ルーティンが翌月分を集める）と公開（month_range: 毎月1日に
当月分を公開する）が同じ月を指すことを確かめる。両者がずれると、収集した行が
取り込み時に期間外で落ちるか、公開時に拾われない。
"""

from datetime import date, timedelta

from news_bot import theater_calendar, vod_calendar

DECEMBER = (date(2026, 12, 1), date(2026, 12, 31))


def test_month_range_covers_calendar_month():
    assert theater_calendar.month_range(date(2026, 12, 1)) == DECEMBER
    assert theater_calendar.month_range(date(2026, 12, 31)) == DECEMBER


def test_month_range_handles_february_and_leap_year():
    assert theater_calendar.month_range(date(2027, 2, 15)) == (date(2027, 2, 1), date(2027, 2, 28))
    assert theater_calendar.month_range(date(2028, 2, 1)) == (date(2028, 2, 1), date(2028, 2, 29))


def test_next_month_range_is_stable_across_the_collection_month():
    """11月のどの日に収集しても12月を返す（隔週ルーティンの実行日・PRマージ日に左右されない）。"""
    for offset in range(30):
        today = date(2026, 11, 1) + timedelta(days=offset)
        assert theater_calendar.next_month_range(today) == DECEMBER, today


def test_next_month_range_crosses_year_boundary():
    assert theater_calendar.next_month_range(date(2026, 12, 16)) == (date(2027, 1, 1), date(2027, 1, 31))


def test_publish_on_first_matches_collected_month():
    """毎月1日の公開は、前月中に next_month_range() で集めた月を対象にする。"""
    collected = theater_calendar.next_month_range(date(2026, 11, 18))
    assert theater_calendar.month_range(date(2026, 12, 1)) == collected


def test_vod_uses_same_month_ranges():
    assert vod_calendar.month_range(date(2026, 12, 1)) == DECEMBER
    assert vod_calendar.next_month_range(date(2026, 11, 4)) == DECEMBER
