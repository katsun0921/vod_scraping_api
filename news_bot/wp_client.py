"""WP REST API クライアント（VOD配信情報収集パイプライン用、仕様書10./11.2）。

`vod_bot/wordpress.py`（配信状況スクレイピングAPI側）とは責務が異なる別クライアント：
そちらは既存投稿のACFフィールド更新、こちらは新規CPT投稿の作成 + 既存レビュー記事の
照合（仕様書10. Katsumascore照合）を担う。theater施策とも共用する想定。

照合部分（find_post_by_title）は `docs/drop/coming-soon-pipeline.md`（凍結済み）の
`enrich_events.py`（fetch_wp_post_by_tmdb_id / build_auth）を土台に、tmdb_idクエリを
タイトル検索へ置き換えて移植した（仕様書10.1参照。tmdb_id ACFフィールドの実在は
未確認のため、確認が取れ次第tmdb_id完全一致クエリを照合順1位として追加すること）。

環境変数:
    VOD_NEWS_WP_API_BASE     : WP REST API ベースURL（例: https://example.com/wp-json/wp/v2）
    VOD_NEWS_WP_USER         : WordPress ユーザー名
    VOD_NEWS_WP_APP_PASSWORD : WordPress Application Password
    VOD_NEWS_CPT_SLUG        : CPTのRESTスラッグ（既定 vod_news。15.未決定事項#1参照。
                                最終的な名称は管理者がWordPress側で決定・登録する）
    VOD_NEWS_WP_STATUS       : 投稿ステータス（既定 draft）
"""

import base64
import os

import requests

_TIMEOUT = 30


def build_auth(user: str, app_pass: str) -> str:
    """WordPress Application Password の Basic 認証ヘッダー値を返す。"""
    token = base64.b64encode(f"{user}:{app_pass.replace(' ', '')}".encode()).decode()
    return f"Basic {token}"


def _base_url() -> str:
    return os.environ["VOD_NEWS_WP_API_BASE"].rstrip("/")


def _headers() -> dict:
    user = os.environ["VOD_NEWS_WP_USER"]
    app_pass = os.environ["VOD_NEWS_WP_APP_PASSWORD"]
    return {"Authorization": build_auth(user, app_pass)}


def find_post_by_title(title: str, title_orig: str = "") -> dict | None:
    """タイトル（無ければ原題）でWP投稿を検索し、最初の1件を返す。

    照合順（仕様書10.）は本来 tmdb_id → 正規化タイトル → 原題だが、tmdb_id ACF
    フィールドの実在が未確認（15.未決定事項#5）なため、現状はタイトル検索のみで
    運用する。実在確認が取れ次第、tmdb_id完全一致クエリ
    （?meta_key=tmdb_id&meta_value=...）を優先順位1位として追加すること。
    """
    for query in (title, title_orig):
        if not query:
            continue
        resp = requests.get(
            f"{_base_url()}/posts",
            params={"search": query, "per_page": 1},
            headers=_headers(),
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        results = resp.json()
        if results:
            return results[0]
    return None


def create_post(title: str, content_html: str, *, excerpt: str = "") -> dict:
    """VOD配信情報CPTへ新規投稿を作成する（仕様書11.2）。

    投稿タイプ（スラッグ）・ステータスは環境変数で注入するため、CPT名称・
    公開運用の最終決定（15.未決定事項#1・#4）を待たずに実装できる。
    """
    cpt_slug = os.environ.get("VOD_NEWS_CPT_SLUG", "vod_news")
    status = os.environ.get("VOD_NEWS_WP_STATUS", "draft")
    payload = {"title": title, "content": content_html, "status": status}
    if excerpt:
        payload["excerpt"] = excerpt

    resp = requests.post(
        f"{_base_url()}/{cpt_slug}",
        json=payload,
        headers=_headers(),
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()
