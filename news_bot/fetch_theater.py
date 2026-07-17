"""劇場公開情報の取得（RSSのみ対応・レイヤー1）。

仕様書 7.（取得元方針）: `docs/feature/theater-release-calendar-spec.md`

レイヤー1データソースは仕様書時点で未確定のため、コード変更なしに追加できるよう
「劇場情報源」シート（sheets.get_active_theater_sources()）に登録された取得元を
巡回する方式にしている。取得方式は現状 "rss" のみ対応（html/apiは未実装・TODO）。

公開日はRSSの構造化フィールドとして提供されない前提で、タイトル・概要からの
正規表現ベストエフォート抽出のみ行う（抽出できない場合は release_date=None）。
精度向上・レイヤー2/3補完は未実装（TODO、仕様書17.参照）。
"""

import logging
import re
from dataclasses import dataclass
from datetime import date
from typing import Optional

import feedparser

logger = logging.getLogger(__name__)

_DATE_FULL_RE = re.compile(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日")
_DATE_MD_KANJI_RE = re.compile(r"(\d{1,2})月\s*(\d{1,2})日\s*(?:\([^)]*\))?\s*(?:から)?公開")
_DATE_MD_SLASH_RE = re.compile(r"(\d{1,2})/(\d{1,2})\s*(?:\([^)]*\))?\s*公開")


@dataclass
class TheaterEntry:
    title: str
    url: str
    source: str
    summary: str = ""
    release_date: Optional[date] = None


def _infer_year(month: int, day: int, today: date) -> int:
    """年表記の無い日付（M月D日）から年を推測する。

    半年以上前の日付になる場合は年またぎ（来年）とみなす。
    """
    try:
        candidate = date(today.year, month, day)
    except ValueError:
        return today.year
    if (today - candidate).days > 183:
        return today.year + 1
    return today.year


def extract_release_date(text: str, today: Optional[date] = None) -> Optional[date]:
    """タイトル・概要テキストから公開日をベストエフォートで抽出する。

    「YYYY年M月D日」形式を最優先し、無ければ「M月D日公開」「M/D公開」のように
    "公開" を伴う表記のみ拾う（誤検出を避けるため、単独のM/D表記は対象外）。
    """
    today = today or date.today()

    m = _DATE_FULL_RE.search(text)
    if m:
        year, month, day = map(int, m.groups())
        try:
            return date(year, month, day)
        except ValueError:
            return None

    m = _DATE_MD_KANJI_RE.search(text) or _DATE_MD_SLASH_RE.search(text)
    if m:
        month, day = map(int, m.groups())
        year = _infer_year(month, day, today)
        try:
            return date(year, month, day)
        except ValueError:
            return None

    return None


def fetch_from_source(source: dict) -> list[TheaterEntry]:
    """1つの「劇場情報源」行から劇場公開情報一覧を取得する。

    Args:
        source: sheets.NewsBotSheets.get_active_theater_sources() が返す行
            （少なくとも "名称" "URL" "取得方式" キーを持つ）
    """
    name = source.get("名称", "")
    method = source.get("取得方式")
    if method != "rss":
        logger.warning("未対応の取得方式のためスキップ: %s (取得方式=%r)", name, method)
        return []

    feed_url = source["URL"]
    parsed = feedparser.parse(feed_url)
    if parsed.bozo:
        logger.warning("RSSパースエラー: %s (%s)", name, parsed.bozo_exception)

    entries = []
    for entry in parsed.entries:
        link = entry.get("link")
        title = entry.get("title")
        if not link or not title:
            continue
        summary = entry.get("summary", "")
        release_date = extract_release_date(f"{title} {summary}")
        entries.append(
            TheaterEntry(title=title, url=link, source=name, summary=summary, release_date=release_date)
        )
    return entries


def fetch_all(sources: list[dict]) -> list[TheaterEntry]:
    """有効な全取得元を巡回して劇場公開情報一覧を取得する。1件の失敗は他に伝播させない。"""
    all_entries: list[TheaterEntry] = []
    for source in sources:
        try:
            all_entries.extend(fetch_from_source(source))
        except Exception:
            logger.exception("劇場情報源取得失敗: %s", source.get("名称"))
    return all_entries
