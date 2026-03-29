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

    リクエストボディは不要。全件チェックを実行する。
    """
    result = run()
    return jsonify(result)


@app.route("/health", methods=["GET"])
def health():
    """ヘルスチェックエンドポイント。"""
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
