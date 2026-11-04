"""対象期間・重複キー生成（VOD配信情報収集パイプライン専用）。

仕様書 6.（取得対象期間）/ 9.（重複判定）: `docs/feature/vod-release-calendar-spec.md`

タイトル正規化・期間内判定・対象期間（暦月）の計算は theater_calendar.py のロジックを
そのまま流用する（仕様書9.: 「theater仕様9.の正規化ルールを流用」）。以前は週次公開で
VODだけ月曜始まりの週を使っていたが、月次公開へ移行して劇場と同じ暦月区切りになった。
VOD固有なのは重複判定キーにサービスを含める点。
"""

from dataclasses import dataclass
from datetime import date

from news_bot.theater_calendar import (
    in_range,
    month_range,
    next_month_range,
    normalize_title,
)

__all__ = [
    "SERVICES",
    "VodEntry",
    "dedupe_key",
    "in_range",
    "month_range",
    "next_month_range",
    "normalize_title",
]

# 対象サービス（仕様書6.）。キーは vod_bot/wordpress.py の VOD_TERM_IDS と揃えた命名規則。
# 表示名はAIへの検索プロンプト用、discover_vod._service_key()で応答テキストをキーへ逆変換する。
#
# discover_vod.py ではなく本モジュールに置いているのは、SERVICES と VodEntry が
# AI SDK（anthropic / openai）に依存しない純粋なデータ定義であるため。discover_vod に
# 置くと、ルーティン成果物を読むだけの import_routine.py までSDKのインストールを
# 要求してしまう。
SERVICES = {
    "netflix": "Netflix",
    "amazon_prime_video": "Amazon Prime Video",
    "unext": "U-NEXT",
    "disney_plus": "Disney+",
    "hulu": "Hulu",
    "dmm_tv": "DMM TV",
}


@dataclass
class VodEntry:
    title: str
    service: str  # SERVICESのキー（netflix / unext 等）
    available_from: date
    source: str
    title_orig: str = ""
    availability_type: str = ""  # 見放題・レンタル・購入・独占 等
    url: str = ""
    category: str = ""  # 映画・ドラマ・アニメ


def dedupe_key(release_date: str, service: str, title: str) -> str:
    """「配信開始日 + サービス + 正規化タイトル」の重複判定キーを生成する（仕様書9.）。"""
    return f"{release_date}|{service}|{normalize_title(title)}"
