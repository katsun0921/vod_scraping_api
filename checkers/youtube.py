"""YouTube 配信状況チェッカー。

対象URL形式:
    https://www.youtube.com/watch?v={video_id}
    https://youtu.be/{video_id}

YouTube は無料公開動画を streaming として扱う。

判定ロジック:
    - og:title メタタグが存在し、内容が空でない → streaming（無料公開）
    - og:title がない、またはタイトルが " - YouTube" のみ → ended（削除・非公開）
    - サーバーエラー (5xx) → RuntimeError
"""

import logging

import requests
from bs4 import BeautifulSoup

from checkers import HEADERS

logger = logging.getLogger(__name__)


class YoutubeChecker:
    """YouTube の配信状況を確認するチェッカー。

    無料公開動画は streaming、削除・非公開は ended を返す。
    """

    def check(self, url: str) -> dict:
        """指定URLの配信状況を確認する。

        Args:
            url: チェック対象の YouTube 動画URL。

        Returns:
            {"status": str, "price": float | None} の辞書。

        Raises:
            RuntimeError: ネットワークエラーまたはサーバーエラー時。
        """
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30, allow_redirects=True)
        except requests.RequestException as e:
            raise RuntimeError(f"YouTube: リクエスト失敗 {e}") from e

        if resp.status_code >= 500:
            raise RuntimeError(f"YouTube: サーバーエラー (HTTP {resp.status_code})")

        soup = BeautifulSoup(resp.text, "html.parser")

        # og:title が存在し内容があれば無料公開動画
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content", "").strip():
            return {"status": "streaming", "price": 0}

        # og:title がない = 削除・非公開・存在しない動画
        logger.debug("YouTube: og:title なし → ended url=%s", url)
        return {"status": "ended", "price": None}
