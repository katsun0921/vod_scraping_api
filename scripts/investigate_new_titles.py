"""新着タイトル取得 調査スクリプト。

以下を調査する:
  1. JustWatch GraphQL — packages + sortBy で新着タイトルを取得できるか
  2. Netflix JP     — 新着ページ（ログイン不要の範囲）
  3. Amazon Prime JP — 新着ページ
  4. U-NEXT          — Playwright で新着ページ

Usage:
    python scripts/investigate_new_titles.py [--service all|justwatch|netflix|amazon|unext]
    python scripts/investigate_new_titles.py --service justwatch
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, ".")

from utils.browser import USER_AGENT

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
}

_JW_HEADERS = {
    "User-Agent": USER_AGENT,
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Origin": "https://www.justwatch.com",
    "Referer": "https://www.justwatch.com/",
}

_JUSTWATCH_API = "https://apis.justwatch.com/graphql"


# ──────────────────────────────────────────────────────────────────────────────
# 1. JustWatch GraphQL
# ──────────────────────────────────────────────────────────────────────────────

# 試験するクエリパターン
_QUERY_NEW_TITLES = """
query NewTitles(
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
          runtime
          posterUrl(profile: S166)
          genres { shortName }
          credits(role: DIRECTOR, first: 3) {
            name
          }
          upcomingReleases(releaseTypes: [DIGITAL]) {
            releaseDate
            package { clearName technicalName }
          }
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

# シンプルな sortBy 列挙値を変えて比較
_QUERY_SORT_TEST = """
query SortTest($country: Country!, $language: Language!, $sortBy: PopularTitlesSorting!) {
  popularTitles(
    country: $country
    sortBy: $sortBy
    filter: { objectTypes: [MOVIE], packages: ["netflix"] }
    first: 5
  ) {
    edges {
      node {
        content(country: $country, language: $language) {
          title
          originalTitle
        }
        offers(country: $country, platform: WEB) {
          availableFrom
          availableTo
          package { technicalName }
          monetizationType
        }
      }
    }
  }
}
"""

# 最近 N 日以内に追加された（availableFrom が最近）タイトルをフィルタするためのクエリ
_QUERY_WITH_DATE_FILTER = """
query RecentlyAdded(
  $country: Country!
  $language: Language!
  $packages: [String!]!
  $addedAfter: Date!
) {
  popularTitles(
    country: $country
    sortBy: NEWEST
    filter: {
      objectTypes: [MOVIE, SHOW]
      packages: $packages
    }
    first: 50
  ) {
    edges {
      node {
        id
        content(country: $country, language: $language) {
          title
          fullPath
        }
        offers(country: $country, platform: WEB) {
          standardWebURL
          package { technicalName }
          monetizationType
          availableFrom
          availableTo
        }
      }
    }
  }
}
"""


def _post_graphql(query: str, variables: dict, label: str = "") -> dict | None:
    """GraphQL リクエストを送信して JSON を返す。エラーは None を返す。"""
    try:
        resp = requests.post(
            _JUSTWATCH_API,
            headers=_JW_HEADERS,
            json={"query": query, "variables": variables},
            timeout=20,
        )
        logger.info("[JW:%s] HTTP %d", label, resp.status_code)
        if not resp.ok:
            logger.error("[JW:%s] エラー: %s", label, resp.text[:300])
            return None
        data = resp.json()
        if "errors" in data:
            logger.error("[JW:%s] GraphQL errors: %s", label, data["errors"])
            return None
        return data
    except Exception as e:
        logger.error("[JW:%s] 例外: %s", label, e)
        return None


def investigate_justwatch() -> dict:
    """JustWatch GraphQL で新着タイトルが取得できるか調査する。

    Returns:
        調査結果の辞書。
    """
    results: dict = {}

    # ── テスト1: Netflix JP 新着 MOVIE (NEWEST) ────────────────────────────
    logger.info("=== JustWatch テスト1: Netflix JP NEWEST ===")
    data1 = _post_graphql(
        _QUERY_NEW_TITLES,
        {
            "country": "JP",
            "language": "ja",
            "packages": ["netflix"],
            "first": 10,
        },
        "netflix_newest",
    )
    if data1:
        edges = data1.get("data", {}).get("popularTitles", {}).get("edges", [])
        titles = []
        for edge in edges:
            node = edge.get("node", {})
            content = node.get("content", {})
            offers = node.get("offers", [])
            netflix_offers = [o for o in offers if o.get("package", {}).get("technicalName") == "netflix"]
            titles.append({
                "title": content.get("title"),
                "originalTitle": content.get("originalTitle"),
                "fullPath": content.get("fullPath"),
                "genres": [g.get("shortName") for g in content.get("genres") or []],
                "offers": [
                    {
                        "url": o.get("standardWebURL"),
                        "type": o.get("monetizationType"),
                        "availableFrom": o.get("availableFrom"),
                        "availableTo": o.get("availableTo"),
                    }
                    for o in netflix_offers
                ],
            })
        results["netflix_newest_jp"] = {
            "count": len(titles),
            "titles": titles,
            "hasNextPage": data1.get("data", {}).get("popularTitles", {}).get("pageInfo", {}).get("hasNextPage"),
        }
        logger.info("Netflix JP 新着: %d 件取得", len(titles))
    else:
        results["netflix_newest_jp"] = {"error": "取得失敗"}

    time.sleep(2)

    # ── テスト2: U-NEXT JP 新着 ────────────────────────────────────────────
    logger.info("=== JustWatch テスト2: U-NEXT JP NEWEST ===")
    data2 = _post_graphql(
        _QUERY_NEW_TITLES,
        {
            "country": "JP",
            "language": "ja",
            "packages": ["unext"],
            "first": 10,
        },
        "unext_newest",
    )
    if data2:
        edges = data2.get("data", {}).get("popularTitles", {}).get("edges", [])
        titles = []
        for edge in edges:
            node = edge.get("node", {})
            content = node.get("content", {})
            offers = node.get("offers", [])
            unext_offers = [o for o in offers if o.get("package", {}).get("technicalName") == "unext"]
            titles.append({
                "title": content.get("title"),
                "genres": [g.get("shortName") for g in content.get("genres") or []],
                "offers": [
                    {
                        "url": o.get("standardWebURL"),
                        "type": o.get("monetizationType"),
                        "availableFrom": o.get("availableFrom"),
                        "availableTo": o.get("availableTo"),
                    }
                    for o in unext_offers
                ],
            })
        results["unext_newest_jp"] = {"count": len(titles), "titles": titles}
        logger.info("U-NEXT JP 新着: %d 件取得", len(titles))
    else:
        results["unext_newest_jp"] = {"error": "取得失敗"}

    time.sleep(2)

    # ── テスト3: Amazon Prime JP 新着 ─────────────────────────────────────
    logger.info("=== JustWatch テスト3: Amazon Prime JP NEWEST ===")
    data3 = _post_graphql(
        _QUERY_NEW_TITLES,
        {
            "country": "JP",
            "language": "ja",
            "packages": ["amazonprime"],
            "first": 10,
        },
        "amazon_newest",
    )
    if data3:
        edges = data3.get("data", {}).get("popularTitles", {}).get("edges", [])
        titles = []
        for edge in edges:
            node = edge.get("node", {})
            content = node.get("content", {})
            offers = node.get("offers", [])
            amazon_offers = [
                o for o in offers
                if o.get("package", {}).get("technicalName") in ("amazonprime", "amazon", "amazonprimevideowithads")
            ]
            titles.append({
                "title": content.get("title"),
                "genres": [g.get("shortName") for g in content.get("genres") or []],
                "offers": [
                    {
                        "url": o.get("standardWebURL"),
                        "type": o.get("monetizationType"),
                        "availableFrom": o.get("availableFrom"),
                    }
                    for o in amazon_offers
                ],
            })
        results["amazon_newest_jp"] = {"count": len(titles), "titles": titles}
        logger.info("Amazon Prime JP 新着: %d 件取得", len(titles))
    else:
        results["amazon_newest_jp"] = {"error": "取得失敗"}

    time.sleep(2)

    # ── テスト4: sortBy 列挙値の確認（POPULAR vs NEWEST） ─────────────────
    logger.info("=== JustWatch テスト4: sortBy 比較 ===")
    for sort_val in ("POPULAR", "NEWEST"):
        data4 = _post_graphql(
            _QUERY_SORT_TEST,
            {"country": "JP", "language": "ja", "sortBy": sort_val},
            f"sortBy_{sort_val}",
        )
        if data4:
            edges = data4.get("data", {}).get("popularTitles", {}).get("edges", [])
            results[f"sort_{sort_val.lower()}"] = {
                "count": len(edges),
                "titles": [
                    {
                        "title": e["node"]["content"].get("title"),
                        "availableFrom": next(
                            (o.get("availableFrom") for o in e["node"].get("offers", [])), None
                        ),
                    }
                    for e in edges
                ],
            }
        else:
            results[f"sort_{sort_val.lower()}"] = {"error": "取得失敗"}
        time.sleep(2)

    # ── テスト5: 複数サービスまとめて取得 ────────────────────────────────
    logger.info("=== JustWatch テスト5: 複数サービス同時 ===")
    data5 = _post_graphql(
        _QUERY_NEW_TITLES,
        {
            "country": "JP",
            "language": "ja",
            "packages": ["netflix", "unext", "amazonprime"],
            "first": 20,
        },
        "multi_service",
    )
    if data5:
        edges = data5.get("data", {}).get("popularTitles", {}).get("edges", [])
        titles = []
        for edge in edges:
            node = edge.get("node", {})
            content = node.get("content", {})
            offers = node.get("offers", [])
            services = list({
                o.get("package", {}).get("technicalName")
                for o in offers
                if o.get("package", {}).get("technicalName") in ("netflix", "unext", "amazonprime", "amazon")
            })
            titles.append({
                "title": content.get("title"),
                "services": services,
                "availableFrom": min(
                    (o.get("availableFrom") for o in offers if o.get("availableFrom")),
                    default=None,
                ),
            })
        results["multi_service_jp"] = {"count": len(titles), "titles": titles}
        logger.info("複数サービス JP 新着: %d 件取得", len(titles))
    else:
        results["multi_service_jp"] = {"error": "取得失敗"}

    return results


# ──────────────────────────────────────────────────────────────────────────────
# 2. Netflix JP 直接スクレイピング調査
# ──────────────────────────────────────────────────────────────────────────────

# Netflix 新着関連 URL 候補
_NETFLIX_NEW_URLS = [
    ("new_and_popular", "https://www.netflix.com/jp/browse/genre/1592210"),
    ("new_additions", "https://www.netflix.com/jp/browse/new-additions"),
]


def investigate_netflix() -> dict:
    """Netflix JP 新着ページの直接スクレイピングを調査する。"""
    results = {}
    for label, url in _NETFLIX_NEW_URLS:
        logger.info("=== Netflix: %s (%s) ===", label, url)
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=20, allow_redirects=True)
            logger.info("HTTP %d | Content-Length: %d", resp.status_code, len(resp.text))
            soup = BeautifulSoup(resp.text, "lxml")

            # ページタイトルを確認
            title_tag = soup.find("title")
            page_title = title_tag.get_text().strip() if title_tag else ""

            # JSON-LD / script タグのデータ確認
            json_ld_count = len(soup.find_all("script", type="application/ld+json"))

            # netflix-reactapp または window.__reactLocation__ のような JS 変数があるか
            script_tags = soup.find_all("script")
            has_react_data = any("reactContext" in (s.string or "") for s in script_tags)
            has_falcor_cache = any("falcorCache" in (s.string or "") for s in script_tags)
            has_netflix_data = any("netflix" in (s.string or "").lower() for s in script_tags)

            # メタタグ
            og_title = (soup.find("meta", property="og:title") or {}).get("content", "")

            # リダイレクト先
            final_url = resp.url

            results[label] = {
                "url": url,
                "final_url": final_url,
                "status_code": resp.status_code,
                "page_title": page_title,
                "og_title": og_title,
                "json_ld_count": json_ld_count,
                "has_react_data": has_react_data,
                "has_falcor_cache": has_falcor_cache,
                "has_netflix_data": has_netflix_data,
                "body_length": len(resp.text),
                "redirected": final_url != url,
            }
        except Exception as e:
            logger.error("Netflix %s 例外: %s", label, e)
            results[label] = {"error": str(e)}
        time.sleep(2)
    return results


