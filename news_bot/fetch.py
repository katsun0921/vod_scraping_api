"""ニュース取得（RSS優先）。

仕様書 4.1: 取得方法は「RSS > 公式API > HTMLスクレイピング」の優先順位。
フェーズ1（MVP）は RSS のみを対象とする（仕様書 6.）。
"""

import logging
from dataclasses import dataclass

import feedparser

logger = logging.getLogger(__name__)


@dataclass
class NewsEntry:
    title: str
    url: str
    source: str
    summary: str = ""


def fetch_from_source(source: dict) -> list[NewsEntry]:
    """1つの「ニュースソース」行（RSS）から記事一覧を取得する。

    Args:
        source: sheets.NewsBotSheets.get_active_sources() が返す行
            （少なくとも "名称" "URL" キーを持つ）
    """
    feed_url = source["URL"]
    name = source["名称"]
    parsed = feedparser.parse(feed_url)
    if parsed.bozo:
        logger.warning("RSSパースエラー: %s (%s)", name, parsed.bozo_exception)

    entries = []
    for entry in parsed.entries:
        link = entry.get("link")
        title = entry.get("title")
        if not link or not title:
            continue
        entries.append(
            NewsEntry(
                title=title,
                url=link,
                source=name,
                summary=entry.get("summary", ""),
            )
        )
    return entries


def fetch_all(sources: list[dict]) -> list[NewsEntry]:
    """有効な全ソースを巡回して記事一覧を取得する。1ソースの失敗は他に伝播させない。"""
    all_entries: list[NewsEntry] = []
    for source in sources:
        try:
            all_entries.extend(fetch_from_source(source))
        except Exception:
            logger.exception("ニュースソース取得失敗: %s", source.get("名称"))
    return all_entries
