"""Cloud Run HTTP エントリーポイント。

HTTP POST リクエストを受け取り、VOD配信状況チェックを実行する。
認証は Cloud Run の IAM (Bearer トークン) で管理する。

エンドポイント:
    POST /weekly-patch       : 週次パッチ統合ランナー（URLチェック + JustWatch検索）
    POST /theater-check      : 劇場公開（上映中フラグ）の週次チェック
    POST /youtube-free-check : YouTube 無料配信の日次チェック
    GET  /health             : ヘルスチェック

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

from theater_patch import run as theater_check_run
from weekly_patch import BATCH_COUNT, DEFAULT_BATCH_SIZE
from weekly_patch import run as weekly_patch_run
from youtube_free_patch import run as youtube_free_check_run

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
    batch を省略すると今日の日付から経過週数を自動判定して対応バッチを実行する。

    スケジューリングバッジ:
        各投稿は post_id % BATCH_COUNT でバッチ番号(0-7)に固定割り当て。
        Cloud Scheduler で毎週月曜に実行し、その週のバッチを自動処理する。
        BATCH_COUNT 週（=2ヶ月）で全バッチを一巡する。

    リクエストボディ（JSON）:
        batch   (int 0-7) : バッチ番号。省略時は日付から自動判定。
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


@app.route("/theater-check", methods=["POST"])
def theater_check():
    """劇場公開（上映中フラグ）の週次チェックを実行するエンドポイント。

    ACF `cinema_info_filed.is_cinema_showing` が ON の記事について、劇場URLの
    生存と劇場公開日からの経過週数を確認し、上映終了と判定した記事のフラグを
    自動で OFF にする。判定ロジックの詳細は theater_patch.py を参照。

    リクエストボディ（JSON）:
        weeks   (int)  : 上映終了とみなす経過週数。省略時は環境変数 → 既定8週。
        dry_run (bool) : 判定のみ（更新・Slack通知なし）。
        slug    (str)  : 特定 slug のみ処理する。
        post_id (int)  : 特定 post_id のみ処理する（slug より優先）。
        limit   (int)  : 最大処理件数。

    Returns:
        判定結果の JSON。対象一覧の取得に失敗した場合は 502 で `error` を返す。
    """
    body = request.get_json(silent=True) or {}

    def _optional_int(key: str):
        raw = body.get(key)
        if raw is None:
            return None
        try:
            return int(raw)
        except (ValueError, TypeError):
            return None

    result = theater_check_run(
        weeks=_optional_int("weeks"),
        dry_run=bool(body.get("dry_run", False)),
        slug=body.get("slug"),
        post_id=_optional_int("post_id"),
        limit=_optional_int("limit"),
    )
    if result.get("error"):
        return jsonify(result), 502
    return jsonify(result)

@app.route("/youtube-free-check", methods=["POST"])
def youtube_free_check():
    """YouTube 無料配信の日次チェックを実行するエンドポイント。

    `youtube.scraping_url` が登録された記事について、いま無料で観られるかを
    確認し ACF を更新する。無料公開は数日〜数週間で終わるため、週次パッチの
    バッチ巡回（2ヶ月に1周）とは別に日次で全件を見る。有料・配信終了だった
    作品が新たに無料公開されるケースも拾う。

    リクエストボディ（JSON）:
        dry_run (bool) : 判定のみ（更新・Slack通知なし）。
        slug    (str)  : 特定 slug のみ処理する。
        post_id (int)  : 特定 post_id のみ処理する（slug より優先）。
        limit   (int)  : 最大処理件数。

    Returns:
        判定結果の JSON。対象記事の取得に失敗した場合は 502 で `error` を返す。
    """
    body = request.get_json(silent=True) or {}

    def _optional_int(key: str):
        raw = body.get(key)
        if raw is None:
            return None
        try:
            return int(raw)
        except (ValueError, TypeError):
            return None

    result = youtube_free_check_run(
        dry_run=bool(body.get("dry_run", False)),
        slug=body.get("slug"),
        post_id=_optional_int("post_id"),
        limit=_optional_int("limit"),
    )
    if result.get("error"):
        return jsonify(result), 502
    return jsonify(result)


@app.route("/health", methods=["GET"])
def health():
    """ヘルスチェックエンドポイント。"""
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
