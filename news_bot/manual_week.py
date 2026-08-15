"""手動実行で指定された週開始日の解析と検証。

通常のcronやPRマージ時は各カレンダーの基準日計算を使うが、過去週の
再取り込み・CPT再作成では対象週を明示する必要がある。劇場は金曜始まり、
VODは月曜始まりという既存の集計単位を崩さないよう、ここで曜日も検証する。
"""

import re
from datetime import date, timedelta

_START_WEEKDAYS = {"theater": 4, "vod": 0}
_KIND_LABELS = {"theater": "劇場", "vod": "VOD"}
_WEEKDAY_LABELS = {"theater": "金曜日", "vod": "月曜日"}


def parse_target_start(raw: str | None, kind: str) -> date | None:
    """任意の ``YYYY-MM-DD`` を解析し、対象種別の週開始曜日を検証する。"""
    value = (raw or "").strip()
    if not value:
        return None
    if kind not in _START_WEEKDAYS:
        raise ValueError(f"不明な対象種別です: {kind}")

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
        raise ValueError(f"対象週の開始日は YYYY-MM-DD 形式で指定してください: {value}")

    try:
        start = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"対象週の開始日は YYYY-MM-DD 形式で指定してください: {value}") from exc

    if start.weekday() != _START_WEEKDAYS[kind]:
        raise ValueError(
            f"{_KIND_LABELS[kind]}の対象週開始日は{_WEEKDAY_LABELS[kind]}を指定してください: {value}"
        )
    return start


def target_range(target_start: date, kind: str) -> tuple[date, date]:
    """検証済みの週開始日から7日間の対象期間を返す。"""
    # プログラムから直接呼ぶ場合にも曜日の取り違えを検出する。
    start = parse_target_start(target_start.isoformat(), kind)
    assert start is not None
    return start, start + timedelta(days=6)


def resolve_range(
    target_start: date | None, kind: str, automatic_range: tuple[date, date]
) -> tuple[date, date]:
    """手動指定があればその週を、無ければ通常計算済みの期間を返す。"""
    if target_start is None:
        return automatic_range
    return target_range(target_start, kind)
