"""対象週計算の検証。

収集（next_week_range）と公開（week_range）が、それぞれの実行曜日で同じ週を指すことを
確かめる。両者がずれると、収集した行が取り込み時に期間外で落ちるか、公開時に拾われない。
"""

from datetime import date, timedelta

from news_bot import theater_calendar

# 2026-08-21 は金曜。対象週は 2026-08-28（金）〜2026-09-03（木）。
TARGET = (date(2026, 8, 28), date(2026, 9, 3))


def test_next_week_range_is_stable_across_the_collection_week():
    """収集週（金〜木）のどの日に実行しても同じ翌週を返す。

    ルーティンは金曜に走るが、PRのレビュー・マージが土日や翌週にずれ込んでも
    theater_import が同じ週を取り込めることを保証する。
    """
    for day in range(7):  # 2026-08-21(金) 〜 2026-08-27(木)
        today = date(2026, 8, 21) + timedelta(days=day)
        assert theater_calendar.next_week_range(today) == TARGET, today


def test_week_range_on_publish_thursday_matches_collected_week():
    """publish（木曜 07:00 JST）は収集済みの週を対象にする。"""
    assert theater_calendar.week_range(date(2026, 8, 27)) == TARGET


def test_week_range_advances_on_friday():
    """金曜以降は1週先へ進む。cronを金〜日へ動かせない根拠。"""
    assert theater_calendar.week_range(date(2026, 8, 28)) == (
        date(2026, 8, 28),
        date(2026, 9, 3),
    )
    assert theater_calendar.next_week_range(date(2026, 8, 28)) == (
        date(2026, 9, 4),
        date(2026, 9, 10),
    )
