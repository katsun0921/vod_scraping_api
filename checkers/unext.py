"""U-NEXT 配信状況チェッカー。

対象URL形式: https://video.unext.jp/title/SID{id}

U-NEXT は SPA（React）のため requests では JS レンダリング後のコンテンツが
取得できない。Playwright を使用してブラウザレンダリング後のテキストを取得する。
"""

import re

from playwright.sync_api import sync_playwright

from checkers import NOT_FOUND_INDICATORS

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

# JS レンダリング待機時間（ミリ秒）
WAIT_MS = 3000


def _parse_price(price_str: str) -> float:
    """価格文字列を float に変換する（カンマ除去）。"""
    return float(price_str.replace(",", ""))


class UnextChecker:
    """U-NEXT の配信状況を確認するチェッカー。

    Playwright でブラウザレンダリング後のテキストを取得して判定する。
    """

    def check(self, url: str) -> dict:
        """指定URLの配信状況を確認する。

        Args:
            url: チェック対象のU-NEXT タイトルURL。

        Returns:
            {"status": str, "price": float | None} の辞書。

        Raises:
            RuntimeError: ブラウザ起動失敗またはページ取得失敗時。
        """
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                response = page.goto(url, timeout=30000)
                page.wait_for_timeout(WAIT_MS)

                status_code = response.status if response else 200
                page_text = page.inner_text("body")
                browser.close()
        except Exception as e:
            raise RuntimeError(f"U-NEXT: ブラウザ取得失敗 {e}") from e

        if status_code == 404:
            return {"status": "ended", "price": None}

        if status_code >= 500:
            raise RuntimeError(f"U-NEXT: サーバーエラー (HTTP {status_code})")

        # 404相当のコンテンツ確認
        for indicator in NOT_FOUND_INDICATORS:
            if indicator in page_text:
                return {"status": "ended", "price": None}

        # 見放題確認
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

        return {"status": "unavailable", "price": None}