# ──────────────────────────────────────────────────────────────────────────────
# 3. Amazon Prime Video JP 直接スクレイピング調査
# ──────────────────────────────────────────────────────────────────────────────

_AMAZON_NEW_URLS = [
    ("prime_movies_new", "https://www.amazon.co.jp/s?i=instant-video&bbn=2351649051&rh=n%3A2351649051%2Cp_n_ways_to_watch%3A12007865051&s=date-desc-rank&dc"),
    ("prime_storefront", "https://www.amazon.co.jp/gp/video/storefront/ref=atv_nn_mv_c_WK_prmo?filterId=OFFER_FILTER_PRIME&contentType=movie&contentId=home"),
    ("prime_new_movie", "https://www.amazon.co.jp/gp/video/storefront/?filterId=OFFER_FILTER_PRIME&contentType=movie&sortBy=DATE_ADDED"),
]


def investigate_amazon() -> dict:
    """Amazon Prime Video JP 新着ページの直接スクレイピングを調査する。"""
    results = {}
    for label, url in _AMAZON_NEW_URLS:
        logger.info("=== Amazon: %s ===", label)
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=20, allow_redirects=True)
            logger.info("HTTP %d | body: %d bytes", resp.status_code, len(resp.text))
            soup = BeautifulSoup(resp.text, "lxml")

            title_tag = soup.find("title")
            page_title = title_tag.get_text().strip() if title_tag else ""

            # ロボット検出
            text_lower = soup.get_text().lower()
            is_robot = any(w in text_lower for w in ["captcha", "robot check", "verify you are a human"])

            # JSON-LD
            json_ld_count = len(soup.find_all("script", type="application/ld+json"))

            # 映画タイトルの候補要素
            # Amazon storefront は JS レンダリング後にデータが現れる可能性が高い
            title_candidates = [
                el.get_text().strip()
                for el in soup.find_all(["h2", "h3", "a"], class_=lambda c: c and "title" in c.lower())
            ][:10]

            results[label] = {
                "url": url,
                "final_url": resp.url,
                "status_code": resp.status_code,
                "page_title": page_title,
                "body_length": len(resp.text),
                "is_robot_detected": is_robot,
                "json_ld_count": json_ld_count,
                "title_candidates": title_candidates,
                "redirected": resp.url != url,
            }
        except Exception as e:
            logger.error("Amazon %s 例外: %s", label, e)
            results[label] = {"error": str(e)}
        time.sleep(3)
    return results


