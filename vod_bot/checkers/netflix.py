"""Netflix 配信状況チェッカー。

対象URL形式: https://www.netflix.com/jp/title/{id}

Netflix はサブスクリプション型のみのため、配信中であれば status=streaming を返す。
404 や非公開の場合は status=ended を返す。
"""

import requests
from bs4 import BeautifulSoup

from checkers import HEADERS, NOT_FOUND_INDICATORS


class NetflixChecker:
    """Netflix の配信状況を確認するチェッカー。"""

    def check(self, url: str) -> dict:
        """指定URLの配信状況を確認する。

        Args:
            url: チェック対象のNetflix タイトルURL。

        Returns:
            {"status": str, "price": None} の辞書。

        Raises:
            RuntimeError: リクエスト失敗またはパースエラー時。
        """
        try:
            response = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        except requests.RequestException as e:
            raise RuntimeError(f"Netflix: リクエスト失敗 {e}") from e

        if response.status_code == 404:
            return {"status": "ended", "price": None}

        if response.status_code >= 500:
            raise RuntimeError(f"Netflix: サーバーエラー (HTTP {response.status_code})")

        soup = BeautifulSoup(response.text, "lxml")
        page_text = soup.get_text().lower()

        # 404相当のコンテンツ確認
        for indicator in NOT_FOUND_INDICATORS:
            if indicator in page_text:
                return {"status": "ended", "price": None}

        # タイトルページが正常に返れば配信中（Netflix はサブスクのみ）
        title_tag = soup.find("title")
        if title_tag and "netflix" in title_tag.get_text().lower():
            return {"status": "streaming", "price": 0}

        # ログインリダイレクト等で判定不能な場合も配信中扱い（URLが存在する）
        if response.status_code == 200:
            return {"status": "streaming", "price": 0}

        return {"status": "unavailable", "price": None}
