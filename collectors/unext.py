"""U-NEXT ラインナップ コレクター。

U-NEXT 公式プレスルームの月次ラインナップ特集ページから新着作品を収集する。

対象 URL（月次で予測可能なパターン）:
    https://www.unext.co.jp/press-room/{YYYY-MM}-unext-lineup
    例: https://www.unext.co.jp/press-room/2026-06-unext-lineup

`video.unext.jp` のアプリ（SPA）ではなくプレスルーム記事を対象とするため、
原則 requests + BeautifulSoup で取得できる（JS レンダリング不要）。
ただしサイト側の bot 保護で 403 になる場合は Playwright へのフォールバックを検討する。

初期対象は「映画カテゴリ（洋画・邦画）」。洋画は lang="en"、邦画は lang="ja" として扱う。
将来アニメ・ドラマへ拡張する。
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

# 映画カテゴリの見出し文言 → 言語コード（プレスルーム「注目ラインナップ」の h4 見出し）
# 洋画＝海外作品（en）、邦画＝国内作品（ja）。ドラマ・アニメは映画ではないため対象外。
_MOVIE_CATEGORIES: dict[str, str] = {
    "洋画": "en",
    "海外映画": "en",
    "邦画": "ja",
    "国内映画": "ja",
}

# カテゴリ見出しに使われるタグ（このタグに到達したらセクション終端）
_HEADING_TAGS = ["h2", "h3", "h4"]

# 日付ラベル行の判定（例: "6月1日（月）" / "配信中"）。タイトルではないので除外する。
_DATE_LINE_PATTERN = re.compile(r"^\s*(配信中|\d{1,2}\s*月\s*\d{1,2}\s*日)")

# タイトル末尾の注記（【独占】【独占先行】等）を除去するパターン
_ANNOTATION_PATTERN = re.compile(r"【[^】]*】")


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
        """プレスルーム特集ページから映画ラインナップ（洋画・邦画）を収集する。

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
        """特集ページ HTML から映画タイトル（洋画・邦画）を抽出する。

        プレスルーム「注目ラインナップ」の構造:
            <h4>洋画</h4>
            <p>6月1日（月） <br>タイトルA<br>タイトルB</p>
            <p>配信中 <br>タイトルC</p>
            <h4>邦画</h4>   ← 次カテゴリでセクション終端

        各 <p> は「日付ラベル + <br> 区切りのタイトル群」。日付ラベル行は除外し、
        タイトル末尾の注記（【独占】等）を取り除く。
        洋画・邦画それぞれの見出し配下を走査し、カテゴリに応じて lang を振り分ける。

        Args:
            html: 特集ページの HTML。

        Returns:
            LineupItem のリスト。
        """
        soup = BeautifulSoup(html, "lxml")

        now_str = date.today().strftime("%Y-%m-%d %H:%M:%S")
        items: list[LineupItem] = []
        seen: set[str] = set()
        for category, lang in _MOVIE_CATEGORIES.items():
            heading = self._find_category_heading(soup, category)
            if heading is None:
                continue
            for raw in self._extract_titles_under(heading):
                clean = self._clean_title(raw)
                if not clean or clean in seen:
                    continue
                seen.add(clean)
                items.append(
                    LineupItem(
                        service=self.service,
                        title=clean,
                        external_id=self._make_external_id(clean),
                        lang=lang,
                        content_type="movie",
                        collected_at=now_str,
                    )
                )
        return items

    def _extract_titles_under(self, heading) -> list[str]:
        """カテゴリ見出し配下（次カテゴリ見出しまで）のタイトルを抽出する。

        見出しから次のカテゴリ見出し（h2〜h4）までの範囲の <p> を走査し、
        各 <p> 内の <br> 区切りテキストからタイトル行を拾う（日付ラベル行は除外）。

        Args:
            heading: カテゴリ見出し要素（例: ``<h4>洋画</h4>``）。

        Returns:
            タイトル文字列（注記付きの生テキスト）のリスト。
        """
        titles: list[str] = []
        for el in heading.find_all_next():
            if el.name in _HEADING_TAGS:
                break  # 次カテゴリ見出しに到達 → セクション終端
            if el.name != "p":
                continue
            # <br> をテキスト区切りとして取り出し、行ごとに処理する
            for line in el.get_text("\n").split("\n"):
                line = line.strip()
                if not line or _DATE_LINE_PATTERN.match(line):
                    continue  # 空行・日付ラベル行はタイトルではない
                titles.append(line)
        return titles

    def _find_category_heading(self, soup: BeautifulSoup, category: str):
        """指定カテゴリの見出し要素（h4 等）を返す。見つからなければ None。

        「注目ラインナップ」セクションの見出しのみを対象とし、
        本文中の ``<p><strong>洋画</strong></p>`` 等は対象外とする
        （見出しタグ ``_HEADING_TAGS`` に限定することで除外）。

        Args:
            soup    : パース済み HTML。
            category: カテゴリ見出し文言（例: "洋画"）。

        Returns:
            見出し要素、または None。
        """
        for heading in soup.find_all(_HEADING_TAGS):
            if heading.get_text(strip=True) == category:
                return heading
        return None

    @staticmethod
    def _clean_title(raw: str) -> str:
        """タイトル文字列から注記（【独占】等）を除去して整形する。"""
        return _ANNOTATION_PATTERN.sub("", raw).strip()

    @staticmethod
    def _make_external_id(title: str) -> str:
        """SID が取れないため、タイトルから差分判定用の安定キーを生成する。

        タイトル文字列をそのまま external_id に用いる（差分判定はタイトル単位）。
        将来 SID リンクが取得できる場合は SID に差し替える。
        """
        return title
