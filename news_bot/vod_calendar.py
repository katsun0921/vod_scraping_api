"""週範囲計算・重複キー生成（VOD配信情報収集パイプライン専用）。

仕様書 6.（取得対象期間）/ 9.（重複判定）: `docs/feature/vod-release-calendar-spec.md`

タイトル正規化・期間内判定は theater_calendar.py のロジックをそのまま流用する
（仕様書9.: 「theater仕様9.の正規化ルールを流用」）。VOD固有なのは週範囲の
計算方法（金曜始まりではなく月曜始まり）と、重複判定キーにサービスを含める点。
"""

from datetime import date, timedelta

from news_bot.theater_calendar import in_range, normalize_title

__all__ = ["next_week_range", "current_week_range", "normalize_title", "in_range", "dedupe_key"]


def next_week_range(today: date) -> tuple[date, date]:
    """翌週月曜〜日曜を返す（`vod_discover`の対象期間、仕様書6.）。"""
    days_until_next_monday = 7 - today.weekday()
    start = today + timedelta(days=days_until_next_monday)
    end = start + timedelta(days=6)
    return start, end


def current_week_range(today: date) -> tuple[date, date]:
    """当週月曜〜日曜を返す（`vod_publish`の対象期間、仕様書6.）。"""
    start = today - timedelta(days=today.weekday())
    end = start + timedelta(days=6)
    return start, end


def dedupe_key(release_date: str, service: str, title: str) -> str:
    """「配信開始日 + サービス + 正規化タイトル」の重複判定キーを生成する（仕様書9.）。"""
    return f"{release_date}|{service}|{normalize_title(title)}"
