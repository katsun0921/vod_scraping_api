"""VOD 配信状況チェッカー メイン処理。

WordPress REST API から投稿データを取得し、各サービスの scraping_url をチェックして
ACF フィールドと vod タクソノミーを更新する。
CLI からも Cloud Run のエントリーポイントからも呼び出せる。

Usage:
    python checker.py              # 通常実行（クールダウン・30日以内更新済みはスキップ）
    python checker.py --dry-run    # 対象の確認のみ（更新なし）
    python checker.py --force      # cooldown / updated_at チェックを無視して全件処理
    python checker.py --slug john-wick  # 特定の slug のみ処理
    python checker.py --limit 10   # 最大 10 件のみ処理
"""

import argparse
import logging
import os
import sys
from datetime import date, datetime
from typing import Optional

from checkers.amazon import AmazonChecker
from checkers.apple_tv import AppleTvChecker
from checkers.disney_plus import DisneyPlusChecker
from checkers.dmm_tv import DmmTvChecker
from checkers.hulu import HuluChecker
from checkers.netflix import NetflixChecker
from checkers.unext import UnextChecker
from checkers.youtube import YoutubeChecker
from checkers.crunchyroll import CrunchyrollChecker
from utils.rate_limit import RateLimiter
from utils.slack import notify_new_streaming
from utils.wordpress import (
    SERVICES,
    VOD_TERM_IDS,
    get_posts,
    get_vod_term_ids,
    patch_cooldown,
    should_skip,
    update_cooldown,
    update_post,
)

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
    "apple_tv":           AppleTvChecker,
    "youtube":            YoutubeChecker,
    "crunchyroll":        CrunchyrollChecker,
}

MAX_CONSECUTIVE_ERRORS = 3

_RELEASE_YEAR_FALLBACK_PHASE1 = 9999  # ASC ソートで空欄を最後尾にする
_RELEASE_YEAR_FALLBACK_PHASE2 = 0     # DESC(-) ソートで空欄を最後尾にする
_DATE_FAR_FUTURE = date(9999, 12, 31)



def _all_updated_at_empty(post: dict) -> bool:
    """全サービスの updated_at が空欄なら True（未スクレイピング post の判定）。"""
    acf = post.get("acf") or {}
    return all(not (acf.get(svc) or {}).get("updated_at") for svc in SERVICES)


def _has_any_streaming(post: dict) -> bool:
    """1つでも配信中のサービスがあれば True。"""
    acf = post.get("acf") or {}
    return any((acf.get(svc) or {}).get("status") == "streaming" for svc in SERVICES)


def _min_updated_at(post: dict) -> date:
    """全サービス中で最も古い updated_at を返す。なければ _DATE_FAR_FUTURE。"""
    acf = post.get("acf") or {}
    dates = []
    for svc in SERVICES:
        val = (acf.get(svc) or {}).get("updated_at") or ""
        if val:
            try:
                dates.append(date.fromisoformat(val[:10]))
            except ValueError:
                pass
    return min(dates) if dates else _DATE_FAR_FUTURE


def _sort_key_phase1(post: dict) -> tuple:
    """フェーズ1ソートキー: release_year ASC（空欄=9999最後尾）, min_updated_at ASC。"""
    acf = post.get("acf") or {}
    try:
        year = int(acf.get("release_year") or 0) or _RELEASE_YEAR_FALLBACK_PHASE1
    except (ValueError, TypeError):
        year = _RELEASE_YEAR_FALLBACK_PHASE1
    return (year, _min_updated_at(post))


def _sort_key_phase2(post: dict) -> tuple:
    """フェーズ2ソートキー: 配信中先頭, release_year DESC（空欄=最後尾）, min_updated_at ASC。"""
    acf = post.get("acf") or {}
    streaming_last = 0 if _has_any_streaming(post) else 1
    try:
        year = int(acf.get("release_year") or 0) or _RELEASE_YEAR_FALLBACK_PHASE2
    except (ValueError, TypeError):
        year = _RELEASE_YEAR_FALLBACK_PHASE2
    return (streaming_last, -year, _min_updated_at(post))


def _select_targets(candidates: list[dict], quota: int) -> tuple[list[dict], int, int]:
    """クォータ内で処理する post を優先順に選ぶ。

    フェーズ1（未スクレイピング post）を先に消化し、枠が余ればフェーズ2（通常優先）で補完。

    Returns:
        (targets, phase1_count, phase2_count)
    """
    phase1 = sorted(
        [p for p in candidates if _all_updated_at_empty(p)],
        key=_sort_key_phase1,
    )
    p1_take = min(len(phase1), quota)
    targets: list[dict] = list(phase1[:p1_take])

    remaining = quota - p1_take
    if remaining > 0:
        p1_ids = {p["id"] for p in targets}
        phase2 = sorted(
            [p for p in candidates if p["id"] not in p1_ids],
            key=_sort_key_phase2,
        )
        p2_take = min(len(phase2), remaining)
        targets.extend(phase2[:p2_take])
    else:
        p2_take = 0

    return targets, p1_take, p2_take



