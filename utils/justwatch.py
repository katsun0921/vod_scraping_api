"""JustWatch 非公式 API クライアント。

JustWatch の GraphQL エンドポイントを使用して、映画タイトルから
各 VOD サービスの配信 URL を取得する。

使用エンドポイント:
    https://apis.justwatch.com/graphql

注意:
    非公式 API のため仕様変更により動作しなくなる可能性がある。
    エラー時は RuntimeError を raise する。
"""

import logging
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_JUSTWATCH_API_URL = "https://apis.justwatch.com/graphql"

from utils.browser import USER_AGENT

_HEADERS = {
    "User-Agent": USER_AGENT,
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Origin": "https://www.justwatch.com",
    "Referer": "https://www.justwatch.com/",
}

# JustWatch の technical_name → 当システムのサービスキー
_JW_PROVIDER_MAP: dict[str, str] = {
    "amp":        "amazon_prime_video",   # Amazon Prime Video
    "nfx":        "netflix",
    "hlu":        "hulu",
    "unx":        "unext",
    "dnp":        "disney_plus",
    "dmt":        "dmm_tv",
    "atp":        "apple_tv",
    "yte":        "youtube",
}

# サービスキー → JustWatch の URL テンプレート（{id} は standardWebURL から取得）
# standardWebURL をそのまま使うためテンプレート不要だが、フォールバック用に保持
_SERVICE_BASE_URLS: dict[str, str] = {
    "amazon_prime_video": "https://www.amazon.co.jp/gp/video/detail/",
    "netflix":            "https://www.netflix.com/jp/title/",
    "hulu":               "https://www.hulu.jp/watch/",
    "unext":              "https://video.unext.jp/title/",
    "disney_plus":        "https://www.disneyplus.com/ja-jp/movies/",
    "dmm_tv":             "https://tv.dmm.com/vod/detail/?season=",
    "apple_tv":           "https://tv.apple.com/jp/movie/",
    "youtube":            "https://www.youtube.com/watch?v=",
}

_SEARCH_QUERY = """
query SearchTitleUrls($query: String!, $country: Country!, $language: Language!) {
  popularTitles(
    country: $country
    filter: { searchQuery: $query, objectTypes: [MOVIE] }
    first: 5
  ) {
    edges {
      node {
        id
        content(country: $country, language: $language) {
          title
          originalTitle
          fullPath
        }
        offers(country: $country, platform: WEB) {
          standardWebURL
          package {
            technicalName
          }
          monetizationType
        }
      }
    }
  }
}
"""


def _post_graphql(query: str, variables: dict, timeout: int = 20) -> dict:
    """GraphQL リクエストを送信して JSON を返す。

    Args:
        query    : GraphQL クエリ文字列。
        variables: クエリ変数。
        timeout  : タイムアウト秒数。

    Returns:
        JSON レスポンス dict。

    Raises:
        RuntimeError: HTTP エラーまたはレスポンスに errors キーがある場合。
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


def _extract_urls_from_node(node: dict) -> dict[str, str]:
    """JustWatch タイトルノードから サービスキー → URL の辞書を抽出する。

    同一サービスに複数 offer がある場合は最初の standardWebURL を使用する。

    Args:
        node: popularTitles.edges[].node

    Returns:
        {service_key: url} の辞書（対応サービスのみ含む）。
    """
    urls: dict[str, str] = {}
    for offer in node.get("offers") or []:
        tech_name = (offer.get("package") or {}).get("technicalName", "")
        service_key = _JW_PROVIDER_MAP.get(tech_name)
        if not service_key:
            continue
        if service_key in urls:
            continue  # 同サービスの2件目以降はスキップ
        web_url = (offer.get("standardWebURL") or "").strip()
        if web_url:
            urls[service_key] = web_url
    return urls


def _pick_best_node(nodes: list[dict], title_query: str) -> Optional[dict]:
    """検索結果からタイトルが最も一致するノードを返す。

    完全一致 → 前方一致 → 先頭ノード の順に評価する。

    Args:
        nodes      : popularTitles.edges[].node のリスト。
        title_query: 検索クエリ文字列。

    Returns:
        最適なノード dict、または None（結果なし）。
    """
    if not nodes:
        return None

    q = title_query.strip().lower()

    for node in nodes:
        content = node.get("content") or {}
        for key in ("title", "originalTitle"):
            t = (content.get(key) or "").strip().lower()
            if t == q:
                return node

    for node in nodes:
        content = node.get("content") or {}
        for key in ("title", "originalTitle"):
            t = (content.get(key) or "").strip().lower()
            if t.startswith(q) or q.startswith(t):
                return node

    return nodes[0]


def search_urls(title: str, slug: str, country: str = "JP", language: str = "ja") -> dict[str, str]:
    """タイトルまたは slug で JustWatch を検索し、サービスキー → URL の辞書を返す。

    title で検索してヒットしなければ slug（英語表記）で再試行する。
    どちらもヒットしない場合は空辞書を返す。

    Args:
        title   : 作品タイトル（日本語可）。
        slug    : WordPress スラッグ（英語表記）。
        country : JustWatch の国コード（デフォルト: "JP"）。
        language: JustWatch の言語コード（デフォルト: "ja"）。

    Returns:
        {service_key: url} の辞書。見つからなければ空辞書。

    Raises:
        RuntimeError: API 通信エラーの場合。
    """
    for query in _build_queries(title, slug):
        logger.debug("JustWatch 検索: query=%r country=%s", query, country)
        data = _post_graphql(
            _SEARCH_QUERY,
            {"query": query, "country": country, "language": language},
        )
        edges = (data.get("data") or {}).get("popularTitles", {}).get("edges") or []
        nodes = [e["node"] for e in edges if e.get("node")]
        if not nodes:
            logger.debug("JustWatch: query=%r → 結果なし", query)
            continue

        node = _pick_best_node(nodes, query)
        if node is None:
            continue

        urls = _extract_urls_from_node(node)
        content = (node.get("content") or {})
        matched_title = content.get("title") or content.get("originalTitle") or ""
        logger.info(
            "JustWatch: query=%r → matched=%r offers=%d urls=%d",
            query, matched_title, len(node.get("offers") or []), len(urls),
        )
        if urls:
            return urls
        # URL が取れなければ次のクエリを試す
        time.sleep(1)

    return {}


def _build_queries(title: str, slug: str) -> list[str]:
    """検索クエリ候補リストを生成する。

    1. title（日本語タイトル）
    2. slug をスペース区切りに変換した英語タイトル（title と異なる場合）

    Args:
        title: 日本語タイトル。
        slug : WordPress スラッグ（ハイフン区切り）。

    Returns:
        重複なしのクエリ文字列リスト。
    """
    queries: list[str] = []
    clean_title = (title or "").strip()
    if clean_title:
        queries.append(clean_title)

    slug_as_title = (slug or "").replace("-", " ").strip()
    if slug_as_title and slug_as_title.lower() != clean_title.lower():
        queries.append(slug_as_title)

    return queries
