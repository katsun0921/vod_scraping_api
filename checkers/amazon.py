"""Amazon Prime Video 配信状況チェッカー。

対象URL形式:
  https://www.amazon.co.jp/dp/{asin}          （日本版）
  https://watch.amazon.co.jp/detail?gti={id}  （日本版）
  https://www.amazon.com/gp/video/detail/{asin} （米国版・英語UI）

ロボット検出時は RuntimeError を raise する。
"""

import re

import requests
from bs4 import BeautifulSoup

from checkers import HEADERS

# ロボット検出ページの判定キーワード
ROBOT_INDICATORS = [
    "robot check",
    "ロボット",
    "captcha",
    "automated access",
    "verify you are a human",
    "api-services-support@amazon.com",
]

# Prime Video 見放題の判定キーワード（テキストフォールバック用）
PRIME_INDICATORS = [
    "Primeで観る",
    "prime会員",
    "prime video",
    "プライムビデオ",
    "プライム会員特典",
    "Primeビデオ",
]

# ページ埋め込み JSON 内のアクションタイプマーカー（日本版・米国版共通）
# SUBSCRIBE : Prime チャンネル導線（見放題自体を意味しないため単独では使わない）
# TRANSACT  : レンタル/購入の取引導線（存在する = 有料でしか視聴できない）
# SUBSCRIBE が存在し TRANSACT が存在しない場合のみ Prime 見放題と判定する
SUBSCRIBE_MARKER = '"actionType":"SUBSCRIBE"'
TRANSACT_MARKER = '"actionType":"TRANSACT"'

# Prime 見放題を示す aria-label（部分一致、大文字小文字区別なし。フォールバック用）
# 日本版: "プライムに登録"（未加入者への訴求ボタン）
PRIME_ARIA_LABELS = [
    "プライムに登録",
]

# レンタル/購入を示す aria-label のキーワード（日本語・英語）
RENTAL_ARIA_KEYWORDS = ["レンタル", "rent"]
PURCHASE_ARIA_KEYWORDS = ["購入", "buy"]

# aria-label から価格を抽出するパターン
# 例: "レンタル HD ￥500" / "購入 HD ￥1,500" / "Rent UHD $3.99" / "Buy UHD $19.99"
ARIA_PRICE_PATTERN = re.compile(r"[¥￥$]\s*(\d[\d,]*\.?\d*)")

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

        # --- Prime 見放題判定（日本版・米国版共通） ---
        # SUBSCRIBE マーカーがあり TRANSACT（レンタル/購入導線）が無い場合のみ見放題と判定する。
        # SUBSCRIBE 単体は Prime チャンネル誘導など見放題以外の文脈でも出現するため、
        # TRANSACT の不在と組み合わせて誤検知を防ぐ。aria-label はフォールバック。
        has_subscribe = SUBSCRIBE_MARKER in response.text
        has_transact = TRANSACT_MARKER in response.text
        is_prime = (has_subscribe and not has_transact) or any(
            any(kw in (el.get("aria-label", "") or "").lower() for kw in PRIME_ARIA_LABELS)
            for el in soup.find_all(["a", "button"], attrs={"aria-label": True})
        )

        rental_result   = None
        purchase_result = None

        for btn in soup.find_all("button", attrs={"aria-label": True}):
            label = btn.get("aria-label", "")
            label_lower = label.lower()
            price_match = ARIA_PRICE_PATTERN.search(label)

            if any(kw in label_lower for kw in RENTAL_ARIA_KEYWORDS) and price_match:
                print(f"[Amazon] aria-label 検出: rental / label={label!r}")
                rental_result = {"status": "rental", "price": _parse_price(price_match.group(1))}

            elif any(kw in label_lower for kw in PURCHASE_ARIA_KEYWORDS) and price_match:
                print(f"[Amazon] aria-label 検出: purchase / label={label!r}")
                purchase_result = {"status": "purchase", "price": _parse_price(price_match.group(1))}

        if is_prime:
            print("[Amazon] aria-label 検出: streaming")
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
