"""Cloud Run HTTP エントリーポイント。

HTTP POST リクエストを受け取り、VOD配信状況チェックを実行する。
認証は Cloud Run の IAM (Bearer トークン) で管理する。

Response:
    {"processed": N, "skipped": N, "errors": N}
"""

import logging
import sys

from flask import Flask, jsonify, request

from checker import run

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
        slug  (str) : 指定した slug の投稿のみ処理する
        force (bool): updated_at に関わらず全件処理する
    """
    body = request.get_json(silent=True) or {}
    slug = body.get("slug")
    force = bool(body.get("force", False))
    result = run(force=force, slug=slug)
    return jsonify(result)


@app.route("/health", methods=["GET"])
def health():
    """ヘルスチェックエンドポイント。"""
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
