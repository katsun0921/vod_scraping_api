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
"""

import argparse
import logging
import sys
import time
from datetime import datetime
from typing import Optional

from utils.justwatch import search_urls
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


def run(dry_run: bool = False, slug: Optional[str] = None) -> dict:
    """JustWatch 月次バッチを実行する。

    1投稿につき JustWatch 検索 1回 + WordPress GET 1回 + PATCH 1回で完結する。

    Args:
        dry_run: True の場合、対象の確認のみ行い更新しない。
        slug   : 指定した場合、該当 slug の投稿のみ処理する。

    Returns:
        {
            "registered": int,   # URL を新規登録したサービス数（延べ）
            "unavailable": int,  # unavailable を書き込んだサービス数（延べ）
            "skipped": int,      # スキップした投稿数
            "errors": int,       # エラーが発生した投稿数
        }
    """
    posts = get_posts_missing_url(slug=slug)
    registered = 0
    unavailable = 0
    skipped = 0
    errors = 0

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

        # JustWatch で検索（title → slug の順で試す）
        try:
            found_urls = search_urls(title=post_title, slug=post_slug)
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

        # 1投稿につき 1回の PATCH で完結
        try:
            patch_multi_service_fields(post_id, service_fields)
        except Exception as e:
            logger.error("ERROR [%s] PATCH 失敗: %s", post_slug, e)
            errors += 1
            # カウント済みの registered / unavailable を差し引く
            registered -= sum(1 for f in service_fields.values() if "scraping_url" in f)
            unavailable -= sum(1 for f in service_fields.values() if "status" in f)

        time.sleep(_JW_WAIT_BETWEEN_POSTS)

    result = {
        "registered": registered,
        "unavailable": unavailable,
        "skipped": skipped,
        "errors": errors,
    }
    logger.info("JustWatch バッチ完了: %s", result)
    return result


def main() -> None:
    """CLI エントリーポイント。"""
    parser = argparse.ArgumentParser(description="JustWatch 月次バッチ")
    parser.add_argument("--dry-run", action="store_true", help="対象の確認のみ（更新なし）")
    parser.add_argument("--slug", type=str, help="特定の slug のみ処理")
    args = parser.parse_args()

    result = run(dry_run=args.dry_run, slug=args.slug)
    print(result)


if __name__ == "__main__":
    main()
