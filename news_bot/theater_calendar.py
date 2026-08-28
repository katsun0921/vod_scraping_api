"""週範囲計算・タイトル正規化・重複キー生成。

仕様書 6.（取得対象期間）/ 9.（重複判定）: `docs/feature/theater-release-calendar-spec.md`

初期MVPの対象期間は「直近の金曜日〜その翌週木曜日」の7日間固定。
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


def week_range(today: date) -> tuple[date, date]:
    """基準日から対象期間（直近の金曜日〜その翌週木曜日）を返す。

    例:
        月曜実行 → その週の金曜〜翌週木曜
        金曜実行 → 当日金曜〜翌週木曜
    """
    days_until_friday = (4 - today.weekday()) % 7
    start = today + timedelta(days=days_until_friday)
    end = start + timedelta(days=6)
    return start, end


def next_week_range(today: date) -> tuple[date, date]:
    """基準日の次の金曜週（翌週金曜〜その翌木曜）を返す。

    収集（ルーティン / theater_discover）用。publish が対象週の前日木曜に走るため、
    収集はその1週間前の金曜に回して承認の猶予を作る。week_range() は「これから来る
    金曜」を返すので金曜当日に実行すると当日起点になってしまい、収集には使えない。

    起点は「基準日が属する金曜週の金曜」の7日後。金〜木のどの曜日に実行しても同じ週を
    返すため、ルーティンPRのマージが土日にずれ込んでも対象週は動かない
    （vod_calendar.next_week_range() と同じ考え方）。

    例:
        金曜 2026-08-21 実行 → 2026-08-28〜2026-09-03
        月曜 2026-08-24 実行 → 2026-08-28〜2026-09-03
        木曜 2026-08-27 実行 → 2026-08-28〜2026-09-03
    """
    days_since_friday = (today.weekday() - 4) % 7
    start = today - timedelta(days=days_since_friday) + timedelta(days=7)
    return start, start + timedelta(days=6)


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
