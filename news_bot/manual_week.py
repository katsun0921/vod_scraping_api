"""対象週の解決（手動指定の検証と、ルーティン成果物からの逆引き）。

通常のcronは各カレンダーの基準日計算を使うが、過去週の再取り込み・CPT再作成では
対象週を明示する必要がある。劇場は金曜始まり、VODは月曜始まりという既存の集計単位を
崩さないよう、ここで曜日も検証する。

ルーティン成果物の取り込み（`*_import`）では、実行日ではなく成果物に入っている
日付から対象週を決める（routine_range()）。
"""

import logging
import re
from collections import Counter
from datetime import date, timedelta

logger = logging.getLogger(__name__)

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


def week_start_of(day: date, kind: str) -> date:
    """``day`` が属する対象週の開始日を返す（劇場=金曜始まり / VOD=月曜始まり）。"""
    if kind not in _START_WEEKDAYS:
        raise ValueError(f"不明な対象種別です: {kind}")
    return day - timedelta(days=(day.weekday() - _START_WEEKDAYS[kind]) % 7)


def routine_range(dates: list[date], kind: str, automatic_range: tuple[date, date]) -> tuple[date, date]:
    """ルーティン成果物に入っている日付から対象週を決める。

    取り込み（`*_import`）の対象週を実行日から逆算すると、ルーティンが走った曜日と
    PRがマージされた曜日がずれた瞬間に全件が期間外で落ちる。実際に #76 / #83 では
    木曜マージのため next_week_range() が成果物より1週手前の週を返し、7件すべてが
    out_of_range で捨てられた（シート保存もSlack通知も0件のままジョブは成功扱い）。

    成果物の日付そのものから週を決めればこのズレに左右されない。ルーティンは1週分を
    まとめて出すので、成果物が示す週＝取り込むべき週になる。

    Args:
        dates: 成果物の公開日／配信開始日の一覧。
        kind: "theater" / "vod"。
        automatic_range: 実行日から計算した対象期間。成果物が空のときはこれを返す。
    """
    if not dates:
        return automatic_range

    # 最多の週を採る。1件だけ日付を取り違えた行があっても週全体が引きずられないようにする。
    # 同数なら早い週を採り、行の並び順で結果が変わらないようにする。
    counts = Counter(week_start_of(day, kind) for day in dates)
    top = max(counts.values())
    start = min(week_start for week_start, count in counts.items() if count == top)
    resolved = (start, start + timedelta(days=6))

    if resolved != automatic_range:
        logger.info(
            "成果物の日付から対象週を決定: %s〜%s（実行日からの計算値: %s〜%s）",
            resolved[0],
            resolved[1],
            automatic_range[0],
            automatic_range[1],
        )
    return resolved
