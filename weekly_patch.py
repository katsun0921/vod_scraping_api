"""週次パッチ統合ランナー（URLチェック + JustWatch検索）。

各 VOD 投稿に対して以下を1パスで実行する:
  - scraping_url が設定済みのサービス → 既存チェッカーでステータス確認
  - scraping_url が未設定のサービス → JustWatch API でURL検索

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
スケジューリングバッジ方式
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  各投稿に post_id % 4 でバッチ番号（0-3）を固定割り当てする。
  同一投稿は毎月同じ週（第1〜4月曜）に処理される。
  ACFフィールドの追加は不要で、WordPress側の変更なしに機能する。

  batch 0 (post_id % 4 == 0) → 毎月第1月曜
  batch 1 (post_id % 4 == 1) → 毎月第2月曜
  batch 2 (post_id % 4 == 2) → 毎月第3月曜
  batch 3 (post_id % 4 == 3) → 毎月第4月曜

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
週次予算（100件/バッチ × 4バッチ = 400件/月）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  JustWatch GraphQL  : 最大 400件 × 1〜2回 = 400〜800 calls/月
  URL スクレイピング : 400件 × 平均3サービス ≈ 1,200 calls/月
    ├ requests ベース: ~3秒/call
    └ Playwright ベース (U-NEXT / DMM / Crunchyroll): ~15秒/call
  WordPress API      : 400件 × 平均7回 ≈ 2,800 calls/月
  推定処理時間       : 100件 × 約25〜40分/回 × 4回 ≈ 2時間/月

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Cloud Scheduler 推奨設定（毎週月曜 02:00 JST）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  POST /weekly-patch  ← batch は実行日から自動判定

Usage:
    python weekly_patch.py              # 今週のバッチを日付から自動判定
    python weekly_patch.py --batch 0    # バッチ0（第1週）を強制実行
    python weekly_patch.py --force      # 7日以内の更新済みもスキップしない
    python weekly_patch.py --dry-run    # 対象の確認のみ（更新なし）
    python weekly_patch.py --limit 50   # 最大50件のみ処理
    python weekly_patch.py --slug john-wick  # 特定 slug のみ
"""

import argparse
import logging
import sys
import time
from datetime import date, datetime
from typing import Optional

