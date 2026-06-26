"""JustWatch GraphQL を使った新着タイトル取得クライアント。

各 VOD サービスに対して直近 N 日以内に追加されたタイトル一覧を返す。
週次パッチとは異なり、WordPress に登録されていない未知のタイトルを発見するために使う。

対応サービス（JP 向け）:
    netflix       — Netflix
    unext         — U-NEXT
    amazonprime   — Amazon Prime Video

使用 API:
    JustWatch GraphQL (https://apis.justwatch.com/graphql)
    popularTitles クエリ with sortBy=NEWEST + packages フィルタ
    各オファーの availableFrom フィールドで直近 N 日以内をフィルタ

主な関数:
    fetch_new_titles(services, country, language, days_back, limit) → list[NewTitle]

Raises:
    RuntimeError: JustWatch API 通信エラー時
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

import requests

from utils.browser import USER_AGENT

logger = logging.getLogger(__name__)

_JUSTWATCH_API_URL = "https://apis.justwatch.com/graphql"

_HEADERS = {
    "User-Agent": USER_AGENT,
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Origin": "https://www.justwatch.com",
    "Referer": "https://www.justwatch.com/",
}

# JustWatch technicalName → 当システムサービスキー（逆引き用）
_JW_TECH_TO_SERVICE: dict[str, str] = {
    "amazonprime":             "amazon_prime_video",
    "amazon":                  "amazon_prime_video",
    "amazonprimevideowithads": "amazon_prime_video",
    "netflix":                 "netflix",
    "netflixbasicwithads":     "netflix",
    "unext":                   "unext",
    "unexthbomax":             "unext",
}

# 当システムサービスキー → JustWatch technicalName のセット
_SERVICE_TO_JW_TECHS: dict[str, frozenset[str]] = {
    "amazon_prime_video": frozenset({"amazonprime", "amazon", "amazonprimevideowithads"}),
    "netflix":            frozenset({"netflix", "netflixbasicwithads"}),
    "unext":              frozenset({"unext", "unexthbomax"}),
}

# JW packages パラメータ用（サービスキー → 代表 technicalName）
_SERVICE_TO_JW_PACKAGE: dict[str, str] = {
    "amazon_prime_video": "amazonprime",
    "netflix":            "netflix",
    "unext":              "unext",
}


@dataclass
class ServiceOffer:
    """VOD サービスの提供情報。"""
    service: str          # 当システムのサービスキー
    url: str              # 配信ページ URL (standardWebURL)
    monetization: str     # FLATRATE / RENT / BUY / FREE
    available_from: Optional[date]
    available_to: Optional[date]


@dataclass
class NewTitle:
    """新着タイトル情報。"""
    jw_id: str
    title: str
    original_title: str
    full_path: str        # JustWatch の相対パス（例: /jp/movie/john-wick）
    poster_url: str
    genres: list[str] = field(default_factory=list)
    offers: list[ServiceOffer] = field(default_factory=list)

    @property
    def services(self) -> list[str]:
        """このタイトルを配信中のサービスキー一覧（重複なし）。"""
        seen: set[str] = set()
        result = []
        for o in self.offers:
            if o.service not in seen:
                seen.add(o.service)
                result.append(o.service)
        return result

    def earliest_available_from(self) -> Optional[date]:
        """全オファーの中で最も早い available_from を返す。"""
        dates = [o.available_from for o in self.offers if o.available_from]
        return min(dates) if dates else None

    def get_offer_url(self, service: str) -> Optional[str]:
        """指定サービスの配信 URL を返す（最初に見つかったもの）。"""
        for o in self.offers:
            if o.service == service and o.url:
                return o.url
        return None


# ─────────────────────────────────────────────────────────────────────────────
# GraphQL クエリ
# ─────────────────────────────────────────────────────────────────────────────

_QUERY_NEW_TITLES = """
query FetchNewTitles(
  $country: Country!
  $language: Language!
  $packages: [String!]!
  $first: Int!
  $after: String
) {
  popularTitles(
    country: $country
    sortBy: NEWEST
    filter: {
      objectTypes: [MOVIE, SHOW]
      packages: $packages
    }
    first: $first
    after: $after
  ) {
    pageInfo {
      endCursor
      hasNextPage
    }
    edges {
      node {
        id
        content(country: $country, language: $language) {
          title
          originalTitle
          fullPath
          posterUrl(profile: S166)
          genres { shortName }
        }
        offers(country: $country, platform: WEB) {
          standardWebURL
          package { technicalName clearName }
          monetizationType
          availableFrom
          availableTo
        }
      }
    }
  }
}
"""


def _post_graphql(query: str, variables: dict, timeout: int = 20) -> dict:
    """GraphQL リクエストを送信して JSON レスポンスを返す。

    Raises:
        RuntimeError: HTTP エラーまたは GraphQL エラーが含まれる場合。
    """
    resp = requests.post(
        _JUSTWATCH_API_URL,
        headers=_HEADERS,
        json={"query": query, "variables": variables},
        timeout=timeout,
    )
    if not resp.ok:
        raise RuntimeError(f"JustWatch API HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    if "errors" in data:
        raise RuntimeError(f"JustWatch API errors: {data['errors']}")
    return data


def _parse_date(val: Optional[str]) -> Optional[date]:
    """JustWatch の日付文字列 (ISO8601) を date に変換する。None や空文字は None を返す。"""
    if not val:
        return None
    try:
        return datetime.fromisoformat(val[:10]).date()
    except (ValueError, TypeError):
        return None


def _parse_node(node: dict, target_services: frozenset[str]) -> Optional[NewTitle]:
    """JustWatch popularTitles ノードを NewTitle に変換する。

    target_services に一致するオファーがない場合は None を返す。
    """
    content = node.get("content") or {}
    title = content.get("title") or ""
    if not title:
        return None

    genres = [g.get("shortName", "") for g in (content.get("genres") or [])]
    offers: list[ServiceOffer] = []

    for raw in node.get("offers") or []:
        tech = (raw.get("package") or {}).get("technicalName", "")
        service = _JW_TECH_TO_SERVICE.get(tech)
        if service is None or service not in target_services:
            continue
        url = (raw.get("standardWebURL") or "").strip()
        if not url:
            continue
        offers.append(ServiceOffer(
            service=service,
            url=url,
            monetization=raw.get("monetizationType") or "",
            available_from=_parse_date(raw.get("availableFrom")),
            available_to=_parse_date(raw.get("availableTo")),
        ))

    if not offers:
        return None

    return NewTitle(
        jw_id=str(node.get("id") or ""),
        title=title,
        original_title=content.get("originalTitle") or "",
        full_path=content.get("fullPath") or "",
        poster_url=content.get("posterUrl") or "",
        genres=genres,
        offers=offers,
    )


def _fetch_page(
    jw_packages: list[str],
    country: str,
    language: str,
    first: int,
    after: Optional[str],
) -> tuple[list[dict], Optional[str], bool]:
    """1ページ分のノードを取得する。

    Returns:
        (nodes, end_cursor, has_next_page) のタプル。
    """
    data = _post_graphql(
        _QUERY_NEW_TITLES,
        {
            "country": country,
            "language": language,
            "packages": jw_packages,
            "first": first,
            "after": after,
        },
    )
    popular = (data.get("data") or {}).get("popularTitles") or {}
    edges = popular.get("edges") or []
    nodes = [e["node"] for e in edges if e.get("node")]
    page_info = popular.get("pageInfo") or {}
    return nodes, page_info.get("endCursor"), bool(page_info.get("hasNextPage"))


def fetch_new_titles(
    services: list[str],
    country: str = "JP",
    language: str = "ja",
    days_back: int = 14,
    limit: int = 50,
    page_size: int = 50,
    max_pages: int = 5,
    sleep_between_pages: float = 2.0,
) -> list[NewTitle]:
    """JustWatch から指定サービスの新着タイトルを取得する。

    popularTitles を sortBy: NEWEST で取得し、各オファーの availableFrom が
    直近 days_back 日以内のタイトルだけを返す。
    availableFrom が null のタイトルも含める（JW が日付を持っていない場合）。

    Args:
        services           : 対象サービスキーのリスト（例: ["netflix", "unext", "amazon_prime_video"]）
        country            : JustWatch 国コード（デフォルト: "JP"）
        language           : JustWatch 言語コード（デフォルト: "ja"）
        days_back          : 何日前までを「新着」とするか（デフォルト: 14）
        limit              : 返す最大件数（デフォルト: 50）
        page_size          : 1 ページあたりの取得件数（デフォルト: 50）
        max_pages          : ページネーションの最大ページ数（デフォルト: 5）
        sleep_between_pages: ページ間の待機秒数（デフォルト: 2.0）

    Returns:
        NewTitle オブジェクトのリスト（available_from の降順）。

    Raises:
        RuntimeError: JustWatch API 通信エラー時。
    """
    if not services:
        return []

    target_services = frozenset(services)
    jw_packages = [
        _SERVICE_TO_JW_PACKAGE[svc]
        for svc in services
        if svc in _SERVICE_TO_JW_PACKAGE
    ]
    if not jw_packages:
        logger.warning("有効な JustWatch packages がありません: services=%s", services)
        return []

    cutoff = date.today() - timedelta(days=days_back)
    logger.info(
        "新着タイトル取得: services=%s country=%s cutoff=%s limit=%d",
        services, country, cutoff, limit,
    )

    results: list[NewTitle] = []
    after: Optional[str] = None
    page = 0
    total_fetched = 0

    while page < max_pages:
        page += 1
        logger.debug("ページ %d 取得中 (after=%s)", page, after)

        nodes, end_cursor, has_next = _fetch_page(
            jw_packages=jw_packages,
            country=country,
            language=language,
            first=min(page_size, limit - len(results)),
            after=after,
        )
        total_fetched += len(nodes)

        # NEWEST ソートなので日付が古くなったら打ち切る
        stop_early = False
        for node in nodes:
            nt = _parse_node(node, target_services)
            if nt is None:
                continue

            avail = nt.earliest_available_from()
            if avail is not None and avail < cutoff:
                stop_early = True
                break  # それ以降はもっと古い → ページネーション不要

            results.append(nt)
            if len(results) >= limit:
                stop_early = True
                break

        logger.info("ページ %d: %d ノード取得 → 累計 %d 件", page, len(nodes), len(results))

        if stop_early or not has_next or end_cursor is None:
            break

        after = end_cursor
        time.sleep(sleep_between_pages)

    # available_from の降順（新しい順）でソート
    results.sort(
        key=lambda t: t.earliest_available_from() or date.min,
        reverse=True,
    )
    logger.info("新着タイトル取得完了: %d 件（%d ノード確認済み）", len(results), total_fetched)
    return results


def group_by_service(titles: list[NewTitle]) -> dict[str, list[NewTitle]]:
    """新着タイトルをサービスごとにグループ化する。

    Args:
        titles: fetch_new_titles() の戻り値。

    Returns:
        {service_key: [NewTitle, ...]} の辞書。
        同一タイトルが複数サービスで配信されている場合は両方に含まれる。
    """
    result: dict[str, list[NewTitle]] = {}
    for title in titles:
        for svc in title.services:
            result.setdefault(svc, []).append(title)
    return result


def to_report(titles: list[NewTitle]) -> dict:
    """NewTitle リストをレポート用辞書に変換する。

    Returns:
        {
            "total": int,
            "by_service": {service: count},
            "titles": [
                {
                    "title": str,
                    "original_title": str,
                    "jw_id": str,
                    "genres": list[str],
                    "services": list[str],
                    "available_from": str | None,
                    "offers": [{"service": str, "url": str, "type": str}, ...]
                },
                ...
            ]
        }
    """
    by_service: dict[str, int] = {}
    for t in titles:
        for svc in t.services:
            by_service[svc] = by_service.get(svc, 0) + 1

    return {
        "total": len(titles),
        "by_service": by_service,
        "titles": [
            {
                "title": t.title,
                "original_title": t.original_title,
                "jw_id": t.jw_id,
                "genres": t.genres,
                "services": t.services,
                "available_from": (t.earliest_available_from() or "").isoformat() if t.earliest_available_from() else None,
                "offers": [
                    {
                        "service": o.service,
                        "url": o.url,
                        "type": o.monetization,
                        "available_from": o.available_from.isoformat() if o.available_from else None,
                        "available_to": o.available_to.isoformat() if o.available_to else None,
                    }
                    for o in t.offers
                ],
            }
            for t in titles
        ],
    }
