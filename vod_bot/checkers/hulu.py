"""Hulu 配信状況チェッカー。

対象URL形式:
  JP: https://www.hulu.jp/watch/{id}
  US: https://www.hulu.com/watch/{id}

Hulu はサブスクリプション型のみのため、配信中であれば status=streaming を返す。
404 や非公開の場合は status=ended を返す。
"""

import requests
from bs4 import BeautifulSoup

from checkers import HEADERS, NOT_FOUND_INDICATORS


class HuluChecker:
    """Hulu の配信状況を確認するチェッカー。"""

    def check(self, url: str) -> dict:
        """指定URLの配信状況を確認する。

        Args:
            url: チェック対象の Hulu タイトルURL。

        Returns:
            {"status": str, "price": None} の辞書。

        Raises:
            RuntimeError: リクエスト失敗またはサーバーエラー時。
        """
        try:
            response = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        except requests.RequestException as e:
            raise RuntimeError(f"Hulu: リクエスト失敗 {e}") from e

        if response.status_code == 404:
            return {"status": "ended", "price": None}

        if response.status_code >= 500:
            raise RuntimeError(f"Hulu: サーバーエラー (HTTP {response.status_code})")

        soup = BeautifulSoup(response.text, "lxml")
        page_text = soup.get_text().lower()

        for indicator in NOT_FOUND_INDICATORS:
            if indicator in page_text:
                return {"status": "ended", "price": None}

        if response.status_code == 200:
            return {"status": "streaming", "price": 0}

        return {"status": "unavailable", "price": None}
