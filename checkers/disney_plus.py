"""Disney+ 配信状況チェッカー。

対象URL形式: https://www.disneyplus.com/ja-jp/browse/entity-{id}
             https://www.disneyplus.com/ja-jp/movies/{slug}
             https://www.disneyplus.com/ja-jp/series/{slug}

Disney+ は見放題（サブスクリプション）専用サービスのため、
配信中であれば streaming を返す。

判定ロジック:
- ページ <title> に「を配信で見る」が含まれる → streaming
- ページ <title> に「Disney+(ディズニープラス)」が含まれる → streaming
- 配信終了・未提供キーワードが含まれる → unavailable
- 404 → ended
"""

import logging

import requests
from bs4 import BeautifulSoup

from checkers import HEADERS, NOT_FOUND_INDICATORS

logger = logging.getLogger(__name__)

# タイトルに含まれる配信中キーワード
TITLE_STREAMING_INDICATORS = [
    "を配信で見る",
    "ディズニープラス",
]

# 配信終了・未提供を示すキーワード（本文）
UNAVAILABLE_INDICATORS = [
    "お住まいの地域ではご利用いただけません",
    "このコンテンツはご利用いただけません",
    "見つかりません",
]


class DisneyPlusChecker:
    """Disney+ の配信状況を確認するチェッカー。

    Disney+ は見放題専用サービスのため、タイトルに配信中キーワードがあれば streaming を返す。
    """

    def check(self, url: str) -> dict:
        """指定URLの配信状況を確認する。

        Args:
            url: チェック対象の Disney+ タイトルURL。

        Returns:
            {"status": str, "price": float | None} の辞書。

        Raises:
            RuntimeError: ネットワークエラーまたはサーバーエラー時。
        """
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30, allow_redirects=True)
        except requests.RequestException as e:
            raise RuntimeError(f"Disney+: リクエスト失敗 {e}") from e

        if resp.status_code == 404:
            return {"status": "ended", "price": None}

        if resp.status_code >= 500:
            raise RuntimeError(f"Disney+: サーバーエラー (HTTP {resp.status_code})")

        soup = BeautifulSoup(resp.text, "html.parser")
        title = soup.title.string if soup.title else ""

        # 404 相当のコンテンツ確認（本文）
        for tag in soup(["script", "style"]):
            tag.decompose()
        body_text = soup.get_text(separator=" ", strip=True)

        for indicator in NOT_FOUND_INDICATORS:
            if indicator in body_text:
                return {"status": "ended", "price": None}

        # 配信中: タイトルに「を配信で見る」「ディズニープラス」が含まれる
        for indicator in TITLE_STREAMING_INDICATORS:
            if indicator in title:
                return {"status": "streaming", "price": 0}

        # 配信不可コンテンツ確認
        for indicator in UNAVAILABLE_INDICATORS:
            if indicator in body_text:
                return {"status": "unavailable", "price": None}

        logger.debug("Disney+: 判定キーワード未検出 url=%s title=%s", url, title)
        return {"status": "unavailable", "price": None}
