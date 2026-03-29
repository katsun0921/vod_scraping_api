"""VOD 配信状況チェッカー メイン処理。

Google Sheets の VODs シートを読み込み、各URLの配信状況を確認して書き戻す。
CLI からも Cloud Run のエントリーポイントからも呼び出せる。

Usage:
    python checker.py              # 通常実行（1ヶ月以内更新済みはスキップ）
    python checker.py --dry-run    # 対象行の確認のみ（更新なし）
    python checker.py --force      # updated_at に関わらず全行処理
    python checker.py --slug john-wick  # 特定の slug のみ処理
"""

import argparse
import logging
import sys
from datetime import datetime, timedelta
from typing import Optional

from checkers.amazon import AmazonChecker
from checkers.hulu import HuluChecker
from checkers.netflix import NetflixChecker
from checkers.unext import UnextChecker
from utils.rate_limit import RateLimiter
from utils.sheets import get_rows, update_row

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# service 列の名前からチェッカーを引くキーワードマップ
_SERVICE_KEYWORDS: list[tuple[str, type]] = [
    ("netflix", NetflixChecker),
    ("amazon",  AmazonChecker),
    ("hulu",    HuluChecker),
    ("u-next",  UnextChecker),
    ("unext",   UnextChecker),
]


def _find_checker(service_name: str) -> Optional[type]:
    """service 列の値からチェッカークラスを返す。"""
    name = service_name.lower()
    for keyword, cls in _SERVICE_KEYWORDS:
        if keyword in name:
            return cls
    return None

MAX_CONSECUTIVE_ERRORS = 3
SKIP_WITHIN_DAYS = 30


def _should_skip(row: dict, force: bool) -> Optional[str]:
    """行をスキップすべき理由を返す。スキップ不要なら None を返す。

    Args:
        row: VODs シートの1行データ（url が設定済みであること前提）。
        force: True の場合は updated_at チェックをスキップする。

    Returns:
        スキップ理由の文字列、またはスキップ不要な場合は None。
    """
    if not force:
        updated_at = str(row.get("updated_at", "")).strip()
        if updated_at:
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
    """VODs シートの配信状況チェックを実行する。

    Args:
        dry_run: True の場合、対象行の確認のみ行い更新しない。
        force: True の場合、updated_at に関わらず全行処理する。
        slug: 指定した場合、該当 slug の行のみ処理する。

    Returns:
        {"processed": int, "skipped": int, "errors": int} の辞書。
    """
    rows = get_rows()
    processed = 0
    skipped = 0
    errors = 0
    consecutive_errors = 0
    rate_limiter = RateLimiter()
    current_vod: Optional[str] = None

    for row in rows:
        row_num = row["_row_num"]

        # slug フィルタ
        if slug and row.get("slug") != slug:
            skipped += 1
            continue

        # スキップ判定
        reason = _should_skip(row, force)
        if reason:
            logger.info("SKIP  [%s] %s", row.get("slug"), reason)
            skipped += 1
            continue

        service_name = str(row.get("service", ""))
        checker_class = _find_checker(service_name)
        if not checker_class:
            logger.info("SKIP  [%s] 未対応VODサービス: %s", row.get("slug"), service_name)
            skipped += 1
            continue

        # VODサービス切り替え時の追加待機
        if current_vod is not None and current_vod != service_name:
            logger.info("INFO  VODサービス切り替え (%s → %s)、10秒待機", current_vod, service_name)
            rate_limiter.wait_service_switch()
        current_vod = service_name

        logger.info("CHECK [%s] %s %s", row.get("slug"), service_name, row.get("url"))

        if dry_run:
            processed += 1
            continue

        try:
            checker = checker_class()
            result = checker.check(row["url"])
            rate_limiter.wait()

            update_row(
                row_index=row_num,
                status=result["status"],
                price=result.get("price"),
                updated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
            logger.info(
                "UPDATE [%s] status=%s price=%s",
                row.get("slug"),
                result["status"],
                result.get("price"),
            )
            processed += 1
            consecutive_errors = 0  # 成功でリセット

        except RuntimeError as e:
            logger.error("ERROR [%s] %s", row.get("slug"), e)
            errors += 1
            consecutive_errors += 1
            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                logger.error("連続エラー %d 回に達したため処理を中断します", MAX_CONSECUTIVE_ERRORS)
                break

        except Exception as e:
            logger.error("ERROR [%s] 予期しないエラー: %s", row.get("slug"), e)
            errors += 1
            consecutive_errors += 1
            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                logger.error("連続エラー %d 回に達したため処理を中断します", MAX_CONSECUTIVE_ERRORS)
                break

    return {"processed": processed, "skipped": skipped, "errors": errors}


def main() -> None:
    """CLIエントリーポイント。"""
    parser = argparse.ArgumentParser(description="VOD配信状況チェッカー")
    parser.add_argument("--dry-run", action="store_true", help="対象行の確認のみ（更新なし）")
    parser.add_argument("--force", action="store_true", help="updated_at に関わらず全行処理")
    parser.add_argument("--slug", type=str, help="特定のslugのみ処理")
    args = parser.parse_args()

    result = run(dry_run=args.dry_run, force=args.force, slug=args.slug)
    print(result)


if __name__ == "__main__":
    main()
