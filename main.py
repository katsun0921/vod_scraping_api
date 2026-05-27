"""Cloud Run HTTP エントリーポイント。

HTTP POST リクエストを受け取り、VOD配信状況チェックを実行する。
認証は Cloud Run の IAM (Bearer トークン) で管理する。

エンドポイント:
    POST /           : 通常スクレイピング（checker.py）
    POST /justwatch  : JustWatch 月次バッチ（justwatch_batch.py）
    GET  /health     : ヘルスチェック

Response:
    {"processed": N, "skipped": N, "errors": N}
"""

import logging
import sys

from flask import Flask, jsonify, request

from checker import run
from justwatch_batch import run as justwatch_run

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


@app.route("/health", methods=["GET"])
def health():
    """ヘルスチェックエンドポイント。"""
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