# ──────────────────────────────────────────────────────────────────────────────
# 4. U-NEXT 直接スクレイピング調査（Playwright）
# ──────────────────────────────────────────────────────────────────────────────

_UNEXT_NEW_URLS = [
    ("new_movie", "https://video.unext.jp/browse/movie?sort=new"),
    ("new_all", "https://video.unext.jp/browse?sort=new"),
    ("new_top", "https://video.unext.jp/browse/new"),
    ("movie_just_arrived", "https://video.unext.jp/browse/movie?type=NEW_ARRIVAL"),
]


def investigate_unext() -> dict:
    """U-NEXT 新着ページを Playwright でスクレイピングして調査する。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"error": "playwright 未インストール"}

    results = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for label, url in _UNEXT_NEW_URLS:
            logger.info("=== U-NEXT: %s (%s) ===", label, url)
            try:
                context = browser.new_context(user_agent=USER_AGENT)
                page = context.new_page()
                response = page.goto(url, timeout=30000)
                page.wait_for_timeout(4000)

                status_code = response.status if response else 200
                final_url = page.url
                page_title = page.title()

                # ページテキストからタイトル候補を探す
                body_text = page.inner_text("body")
                text_length = len(body_text)

                # タイトル候補（h2, h3, .title 等）
                title_elements = page.query_selector_all("h2, h3, [class*='title']")
                title_candidates = [
                    el.inner_text().strip()
                    for el in title_elements[:20]
                    if el.inner_text().strip()
                ]

                # 新着キーワードが含まれるか
                has_new_keyword = any(
                    kw in body_text
                    for kw in ["新着", "NEW", "新規追加", "追加日", "配信開始"]
                )

                results[label] = {
                    "url": url,
                    "final_url": final_url,
                    "status_code": status_code,
                    "page_title": page_title,
                    "body_length": text_length,
                    "has_new_keyword": has_new_keyword,
                    "title_candidates": title_candidates[:15],
                    "redirected": final_url != url,
                }
                logger.info(
                    "U-NEXT %s: status=%d body=%d chars titles=%d",
                    label, status_code, text_length, len(title_candidates),
                )
                context.close()
            except Exception as e:
                logger.error("U-NEXT %s 例外: %s", label, e)
                results[label] = {"error": str(e)}
            time.sleep(3)
        browser.close()
    return results


# ──────────────────────────────────────────────────────────────────────────────
# メイン
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="新着タイトル取得可能性 調査スクリプト")
    parser.add_argument(
        "--service",
        choices=["all", "justwatch", "netflix", "amazon", "unext"],
        default="all",
        help="調査対象サービス（default: all）",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="結果を JSON ファイルに出力するパス（省略時は stdout）",
    )
    args = parser.parse_args()

    report: dict = {
        "investigated_at": datetime.now().isoformat(),
        "services": args.service,
    }

    if args.service in ("all", "justwatch"):
        logger.info("━━ JustWatch GraphQL 調査 ━━")
        report["justwatch"] = investigate_justwatch()

    if args.service in ("all", "netflix"):
        logger.info("━━ Netflix JP 直接スクレイピング 調査 ━━")
        report["netflix_direct"] = investigate_netflix()

    if args.service in ("all", "amazon"):
        logger.info("━━ Amazon Prime JP 直接スクレイピング 調査 ━━")
        report["amazon_direct"] = investigate_amazon()

    if args.service in ("all", "unext"):
        logger.info("━━ U-NEXT Playwright 調査 ━━")
        report["unext_direct"] = investigate_unext()

    output = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(output)
        logger.info("結果を %s に出力しました", args.out)
    else:
        print(output)


if __name__ == "__main__":
    main()
