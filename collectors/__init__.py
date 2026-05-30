"""VOD ラインナップ コレクター共通定義。

各サービスのラインナップ/新着ページから取得した作品を表す ``LineupItem`` と、
コレクター共通の定数を定義する。

``checkers/`` が「登録済み URL の配信状況を判定する」のに対し、
``collectors/`` は「サービス側のラインナップから新作を発見する」役割を持つ。
"""

from dataclasses import dataclass
from typing import Optional

# ラインナップ収集対象サービス（初期は 3 サービス）
LINEUP_SERVICES = ["unext", "netflix", "amazon_prime_video"]

# サービスキー → 表示ラベル（Slack 通知・ログ用）
SERVICE_LABELS: dict[str, str] = {
    "unext": "U-NEXT",
    "netflix": "Netflix",
    "amazon_prime_video": "Amazon Prime Video",
}


@dataclass
class LineupItem:
    """ラインナップ作品 1 件を表す正規化データ。

    Slack 通知・JSON 出力では ``title`` のみを使用するが、
    差分判定（``external_id``）や将来の拡張（``content_type`` 等）のため
    付随情報を内部で保持する。

    Attributes:
        service     : サービスキー（"unext" / "netflix" / "amazon_prime_video"）。
        title       : 表示タイトル。
        url         : 作品ページ URL（既存 checker の入力形式と互換）。
        original_title: 原題（英語）。
        external_id : サービス内の作品 ID（差分判定の一意キー）。
        release_year: 公開年。取得できなければ None。
        lang        : 言語コード（初期は "en" のみ通す）。
        content_type: 種別（"movie" / 将来 "anime" / "drama"）。
        collected_at: 収集日時 "YYYY-MM-DD HH:MM:SS"。
    """

    service: str
    title: str
    url: str = ""
    original_title: str = ""
    external_id: str = ""
    release_year: Optional[int] = None
    lang: str = "en"
    content_type: str = "movie"
    collected_at: str = ""

    @property
    def key(self) -> str:
        """差分判定用の一意キー ``{service}:{external_id}`` を返す。"""
        return f"{self.service}:{self.external_id}"
