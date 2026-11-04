"""週範囲計算・タイトル正規化・重複キー生成。

仕様書 6.（取得対象期間）/ 9.（重複判定）: `docs/feature/theater-release-calendar-spec.md`

対象期間は暦月（1日〜末日）。まとめ記事は毎月1日に当月分を公開し、収集は隔週で翌月分を集める
（以前は「直近の金曜日〜その翌週木曜日」の7日間で週次公開していた）。
重複判定キーは「公開日 + 正規化タイトル」の完全一致のみ（フェーズ1スコープ）。
tmdb_id・類似度判定によるあいまい一致は将来拡張（仕様書9.将来拡張）。
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional


@dataclass
class TheaterEntry:
    """劇場公開作品1件。取得元（RSS / AI Web検索 / ルーティン成果物）によらず共通。

    fetch_theater.py ではなく本モジュールに置いているのは、AI SDK・feedparser・requests に
    依存しない純粋なデータ定義であるため。fetch_theater に置くと、ルーティン成果物を
    読むだけの import_routine.py までRSS取得系の依存を要求してしまう。
    """

    title: str
    url: str
    source: str
    summary: str = ""
    release_date: Optional[date] = None
    original_title: str = ""
    category: str = ""
    distributor: str = ""


# 副題区切り等で表記が揺れやすい記号を軽く吸収する（仕様書9.正規化ルール）。
# NFKC正規化で全角/半角の大半は統一されるが、波ダッシュ(U+301C)や各種ダッシュ記号は
# NFKCの対象外のため個別に統一する。長音記号「ー」は語の一部として使われるため対象外。
_SYMBOL_UNIFY = {
    "〜": "~",  # 波ダッシュ → 半角チルダ
    "―": "-",  # ホリゾンタルバー
    "—": "-",  # エムダッシュ
    "–": "-",  # エンダッシュ
}
_WHITESPACE_RE = re.compile(r"\s+")


def month_range(day: date) -> tuple[date, date]:
    """``day`` が属する暦月の1日〜末日を返す（公開 `theater_publish` / `vod_publish` の対象期間）。

    まとめ記事は毎月1日に「その月に公開・配信開始される作品」を1本にまとめて公開する。
    区切りは暦月（1日〜末日）で、劇場（公開日別）・VOD（サービス別）で共通。

    例:
        2026-12-01 → 2026-12-01〜2026-12-31
        2027-02-15 → 2027-02-01〜2027-02-28
    """
    start = day.replace(day=1)
    next_first = (start + timedelta(days=32)).replace(day=1)
    return start, next_first - timedelta(days=1)


def next_month_range(day: date) -> tuple[date, date]:
    """``day`` の翌月1日〜末日を返す（収集＝ルーティン / `*_discover` の対象期間）。

    収集は隔週で走り、毎回「翌月分」を集める。月内のどの日に実行しても同じ月を返すため、
    同じ月を2回（以上）収集することになるが、重複キーで弾かれるので2回目以降は
    新たに判明した作品だけが追加される（後から発表された作品の拾い直しを兼ねる）。

    例:
        2026-11-04 → 2026-12-01〜2026-12-31
        2026-11-30 → 2026-12-01〜2026-12-31
        2026-12-16 → 2027-01-01〜2027-01-31
    """
    first = day.replace(day=1)
    return month_range(first + timedelta(days=32))


def normalize_title(title: str) -> str:
    """タイトルを正規化する（仕様書9.正規化ルール）。

    - 全角/半角の統一（NFKC）
    - 前後空白の削除・連続空白の統一
    - 記号の揺れの軽い吸収（波ダッシュ・各種ダッシュ）
    """
    normalized = unicodedata.normalize("NFKC", title)
    for src, dst in _SYMBOL_UNIFY.items():
        normalized = normalized.replace(src, dst)
    return _WHITESPACE_RE.sub(" ", normalized).strip()


def dedupe_key(release_date: str, title: str) -> str:
    """「公開日 + 正規化タイトル」の重複判定キーを生成する。

    Args:
        release_date: ISO形式の公開日文字列（例: "2026-07-24"）。
        title: 正規化前のタイトル。
    """
    return f"{release_date}|{normalize_title(title)}"


def in_range(release_date: date, start: date, end: date) -> bool:
    """公開日が対象期間内（両端含む）かどうかを返す。"""
    return start <= release_date <= end
