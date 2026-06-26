"""新着タイトル発見ジョブ。

隔週（2週間ごと）に JustWatch から各 VOD サービスの新着タイトルを取得し
Slack に通知するジョブ。

対象サービス（JP）:
    - Netflix
    - U-NEXT
    - Amazon Prime Video

Usage:
    python new_titles_job.py                      # 直近14日の新着を取得
    python new_titles_job.py --days 30            # 直近30日
    python new_titles_job.py --services netflix   # Netflix のみ
    python new_titles_job.py --dry-run            # Slack 通知しない
    python new_titles_job.py --limit 100          # 最大100件
"""

import argparse
import json
import logging
import sys
from datetime import date, datetime
from typing import Optional

from utils.new_titles import fetch_new_titles, group_by_service, to_report
from utils.slack import _post as slack_post

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# デフォルト対象サービス
DEFAULT_SERVICES = ["netflix", "unext", "amazon_prime_video"]

# デフォルト日数（隔週運用: 14日）
DEFAULT_DAYS_BACK = 14

# デフォルト最大件数
DEFAULT_LIMIT = 100

# Slack 通知用サービス表示名
_SERVICE_LABELS: dict[str, str] = {
    "netflix":            "Netflix",
    "unext":              "U-NEXT",
    "amazon_prime_video": "Amazon Prime Video",
}

# JustWatch サービスごとの国・言語設定
_SERVICE_LOCALE: dict[str, dict[str, str]] = {
    "netflix":            {"country": "JP", "language": "ja"},
    "unext":              {"country": "JP", "language": "ja"},
    "amazon_prime_video": {"country": "JP", "language": "ja"},
}


def run(
    services: Optional[list[str]] = None,
    days_back: int = DEFAULT_DAYS_BACK,
    limit: int = DEFAULT_LIMIT,
    dry_run: bool = False,
) -> dict:
    """新着タイトル発見ジョブを実行する。

    Args:
        services : 対象サービスキーのリスト。None の場合は DEFAULT_SERVICES を使用。
        days_back: 何日前までを「新着」とするか。
        limit    : 1回の実行で取得する最大タイトル数。
        dry_run  : True の場合、Slack 通知を送信しない。

    Returns:
        {
            "executed_at": str,
            "services": list[str],
            "days_back": int,
            "results": {
                "total": int,
                "by_service": {service: count},
                "titles": [...]
            },
            "dry_run": bool
        }
    """
    target_services = services or DEFAULT_SERVICES
    executed_at = datetime.now().isoformat()

    logger.info(
        "新着タイトルジョブ開始: services=%s days_back=%d limit=%d dry_run=%s",
        target_services, days_back, limit, dry_run,
    )

    try:
        titles = fetch_new_titles(
            services=target_services,
            country="JP",
            language="ja",
            days_back=days_back,
            limit=limit,
        )
    except RuntimeError as e:
        logger.error("新着タイトル取得失敗: %s", e)
        return {
            "executed_at": executed_at,
            "services": target_services,
            "days_back": days_back,
            "error": str(e),
        }

    report = to_report(titles)
    logger.info(
        "取得完了: %d 件 by_service=%s",
        report["total"], report["by_service"],
    )

    if not dry_run:
        _notify_slack(report, days_back, target_services)
    else:
        logger.info("DRY-RUN: Slack 通知をスキップ")

    return {
        "executed_at": executed_at,
        "services": target_services,
        "days_back": days_back,
        "results": report,
        "dry_run": dry_run,
    }


def _notify_slack(report: dict, days_back: int, services: list[str]) -> None:
    """新着タイトルのサマリーを Slack に通知する。

    通知内容:
        - 期間と対象サービス
        - サービスごとの件数
        - 上位10件のタイトルリスト（サービス別）

    Args:
        report  : to_report() の戻り値。
        days_back: 対象期間（日数）。
        services: 対象サービスキーリスト。
    """
    total = report.get("total", 0)
    by_service = report.get("by_service", {})
    titles = report.get("titles", [])

    if total == 0:
        logger.info("新着タイトルが0件のため Slack 通知をスキップ")
        return

    service_labels = " / ".join(_SERVICE_LABELS.get(s, s) for s in services)
    lines = [
        f":clapper: *新着タイトル ({days_back}日間)*  |  {service_labels}",
        "",
    ]

    # サービスごとの件数
    for svc in services:
        count = by_service.get(svc, 0)
        label = _SERVICE_LABELS.get(svc, svc)
        lines.append(f"  • {label}: *{count}* 件")

    lines.append(f"\n合計: *{total}* 件")

    # サービス別に上位5件ずつ表示
    by_svc = group_by_service([
        type("T", (), {
            "__dict__": dict(
                title=t["title"],
                services=t["services"],
                earliest_available_from=lambda: t.get("available_from"),
                offers=t["offers"],
                get_offer_url=lambda s: next(
                    (o["url"] for o in t["offers"] if o["service"] == s), None
                ),
            )
        })()
        for t in titles
    ])

    for svc in services:
        svc_titles = [t for t in titles if svc in t.get("services", [])]
        if not svc_titles:
            continue
        label = _SERVICE_LABELS.get(svc, svc)
        lines.append(f"\n*{label}* 新着（上位5件）:")
        for t in svc_titles[:5]:
            url = next(
                (o["url"] for o in t.get("offers", []) if o.get("service") == svc),
                "",
            )
            avail = t.get("available_from") or ""
            avail_str = f"（{avail}〜）" if avail else ""
            title_str = t.get("title") or ""
            genres_str = ", ".join(t.get("genres", []))
            line = f"  • {title_str}"
            if genres_str:
                line += f"  [{genres_str}]"
            if avail_str:
                line += f"  {avail_str}"
            if url:
                line += f"\n    {url}"
            lines.append(line)

    slack_post({"text": "\n".join(lines)})
    logger.info("Slack 通知送信完了: total=%d", total)


def main() -> None:
    """CLI エントリーポイント。"""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    parser = argparse.ArgumentParser(
        description="新着タイトル発見ジョブ（隔週実行）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
対象サービス:
  netflix          Netflix
  unext            U-NEXT
  amazon_prime_video  Amazon Prime Video

Cloud Scheduler 推奨設定（隔週月曜 03:00 JST）:
  POST /new-titles
        """,
    )
    parser.add_argument(
        "--services",
        nargs="+",
        choices=DEFAULT_SERVICES,
        default=None,
        help=f"対象サービスキー（デフォルト: {DEFAULT_SERVICES}）",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DAYS_BACK,
        help=f"直近何日を新着とみなすか（デフォルト: {DEFAULT_DAYS_BACK}）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"最大取得件数（デフォルト: {DEFAULT_LIMIT}）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Slack 通知せず結果を確認のみ",
    )
    args = parser.parse_args()

    result = run(
        services=args.services,
        days_back=args.days,
        limit=args.limit,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