def run(
    dry_run: bool = False,
    force: bool = False,
    slug: Optional[str] = None,
    limit: Optional[int] = None,
) -> dict:
    """WordPress 投稿の VOD 配信状況チェックを実行する。

    Args:
        dry_run: True の場合、対象の確認のみ行い更新しない。
        force  : True の場合、cooldown / updated_at チェックを無視して全件処理する。
        slug   : 指定した場合、該当 slug の投稿のみ処理する。
        limit  : 指定した場合、最大 limit 件のみ処理する。

    Returns:
        {"processed": int, "skipped": int, "errors": int} の辞書。
    """
    posts = get_posts(slug=slug, limit=limit)
    processed = 0
    skipped = 0
    errors = 0
    consecutive_errors = 0
    rate_limiter = RateLimiter()
    current_service: Optional[str] = None
    today = date.today()
    quota = len(posts) if force else int(os.environ.get("DAILY_QUOTA", "30"))

    # 投稿レベルのスキップ判定（scraping_disabled / cooldown）
    candidates: list[dict] = []
    if force:
        candidates = list(posts)
    else:
        for post in posts:
            post_slug = post.get("slug", "")
            acf = post.get("acf") or {}
            if acf.get("scraping_disabled"):
                logger.info("SKIP  [%s] scraping_disabled=true", post_slug)
                skipped += 1
                continue
            cooldown_str = acf.get("scraping_cooldown_until") or ""
            if cooldown_str:
                try:
                    cooldown_date = date.fromisoformat(cooldown_str)
                    if cooldown_date >= today:
                        logger.info("SKIP  [%s] cooldown_until=%s", post_slug, cooldown_str)
                        skipped += 1
                        continue
                except ValueError:
                    logger.warning("[%s] scraping_cooldown_until の形式が不正: %r", post_slug, cooldown_str)
            candidates.append(post)

    # 日次クォータ適用（優先順位ソート → スライス）
    targets, phase1_count, phase2_count = _select_targets(candidates, quota)
    quota_skipped = len(candidates) - len(targets)
    skipped += quota_skipped
    logger.info(
        "QUOTA phase1=%d phase2=%d total=%d quota_skipped=%d",
        phase1_count, phase2_count, len(targets), quota_skipped,
    )

    for post in targets:
        post_id = post["id"]
        post_slug = post.get("slug", "")
        post_title = (post.get("title") or {}).get("rendered") or post_slug
        vod_term_ids = get_vod_term_ids(post)
        post_checked = False  # この投稿で1サービスでも処理したか

        for service in SERVICES:
            # サービスレベルのスキップ判定（scraping_url / updated_at）
            if not force:
                skip, reason = should_skip(post, service, today)
                # 投稿レベル判定は上で処理済みなので scraping_disabled / cooldown は無視
                if skip and reason not in ("scraping_disabled=true",) and not reason.startswith("cooldown_until="):
                    logger.info("SKIP  [%s][%s] %s", post_slug, service, reason)
                    skipped += 1
                    continue

            checker_class = _CHECKER_MAP.get(service)
            if not checker_class:
                logger.info("SKIP  [%s][%s] チェッカー未実装", post_slug, service)
                skipped += 1
                continue

            acf = post.get("acf") or {}
            service_data = acf.get(service) or {}
            scraping_url = (service_data.get("scraping_url") or "").strip()
            if not scraping_url:
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
                post_checked = True
                continue

            try:
                checker = checker_class()
                result = checker.check(scraping_url)
                rate_limiter.wait()

                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                is_new_streaming = update_post(
                    post_id=post_id,
                    service=service,
                    status=result["status"],
                    price=result.get("price"),
                    updated_at=now,
                    current_vod_term_ids=vod_term_ids,
                )
                if is_new_streaming:
                    notify_new_streaming(post_title, service, scraping_url)
                # taxonomy 更新後の term_ids を反映（次サービスの処理に使う）
                term_id = VOD_TERM_IDS.get(service, 0)
                if term_id:
                    if result["status"] == "streaming":
                        if term_id not in vod_term_ids:
                            vod_term_ids.append(term_id)
                    else:
                        vod_term_ids = [t for t in vod_term_ids if t != term_id]

                # post の acf を更新してクールダウン計算に反映
                if post.get("acf") is None:
                    post["acf"] = {}
                if post["acf"].get(service) is None:
                    post["acf"][service] = {}
                post["acf"][service]["status"] = result["status"]

                logger.info(
                    "UPDATE [%s][%s] status=%s price=%s",
                    post_slug, service, result["status"], result.get("price"),
                )
                processed += 1
                post_checked = True
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

        # 全サービスチェック完了後にクールダウンを更新
        if post_checked and not dry_run:
            cooldown_acf: dict = {}
            update_cooldown(post, today, cooldown_acf)
            if cooldown_acf:
                try:
                    patch_cooldown(post_id, cooldown_acf)
                    logger.info("COOLDOWN [%s] %s", post_slug, cooldown_acf)
                except Exception as e:
                    logger.error("ERROR [%s] cooldown PATCH 失敗: %s", post_slug, e)

    return {"processed": processed, "skipped": skipped, "errors": errors}


def main() -> None:
    """CLIエントリーポイント。"""
    # ローカル実行時は .env から環境変数を読み込む（Cloud Run では不要）
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    parser = argparse.ArgumentParser(description="VOD配信状況チェッカー")
    parser.add_argument("--dry-run", action="store_true", help="対象の確認のみ（更新なし）")
    parser.add_argument("--force", action="store_true", help="cooldown / updated_at を無視して全件処理")
    parser.add_argument("--slug", type=str, help="特定のslugのみ処理")
    parser.add_argument("--limit", type=int, default=None, help="処理する最大件数")
    args = parser.parse_args()

    result = run(dry_run=args.dry_run, force=args.force, slug=args.slug, limit=args.limit)
    print(result)


if __name__ == "__main__":
    main()
