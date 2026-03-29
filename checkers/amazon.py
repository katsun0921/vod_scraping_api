"""Amazon Prime Video 配信状況チェッカー。

対象URL形式:
  https://www.amazon.co.jp/dp/{asin}
  https://watch.amazon.co.jp/detail?gti={id}

ロボット検出時は RuntimeError を raise する。
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
    "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
}

# ロボット検出ページの判定キーワード
ROBOT_INDICATORS = [
    "robot check",
    "ロボット",
    "captcha",
    "automated access",
    "verify you are a human",
    "api-services-support@amazon.com",
]

# Prime Video 見放題の判定キーワード
PRIME_INDICATORS = [
    "Primeで観る",
    "prime会員",
    "prime video",
    "プライムビデオ",
    "プライム会員特典",
    "Primeビデオ",
]

# aria-label から価格を抽出するパターン
# 例: "レンタル HD ￥500" / "購入 HD ￥1,500"
ARIA_PRICE_PATTERN = re.compile(r"[¥￥]\s*(\d[\d,]+)")

# テキストベースのフォールバックパターン（amazon.co.jp/dp/ 向け）
RENTAL_PATTERN_TEXT  = re.compile(r"レンタル[^\d]*[¥￥]?\s*(\d[\d,]+)", re.IGNORECASE)
PURCHASE_PATTERN_TEXT = re.compile(r"(?:HD|SD)?で?購入[^\d]*[¥￥]?\s*(\d[\d,]+)", re.IGNORECASE)


def _parse_price(price_str: str) -> float:
    """価格文字列を float に変換する（カンマ除去）。"""
    return float(price_str.replace(",", ""))


class AmazonChecker:
    """Amazon Prime Video の配信状況を確認するチェッカー。"""

    def check(self, url: str) -> dict:
        """指定URLの配信状況を確認する。

        Args:
            url: チェック対象のAmazon 商品URL。

        Returns:
            {"status": str, "price": float | None} の辞書。

        Raises:
            RuntimeError: ロボット検出時またはリクエスト失敗時。
        """
        try:
            response = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        except requests.RequestException as e:
            raise RuntimeError(f"Amazon: リクエスト失敗 {e}") from e

        if response.status_code == 404:
            return {"status": "ended", "price": None}

        if response.status_code >= 500:
            raise RuntimeError(f"Amazon: サーバーエラー (HTTP {response.status_code})")

        soup = BeautifulSoup(response.text, "lxml")
        page_text = soup.get_text()
        page_text_lower = page_text.lower()

        # ロボット検出確認
        for indicator in ROBOT_INDICATORS:
            if indicator.lower() in page_text_lower:
                raise RuntimeError("Amazon: ロボット検出")

        # 商品が存在しない場合
        title_tag = soup.find("title")
        if title_tag:
            title_text = title_tag.get_text().lower()
            if "page not found" in title_text or "ページが見つかりません" in title_text:
                return {"status": "ended", "price": None}

        # --- aria-label ベースの検出（amazon.co.jp/gp/video/ 向け） ---
        # <a> タグの "プライムに登録" で Prime 見放題を判定する
        # レンタル・購入ボタンが同時に存在しても streaming を優先する
        is_prime = any(
            "プライムに登録" in el.get("aria-label", "")
            for el in soup.find_all("a", attrs={"aria-label": True})
        )

        rental_result   = None
        purchase_result = None

        for btn in soup.find_all("button", attrs={"aria-label": True}):
            label = btn.get("aria-label", "")
            price_match = ARIA_PRICE_PATTERN.search(label)

            if "レンタル" in label and price_match:
                print(f"[Amazon] aria-label 検出: rental / label={label!r}")
                rental_result = {"status": "rental", "price": _parse_price(price_match.group(1))}

            elif "購入" in label and price_match:
                print(f"[Amazon] aria-label 検出: purchase / label={label!r}")
                purchase_result = {"status": "purchase", "price": _parse_price(price_match.group(1))}

        if is_prime:
            print("[Amazon] aria-label 検出: streaming / label='プライムに登録'")
            return {"status": "streaming", "price": 0}
        if rental_result:
            return rental_result
        if purchase_result:
            return purchase_result

        # --- テキストベースのフォールバック（amazon.co.jp/dp/ 向け） ---
        for indicator in PRIME_INDICATORS:
            if indicator in page_text:
                print(f"[Amazon] テキスト検出: streaming / keyword={indicator!r}")
                return {"status": "streaming", "price": 0}

        rental_match = RENTAL_PATTERN_TEXT.search(page_text)
        if rental_match:
            return {"status": "rental", "price": _parse_price(rental_match.group(1))}

        purchase_match = PURCHASE_PATTERN_TEXT.search(page_text)
        if purchase_match:
            return {"status": "purchase", "price": _parse_price(purchase_match.group(1))}

        return {"status": "unavailable", "price": None}
