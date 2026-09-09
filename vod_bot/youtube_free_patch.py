"""YouTube 無料配信の日次チェック。

`youtube.scraping_url` が登録された記事について、いま**無料で観られるか**を
日次で確認し、ACF の配信ステータス・価格・チャンネル名を更新する。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
なぜ週次パッチと別建てなのか
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  映画配給会社の公式チャンネル（例:【公式】プレシディオチャンネル）による
  無料公開は数日〜数週間で終わる。`weekly_patch.py` は post_id % 8 のバッチ制で
  同じ記事を2ヶ月に1回しか巡回しないため、無料公開の開始も終了も取り逃がす。

  TOP の「YouTube で無料配信中」セクション（`/v1/youtube-free-list`）は
  `youtube.status = streaming` かつ `youtube.price` が 0 を母集団にするため、
  終了済みの作品が残り続けると「観に行ったらもう有料だった」が起きる。

  対象は YouTube URL 付きの記事だけで母数が小さく、requests ベースで
  1件あたり数秒のため、日次で全件を見ても負荷は小さい。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
対象の選び方（重要）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  現在のステータスでは絞らない。無料 → 有料だけでなく、**有料・配信終了だった
  作品が新たに無料公開される**ケースも拾う必要があるため、
  `rental` / `purchase` / `ended` の記事も毎回チェックする。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
実行
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  GitHub Actions: .github/workflows/youtube-free-check.yml（毎日 06:00 JST）
  Cloud Run     : POST /youtube-free-check

Usage:
    python youtube_free_patch.py                # YouTube URL 付きの全記事をチェック
    python youtube_free_patch.py --dry-run      # 判定のみ（WordPress を更新しない）
    python youtube_free_patch.py --slug one-missed-call-2003  # 特定 slug のみ
    python youtube_free_patch.py --post-id 123  # 特定 post_id のみ
    python youtube_free_patch.py --limit 10     # 上限10件（デバッグ用）
"""

import argparse
import logging
import sys
from datetime import datetime
from typing import Optional

