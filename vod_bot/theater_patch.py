"""劇場公開（上映中フラグ）の週次チェック。

ACF `cinema_info_filed.is_cinema_showing`（現在上映中）が ON の記事について、
まだ劇場で公開中かどうかを週次で確認し、終了と判定した記事はフラグを自動で
OFF にする。上映終了時にチェックを外し忘れると、フロントの「劇場公開中」一覧
（`/v1/theater-list` → `/now-showing`）に終映済みの作品が残り続けるため。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
判定ロジック（記事ごと）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  1. 劇場URL（`cinema_info_filed.cinema_list_filed`）へ GET
       404 / 410           → 上映終了（reason=url_gone）
       2xx / 3xx           → 上映中
       5xx / 429 / 接続失敗 → 判定不能（今回は据え置き）
  2. 劇場公開日（`release.release_date`）からの経過日数
       SHOWING_WEEKS 週以上経過 → 上映終了（reason=expired）
  3. 劇場URLも公開日も無い → 判定不能（reason=no_signal、据え置き）

外部の映画情報サイト（映画.com・MOVIE WALKER 等）はスクレイピングしない。
利用規約上の判断は docs/feature/theater-sources-candidates.md のとおりで、
見に行くのは記事に登録された劇場URLだけに限定する。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
実行
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  GitHub Actions: .github/workflows/theater-showing-check.yml（毎週月曜 05:00 JST）
  Cloud Run     : POST /theater-check

Usage:
    python theater_patch.py                # 上映中の全記事をチェックして自動OFF
    python theater_patch.py --dry-run      # 判定のみ（WordPress を更新しない）
    python theater_patch.py --weeks 12     # 経過週数のしきい値を変更
    python theater_patch.py --slug john-wick   # 特定 slug のみ
    python theater_patch.py --post-id 123      # 特定 post_id のみ
"""

import argparse
import logging
import os
import sys
from datetime import date, datetime
from typing import Optional

import requests

