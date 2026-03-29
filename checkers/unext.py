"""U-NEXT 配信状況チェッカー。

対象URL形式: https://video.unext.jp/title/SID{id}
"""

import re

import requests
from bs4 import BeautifulSoup


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja-JP,ja;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# 見放題の判定キーワード
STREAMING_INDICATORS = [
    "見放題",
    "U-NEXT見放題",
    "ポイント不要",
]

# レンタル価格パターン (例: "330ポイント" / "レンタル 330pt")
RENTAL_PATTERN = re.compile(r"レンタル[^\d]*(\d[\d,]+)\s*(?:ポイント|pt|円)", re.IGNORECASE)
RENTAL_PT_PATTERN = re.compile(r"(\d[\d,]+)\s*(?:ポイント|pt)(?!\s*不要)", re.IGNORECASE)

# 購入価格パターン
PURCHASE_PATTERN = re.compile(r"購入[^\d]*(\d[\d,]+)\s*(?:ポイント|pt|円)", re.IGNORECASE)

NOT_FOUND_INDICATORS = [
    "ページが見つかりません",
    "404",
    "not found",
    "お探しのページは見つかりませんでした",
]


def _parse_price(price_str: str) -> float:
    """価格文字列を float に変換する（カンマ除去）。"""
    return float(price_str.replace(",", ""))


class UnextChecker:
    """U-NEXT の配信状況を確認するチェッカー。"""

    def check(self, url: str) -> dict:
        """指定URLの配信状況を確認する。

        Args:
            url: チェック対象のU-NEXT タイトルURL。

        Returns:
            {"status": str, "price": float | None} の辞書。

        Raises:
            RuntimeError: リクエスト失敗時。
        """
        try:
            response = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        except requests.RequestException as e:
            raise RuntimeError(f"U-NEXT: リクエスト失敗 {e}") from e

        if response.status_code == 404:
            return {"status": "ended", "price": None}

        if response.status_code >= 500:
            raise RuntimeError(f"U-NEXT: サーバーエラー (HTTP {response.status_code})")

        soup = BeautifulSoup(response.text, "lxml")
        page_text = soup.get_text()

        # 404相当のコンテンツ確認
        for indicator in NOT_FOUND_INDICATORS:
            if indicator in page_text:
                return {"status": "ended", "price": None}

        # 見放題確認（ポイント不要キーワード優先）
        for indicator in STREAMING_INDICATORS:
            if indicator in page_text:
                return {"status": "streaming", "price": 0}

        # レンタル確認
        rental_match = RENTAL_PATTERN.search(page_text)
        if rental_match:
            price = _parse_price(rental_match.group(1))
            return {"status": "rental", "price": price}

        # 購入確認
        purchase_match = PURCHASE_PATTERN.search(page_text)
        if purchase_match:
            price = _parse_price(purchase_match.group(1))
            return {"status": "purchase", "price": price}

        # ポイント消費型（レンタル相当）の汎用パターン
        pt_match = RENTAL_PT_PATTERN.search(page_text)
        if pt_match:
            price = _parse_price(pt_match.group(1))
            return {"status": "rental", "price": price}

        if response.status_code == 200:
            return {"status": "unavailable", "price": None}

        return {"status": "unavailable", "price": None}
