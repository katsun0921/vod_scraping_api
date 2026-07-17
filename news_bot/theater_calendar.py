"""週範囲計算・タイトル正規化・重複キー生成。

仕様書 6.（取得対象期間）/ 9.（重複判定）: `docs/feature/theater-release-calendar-spec.md`

初期MVPの対象期間は「直近の金曜日〜その翌週木曜日」の7日間固定。
重複判定キーは「公開日 + 正規化タイトル」の完全一致のみ（フェーズ1スコープ）。
tmdb_id・類似度判定によるあいまい一致は将来拡張（仕様書9.将来拡張）。
"""

import re
import unicodedata
from datetime import date, timedelta

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
