"""U-NEXT ラインナップ コレクター。

U-NEXT 公式プレスルームの月次ラインナップ特集ページから新着作品を収集する。

対象 URL（月次で予測可能なパターン）:
    https://www.unext.co.jp/press-room/{YYYY-MM}-unext-lineup
    例: https://www.unext.co.jp/press-room/2026-06-unext-lineup

`video.unext.jp` のアプリ（SPA）ではなくプレスルーム記事を対象とするため、
原則 requests + BeautifulSoup で取得できる（JS レンダリング不要）。
ただしサイト側の bot 保護で 403 になる場合は Playwright へのフォールバックを検討する。

初期対象は「洋画（英語作品）」セクションのみ。将来アニメ・ドラマへ拡張する。
"""

import logging
import re
from datetime import date
from typing import Optional

import requests
from bs4 import BeautifulSoup

from checkers import HEADERS
from collectors import LineupItem
from collectors.base import BaseCollector

logger = logging.getLogger(__name__)

# プレスルーム月次ラインナップ URL テンプレート
_LINEUP_URL_TEMPLATE = "https://www.unext.co.jp/press-room/{cycle}-unext-lineup"

# 洋画セクションを示す見出しキーワード（実ページの見出し文言に合わせて調整する）
_FOREIGN_MOVIE_HEADINGS = ["洋画", "海外映画"]

# タイトルから配信日などの付随表記を除去するための補助パターン（必要に応じて調整）
_DATE_SUFFIX_PATTERN = re.compile(r"\s*[（(]\s*\d{1,2}\s*月\s*\d{1,2}\s*日.*$")


def build_lineup_url(cycle: str) -> str:
    """サイクル "YYYY-MM" からプレスルーム特集ページ URL を組み立てる。

    Args:
        cycle: 対象サイクル "YYYY-MM"（例: "2026-06"）。

    Returns:
        プレスルーム特集ページの URL。
    """
    return _LINEUP_URL_TEMPLATE.format(cycle=cycle)


class UnextCollector(BaseCollector):
    """U-NEXT プレスルームから月次ラインナップを収集するコレクター。"""

    service = "unext"

    def __init__(self, cycle: Optional[str] = None) -> None:
        """コレクターを初期化する。

        Args:
            cycle: 対象サイクル "YYYY-MM"。None の場合は今月を使用する。
        """
        self.cycle = cycle or date.today().strftime("%Y-%m")

    def collect(self, limit: Optional[int] = None) -> list[LineupItem]:
        """プレスルーム特集ページから洋画ラインナップを収集する。

        Args:
            limit: 最大取得件数。None なら全件。

        Returns:
            LineupItem のリスト。

        Raises:
            RuntimeError: 取得失敗・bot 検出・サーバーエラー時。
        """
        url = build_lineup_url(self.cycle)
        html = self._fetch(url)
        items = self._parse(html)

        if limit is not None:
            items = items[:limit]

        logger.info("U-NEXT collect: cycle=%s url=%s items=%d", self.cycle, url, len(items))
        return items

    # ── 取得 ────────────────────────────────────────────────

    def _fetch(self, url: str) -> str:
        """特集ページの HTML を取得する。

        Args:
            url: 取得対象 URL。

        Returns:
            HTML 文字列。

        Raises:
            RuntimeError: リクエスト失敗・404・サーバーエラー時。
        """
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20, allow_redirects=True)
        except requests.RequestException as e:
            raise RuntimeError(f"U-NEXT lineup: リクエスト失敗 {e}") from e

        if resp.status_code == 404:
            raise RuntimeError(f"U-NEXT lineup: ページなし (HTTP 404) url={url}")
        if resp.status_code == 403:
            # bot 保護による拒否。Playwright フォールバックの検討対象。
            raise RuntimeError(f"U-NEXT lineup: アクセス拒否 (HTTP 403) url={url}")
        if resp.status_code >= 500:
            raise RuntimeError(f"U-NEXT lineup: サーバーエラー (HTTP {resp.status_code})")

        return resp.text

    # ── パース ──────────────────────────────────────────────

    def _parse(self, html: str) -> list[LineupItem]:
        """特集ページ HTML から洋画タイトルを抽出する。

        ⚠️ 実ページの DOM 構造に依存するため、セレクタは実 HTML で確定する必要がある。
        現状は「洋画見出しに続くリスト項目を拾う」ヒューリスティックな実装で、
        実 HTML 確認後に確定セレクタへ差し替える（docs/vod-lineup-todo.md Phase 2）。

        Args:
            html: 特集ページの HTML。

        Returns:
            LineupItem のリスト。
        """
        soup = BeautifulSoup(html, "lxml")
        titles = self._extract_foreign_movie_titles(soup)

        now_str = date.today().strftime("%Y-%m-%d %H:%M:%S")
        items: list[LineupItem] = []
        seen: set[str] = set()
        for title in titles:
            clean = self._clean_title(title)
            if not clean or clean in seen:
                continue
            seen.add(clean)
            items.append(
                LineupItem(
                    service=self.service,
                    title=clean,
                    external_id=self._make_external_id(clean),
                    lang="en",
                    content_type="movie",
                    collected_at=now_str,
                )
            )
        return items

    def _extract_foreign_movie_titles(self, soup: BeautifulSoup) -> list[str]:
        """洋画セクション配下のタイトル文字列を抽出する（ヒューリスティック）。

        実 HTML 確認後に確定セレクタへ差し替える。
        見出し（h2〜h4 等）に「洋画」を含む要素を探し、その直後の
        リスト/テーブルからタイトルを拾う想定。
        """
        titles: list[str] = []
        for heading in soup.find_all(re.compile(r"^h[1-6]$")):
            heading_text = heading.get_text(strip=True)
            if not any(kw in heading_text for kw in _FOREIGN_MOVIE_HEADINGS):
                continue
            # 見出しに続く要素から、次の見出しが現れるまでの範囲を収集する
            for el in heading.find_all_next(limit=500):
                if el.name and re.fullmatch(r"h[1-6]", el.name):
                    break  # 次セクションの見出しに到達したら打ち切る
                if el.name in ("li", "td", "a"):
                    text = el.get_text(strip=True)
                    if text:
                        titles.append(text)
            break  # 最初の洋画見出しのみ対象（実構造に応じて調整）
        return titles

    @staticmethod
    def _clean_title(raw: str) -> str:
        """タイトル文字列から配信日などの付随表記を除去する。"""
        return _DATE_SUFFIX_PATTERN.sub("", raw).strip()

    @staticmethod
    def _make_external_id(title: str) -> str:
        """SID が取れないため、タイトルから差分判定用の安定キーを生成する。

        タイトル文字列をそのまま external_id に用いる（差分判定はタイトル単位）。
        将来 SID リンクが取得できる場合は SID に差し替える。
        """
        return title