from checkers.youtube import YoutubeChecker
from slack import notify_youtube_free_failure, notify_youtube_free_result
from utils.rate_limit import RateLimiter
from wordpress import (
    FRONT_BASE_URL,
    YOUTUBE_FIELD,
    YOUTUBE_FREE_STATUS,
    build_front_url,
    get_category_slug_map,
    get_vod_term_ids,
    get_youtube_url_posts,
    patch_youtube_status,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# 同一ホスト（YouTube）へ連続アクセスするため、既定より広めの待機を取る
_RATE_LIMITER = RateLimiter(min_wait=4.0, max_wait=7.0)


def _front_url(post: dict, category_slug_map: dict[int, str]) -> str:
    """記事のフロントエンド表示 URL を組み立てる。

    Args:
        post             : WordPress REST の投稿データ。
        category_slug_map: term_id → スラッグの対応表。

    Returns:
        フロントの記事 URL。組み立てられない場合はサイトのトップ URL。
    """
    acf = post.get("acf") or {}
    category_ids = post.get("categories") or []
    category_slug = next(
        (category_slug_map.get(cid, "") for cid in category_ids if category_slug_map.get(cid)),
        "",
    )
    slug = post.get("slug", "")
    if not slug:
        return FRONT_BASE_URL
    return build_front_url(slug, acf.get("lang", "ja"), category_slug)


def _post_title(post: dict) -> str:
    """投稿データから表示用タイトルを取り出す。"""
    title = (post.get("title") or {}).get("rendered", "")
    return title or post.get("slug", "")


def _select_targets(
    posts: list[dict],
    limit: Optional[int],
) -> list[dict]:
    """件数上限を適用する（slug / post_id の絞り込みは取得時に済んでいる）。"""
    return posts[:limit] if limit is not None else posts


def run(
    dry_run: bool = False,
    slug: Optional[str] = None,
    post_id: Optional[int] = None,
    limit: Optional[int] = None,
) -> dict:
    """YouTube 無料配信の日次チェックを実行する。

    Args:
        dry_run: True の場合、判定のみで更新も Slack 通知も行わない。
        slug   : 指定した場合、該当 slug のみ処理する。
        post_id: 指定した場合、該当 post_id のみ処理する（slug より優先）。
        limit  : 最大処理件数。

    Returns:
        実行結果の辞書。

    Example return value::

        {
            "dry_run": false,
            "posts": {"total": 24, "free": 6, "paid": 15, "started": 1, "ended": 2,
                      "skipped": 1, "errors": 0},
            "started": [{"id": 16233, "slug": "one-missed-call-2003", ...}],
            "ended": [],
            "skipped": [],
            "errors_detail": []
        }

    Slack には開始・終了・判定不能・更新失敗をまとめて1通で通知する
    （変化が無い日は通知しない）。対象一覧の取得に失敗した場合は
    中断そのものを別途通知する。
    """
    logger.info("YouTube 無料配信チェック開始: dry_run=%s", dry_run)

    try:
        posts = get_youtube_url_posts(slug=slug, post_id=post_id)
    except Exception as e:
        logger.error("対象記事の取得に失敗したため中断する（更新なし）: %s", e)
        # 無通知だと「今日は変化が無かった」と区別できず、TOP の無料枠が
        # 古いまま放置される。失敗そのものを必ず知らせる
        if not dry_run:
            notify_youtube_free_failure(str(e))
        return {
            "dry_run": dry_run,
            "error": str(e),
            "posts": {"total": 0, "free": 0, "paid": 0, "started": 0, "ended": 0, "skipped": 0, "errors": 0},
            "started": [],
            "ended": [],
            "skipped": [],
            "errors": [],
        }

    targets = _select_targets(posts, limit)
    logger.info("チェック対象: %d件", len(targets))

    category_slug_map = get_category_slug_map() if targets else {}
    checker = YoutubeChecker()

    free = 0
    paid = 0
    started_items: list[dict] = []
    ended_items: list[dict] = []
    skipped_items: list[dict] = []
    error_items: list[dict] = []
    checked = 0

    for post in targets:
        acf = post.get("acf") or {}
        youtube = acf.get(YOUTUBE_FIELD) or {}
        youtube_url = youtube.get("scraping_url") or ""
        prev_status = youtube.get("status") or ""
        pid = int(post.get("id") or 0)

        record = {
            "id": pid,
            "slug": post.get("slug", ""),
            "title": _post_title(post),
            "prev_status": prev_status,
            "youtube_url": youtube_url,
            "url": _front_url(post, category_slug_map),
            "channel_name": youtube.get("channel_name") or "",
        }

        if checked:
            _RATE_LIMITER.wait()
        checked += 1

        try:
            result = checker.check(youtube_url)
        except RuntimeError as e:
            # 判定不能。既存の値を据え置く（誤って無料枠から落とさない）
            skipped_items.append({**record, "status": prev_status, "reason": str(e)})
            logger.warning("判定不能（据え置き）: post_id=%d slug=%s %s", pid, record["slug"], e)
            continue

        status = result["status"]
        price = result.get("price")
        channel_name = result.get("channel_name") or ""
        if channel_name:
            record["channel_name"] = channel_name

        if status == YOUTUBE_FREE_STATUS:
            free += 1
        else:
            paid += 1

        became_free = status == YOUTUBE_FREE_STATUS and prev_status != YOUTUBE_FREE_STATUS
        became_paid = prev_status == YOUTUBE_FREE_STATUS and status != YOUTUBE_FREE_STATUS

        if status == prev_status and not became_free and not became_paid:
            logger.info(
                "変化なし: post_id=%d slug=%s status=%s", pid, record["slug"], status
            )

        if dry_run:
            if became_free:
                started_items.append({**record, "status": status, "price": price})
            elif became_paid:
                ended_items.append({**record, "status": status, "price": price})
            logger.info(
                "[dry-run] post_id=%d slug=%s %s → %s（price=%s, channel=%s）",
                pid, record["slug"], prev_status or "未取得", status, price, channel_name,
            )
            continue

        updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            patch_youtube_status(
                post_id=pid,
                status=status,
                price=price,
                updated_at=updated_at,
                channel_name=channel_name,
                current_vod_term_ids=get_vod_term_ids(post),
            )
        except Exception as e:
            error_items.append({**record, "status": status, "price": price, "reason": str(e)})
            logger.error("更新に失敗: post_id=%d slug=%s error=%s", pid, record["slug"], e)
            continue

        if became_free:
            started_items.append({**record, "status": status, "price": price})
            logger.info("無料公開の開始: post_id=%d slug=%s channel=%s", pid, record["slug"], channel_name)
        elif became_paid:
            ended_items.append({**record, "status": status, "price": price})
            logger.info("無料公開の終了: post_id=%d slug=%s → %s", pid, record["slug"], status)

    result = {
        "dry_run": dry_run,
        "posts": {
            "total": len(targets),
            "free": free,
            "paid": paid,
            "started": len(started_items),
            "ended": len(ended_items),
            "skipped": len(skipped_items),
            "errors": len(error_items),
        },
        "started": started_items,
        "ended": ended_items,
        "skipped": skipped_items,
        "errors_detail": error_items,
    }

    if dry_run:
        logger.info("[dry-run] Slack 通知はスキップ: %s", result["posts"])
    else:
        notify_youtube_free_result(started_items, ended_items, skipped_items, error_items)

    logger.info("YouTube 無料配信チェック完了: %s", result["posts"])
    return result


def main() -> None:
    """CLI エントリーポイント。"""
    parser = argparse.ArgumentParser(description="YouTube 無料配信の日次チェック")
    parser.add_argument("--dry-run", action="store_true", help="判定のみ（WordPress を更新しない）")
    parser.add_argument("--slug", type=str, default=None, help="特定 slug のみ処理する")
    parser.add_argument("--post-id", type=int, default=None, help="特定 post_id のみ処理する")
    parser.add_argument("--limit", type=int, default=None, help="最大処理件数")
    args = parser.parse_args()

    result = run(
        dry_run=args.dry_run,
        slug=args.slug,
        post_id=args.post_id,
        limit=args.limit,
    )

    if result.get("error"):
        sys.exit(1)


if __name__ == "__main__":
    main()