from slack import notify_theater_showing_result
from utils.browser import USER_AGENT
from utils.rate_limit import RateLimiter
from wordpress import (
    TheaterListError,
    build_front_url,
    get_theater_showing_posts,
    patch_cinema_showing,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# 劇場公開日からこの週数を過ぎたら上映終了とみなす（環境変数 THEATER_SHOWING_WEEKS で変更可）
# 日本の劇場公開は概ね4〜8週間で終映するため、ロングラン作品を巻き込みにくい8週を既定にする。
DEFAULT_SHOWING_WEEKS = 8

# 劇場URLが上映終了とみなせる HTTP ステータス（ページ自体が消えたケース）
ENDED_HTTP_STATUSES = frozenset({404, 410})

_URL_TIMEOUT = 20


def default_showing_weeks() -> int:
    """しきい値の週数を環境変数 THEATER_SHOWING_WEEKS から解決する。

    未登録の GitHub Secret は空文字で渡るため、`or` でフォールバックする。

    Returns:
        週数。未設定・不正値の場合は DEFAULT_SHOWING_WEEKS。
    """
    raw = os.environ.get("THEATER_SHOWING_WEEKS") or ""
    try:
        weeks = int(raw)
    except ValueError:
        return DEFAULT_SHOWING_WEEKS
    return weeks if weeks > 0 else DEFAULT_SHOWING_WEEKS


def elapsed_days(release_date: str, today: date) -> Optional[int]:
    """劇場公開日からの経過日数を返す。

    Args:
        release_date: `YYYY-MM-DD` 形式の公開日。空文字・不正値は None を返す。
        today       : 基準日。

    Returns:
        経過日数（公開日が未来なら負数）。判定できない場合は None。
    """
    if not release_date:
        return None
    try:
        released = datetime.strptime(release_date, "%Y-%m-%d").date()
    except ValueError:
        logger.warning("公開日の形式が不正: %r", release_date)
        return None
    return (today - released).days


def check_cinema_url(url: str, session: requests.Session) -> str:
    """劇場URLの生存を確認する。

    Args:
        url    : 劇場URL（`cinema_info_filed.cinema_list_filed`）。
        session: リクエストに使うセッション。

    Returns:
        "gone"    : 404 / 410（ページが消えている）
        "alive"   : 2xx / 3xx
        "unknown" : 5xx / 429 / 接続失敗など判定できない場合
    """
    try:
        # 本文は判定に使わないため stream=True でダウンロードせずに閉じる
        resp = session.get(url, timeout=_URL_TIMEOUT, allow_redirects=True, stream=True)
    except requests.RequestException as e:
        logger.warning("劇場URL確認失敗（判定不能）: url=%s error=%s", url, e)
        return "unknown"

    status = resp.status_code
    resp.close()
    if status in ENDED_HTTP_STATUSES:
        logger.info("劇場URL: status=%d → 上映終了とみなす url=%s", status, url)
        return "gone"
    if status < 400:
        return "alive"
    logger.warning("劇場URL: status=%d（判定不能）url=%s", status, url)
    return "unknown"


def judge(item: dict, today: date, weeks: int, url_state: str) -> tuple[str, str]:
    """1記事の上映状況を判定する（外部アクセスなしの純粋関数）。

    Args:
        item     : get_theater_showing_posts() が返す記事1件。
        today    : 基準日。
        weeks    : 上映終了とみなす経過週数。
        url_state: check_cinema_url() の戻り値（劇場URL未登録なら "unknown"）。

    Returns:
        (verdict, reason) のタプル。
        verdict は "ended"（上映終了） / "showing"（上映中） / "unknown"（判定不能）。
    """
    if url_state == "gone":
        return "ended", "url_gone"

    days = elapsed_days(item.get("release_date") or "", today)
    if days is not None and days >= weeks * 7:
        return "ended", "expired"

    if url_state == "alive":
        return "showing", "url_alive"
    if days is not None:
        return "showing", "within_period"
    return "unknown", "no_signal"


def _front_url(item: dict) -> str:
    """記事のフロントエンド表示 URL を組み立てる。"""
    category_slug = next(iter(item.get("category_slugs") or []), "")
    return build_front_url(item.get("slug", ""), item.get("lang", "ja"), category_slug)


def _select_targets(
    items: list[dict],
    slug: Optional[str],
    post_id: Optional[int],
    limit: Optional[int],
) -> list[dict]:
    """CLI オプションに従って対象記事を絞り込む。

    Args:
        items  : 上映中フラグ ON の記事一覧。
        slug   : 指定した場合、該当 slug のみ（post_id 未指定時のみ有効）。
        post_id: 指定した場合、該当 post_id のみ（slug より優先）。
        limit  : 最大件数。

    Returns:
        絞り込み後のリスト。
    """
    if post_id is not None:
        items = [i for i in items if i.get("id") == post_id]
    elif slug:
        items = [i for i in items if i.get("slug") == slug]
    if limit is not None:
        items = items[:limit]
    return items


def run(
    weeks: Optional[int] = None,
    dry_run: bool = False,
    slug: Optional[str] = None,
    post_id: Optional[int] = None,
    limit: Optional[int] = None,
    today: Optional[date] = None,
) -> dict:
    """上映中フラグの週次チェックを実行する。

    対象一覧の取得に失敗した場合は WordPress を一切更新せず、`error` キーを
    含む結果を返す（誤って全記事のフラグを外さないため）。

    Args:
        weeks  : 上映終了とみなす経過週数。None で環境変数 → 既定値。
        dry_run: True の場合、判定のみで更新も Slack 通知も行わない。
        slug   : 指定した場合、該当 slug のみ処理する。
        post_id: 指定した場合、該当 post_id のみ処理する（slug より優先）。
        limit  : 最大処理件数。
        today  : 基準日（テスト用）。None で当日。

    Returns:
        実行結果の辞書。

    Example return value::

        {
            "weeks": 8,
            "dry_run": false,
            "posts": {"total": 12, "showing": 9, "ended": 2, "unknown": 1, "errors": 0},
            "ended": [
                {
                    "id": 16233, "slug": "example-movie", "title": "作品A",
                    "reason": "expired", "release_date": "2026-06-01",
                    "elapsed_days": 70, "cinema_url": "https://...",
                    "url": "https://katsumascore.blog/ja/movie/example-movie"
                }
            ],
            "unknown": []
        }
    """
    today = today or date.today()
    weeks = weeks if weeks is not None else default_showing_weeks()

    logger.info("劇場公開チェック開始: weeks=%d dry_run=%s", weeks, dry_run)

    try:
        items = get_theater_showing_posts()
    except TheaterListError as e:
        logger.error("上映中一覧の取得に失敗したため中断する（更新なし）: %s", e)
        return {
            "weeks": weeks,
            "dry_run": dry_run,
            "error": str(e),
            "posts": {"total": 0, "showing": 0, "ended": 0, "unknown": 0, "errors": 0},
            "ended": [],
            "unknown": [],
        }

    targets = _select_targets(items, slug, post_id, limit)
    logger.info("チェック対象: %d件（上映中フラグ ON: %d件）", len(targets), len(items))

    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    rate_limiter = RateLimiter()

    showing = 0
    errors = 0
    ended_items: list[dict] = []
    unknown_items: list[dict] = []
    url_checked = 0

    for item in targets:
        cinema_url = item.get("cinema_url") or ""
        url_state = "unknown"
        if cinema_url:
            if url_checked:
                rate_limiter.wait()
            url_state = check_cinema_url(cinema_url, session)
            url_checked += 1

        verdict, reason = judge(item, today, weeks, url_state)
        days = elapsed_days(item.get("release_date") or "", today)
        record = {
            "id": item.get("id"),
            "slug": item.get("slug", ""),
            "title": item.get("title", ""),
            "reason": reason,
            "release_date": item.get("release_date", ""),
            "elapsed_days": days,
            "cinema_url": cinema_url,
            "url": _front_url(item),
        }

        if verdict == "showing":
            showing += 1
            logger.info("上映中: post_id=%s slug=%s reason=%s", item.get("id"), item.get("slug"), reason)
            continue

        if verdict == "unknown":
            unknown_items.append(record)
            logger.warning(
                "判定不能（据え置き）: post_id=%s slug=%s（公開日が未入力で劇場URLも判定できない）",
                item.get("id"), item.get("slug"),
            )
            continue

        # verdict == "ended"
        if dry_run:
            logger.info(
                "[dry-run] 上映終了: post_id=%s slug=%s reason=%s",
                item.get("id"), item.get("slug"), reason,
            )
            ended_items.append(record)
            continue

        try:
            patch_cinema_showing(item["id"], False)
        except Exception as e:
            errors += 1
            logger.error(
                "上映中フラグの更新に失敗: post_id=%s slug=%s error=%s",
                item.get("id"), item.get("slug"), e,
            )
            continue

        ended_items.append(record)
        logger.info(
            "上映終了 → フラグOFF: post_id=%s slug=%s reason=%s",
            item.get("id"), item.get("slug"), reason,
        )

    result = {
        "weeks": weeks,
        "dry_run": dry_run,
        "posts": {
            "total": len(targets),
            "showing": showing,
            "ended": len(ended_items),
            "unknown": len(unknown_items),
            "errors": errors,
        },
        "ended": ended_items,
        "unknown": unknown_items,
    }

    if dry_run:
        logger.info("[dry-run] Slack 通知はスキップ: %s", result["posts"])
    else:
        notify_theater_showing_result(ended_items, unknown_items, weeks)

    logger.info("劇場公開チェック完了: %s", result["posts"])
    return result


def main() -> None:
    """CLI エントリーポイント。"""
    parser = argparse.ArgumentParser(description="劇場公開（上映中フラグ）の週次チェック")
    parser.add_argument("--weeks", type=int, default=None, help="上映終了とみなす経過週数（既定: 8）")
    parser.add_argument("--dry-run", action="store_true", help="判定のみ（WordPress を更新しない）")
    parser.add_argument("--slug", type=str, default=None, help="特定 slug のみ処理する")
    parser.add_argument("--post-id", type=int, default=None, help="特定 post_id のみ処理する")
    parser.add_argument("--limit", type=int, default=None, help="最大処理件数")
    args = parser.parse_args()

    result = run(
        weeks=args.weeks,
        dry_run=args.dry_run,
        slug=args.slug,
        post_id=args.post_id,
        limit=args.limit,
    )

    if result.get("error"):
        sys.exit(1)


if __name__ == "__main__":
    main()
