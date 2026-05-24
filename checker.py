"""VOD 配信状況チェッカー メイン処理。

WordPress REST API から投稿データを取得し、各サービスの scraping_url をチェックして
ACF フィールドと vod タクソノミーを更新する。
CLI からも Cloud Run のエントリーポイントからも呼び出せる。

Usage:
    python checker.py              # 通常実行（1ヶ月以内更新済みはスキップ）
    python checker.py --dry-run    # 対象の確認のみ（更新なし）
    python checker.py --force      # updated_at に関わらず全件処理
    python checker.py --slug john-wick  # 特定の slug のみ処理
"""

import argparse
import logging
import sys
from datetime import datetime, timedelta
from typing import Optional

from checkers.amazon import AmazonChecker
from checkers.disney_plus import DisneyPlusChecker
from checkers.dmm_tv import DmmTvChecker
from checkers.hulu import HuluChecker
from checkers.netflix import NetflixChecker
from checkers.unext import UnextChecker
from checkers.youtube import YoutubeChecker
from utils.rate_limit import RateLimiter
from utils.wordpress import SERVICES, get_posts, get_vod_term_ids, update_post

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# サービス名 → チェッカークラス
_CHECKER_MAP: dict[str, type] = {
    "amazon_prime_video": AmazonChecker,
    "netflix":            NetflixChecker,
    "hulu":               HuluChecker,
    "unext":              UnextChecker,
    "disney_plus":        DisneyPlusChecker,
    "dmm_tv":             DmmTvChecker,
    "youtube":            YoutubeChecker,
}

MAX_CONSECUTIVE_ERRORS = 3
SKIP_WITHIN_DAYS = 30


def _should_skip(updated_at_str: str, force: bool) -> Optional[str]:
    """スキップすべき理由を返す。スキップ不要なら None を返す。

    Args:
        updated_at_str: ACF フィールドの updated_at 値（空文字可）。
        force         : True の場合は updated_at チェックをスキップする。

    Returns:
        スキップ理由の文字列、またはスキップ不要な場合は None。
    """
    if force:
        return None
    updated_at = updated_at_str.strip()
    if not updated_at:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(updated_at, fmt)
            if datetime.now() - dt < timedelta(days=SKIP_WITHIN_DAYS):
                return f"updated_at={updated_at}（{SKIP_WITHIN_DAYS}日以内）"
            break
        except ValueError:
            continue
    return None


def run(dry_run: bool = False, force: bool = False, slug: Optional[str] = None) -> dict:
    """WordPress 投稿の VOD 配信状況チェックを実行する。

    Args:
        dry_run: True の場合、対象の確認のみ行い更新しない。
        force  : True の場合、updated_at に関わらず全件処理する。
        slug   : 指定した場合、該当 slug の投稿のみ処理する。

    Returns:
        {"processed": int, "skipped": int, "errors": int} の辞書。
    """
    posts = get_posts()
    processed = 0
    skipped = 0
    errors = 0
    consecutive_errors = 0
    rate_limiter = RateLimiter()
    current_service: Optional[str] = None

    for post in posts:
        post_id = post["id"]
        post_slug = post.get("slug", "")

        # slug フィルタ
        if slug and post_slug != slug:
            skipped += 1
            continue

        acf = post.get("acf") or {}
        vod_term_ids = get_vod_term_ids(post)

        for service in SERVICES:
            service_data = acf.get(service) or {}
            scraping_url = (service_data.get("scraping_url") or "").strip()

            if not scraping_url:
                continue

            # updated_at スキップ判定
            reason = _should_skip(service_data.get("updated_at") or "", force)
            if reason:
                logger.info("SKIP  [%s][%s] %s", post_slug, service, reason)
                skipped += 1
                continue

            checker_class = _CHECKER_MAP.get(service)
            if not checker_class:
                logger.info("SKIP  [%s][%s] チェッカー未実装", post_slug, service)
                skipped += 1
                continue

            # VODサービス切り替え時の追加待機
            if current_service is not None and current_service != service:
                logger.info(
                    "INFO  サービス切り替え (%s → %s)、10秒待機",
                    current_service, service,
                )
                rate_limiter.wait_service_switch()
            current_service = service

            logger.info("CHECK [%s][%s] %s", post_slug, service, scraping_url)

            if dry_run:
                processed += 1
                continue

            try:
                checker = checker_class()
                result = checker.check(scraping_url)
                rate_limiter.wait()

                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                update_post(
                    post_id=post_id,
                    service=service,
                    status=result["status"],
                    price=result.get("price"),
                    updated_at=now,
                    current_vod_term_ids=vod_term_ids,
                )
                # taxonomy 更新後の term_ids を反映（次サービスの処理に使う）
                from utils.wordpress import VOD_TERM_IDS
                term_id = VOD_TERM_IDS.get(service, 0)
                if term_id:
                    if result["status"] == "streaming":
                        if term_id not in vod_term_ids:
                            vod_term_ids.append(term_id)
                    else:
                        vod_term_ids = [t for t in vod_term_ids if t != term_id]

                logger.info(
                    "UPDATE [%s][%s] status=%s price=%s",
                    post_slug, service, result["status"], result.get("price"),
                )
                processed += 1
                consecutive_errors = 0

            except RuntimeError as e:
                logger.error("ERROR [%s][%s] %s", post_slug, service, e)
                errors += 1
                consecutive_errors += 1
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    logger.error(
                        "連続エラー %d 回に達したため処理を中断します", MAX_CONSECUTIVE_ERRORS
                    )
                    return {"processed": processed, "skipped": skipped, "errors": errors}

            except Exception as e:
                logger.exception("ERROR [%s][%s] 予期しないエラー: %s", post_slug, service, e)
                errors += 1
                consecutive_errors += 1
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    logger.error(
                        "連続エラー %d 回に達したため処理を中断します", MAX_CONSECUTIVE_ERRORS
                    )
                    return {"processed": processed, "skipped": skipped, "errors": errors}

    return {"processed": processed, "skipped": skipped, "errors": errors}


def main() -> None:
    """CLIエントリーポイント。"""
    parser = argparse.ArgumentParser(description="VOD配信状況チェッカー")
    parser.add_argument("--dry-run", action="store_true", help="対象の確認のみ（更新なし）")
    parser.add_argument("--force", action="store_true", help="updated_at に関わらず全件処理")
    parser.add_argument("--slug", type=str, help="特定のslugのみ処理")
    args = parser.parse_args()

    result = run(dry_run=args.dry_run, force=args.force, slug=args.slug)
    print(result)


if __name__ == "__main__":
    main()
