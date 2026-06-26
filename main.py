"""Cloud Run HTTP エントリーポイント。

HTTP POST リクエストを受け取り、VOD配信状況チェックを実行する。
認証は Cloud Run の IAM (Bearer トークン) で管理する。

エンドポイント:
    POST /weekly-patch  : 週次パッチ統合ランナー（URLチェック + JustWatch検索）
    POST /new-titles    : 隔週新着タイトル発見ジョブ（JustWatch 経由）
    GET  /health        : ヘルスチェック

レスポンス（POST /weekly-patch）:
    {
        "batch": 0,
        "cycle": "2026-05",
        "badge_distribution": {"batch0": 120, "batch1": 115, "batch2": 118, "batch3": 122},
        "posts": {"total": 100, "processed": 94, "skipped": 3, "errors": 3},
        "services": {
            "url_checked": 280,
            "jw_searched": 94,
            "urls_registered": 18,
            "status_updated": 262
        },
        "budget": {
            "wp_api_calls": 820,
            "jw_api_calls": 94,
            "scraping_calls": 215,
            "playwright_calls": 65,
            "estimated_minutes": 38.5
        }
    }

レスポンス（POST /new-titles）:
    {
        "executed_at": "2026-06-26T03:00:00",
        "services": ["netflix", "unext", "amazon_prime_video"],
        "days_back": 14,
        "results": {
            "total": 42,
            "by_service": {"netflix": 18, "unext": 15, "amazon_prime_video": 9},
            "titles": [
                {
                    "title": "タイトル名",
                    "original_title": "Original Title",
                    "jw_id": "12345",
                    "genres": ["Action", "Thriller"],
                    "services": ["netflix"],
                    "available_from": "2026-06-20",
                    "offers": [{"service": "netflix", "url": "https://...", "type": "FLATRATE"}]
                }
            ]
        },
        "dry_run": false
    }
"""

import logging
import sys

from flask import Flask, jsonify, request

from new_titles_job import DEFAULT_DAYS_BACK, DEFAULT_LIMIT, DEFAULT_SERVICES
from new_titles_job import run as new_titles_run
from weekly_patch import BATCH_COUNT, DEFAULT_BATCH_SIZE
from weekly_patch import run as weekly_patch_run

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)

app = Flask(__name__)


@app.route("/weekly-patch", methods=["POST"])
def weekly_patch():
    """週次パッチ統合ランナーを実行するエンドポイント。

    URLあり投稿は既存チェッカーで確認し、URLなし投稿は JustWatch API で検索する。
    batch を省略すると今日の日付から第1〜4週を自動判定して対応バッチを実行する。

    スケジューリングバッジ:
        各投稿は post_id % 4 でバッチ番号(0-3)に固定割り当て。
        Cloud Scheduler で毎週月曜に実行し、その週のバッチを自動処理する。

    リクエストボディ（JSON）:
        batch   (int 0-3) : バッチ番号。省略時は日付から自動判定。
        limit   (int)     : 最大処理件数。省略時はバッジ内全件（投稿数増加に自動追従）。
        force   (bool)    : 直近更新チェックをスキップして強制処理。
        dry_run (bool)    : 対象確認のみ（更新なし）。
        slug    (str)     : 特定 slug のみ処理する。
    """
    body = request.get_json(silent=True) or {}

    batch = body.get("batch")
    if batch is not None:
        try:
            batch = int(batch)
            if batch not in range(BATCH_COUNT):
                return jsonify({"error": f"batch must be 0-{BATCH_COUNT - 1}"}), 400
        except (ValueError, TypeError):
            return jsonify({"error": f"batch must be an integer 0-{BATCH_COUNT - 1}"}), 400

    # limit が未指定 → None（バッジ内全件処理）
    raw_limit = body.get("limit", DEFAULT_BATCH_SIZE)
    if raw_limit is None:
        limit = None
    else:
        try:
            limit = int(raw_limit)
        except (ValueError, TypeError):
            limit = DEFAULT_BATCH_SIZE

    force = bool(body.get("force", False))
    dry_run = bool(body.get("dry_run", False))
    slug = body.get("slug")

    result = weekly_patch_run(
        batch=batch,
        limit=limit,
        dry_run=dry_run,
        force=force,
        slug=slug,
    )
    return jsonify(result)


@app.route("/new-titles", methods=["POST"])
def new_titles():
    """隔週新着タイトル発見ジョブを実行するエンドポイント。

    JustWatch GraphQL から指定サービスの新着タイトル（直近 days_back 日以内）を取得し
    Slack に通知する。Cloud Scheduler で隔週月曜に自動実行する想定。

    リクエストボディ（JSON）:
        services  (list[str]) : 対象サービスキー。省略時は ["netflix", "unext", "amazon_prime_video"]。
                                有効値: "netflix" / "unext" / "amazon_prime_video"
        days      (int)       : 直近何日を新着とみなすか。省略時は 14。
        limit     (int)       : 最大取得件数。省略時は 100。
        dry_run   (bool)      : Slack 通知しない（確認モード）。省略時は false。
    """
    body = request.get_json(silent=True) or {}

    raw_services = body.get("services")
    if raw_services is not None:
        if not isinstance(raw_services, list):
            return jsonify({"error": "services must be a list"}), 400
        valid = set(DEFAULT_SERVICES)
        invalid = [s for s in raw_services if s not in valid]
        if invalid:
            return jsonify({"error": f"unknown services: {invalid}. valid: {sorted(valid)}"}), 400
        services = raw_services if raw_services else None
    else:
        services = None

    try:
        days_back = int(body.get("days", DEFAULT_DAYS_BACK))
        if days_back < 1 or days_back > 365:
            return jsonify({"error": "days must be between 1 and 365"}), 400
    except (ValueError, TypeError):
        return jsonify({"error": "days must be an integer"}), 400

    try:
        limit = int(body.get("limit", DEFAULT_LIMIT))
        if limit < 1 or limit > 500:
            return jsonify({"error": "limit must be between 1 and 500"}), 400
    except (ValueError, TypeError):
        return jsonify({"error": "limit must be an integer"}), 400

    dry_run = bool(body.get("dry_run", False))

    result = new_titles_run(
        services=services,
        days_back=days_back,
        limit=limit,
        dry_run=dry_run,
    )
    return jsonify(result)


@app.route("/health", methods=["GET"])
def health():
    """ヘルスチェックエンドポイント。"""
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
