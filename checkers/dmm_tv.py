"""DMM TV 配信状況チェッカー。

対象URL形式: https://info.tv.dmm.com/pr/season/{id}/
             https://tv.dmm.com/vod/detail/?season={id}

DMM TV は見放題・レンタル・購入の複合サービス。
Playwright を使用してブラウザレンダリング後のテキストを取得する。

判定ロジック:
- 「見放題で視聴する」「今すぐ見放題で視聴する」が含まれる → streaming
- 「レンタルして視聴する」＋価格 → rental
- 「購入して視聴する」＋価格 → purchase
- 上記いずれも含まれない → unavailable
- 404 → ended
"""

import logging
import re

from playwright.sync_api import sync_playwright

from checkers import NOT_FOUND_INDICATORS

logger = logging.getLogger(__name__)

# 見放題の判定キーワード
# 「DMMプレミアム」「見放題対象」は購入ページのプロモーション文言にも登場するため使用しない
STREAMING_INDICATORS = [
    "見放題で視聴する",
    "今すぐ見放題で視聴する",
    "見放題再生",
]

# 購入ボタンキーワード（改行を跨いで価格が続く）
PURCHASE_BUTTON = "購入して視聴する"

# レンタルボタンキーワード
RENTAL_BUTTON = "レンタルして視聴する"

# 価格抽出パターン（最初にマッチした円表記）
PRICE_PATTERN = re.compile(r"(\d[\d,]+)\s*円")

# 配信終了・未提供を示すキーワード
UNAVAILABLE_INDICATORS = [
    "配信終了",
    "現在配信していません",
    "取り扱いがありません",
]

# JS レンダリング待機時間（ミリ秒）
WAIT_MS = 3000


def _parse_price(price_str: str) -> float:
    """価格文字列を float に変換する（カンマ除去）。"""
    return float(price_str.replace(",", ""))


def _extract_price_after(text: str, keyword: str) -> float | None:
    """keyword 以降に最初に登場する円価格を返す。見つからなければ None。"""
    idx = text.find(keyword)
    if idx == -1:
        return None
    m = PRICE_PATTERN.search(text, idx)
    return _parse_price(m.group(1)) if m else None


class DmmTvChecker:
    """DMM TV の配信状況を確認するチェッカー。

    DMM TV は SPA のため Playwright でブラウザレンダリング後のテキストを取得して判定する。
    """

    def check(self, url: str) -> dict:
        """指定URLの配信状況を確認する。

        Args:
            url: チェック対象の DMM TV タイトルURL。

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
            raise RuntimeError(f"DMM TV: ブラウザ取得失敗 {e}") from e

        if status_code == 404:
            return {"status": "ended", "price": None}

        if status_code >= 500:
            raise RuntimeError(f"DMM TV: サーバーエラー (HTTP {status_code})")

        # 404 相当のコンテンツ確認
        for indicator in NOT_FOUND_INDICATORS:
            if indicator in page_text:
                return {"status": "ended", "price": None}

        # 配信終了・未提供確認
        for indicator in UNAVAILABLE_INDICATORS:
            if indicator in page_text:
                return {"status": "unavailable", "price": None}

        # 見放題確認
        for indicator in STREAMING_INDICATORS:
            if indicator in page_text:
                return {"status": "streaming", "price": 0}

        # レンタル確認（「レンタルして視聴する」ボタン ＋ 直後の価格）
        if RENTAL_BUTTON in page_text:
            price = _extract_price_after(page_text, RENTAL_BUTTON)
            return {"status": "rental", "price": price}

        # 購入確認（「購入して視聴する」ボタン ＋ 直後の価格）
        if PURCHASE_BUTTON in page_text:
            price = _extract_price_after(page_text, PURCHASE_BUTTON)
            return {"status": "purchase", "price": price}

        return {"status": "unavailable", "price": None}
