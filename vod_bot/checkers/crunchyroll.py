"""Crunchyroll 配信状況チェッカー。

対象URL形式:
    https://www.crunchyroll.com/series/{ID}/{slug}
    https://www.crunchyroll.com/watch/{ID}/{slug}

Crunchyroll はサブスクリプション型のみ（見放題）のため、
price は常に None を返す。

Crunchyroll は Cloudflare で保護されており requests では 403 を返すため
Playwright を使用してブラウザレンダリング後のテキストを取得する。

判定ロジック:
  - HTTP 404                                       → status=ended
  - "Start Watching" / "Watch Now" 等が含まれる    → status=streaming
  - "Not available" / "not found" 等が含まれる     → status=ended
  - 上記いずれも含まれない（200）                  → status=unavailable
"""

import logging

from playwright.sync_api import sync_playwright

from checkers import NOT_FOUND_INDICATORS
from utils.browser import USER_AGENT

logger = logging.getLogger(__name__)

# 配信中を示す英語キーワード（Crunchyroll は英語 UI が基本）
STREAMING_INDICATORS = [
    "Start Watching",
    "Watch Now",
    "Free Trial",
    "Start Free Trial",
    "Add to Watchlist",
    "Resume Watching",
]

# 未配信・終了を示すキーワード
UNAVAILABLE_INDICATORS = [
    "not available in your region",
    "not available",
    "coming soon",
]

# JS レンダリング待機時間（ミリ秒）
WAIT_MS = 4000


class CrunchyrollChecker:
    """Crunchyroll の配信状況を確認するチェッカー。

    Crunchyroll は Cloudflare 保護のため Playwright でブラウザレンダリング後の
    テキストを取得して判定する。
    """

    def check(self, url: str) -> dict:
        """指定URLの配信状況を確認する。

        Args:
            url: チェック対象の Crunchyroll タイトルURL。

        Returns:
            {"status": str, "price": None} の辞書。
            Crunchyroll はサブスクのみのため price は常に None。

        Raises:
            RuntimeError: ブラウザ起動失敗またはページ取得失敗時。
        """
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(
                    # Crunchyroll は英語 UI で判定するため locale を en-US に設定
                    locale="en-US",
                    user_agent=USER_AGENT,
                )
                page = context.new_page()
                response = page.goto(url, timeout=30000)
                page.wait_for_timeout(WAIT_MS)

                status_code = response.status if response else 200
                page_text = page.inner_text("body")
                browser.close()
        except Exception as e:
            raise RuntimeError(f"Crunchyroll: ブラウザ取得失敗 {e}") from e

        if status_code == 404:
            return {"status": "ended", "price": None}

        if status_code >= 500:
            raise RuntimeError(f"Crunchyroll: サーバーエラー (HTTP {status_code})")

        page_text_lower = page_text.lower()

        # 404 相当のコンテンツ確認
        for indicator in NOT_FOUND_INDICATORS:
            if indicator in page_text_lower:
                return {"status": "ended", "price": None}

        # 未配信・地域制限確認（streaming より先に判定）
        for indicator in UNAVAILABLE_INDICATORS:
            if indicator in page_text_lower:
                return {"status": "unavailable", "price": None}

        # 配信中確認（ボタンテキスト等で判定）
        for indicator in STREAMING_INDICATORS:
            if indicator in page_text:  # 大文字小文字を保持して照合
                return {"status": "streaming", "price": None}

        if status_code == 200:
            return {"status": "unavailable", "price": None}

        return {"status": "unavailable", "price": None}
