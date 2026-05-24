"""Apple TV チェッカー。

対象URL形式: https://tv.apple.com/jp/movie/{slug}/{id}
             https://tv.apple.com/us/movie/{slug}/{id}

判定ロジック:
  - 404                         → status=ended
  - "offerType": "subscribe" あり → status=streaming（Apple TV+ 見放題）
  - Movie / Show JSON-LD あり   → status=purchase（iTunes Store 購入・レンタル）
  - その他 200                  → status=unavailable

注意:
  Apple TV は価格情報が JS レンダリング後に表示されるため、
  requests + BeautifulSoup では詳細価格を取得できない。
  purchase 時の price は None を返す。
"""

import json
import re

import requests
from bs4 import BeautifulSoup

from checkers import HEADERS, NOT_FOUND_INDICATORS


class AppleTvChecker:
    """Apple TV の配信状況を確認するチェッカー。"""

    # ページ HTML 内で Apple TV+ 見放題を示す offerType 値
    _SUBSCRIBE_PATTERN = re.compile(r'"offerType"\s*:\s*"subscribe"', re.IGNORECASE)

    def check(self, url: str) -> dict:
        """指定URLの配信状況を確認する。

        Args:
            url: チェック対象の Apple TV タイトルURL。

        Returns:
            {"status": str, "price": float | None} の辞書。

        Raises:
            RuntimeError: リクエスト失敗またはサーバーエラー時。
        """
        try:
            response = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        except requests.RequestException as e:
            raise RuntimeError(f"AppleTV: リクエスト失敗 {e}") from e

        if response.status_code == 404:
            return {"status": "ended", "price": None}

        if response.status_code >= 500:
            raise RuntimeError(f"AppleTV: サーバーエラー (HTTP {response.status_code})")

        response.encoding = "utf-8"
        html = response.text
        soup = BeautifulSoup(html, "lxml")
        page_text = soup.get_text().lower()

        # NOT_FOUND 相当のコンテンツ確認
        for indicator in NOT_FOUND_INDICATORS:
            if indicator in page_text:
                return {"status": "ended", "price": None}

        # Apple TV+ 見放題判定（"offerType": "subscribe" が HTML 内に存在）
        if self._SUBSCRIBE_PATTERN.search(html):
            return {"status": "streaming", "price": None}

        # iTunes Store 購入・レンタル判定（Movie / Show の JSON-LD が存在）
        if self._has_content_jsonld(soup):
            return {"status": "purchase", "price": None}

        if response.status_code == 200:
            return {"status": "unavailable", "price": None}

        return {"status": "unavailable", "price": None}

    def _has_content_jsonld(self, soup: BeautifulSoup) -> bool:
        """JSON-LD に Movie / TVSeries / TVEpisode が存在するか確認する。"""
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                content_type = data.get("@type", "")
                if content_type in ("Movie", "TVSeries", "TVEpisode", "TVSeason"):
                    return True
            except (json.JSONDecodeError, AttributeError):
                continue
        return False
