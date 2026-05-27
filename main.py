"""Cloud Run HTTP エントリーポイント。

HTTP POST リクエストを受け取り、VOD配信状況チェックを実行する。
認証は Cloud Run の IAM (Bearer トークン) で管理する。

エンドポイント:
    POST /weekly-patch : 週次パッチ統合ランナー（URLチェック + JustWatch検索）
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
"""

import logging
import sys

from flask import Flask, jsonify, request

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
        limit   (int)     : 最大処理件数（デフォルト: 100）。force=true 時は無視。
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

    limit = body.get("limit", DEFAULT_BATCH_SIZE)
    try:
        limit = int(limit)
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


@app.route("/health", methods=["GET"])
def health():
    """ヘルスチェックエンドポイント。"""
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
