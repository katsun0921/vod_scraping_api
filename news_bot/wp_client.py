"""WP REST API クライアント（VOD配信情報収集パイプライン用、仕様書10./11.2）。

`vod_bot/wordpress.py`（配信状況スクレイピングAPI側）とは責務が異なる別クライアント：
そちらは既存投稿のACFフィールド更新、こちらは新規CPT投稿の作成 + 既存レビュー記事の
照合（仕様書10. Katsumascore照合）を担う。theater施策とも共用する想定。

照合部分（find_post_by_title）は `docs/drop/coming-soon-pipeline.md`（凍結済み）の
`enrich_events.py`（fetch_wp_post_by_tmdb_id / build_auth）を土台に、tmdb_idクエリを
タイトル検索へ置き換えて移植した（仕様書10.1参照。tmdb_id ACFフィールドの実在は
未確認のため、確認が取れ次第tmdb_id完全一致クエリを照合順1位として追加すること）。

環境変数:
    WP_API_URL               : WP REST API ベースURL（例: https://example.com/wp-json/wp/v2。
                                vod_bot/wordpress.pyの配信状況チェックと共用）
    WP_USER                  : WordPressユーザー名（同上）
    WP_APP_PASSWORD          : WordPress Application Password（同上）
    VOD_NEWS_CPT_SLUG        : CPTのRESTスラッグ（既定 vod_release）
    VOD_NEWS_WP_STATUS       : 投稿ステータス（既定 draft）
"""

import base64
import logging
import os

import requests

from news_bot.theater_calendar import normalize_title

logger = logging.getLogger(__name__)

_TIMEOUT = 30
# search はあいまい検索のため、完全一致を探す候補数。1件だけ見ると
# 目的の記事が2位以降にいる場合に取りこぼす。
_SEARCH_CANDIDATES = 10

# ConoHa WING の WAF が既定の User-Agent（`python-requests/*`）を403でブロックするため、
# 明示的に本ボットを名乗る。2026-08-03に実測で確認:
#   UA=python-requests/2.31.0 -> 403 / UA=curl・Mozilla・news_bot -> 200
# （/wp-json/wp/v2/posts でも同様に発生するため、CPTに限らずWP REST API全体が対象）
USER_AGENT = "katsumascore-news-bot/1.0 (+https://katsumascore.blog)"


def build_auth(user: str, app_pass: str) -> str:
    """WordPress Application Password の Basic 認証ヘッダー値を返す。"""
    token = base64.b64encode(f"{user}:{app_pass.replace(' ', '')}".encode()).decode()
    return f"Basic {token}"


def _base_url() -> str:
    return os.environ["WP_API_URL"].rstrip("/")


def _headers() -> dict:
    user = os.environ["WP_USER"]
    app_pass = os.environ["WP_APP_PASSWORD"]
    return {
        "Authorization": build_auth(user, app_pass),
        "User-Agent": USER_AGENT,
    }


def _title_matches(candidate_title: str, query: str) -> bool:
    """WP投稿のタイトルが検索語と実質同一かを判定する。

    WP REST の `search` は部分一致・あいまい検索のため、無関係な記事でも
    ヒットする。正規化した上で完全一致した場合のみ採用する。
    """
    return bool(query) and normalize_title(candidate_title) == normalize_title(query)


def find_post_by_title(title: str, title_orig: str = "") -> dict | None:
    """タイトル（無ければ原題）でWP投稿を検索し、タイトルが一致した1件を返す。

    照合順（仕様書10.）は本来 tmdb_id → 正規化タイトル → 原題だが、tmdb_id ACF
    フィールドの実在が未確認（15.未決定事項#5）なため、現状はタイトル検索のみで
    運用する。実在確認が取れ次第、tmdb_id完全一致クエリ
    （?meta_key=tmdb_id&meta_value=...）を優先順位1位として追加すること。

    **誤照合を絶対に避けるため、正規化タイトルが完全一致した場合のみ採用する。**
    WP REST の `search` はあいまい検索で無関係な記事も返す（実際に「モリア」で
    『映画大好きポンポさん』が、「リーチャー」で『ウォーマシン』がヒットした）。
    誤ったレビューURLを週次まとめ記事に載せると読者を無関係な記事へ送ることになり、
    メディアの信頼性を直接損なうため、照合できない場合は None（＝リンクなし）を返す。
    取りこぼしは人間が承認時に補える一方、誤リンクは気付かれにくい。
    """
    for query in (title, title_orig):
        if not query:
            continue
        resp = requests.get(
            f"{_base_url()}/posts",
            params={"search": query, "per_page": _SEARCH_CANDIDATES},
            headers=_headers(),
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        for post in resp.json():
            rendered = (post.get("title") or {}).get("rendered", "")
            if _title_matches(rendered, query):
                return post
        logger.info("WP照合: タイトル一致なしのためリンクを付与しません: %r", query)
    return None


def create_post(
    title: str,
    content_html: str,
    *,
    excerpt: str = "",
    cpt_slug_env: str = "VOD_NEWS_CPT_SLUG",
    cpt_slug_default: str = "vod_release",
    status_env: str = "VOD_NEWS_WP_STATUS",
) -> dict:
    """CPTへ新規投稿を作成する（VOD: 仕様書11.2 / 劇場: 劇場仕様書11.）。

    投稿タイプ（スラッグ）・ステータスは環境変数で注入するため、CPT名称・
    公開運用の最終決定（15.未決定事項#1・#4）を待たずに実装できる。

    参照する環境変数名を引数で差し替えられるようにしているのは、VOD配信情報
    （vod_release）と劇場公開情報（theater_release）が別CPTであり、公開運用の
    切り替え（draft→publish）も独立して判断したいため。既定値はVOD側のままなので
    既存の呼び出しは変更不要。
    """
    # GitHub Actions は未登録のsecretを「空文字」として渡すため、os.environ.get()の
    # 既定値では拾えない（キー自体は存在する）。空文字は未設定として扱わないと、
    # 投稿先URLが .../wp/v2/ になって別のエンドポイントを叩いてしまう。
    cpt_slug = os.environ.get(cpt_slug_env) or cpt_slug_default
    status = os.environ.get(status_env) or "draft"
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
