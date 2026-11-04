"""対象月の解決（手動指定の検証と、ルーティン成果物からの逆引き）。

まとめ記事は暦月（1日〜末日）単位で、毎月1日に当月分を公開する。収集（ルーティン）は
隔週で翌月分を集める。通常のcronは実行日から対象月を計算するが、過去月の再取り込み・
CPT再作成では対象月を明示する必要があるため、ここで ``YYYY-MM`` を検証する。

ルーティン成果物の取り込み（`*_import`）では、実行日ではなく成果物に入っている
日付から対象月を決める（routine_range()）。

（以前は週次公開で、劇場=金曜始まり / VOD=月曜始まりの週を `manual_week.py` で扱っていた）
"""

import logging
import re
from collections import Counter
from datetime import date, datetime
from zoneinfo import ZoneInfo

from news_bot.theater_calendar import month_range

logger = logging.getLogger(__name__)

_JST = ZoneInfo("Asia/Tokyo")


def jst_today() -> date:
    """JSTでの「今日」を返す。

    GitHub Actionsのcronランナーは UTC で動くが、cronは 07:00 JST 起点で組んでいる。
    07:00 JST は 22:00 UTC（前日）にあたるため、date.today() をそのまま期間計算の
    基準日に使うとUTC/JSTの日付境界をまたいだ分だけずれる。月次公開では毎月1日 07:00 JST
    （＝前月末日 22:00 UTC）に走るため、date.today() だと丸ごと前月を処理してしまう
    （週次時代にも 2026-09-21実行分が1週前の週を処理して対象0件で完了扱いになった）。
    期間計算の基準日はここで揃える。
    """
    return datetime.now(_JST).date()


def parse_target_month(raw: str | None) -> date | None:
    """任意の ``YYYY-MM`` を解析し、その月の1日を返す。空欄なら None。"""
    value = (raw or "").strip()
    if not value:
        return None

    # 週次時代の入力（YYYY-MM-DD）をうっかり渡しても、月の取り違えに気付けるよう弾く。
    if re.fullmatch(r"\d{4}-\d{2}", value) is None:
        raise ValueError(f"対象月は YYYY-MM 形式で指定してください: {value}")

    year, month = (int(part) for part in value.split("-"))
    if not 1 <= month <= 12:
        raise ValueError(f"対象月は YYYY-MM 形式で指定してください: {value}")
    return date(year, month, 1)


def resolve_range(target_month: date | None, automatic_range: tuple[date, date]) -> tuple[date, date]:
    """手動指定があればその月を、無ければ通常計算済みの期間を返す。"""
    if target_month is None:
        return automatic_range
    return month_range(target_month)


def routine_range(dates: list[date], automatic_range: tuple[date, date]) -> tuple[date, date]:
    """ルーティン成果物に入っている日付から対象月を決める。

    取り込み（`*_import`）の対象期間を実行日から逆算すると、ルーティンが走った日と
    PRがマージされた日が月をまたいだ瞬間に全件が期間外で落ちる（週次時代の #76 / #83 では
    木曜マージで1週手前を指し、7件すべてが out_of_range で捨てられた）。
    成果物の日付そのものから月を決めればこのズレに左右されない。

    Args:
        dates: 成果物の公開日／配信開始日の一覧。
        automatic_range: 実行日から計算した対象期間。成果物が空のときはこれを返す。
    """
    if not dates:
        return automatic_range

    # 最多の月を採る。1件だけ日付を取り違えた行があっても月全体が引きずられないようにする。
    # 同数なら早い月を採り、行の並び順で結果が変わらないようにする。
    counts = Counter(day.replace(day=1) for day in dates)
    top = max(counts.values())
    first = min(month for month, count in counts.items() if count == top)
    resolved = month_range(first)

    if resolved != automatic_range:
        logger.info(
            "成果物の日付から対象月を決定: %s〜%s（実行日からの計算値: %s〜%s）",
            resolved[0],
            resolved[1],
            automatic_range[0],
            automatic_range[1],
        )
    return resolved