from checkers.amazon import AmazonChecker
from checkers.apple_tv import AppleTvChecker
from checkers.crunchyroll import CrunchyrollChecker
from checkers.disney_plus import DisneyPlusChecker
from checkers.dmm_tv import DmmTvChecker
from checkers.hulu import HuluChecker
from checkers.netflix import NetflixChecker
from checkers.unext import UnextChecker
from checkers.youtube import YoutubeChecker
from utils.justwatch import search_urls
from utils.rate_limit import RateLimiter
from utils.slack import notify_new_streaming
from utils.wordpress import (
    SERVICES,
    SERVICE_REQUIRED_CATEGORY_IDS,
    SERVICE_SUPPORTED_LANGUAGES,
    VOD_TERM_IDS,
    get_all_posts_for_patch,
    get_vod_term_ids,
    patch_cooldown,
    patch_multi_service_fields,
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

# Playwright を使用するサービス（処理時間が大きいため予算計算で区別する）
_PLAYWRIGHT_SERVICES = frozenset({"unext", "dmm_tv", "crunchyroll"})

# バッチ数（週数）
BATCH_COUNT = 4

# デフォルト処理件数（1バッチあたり）
DEFAULT_BATCH_SIZE = 100

# JustWatch リクエスト間隔（秒）
_JW_WAIT_SECONDS = 3.0

# 週次パッチで直近何日以内に更新済みのサービスをスキップするか
# force=True のときはこのチェックをスキップする
_PATCH_SKIP_WITHIN_DAYS = 7

# 最大連続エラー数（この回数連続してエラーが発生したら処理中断）
MAX_CONSECUTIVE_ERRORS = 3

# リリース年がない場合のフォールバック値（ソートキー用）
_RELEASE_YEAR_FALLBACK_PHASE1 = 9999  # ASC ソートで空欄を最後尾にする
_RELEASE_YEAR_FALLBACK_PHASE2 = 0     # DESC(-) ソートで空欄を最後尾にする

# updated_at が空の場合のフォールバック（ASC ソートで空欄を最後尾にする）
_DATE_FAR_FUTURE = date(9999, 12, 31)


# ──────────────────────────────────────────────────────────────
# スケジューリングバッジ
# ──────────────────────────────────────────────────────────────

def get_batch_for_date(d: Optional[date] = None) -> int:
    """指定日が月の第何バッチ（0-3）かを返す。

    月の日付を7で割った商をバッチ番号とする。
      1〜7  日 → batch 0（第1週）
      8〜14 日 → batch 1（第2週）
      15〜21日 → batch 2（第3週）
      22〜31日 → batch 3（第4週）

    Args:
        d: 基準日。None の場合は今日。

    Returns:
        バッチ番号（0-3）。
    """
    if d is None:
        d = date.today()
    return min((d.day - 1) // 7, BATCH_COUNT - 1)


def get_post_badge(post: dict) -> int:
    """投稿のスケジューリングバッジ（バッチ番号 0-3）を返す。

    post_id % BATCH_COUNT による静的ハッシュ割り当て。
    同一投稿は毎月必ず同じ週に処理される。

    Args:
        post: WordPress 投稿データ。

    Returns:
        バッチ番号（0-3）。
    """
    return post.get("id", 0) % BATCH_COUNT


# ──────────────────────────────────────────────────────────────
# 優先度ソート（旧 checker.py から移植）
# ──────────────────────────────────────────────────────────────

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

    Args:
        candidates: 選択対象の投稿リスト。
        quota     : 最大選択件数。

    Returns:
        (targets, phase1_count, phase2_count) のタプル。
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


# ──────────────────────────────────────────────────────────────
# 対象投稿の選定
# ──────────────────────────────────────────────────────────────

def _get_batch_targets(
    batch: int,
    slug: Optional[str],
    limit: int,
    force: bool,
) -> tuple[list[dict], dict]:
    """バッチ番号に対応する処理対象投稿リストを返す。

    Args:
        batch: バッチ番号（0-3）。
        slug : 指定した場合、該当 slug のみ（バッジフィルタ不適用）。
        limit: 最大件数。
        force: True のとき cooldown フィルタをスキップして全件候補とする。

    Returns:
        (targets, badge_stats) のタプル。
        badge_stats は各バッチの件数サマリ辞書。
    """
    all_posts = get_all_posts_for_patch(slug=slug)

    if slug:
        # slug 指定時はバッジフィルタ・クォータ不要
        return all_posts[:limit], {}

    # バッジ別件数を集計（レポート用）
    badge_stats: dict[int, int] = {i: 0 for i in range(BATCH_COUNT)}
    for p in all_posts:
        b = get_post_badge(p)
        badge_stats[b] = badge_stats.get(b, 0) + 1

    # バッジフィルタ: この週に属する投稿のみ
    batch_posts = [p for p in all_posts if get_post_badge(p) == batch]

    # force=True のときはバッチ全件をクォータとする
    quota = len(batch_posts) if force else limit
    targets, p1, p2 = _select_targets(batch_posts, quota)

    logger.info(
        "バッジ分布: batch0=%d batch1=%d batch2=%d batch3=%d"
        " → 今週(batch%d)=%d件 phase1=%d phase2=%d limit=%d",
        badge_stats[0], badge_stats[1], badge_stats[2], badge_stats[3],
        batch, len(batch_posts), p1, p2, limit,
    )

    return targets, {f"batch{k}": v for k, v in badge_stats.items()}


# ──────────────────────────────────────────────────────────────
# メイン実行
# ──────────────────────────────────────────────────────────────

def run(
    batch: Optional[int] = None,
    limit: int = DEFAULT_BATCH_SIZE,
    dry_run: bool = False,
    force: bool = False,
    slug: Optional[str] = None,
) -> dict:
    """週次パッチを実行する。

    バッチ番号が None の場合は今日の日付から自動判定する。

    処理フロー（投稿ごと）:
      1. サービスを「URL あり」と「URL なし」に分類
      2. URL なし → JustWatch で一括検索、見つかれば scraping_url 登録
      3. URL あり（新規登録含む）→ 各チェッカーでステータス確認
      4. 全結果を WordPress に更新

    force=True のとき:
      - 直近 _PATCH_SKIP_WITHIN_DAYS 日以内の更新済みチェックをスキップしない
      - バッチ内の全件を処理する（limit を超えても全件）

    Args:
        batch  : バッチ番号 0-3。None の場合は日付から自動判定。
        limit  : 最大処理件数（デフォルト: 100）。force=True の場合は無視。
        dry_run: True の場合、対象の確認のみ（更新なし）。
        force  : True の場合、直近更新チェックをスキップして強制処理。
        slug   : 指定した場合、該当 slug のみ処理する。

    Returns:
        週次パッチの実行結果と予算レポートの辞書。

    Example return value::

        {
            "batch": 0,
            "cycle": "2026-05",
            "badge_distribution": {"batch0": 120, "batch1": 115, ...},
            "posts": {"total": 100, "processed": 94, "skipped": 3, "errors": 3},
            "services": {
                "url_checked": 280,
                "jw_searched": 94,
                "urls_registered": 18,
                "status_updated": 262,
            },
            "budget": {
                "wp_api_calls": 820,
                "jw_api_calls": 94,
                "scraping_calls": 215,
                "playwright_calls": 65,
                "estimated_minutes": 38.5,
            },
        }
    """
    today = date.today()
    if batch is None:
        batch = get_batch_for_date(today)
    cycle = today.strftime("%Y-%m")

    logger.info(
        "週次パッチ開始: batch=%d cycle=%s limit=%d dry_run=%s force=%s",
        batch, cycle, limit, dry_run, force,
    )

    # ── 対象投稿を取得 ──────────────────────────────────────────
    targets, badge_distribution = _get_batch_targets(
        batch=batch, slug=slug, limit=limit, force=force,
    )
    logger.info("対象投稿数: %d件（batch=%d）", len(targets), batch)

    # ── カウンタ初期化 ─────────────────────────────────────────
    processed = 0
    skipped = 0
    errors = 0
    consecutive_errors = 0

    url_checked = 0
    jw_searched = 0
    urls_registered = 0
    status_updated = 0

    wp_api_calls = 0
    jw_api_calls = 0
    scraping_calls = 0
    playwright_calls = 0

    rate_limiter = RateLimiter()
    current_service: Optional[str] = None

    # ── 各投稿を処理 ───────────────────────────────────────────
    for post in targets:
        post_id = post["id"]
        post_slug = post.get("slug", "")
        post_title = (post.get("title") or {}).get("rendered") or post_slug
        acf = post.get("acf") or {}
        post_lang = acf.get("lang") or "ja"

        badge = get_post_badge(post)

        # サービスを分類: URL あり vs なし
        url_services: list[str] = []
        missing_services: list[str] = []
        for svc in SERVICES:
            svc_data = acf.get(svc) or {}
            if (svc_data.get("scraping_url") or "").strip():
                url_services.append(svc)
            else:
                missing_services.append(svc)

        logger.info(
            "[%s] badge=%d url_services=%d missing_services=%d",
            post_slug, badge, len(url_services), len(missing_services),
        )

        if dry_run:
            logger.info(
                "DRY-RUN [%s] badge=%d url=%s missing=%s",
                post_slug, badge,
                url_services or "[]",
                missing_services or "[]",
            )
            skipped += 1
            continue

        post_had_error = False
        post_checked = False
        vod_term_ids = get_vod_term_ids(post)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # ── Phase 1: URL なし → JustWatch 検索 ─────────────────
        new_url_map: dict[str, str] = {}
        jw_service_fields: dict[str, dict] = {}

        if missing_services:
            jw_country = "US" if post_lang == "en" else "JP"
            jw_language = "en" if post_lang == "en" else "ja"

            try:
                found_urls = search_urls(
                    title=post_title,
                    slug=post_slug,
                    country=jw_country,
                    language=jw_language,
                )
                jw_api_calls += 1
                jw_searched += 1

                for svc in missing_services:
                    url = found_urls.get(svc, "")
                    if url:
                        new_url_map[svc] = url
                        url_services.append(svc)   # URL 確定 → Phase 2 でチェック
                        urls_registered += 1
                        jw_service_fields[svc] = {"scraping_url": url}
                        logger.info("JW_FOUND [%s][%s] url=%s", post_slug, svc, url)
                    else:
                        jw_service_fields[svc] = {
                            "status": "unavailable",
                            "updated_at": now_str,
                        }
                        status_updated += 1
                        logger.info("JW_MISS  [%s][%s] → unavailable", post_slug, svc)

            except RuntimeError as e:
                logger.error("ERROR [%s] JustWatch 検索失敗: %s", post_slug, e)
                errors += 1
                post_had_error = True

            # JustWatch 結果を 1回の PATCH で書き込む
            if jw_service_fields:
                try:
                    patch_multi_service_fields(post_id, jw_service_fields)
                    wp_api_calls += 2  # GET + PATCH
                except Exception as e:
                    logger.error("ERROR [%s] JW PATCH 失敗: %s", post_slug, e)
                    post_had_error = True

            time.sleep(_JW_WAIT_SECONDS)

        # ── Phase 2: URL あり → チェッカーで確認 ────────────────
        for service in url_services:
            checker_class = _CHECKER_MAP.get(service)
            if not checker_class:
                logger.debug("SKIP  [%s][%s] チェッカー未実装", post_slug, service)
                skipped += 1
                continue

            # 言語ミスマッチ
            if post_lang:
                supported = SERVICE_SUPPORTED_LANGUAGES.get(
                    service, frozenset({"ja", "en"})
                )
                if post_lang not in supported:
                    logger.debug(
                        "SKIP  [%s][%s] language_mismatch=%s",
                        post_slug, service, post_lang,
                    )
                    skipped += 1
                    continue

            # カテゴリ制約
            required_cat_ids = SERVICE_REQUIRED_CATEGORY_IDS.get(service)
            if required_cat_ids:
                post_cat_ids = set(post.get("categories") or [])
                if not post_cat_ids & required_cat_ids:
                    logger.debug("SKIP  [%s][%s] category_mismatch", post_slug, service)
                    skipped += 1
                    continue

            # scraping_url の取得（新規登録分は new_url_map から、既存は acf から）
            if service in new_url_map:
                scraping_url = new_url_map[service]
            else:
                svc_data = acf.get(service) or {}
                scraping_url = (svc_data.get("scraping_url") or "").strip()

            if not scraping_url:
                continue

            # 直近 _PATCH_SKIP_WITHIN_DAYS 日以内に更新済みのサービスはスキップ
            # force=True のときはスキップしない
            if not force:
                svc_data = acf.get(service) or {}
                updated_at_str = (svc_data.get("updated_at") or "")[:10]
                if updated_at_str:
                    try:
                        updated_date = date.fromisoformat(updated_at_str)
                        if (today - updated_date).days < _PATCH_SKIP_WITHIN_DAYS:
                            logger.info(
                                "SKIP  [%s][%s] updated_within_%dd=%s",
                                post_slug, service, _PATCH_SKIP_WITHIN_DAYS, updated_at_str,
                            )
                            skipped += 1
                            continue
                    except ValueError:
                        pass

            # サービス切り替え時の追加待機
            if current_service is not None and current_service != service:
                logger.info(
                    "INFO  サービス切り替え (%s → %s)、10秒待機",
                    current_service, service,
                )
                rate_limiter.wait_service_switch()
            current_service = service

            logger.info("CHECK [%s][%s] %s", post_slug, service, scraping_url)

            try:
                checker = checker_class()
                result = checker.check(scraping_url)
                rate_limiter.wait()

                # 予算カウント
                url_checked += 1
                if service in _PLAYWRIGHT_SERVICES:
                    playwright_calls += 1
                else:
                    scraping_calls += 1

                # WordPress 更新
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                is_new_streaming = update_post(
                    post_id=post_id,
                    service=service,
                    status=result["status"],
                    price=result.get("price"),
                    updated_at=now_str,
                    current_vod_term_ids=vod_term_ids,
                )
                wp_api_calls += 3  # GET(ACF) + PATCH(ACF) + PATCH(vod)
                status_updated += 1

                if is_new_streaming:
                    notify_new_streaming(post_title, service, scraping_url)

                # vod_term_ids をローカルで更新（次サービスの処理に反映）
                term_id = VOD_TERM_IDS.get(service, 0)
                if term_id:
                    if result["status"] == "streaming":
                        if term_id not in vod_term_ids:
                            vod_term_ids.append(term_id)
                    else:
                        vod_term_ids = [t for t in vod_term_ids if t != term_id]

                # post の acf をローカル更新（cooldown 計算に使う）
                if post.get("acf") is None:
                    post["acf"] = {}
                if post["acf"].get(service) is None:
                    post["acf"][service] = {}
                post["acf"][service]["status"] = result["status"]

                logger.info(
                    "UPDATE [%s][%s] status=%s price=%s",
                    post_slug, service, result["status"], result.get("price"),
                )
                post_checked = True
                consecutive_errors = 0

            except RuntimeError as e:
                logger.error("ERROR [%s][%s] %s", post_slug, service, e)
                errors += 1
                consecutive_errors += 1
                post_had_error = True
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    logger.error(
                        "連続エラー %d 回に達したため処理を中断します", MAX_CONSECUTIVE_ERRORS
                    )
                    return _build_result(
                        batch, cycle, badge_distribution, len(targets),
                        processed, skipped, errors,
                        url_checked, jw_searched, urls_registered, status_updated,
                        wp_api_calls, jw_api_calls, scraping_calls, playwright_calls,
                    )

            except Exception as e:
                logger.exception("ERROR [%s][%s] 予期しないエラー: %s", post_slug, service, e)
                errors += 1
                consecutive_errors += 1
                post_had_error = True
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    logger.error(
                        "連続エラー %d 回に達したため処理を中断します", MAX_CONSECUTIVE_ERRORS
                    )
                    return _build_result(
                        batch, cycle, badge_distribution, len(targets),
                        processed, skipped, errors,
                        url_checked, jw_searched, urls_registered, status_updated,
                        wp_api_calls, jw_api_calls, scraping_calls, playwright_calls,
                    )

        # ── Phase 3: クールダウン更新（URL チェックを1件でもした場合）──
        if post_checked:
            cooldown_acf: dict = {}
            update_cooldown(post, today, cooldown_acf)
            if cooldown_acf:
                try:
                    patch_cooldown(post_id, cooldown_acf)
                    wp_api_calls += 2  # GET + PATCH
                    logger.info("COOLDOWN [%s] %s", post_slug, cooldown_acf)
                except Exception as e:
                    logger.error("ERROR [%s] cooldown PATCH 失敗: %s", post_slug, e)

        if not post_had_error:
            processed += 1

    return _build_result(
        batch, cycle, badge_distribution, len(targets),
        processed, skipped, errors,
        url_checked, jw_searched, urls_registered, status_updated,
        wp_api_calls, jw_api_calls, scraping_calls, playwright_calls,
    )


def _build_result(
    batch: int,
    cycle: str,
    badge_distribution: dict,
    total: int,
    processed: int,
    skipped: int,
    errors: int,
    url_checked: int,
    jw_searched: int,
    urls_registered: int,
    status_updated: int,
    wp_api_calls: int,
    jw_api_calls: int,
    scraping_calls: int,
    playwright_calls: int,
) -> dict:
    """実行結果辞書を組み立てる。予算の推定処理時間も計算する。

    推定時間の内訳:
        requests ベース : 3秒/call（平均）
        Playwright ベース: 15秒/call（平均）
        JustWatch API   : 3秒/call
        WordPress API   : 0.5秒/call
    """
    estimated_seconds = (
        scraping_calls * 3
        + playwright_calls * 15
        + jw_api_calls * 3
        + wp_api_calls * 0.5
    )
    estimated_minutes = round(estimated_seconds / 60, 1)

    result = {
        "batch": batch,
        "cycle": cycle,
        "badge_distribution": badge_distribution,
        "posts": {
            "total": total,
            "processed": processed,
            "skipped": skipped,
            "errors": errors,
        },
        "services": {
            "url_checked": url_checked,
            "jw_searched": jw_searched,
            "urls_registered": urls_registered,
            "status_updated": status_updated,
        },
        "budget": {
            "wp_api_calls": wp_api_calls,
            "jw_api_calls": jw_api_calls,
            "scraping_calls": scraping_calls,
            "playwright_calls": playwright_calls,
            "estimated_minutes": estimated_minutes,
        },
    }
    logger.info("週次パッチ完了: %s", result)
    return result


# ──────────────────────────────────────────────────────────────
# CLI エントリーポイント
# ──────────────────────────────────────────────────────────────

def main() -> None:
    """CLI エントリーポイント。"""
    # ローカル実行時は .env から環境変数を読み込む（Cloud Run では不要）
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    parser = argparse.ArgumentParser(
        description="週次パッチ統合ランナー（URLチェック + JustWatch検索）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
バッジ方式（スケジューリング）:
  各投稿は post_id %% 4 でバッチ番号(0-3)に固定割り当て。
  毎週月曜に実行し、その週に対応するバッチを処理する。

  第1週 → batch 0
  第2週 → batch 1
  第3週 → batch 2
  第4週 → batch 3
        """,
    )
    parser.add_argument(
        "--batch",
        type=int,
        choices=range(BATCH_COUNT),
        default=None,
        help="バッチ番号(0-3)。省略時は今日の日付から自動判定",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"最大処理件数（デフォルト: {DEFAULT_BATCH_SIZE}）。--force 時は無視",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="直近更新チェックをスキップして強制処理",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="対象の確認のみ（更新なし）",
    )
    parser.add_argument(
        "--slug",
        type=str,
        default=None,
        help="特定の slug のみ処理",
    )
    args = parser.parse_args()

    result = run(
        batch=args.batch,
        limit=args.limit,
        dry_run=args.dry_run,
        force=args.force,
        slug=args.slug,
    )
    import json
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
