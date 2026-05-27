"""Cloud Run HTTP エントリーポイント。

HTTP POST リクエストを受け取り、VOD配信状況チェックを実行する。
認証は Cloud Run の IAM (Bearer トークン) で管理する。

エンドポイント:
    POST /               : 通常スクレイピング（checker.py）
    POST /justwatch      : JustWatch 月次バッチ（justwatch_batch.py）
    POST /monthly-patch  : 月次パッチ統合ランナー（monthly_patch.py）
    GET  /health         : ヘルスチェック

Response:
    POST /               → {"processed": N, "skipped": N, "errors": N}
    POST /justwatch      → {"registered": N, "unavailable": N, ...}
    POST /monthly-patch  → {
        "batch": 0-3, "cycle": "YYYY-MM",
        "badge_distribution": {...},
        "posts": {...}, "services": {...}, "budget": {...}
    }
"""

import logging
import sys

from flask import Flask, jsonify, request

from checker import run
from justwatch_batch import run as justwatch_run
from monthly_patch import run as monthly_patch_run

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)

app = Flask(__name__)


@app.route("/", methods=["POST"])
def index():
    """VOD配信状況チェックを実行するエンドポイント。

    リクエストボディ（JSON）で以下のオプションを指定できる：
        slug  (str)  : 指定した slug の投稿のみ処理する
        force (bool) : updated_at に関わらず全件処理する
        limit (int)  : 最大処理件数
    """
    body = request.get_json(silent=True) or {}
    slug = body.get("slug")
    force = bool(body.get("force", False))
    limit = body.get("limit")
    if limit is not None:
        try:
            limit = int(limit)
        except (ValueError, TypeError):
            limit = None
    result = run(force=force, slug=slug, limit=limit)
    return jsonify(result)


@app.route("/justwatch", methods=["POST"])
def justwatch():
    """JustWatch 月次バッチを実行するエンドポイント。

    scraping_url が未設定のサービスに対して JustWatch API で URL を検索し、
    見つかれば登録、見つからなければ status=unavailable を書き込む。

    リクエストボディ（JSON）で以下のオプションを指定できる：
        slug    (str)  : 指定した slug の投稿のみ処理する
        dry_run (bool) : 対象確認のみ（更新なし）
        limit   (int)  : 最大処理件数
    """
    body = request.get_json(silent=True) or {}
    slug = body.get("slug")
    dry_run = bool(body.get("dry_run", False))
    limit = body.get("limit")
    if limit is not None:
        try:
            limit = int(limit)
        except (ValueError, TypeError):
            limit = None
    result = justwatch_run(dry_run=dry_run, slug=slug, limit=limit)
    return jsonify(result)


@app.route("/monthly-patch", methods=["POST"])
def monthly_patch():
    """月次パッチ統合ランナーを実行するエンドポイント。

    URLあり投稿は既存チェッカーで確認し、URLなし投稿は JustWatch API で検索する。
    バッチ番号を省略した場合、今日の日付から第1〜4週を自動判定して対応バッチを実行する。

    スケジューリングバッジ:
        各投稿は post_id % 4 でバッチ番号(0-3)に固定割り当て。
        Cloud Scheduler で毎週月曜に実行し、その週のバッチを自動処理する。

    リクエストボディ（JSON）:
        batch   (int)  : バッチ番号 0-3。省略時は日付から自動判定。
        limit   (int)  : 最大処理件数（デフォルト: 100）。
        dry_run (bool) : 対象確認のみ（更新なし）。
        slug    (str)  : 特定 slug のみ処理する。

    レスポンス:
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
    body = request.get_json(silent=True) or {}
    batch = body.get("batch")
    if batch is not None:
        try:
            batch = int(batch)
            if batch not in range(4):
                return jsonify({"error": "batch must be 0-3"}), 400
        except (ValueError, TypeError):
            return jsonify({"error": "batch must be an integer 0-3"}), 400

    limit = body.get("limit", 100)
    try:
        limit = int(limit)
    except (ValueError, TypeError):
        limit = 100

    dry_run = bool(body.get("dry_run", False))
    slug = body.get("slug")

    result = monthly_patch_run(batch=batch, limit=limit, dry_run=dry_run, slug=slug)
    return jsonify(result)


@app.route("/health", methods=["GET"])
def health():
    """ヘルスチェックエンドポイント。"""
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
