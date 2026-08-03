"""WordPress CPT への接続確認スクリプト（セットアップ検証用・パイプライン本体からは呼ばれない）。

GitHub Actions の `vod-wp-connection-test.yml` から実行し、`vod_publish` が実際に投稿する前に
以下を切り分ける:

1. 環境変数が揃っているか
2. CPT が REST API に露出しているか（GET）
3. Application Password で投稿できるか（POST → 後始末で削除）

`news_bot/wp_client.py` と同じ環境変数・同じ認証方式を使うため、ここが通れば
`vod_publish` の WP 投稿部分も通る。認証情報は一切ログ出力しない。

実行:
    python -m news_bot.check_wp_connection
"""

import logging
import os
import sys

import requests

from news_bot import wp_client

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

_TIMEOUT = 30

_REQUIRED_ENV = ["VOD_NEWS_WP_API_BASE", "WP_USER", "WP_APP_PASSWORD"]


def _check_env() -> bool:
    """必須環境変数の有無を確認する（値そのものは出力しない）。"""
    logger.info("=== 1. 環境変数 ===")
    missing = [name for name in _REQUIRED_ENV if not os.environ.get(name)]
    for name in _REQUIRED_ENV:
        logger.info("  %s: %s", name, "OK" if os.environ.get(name) else "未設定")

    cpt_slug = os.environ.get("VOD_NEWS_CPT_SLUG", "vod_news")
    status = os.environ.get("VOD_NEWS_WP_STATUS", "draft")
    logger.info("  VOD_NEWS_CPT_SLUG: %s%s", cpt_slug, "（既定値）" if not os.environ.get("VOD_NEWS_CPT_SLUG") else "")
    logger.info("  VOD_NEWS_WP_STATUS: %s%s", status, "（既定値）" if not os.environ.get("VOD_NEWS_WP_STATUS") else "")

    if missing:
        logger.error("必須の環境変数が未設定: %s", ", ".join(missing))
        return False

    if cpt_slug == "vod_news":
        logger.warning(
            "VOD_NEWS_CPT_SLUG が既定値 'vod_news' のままです。"
            "WordPress 側の CPT が 'vod_release' の場合は投稿先が一致せず失敗します。"
        )
    return True


def _check_get(cpt_slug: str) -> bool:
    """CPT が REST API に露出しているかを GET で確認する（認証不要）。"""
    logger.info("=== 2. CPT の REST API 露出確認（GET）===")
    url = f"{os.environ['VOD_NEWS_WP_API_BASE'].rstrip('/')}/{cpt_slug}"
    try:
        # UAは wp_client と揃える（既定の python-requests/* はWAFに403で弾かれる）
        resp = requests.get(
            url,
            params={"per_page": 1},
            headers={"User-Agent": wp_client.USER_AGENT},
            timeout=_TIMEOUT,
        )
    except requests.RequestException as exc:
        logger.error("  接続失敗: %s", exc)
        return False

    logger.info("  GET %s -> %s", url, resp.status_code)
    if resp.status_code == 404:
        logger.error("  404: CPT が未登録か show_in_rest が false。ACF管理画面での同期を確認する")
        return False
    if resp.status_code == 403:
        logger.error("  403: WAF によるブロックの可能性。User-Agent 制限を確認する")
        return False
    if resp.status_code != 200:
        logger.error("  想定外のステータス: %s", resp.text[:200])
        return False

    logger.info("  OK（既存投稿 %d 件）", len(resp.json()))
    return True


def _check_post(cpt_slug: str, status: str) -> bool:
    """テスト投稿を作成し、成功したら削除する（後始末まで行う）。"""
    logger.info("=== 3. 認証付き投稿確認（POST → 削除）===")
    try:
        created = wp_client.create_post(
            "【接続テスト】この投稿は自動削除されます",
            "<p>news_bot からの接続確認用の投稿です。</p>",
        )
    except requests.HTTPError as exc:
        resp = exc.response
        logger.error("  投稿失敗: HTTP %s", resp.status_code if resp is not None else "?")
        if resp is not None:
            logger.error("  レスポンス: %s", resp.text[:300])
            if resp.status_code == 401:
                logger.error("  401: WP_USER / WP_APP_PASSWORD を確認する")
            elif resp.status_code == 403:
                logger.error("  403: 投稿権限、または WAF によるブロックを確認する")
            elif resp.status_code == 404:
                logger.error("  404: VOD_NEWS_CPT_SLUG（現在: %s）が WordPress 側の CPT と一致しない", cpt_slug)
        return False
    except requests.RequestException as exc:
        logger.error("  接続失敗: %s", exc)
        return False

    post_id = created.get("id")
    logger.info("  投稿成功: id=%s status=%s", post_id, created.get("status"))
    if created.get("status") != status:
        logger.warning("  投稿ステータスが想定（%s）と異なります: %s", status, created.get("status"))

    return _delete_test_post(cpt_slug, post_id)


def _delete_test_post(cpt_slug: str, post_id: int | None) -> bool:
    """テスト投稿を完全削除する。削除に失敗しても接続確認自体は成功として扱う。"""
    if not post_id:
        logger.warning("  投稿IDが取得できず削除をスキップ。管理画面から手動で削除すること")
        return True

    url = f"{os.environ['VOD_NEWS_WP_API_BASE'].rstrip('/')}/{cpt_slug}/{post_id}"
    try:
        resp = requests.delete(
            url,
            params={"force": "true"},
            headers={
                "Authorization": wp_client.build_auth(os.environ["WP_USER"], os.environ["WP_APP_PASSWORD"]),
                "User-Agent": wp_client.USER_AGENT,
            },
            timeout=_TIMEOUT,
        )
        if resp.status_code == 200:
            logger.info("  テスト投稿を削除しました: id=%s", post_id)
        else:
            logger.warning(
                "  テスト投稿の削除に失敗（HTTP %s）。管理画面から id=%s を手動削除すること",
                resp.status_code,
                post_id,
            )
    except requests.RequestException as exc:
        logger.warning("  テスト投稿の削除に失敗（%s）。管理画面から id=%s を手動削除すること", exc, post_id)
    return True


def main() -> int:
    cpt_slug = os.environ.get("VOD_NEWS_CPT_SLUG", "vod_news")
    status = os.environ.get("VOD_NEWS_WP_STATUS", "draft")

    if not _check_env():
        return 1
    if not _check_get(cpt_slug):
        return 1
    if not _check_post(cpt_slug, status):
        return 1

    logger.info("=== すべて成功: vod_publish の WP 投稿は動作します ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
