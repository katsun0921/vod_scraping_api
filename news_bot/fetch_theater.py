"""劇場公開情報の取得（RSS / TMDb discover API・レイヤー1）。

仕様書 7.（取得元方針）: `docs/feature/theater-release-calendar-spec.md`

レイヤー1データソースは「劇場情報源」シート（sheets.get_active_theater_sources()）に
登録された取得元を巡回する方式にしている（コード変更なしに追加できるようにするため）。
取得方式は "rss"（feedparser）と "tmdb"（TMDb discover API）に対応する
（htmlスクレイピングは未実装・TODO、仕様書17.参照）。

[保留中] TMDb取得（"tmdb"）は実装済みだが、現状「劇場情報源」シートには登録しないこと。
KatsumascoreはGoogle AdSenseを掲載しており収益を得ているため、TMDb APIの
「Personal Use」申請（non-commercial / generates no revenue の誓約）が事実に反する。
商用ライセンス（$149/月〜、要 api@themoviedb.org への問い合わせ）を契約するまでは
無償利用してはならない。詳細経緯は docs/feature/theater-sources-candidates.md A.節。
レイヤー1データソースは現在RSS/HTML一覧/PR TIMES企業別RSSから再選定中。

RSS取得時の公開日は構造化フィールドとして提供されない前提で、タイトル・概要からの
正規表現ベストエフォート抽出のみ行う（抽出できない場合は release_date=None）。
TMDb取得時は release_date がAPIレスポンスの構造化データからそのまま得られる。
"""

import logging
import os
import re
from dataclasses import dataclass
from datetime import date
from typing import Optional

import feedparser
import requests

logger = logging.getLogger(__name__)

_DATE_FULL_RE = re.compile(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日")
_DATE_MD_KANJI_RE = re.compile(r"(\d{1,2})月\s*(\d{1,2})日\s*(?:\([^)]*\))?\s*(?:から)?公開")
_DATE_MD_SLASH_RE = re.compile(r"(\d{1,2})/(\d{1,2})\s*(?:\([^)]*\))?\s*公開")

_TMDB_API_BASE = "https://api.themoviedb.org/3"

# TMDb公式ジャンルリスト（/genre/movie/list?language=ja-JP）。頻繁に変わらないため静的に保持する。
_TMDB_GENRE_JA = {
    28: "アクション", 12: "アドベンチャー", 16: "アニメーション", 35: "コメディ",
    80: "クライム", 99: "ドキュメンタリー", 18: "ドラマ", 10751: "ファミリー",
    14: "ファンタジー", 36: "歴史", 27: "ホラー", 10402: "音楽",
    9648: "ミステリー", 10749: "ロマンス", 878: "SF", 10770: "TVムービー",
    53: "スリラー", 10752: "戦争", 37: "西部劇",
}


@dataclass
class TheaterEntry:
    title: str
    url: str
    source: str
    summary: str = ""
    release_date: Optional[date] = None
    original_title: str = ""
    category: str = ""


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


def _fetch_rss(source: dict) -> list[TheaterEntry]:
    name = source.get("名称", "")
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


def _fetch_tmdb(source: dict, start: date, end: date) -> list[TheaterEntry]:
    """TMDb discover APIで対象期間の劇場公開作品（映画）を取得する。

    with_release_type=2|3（劇場公開）を対象期間内でフィルタするため、
    RSSと違い公開日はAPIレスポンスから構造化データとしてそのまま得られる。

    [保留中] KatsumascoreはAdSense収益があるためTMDb無償利用（Personal Use）の
    誓約に反する。商用ライセンス契約までは「劇場情報源」シートに
    取得方式=tmdbの行を登録しないこと（モジュールdocstring参照）。
    """
    name = source.get("名称") or "TMDb"
    api_key = os.environ["TMDB_API_KEY"]
    base_params = {
        "api_key": api_key,
        "language": "ja-JP",
        "region": "JP",
        "sort_by": "primary_release_date.asc",
        "with_release_type": "2|3",
        "primary_release_date.gte": start.isoformat(),
        "primary_release_date.lte": end.isoformat(),
        "include_adult": "false",
    }

    entries: list[TheaterEntry] = []
    page = 1
    while True:
        resp = requests.get(
            f"{_TMDB_API_BASE}/discover/movie", params={**base_params, "page": page}, timeout=30
        )
        resp.raise_for_status()
        data = resp.json()

        for movie in data.get("results", []):
            release_date = None
            raw_date = movie.get("release_date")
            if raw_date:
                try:
                    release_date = date.fromisoformat(raw_date)
                except ValueError:
                    release_date = None
            genres = [_TMDB_GENRE_JA.get(gid, "") for gid in movie.get("genre_ids", [])]
            entries.append(
                TheaterEntry(
                    title=movie.get("title") or movie.get("original_title", ""),
                    url=f"https://www.themoviedb.org/movie/{movie['id']}",
                    source=name,
                    summary=movie.get("overview", ""),
                    release_date=release_date,
                    original_title=movie.get("original_title", ""),
                    category="/".join(g for g in genres if g),
                )
            )

        total_pages = data.get("total_pages", 1)
        if page >= total_pages:
            break
        page += 1

    return entries


def fetch_from_source(source: dict, start: date, end: date) -> list[TheaterEntry]:
    """1つの「劇場情報源」行から劇場公開情報一覧を取得する。

    Args:
        source: sheets.NewsBotSheets.get_active_theater_sources() が返す行
            （少なくとも "名称" "取得方式" キーを持つ。"rss" は "URL" も必須）
        start, end: 対象期間（theater_calendar.week_range()）。tmdb取得時のみ
            APIクエリの絞り込みに使う（rss取得時は無視し、後段でフィルタする）
    """
    name = source.get("名称", "")
    method = source.get("取得方式")
    if method == "rss":
        return _fetch_rss(source)
    if method == "tmdb":
        return _fetch_tmdb(source, start, end)

    logger.warning("未対応の取得方式のためスキップ: %s (取得方式=%r)", name, method)
    return []


def fetch_all(sources: list[dict], start: date, end: date) -> list[TheaterEntry]:
    """有効な全取得元を巡回して劇場公開情報一覧を取得する。1件の失敗は他に伝播させない。"""
    all_entries: list[TheaterEntry] = []
    for source in sources:
        try:
            all_entries.extend(fetch_from_source(source, start, end))
        except Exception:
            logger.exception("劇場情報源取得失敗: %s", source.get("名称"))
    return all_entries
