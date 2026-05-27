"""JustWatch 月次バッチ。

scraping_url が未設定のサービスに対して JustWatch 非公式 API で URL を検索し、
- URL が見つかれば scraping_url を登録する
- URL が見つからなければ status=unavailable / updated_at=現在日時 を書き込む

全サービスの更新を 1投稿あたり 1回の GET + 1回の PATCH にまとめて実行する。

実行後は通常の checker.py が scraping_url ありのサービスとして扱う。

Usage:
    python justwatch_batch.py              # 全投稿・全サービスを処理
    python justwatch_batch.py --dry-run    # 対象の確認のみ（更新なし）
    python justwatch_batch.py --slug john-wick  # 特定 slug のみ
    python justwatch_batch.py --limit 10   # 最大 10 件のみ処理
"""

import argparse
import logging
import sys
import time
from datetime import date, datetime
from typing import Optional

from utils.justwatch import search_urls
from utils.slack import (
    notify_justwatch_post_result,
    notify_justwatch_start,
    notify_justwatch_summary,
)
from utils.wordpress import (
    SERVICES,
    get_posts_missing_url,
    patch_multi_service_fields,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# JustWatch API へのリクエスト間隔（秒）
_JW_WAIT_BETWEEN_POSTS = 3.0

# この年数以上経過した作品で全サービス URL 未発見の場合にスクレイピングを停止する
_AUTO_DISABLE_YEARS = 10


def run(
    dry_run: bool = False,
    slug: Optional[str] = None,
    limit: Optional[int] = None,
) -> dict:
    """JustWatch 月次バッチを実行する。

    1投稿につき JustWatch 検索 1回 + WordPress GET 1回 + PATCH 1回で完結する。

    Args:
        dry_run: True の場合、対象の確認のみ行い更新しない。
        slug   : 指定した場合、該当 slug の投稿のみ処理する。
        limit  : 指定した場合、最大 limit 件のみ処理する。

    Returns:
        {
            "registered": int,   # URL を新規登録したサービス数（延べ）
            "unavailable": int,  # unavailable を書き込んだサービス数（延べ）
            "disabled": int,     # scraping_disabled=true にした投稿数
            "skipped": int,      # スキップした投稿数
            "errors": int,       # エラーが発生した投稿数
        }
    """
    posts = get_posts_missing_url(slug=slug, limit=limit)
    registered = 0
    unavailable = 0
    disabled = 0
    skipped = 0
    errors = 0
    current_year = date.today().year

    if not dry_run:
        notify_justwatch_start(total=len(posts), limit=limit)

    for post in posts:
        post_id = post["id"]
        post_slug = post.get("slug", "")
        post_title = (post.get("title") or {}).get("rendered") or post_slug

        # scraping_url が空のサービスを抽出
        acf = post.get("acf") or {}
        missing_services = [
            svc for svc in SERVICES
            if not (acf.get(svc) or {}).get("scraping_url")
        ]
        if not missing_services:
            skipped += 1
            continue

        logger.info(
            "SEARCH [%s] title=%r missing_services=%s",
            post_slug, post_title, missing_services,
        )

        if dry_run:
            skipped += 1
            continue

        # lang フィールドに応じて country / language を切り替え
        lang = acf.get("lang") or "ja"
        if lang == "en":
            jw_country, jw_language = "US", "en"
        else:
            jw_country, jw_language = "JP", "ja"

        # JustWatch で検索（title → slug の順で試す）
        try:
            found_urls = search_urls(
                title=post_title, slug=post_slug,
                country=jw_country, language=jw_language,
            )
        except RuntimeError as e:
            logger.error("ERROR [%s] JustWatch 検索失敗: %s", post_slug, e)
            errors += 1
            time.sleep(_JW_WAIT_BETWEEN_POSTS)
            continue

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 全空サービスの更新内容を 1つの dict に集約（GET+PATCH は 1回）
        service_fields: dict[str, dict] = {}
        for service in missing_services:
            url = found_urls.get(service, "")
            if url:
                service_fields[service] = {"scraping_url": url}
                logger.info("REGISTERED [%s][%s] url=%s", post_slug, service, url)
                registered += 1
            else:
                service_fields[service] = {"status": "unavailable", "updated_at": now}
                logger.info(
                    "UNAVAILABLE [%s][%s] url not found → status=unavailable",
                    post_slug, service,
                )
                unavailable += 1

        # release_year から10年以上経過 かつ 全空サービスで URL 未発見 → scraping_disabled=true
        top_level_fields: dict | None = None
        post_auto_disabled = False
        if not any("scraping_url" in f for f in service_fields.values()):
            try:
                release_year = int(acf.get("release_year") or 0)
            except (ValueError, TypeError):
                release_year = 0
            if release_year and (current_year - release_year) >= _AUTO_DISABLE_YEARS:
                top_level_fields = {"scraping_disabled": True}
                post_auto_disabled = True
                logger.info(
                    "AUTO_DISABLE [%s] release_year=%d (%d年経過) → scraping_disabled=true",
                    post_slug, release_year, current_year - release_year,
                )

        # 1投稿につき 1回の PATCH で完結
        post_registered: dict[str, str] = {}
        post_unavailable: list[str] = []
        post_error = False
        try:
            patch_multi_service_fields(post_id, service_fields, top_level_fields=top_level_fields)
            # 通知用に登録・unavailable を分類
            for svc, fields in service_fields.items():
                if "scraping_url" in fields:
                    post_registered[svc] = fields["scraping_url"]
                else:
                    post_unavailable.append(svc)
            if post_auto_disabled:
                disabled += 1
        except Exception as e:
            logger.error("ERROR [%s] PATCH 失敗: %s", post_slug, e)
            errors += 1
            post_error = True
            post_auto_disabled = False
            # カウント済みの registered / unavailable を差し引く
            registered -= sum(1 for f in service_fields.values() if "scraping_url" in f)
            unavailable -= sum(1 for f in service_fields.values() if "status" in f)

        notify_justwatch_post_result(
            title=post_title,
            slug=post_slug,
            registered=post_registered,
            unavailable=post_unavailable,
            error=post_error,
            auto_disabled=post_auto_disabled,
        )

        time.sleep(_JW_WAIT_BETWEEN_POSTS)

    result = {
        "registered": registered,
        "unavailable": unavailable,
        "disabled": disabled,
        "skipped": skipped,
        "errors": errors,
    }
    logger.info("JustWatch バッチ完了: %s", result)
    if not dry_run:
        notify_justwatch_summary(result)
    return result


def main() -> None:
    """CLI エントリーポイント。"""
    # ローカル実行時は .env から環境変数を読み込む（Cloud Run では不要）
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    parser = argparse.ArgumentParser(description="JustWatch 月次バッチ")
    parser.add_argument("--dry-run", action="store_true", help="対象の確認のみ（更新なし）")
    parser.add_argument("--slug", type=str, help="特定の slug のみ処理")
    parser.add_argument("--limit", type=int, default=None, help="処理する最大件数")
    args = parser.parse_args()

    result = run(dry_run=args.dry_run, slug=args.slug, limit=args.limit)
    print(result)


if __name__ == "__main__":
    main()
