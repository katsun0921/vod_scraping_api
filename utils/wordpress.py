"""WordPress REST API クライアント。

WordPress の投稿データを取得し、ACF フィールドと vod タクソノミーを更新する。

環境変数:
    WP_API_URL      : WordPress REST API のベース URL（例: https://example.com/wp-json/wp/v2）
    WP_USER         : WordPress ユーザー名
    WP_APP_PASSWORD : WordPress Application Password（スペース除去済みでも可）
    WP_BASIC_USER   : サーバー Basic 認証のユーザー名（任意）
    WP_BASIC_PASSWORD: サーバー Basic 認証のパスワード（任意）
"""

import logging
import os
import time
from typing import Optional

import requests
from requests.auth import HTTPBasicAuth

logger = logging.getLogger(__name__)

# vod taxonomy のサービス名 → term_id マッピング
# WordPress 管理画面で確認した値
VOD_TERM_IDS: dict[str, int] = {
    "amazon_prime_video": 70,
    "netflix": 161,
    "hulu": 0,      # Hulu は term_id 未確定のため 0（配信中でも taxonomy 付与しない）
    "unext": 71,
    "disney_plus": 232,
    "dmm_tv": 838,
    "youtube": 973,
}

# スクレイピング対象サービス一覧
SERVICES = ["amazon_prime_video", "netflix", "hulu", "unext", "disney_plus", "dmm_tv", "youtube"]

PER_PAGE = 20


def _wp_auth_header() -> str:
    """WordPress Application Password の Basic 認証ヘッダー値を返す。"""
    import base64
    user = os.environ["WP_USER"]
    password = os.environ["WP_APP_PASSWORD"].replace(" ", "")
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return f"Basic {token}"


def _session(wp_auth: bool = False) -> requests.Session:
    """認証済みセッションを返す。

    Args:
        wp_auth: True の場合、WordPress Application Password を Authorization ヘッダーに設定する。
                 False の場合、サーバー Basic 認証のみ使用（GET用）。
    """
    session = requests.Session()
    session.headers.update({"User-Agent": "vod-scraping-api/1.0"})
    if wp_auth:
        # PATCH用: WP Application Password を Authorization ヘッダーで渡す
        # サーバー Basic 認証は /wp-json/ では不要（.htaccess で除外済み）
        session.headers.update({"Authorization": _wp_auth_header()})
    else:
        # GET用: サーバー Basic 認証
        session.auth = _auth()
    return session


def _auth() -> HTTPBasicAuth:
    """認証オブジェクトを返す。

    サーバー Basic 認証（WP_BASIC_USER / WP_BASIC_PASSWORD）が設定されている場合はそちらを優先。
    未設定の場合は WordPress Application Password を使用。
    """
    basic_user = os.environ.get("WP_BASIC_USER")
    basic_password = os.environ.get("WP_BASIC_PASSWORD")
    if basic_user and basic_password:
        return HTTPBasicAuth(basic_user, basic_password)
    user = os.environ["WP_USER"]
    password = os.environ["WP_APP_PASSWORD"].replace(" ", "")
    return HTTPBasicAuth(user, password)


def _base_url() -> str:
    """WordPress REST API のベース URL を返す（末尾スラッシュなし）。"""
    return os.environ["WP_API_URL"].rstrip("/")


def get_posts() -> list[dict]:
    """全投稿を取得して返す。

    scraping_url が 1 件以上設定されている投稿のみ返す。
    ページネーションで全件取得する（per_page=100）。

    Returns:
        投稿データのリスト。各要素は WordPress REST API のレスポンス形式。
    """
    url = f"{_base_url()}/posts"
    posts: list[dict] = []
    page = 1
    session = _session()

    while True:
        # 502 等の一時的エラーに備えてリトライ
        for attempt in range(3):
            resp = session.get(
                url,
                params={"per_page": PER_PAGE, "page": page, "_fields": "id,slug,acf,vod"},
                timeout=30,
            )
            logger.info("GET posts page=%d status=%d (attempt=%d)", page, resp.status_code, attempt + 1)
            if resp.status_code < 500:
                break
            logger.warning("GET posts page=%d server error, retrying in 5s...", page)
            time.sleep(5)
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        posts.extend(batch)
        if len(batch) < PER_PAGE:
            break
        page += 1
        logger.info("GET posts page=%d (%d件取得済み)", page - 1, len(posts))

    # scraping_url が 1 件以上ある投稿のみ対象
    filtered = []
    for post in posts:
        acf = post.get("acf") or {}
        has_url = any(
            (acf.get(svc) or {}).get("scraping_url")
            for svc in SERVICES
        )
        if has_url:
            filtered.append(post)

    logger.info("全投稿 %d 件中、scraping_url あり: %d 件", len(posts), len(filtered))
    return filtered


# number|null 型として定義されている ACF フィールド名のセット
_NUMBER_OR_NULL_FIELDS = {"price", "score", "score_audience", "review_score"}

# WordPress REST API スキーマから取得した ACF フィールド定義キャッシュ
_acf_schema_cache: dict | None = None


def _get_acf_schema() -> dict:
    """WordPress REST API から ACF フィールドのスキーマを取得・キャッシュして返す。

    Returns:
        {フィールド名: {"type": list, "required": bool, "minItems": int|None}} の辞書。
    """
    global _acf_schema_cache
    if _acf_schema_cache is not None:
        return _acf_schema_cache

    session = _session()
    resp = session.options(f"{_base_url()}/posts", timeout=30)
    if not resp.ok:
        # スキーマ取得失敗時は空辞書を返す（normalize がスキップされる）
        logger.warning("ACF スキーマ取得失敗: status=%d", resp.status_code)
        _acf_schema_cache = {}
        return {}
    data = resp.json()
    if not isinstance(data, dict):
        logger.warning("ACF スキーマ取得: 予期しないレスポンス型 %s", type(data).__name__)
        _acf_schema_cache = {}
        return {}
    schema = data.get("schema", {})
    acf_props = schema.get("properties", {}).get("acf", {}).get("properties", {})
    if not isinstance(acf_props, dict):
        logger.warning("ACF properties が dict でない: %s", type(acf_props).__name__)
        _acf_schema_cache = {}
        return {}
    _acf_schema_cache = acf_props
    logger.info("ACF スキーマ取得完了: %d フィールド", len(acf_props))
    return acf_props


def _normalize_acf_for_patch(acf: dict, schema: dict) -> dict:
    """PATCH 送信前に ACF データを正規化する。

    - number|null 型フィールドの空文字列 → null
    - array 型で minItems >= 1 のフィールドが空値（"", null）→ キーを除外
    - array|null 型で null の値 → キーを除外（ACF の null 送信バグ回避）

    Args:
        acf   : 既存の ACF データ辞書。
        schema: _get_acf_schema() の戻り値。

    Returns:
        正規化済みの新しい辞書。
    """
    if not isinstance(schema, dict):
        schema = {}
    result = {}
    for key, val in acf.items():
        field_schema = schema.get(key, {})
        field_type = field_schema.get("type") or []
        if isinstance(field_type, str):
            field_type = [field_type]
        min_items = field_schema.get("minItems")

        # array 型で minItems >= 1 のフィールドが空値ならスキップ
        if "array" in field_type and min_items and min_items >= 1:
            if val == "" or val is None or val == []:
                logger.debug("ACF normalize: skip %s (array minItems=%d, val=%r)", key, min_items, val)
                continue

        # array|null 型で null の場合、バリデーションエラー回避のためスキップ
        if "array" in field_type and "null" in field_type and val is None:
            logger.debug("ACF normalize: skip %s (array|null, val=null)", key)
            continue

        # number|null 型の空文字列 → null
        if key in _NUMBER_OR_NULL_FIELDS and val == "":
            result[key] = None
            continue

        # ネストされた dict / list も再帰的に処理（score 等の子フィールド）
        if isinstance(val, dict):
            result[key] = _normalize_nested_numbers(val)
        elif isinstance(val, list):
            result[key] = [
                _normalize_nested_numbers(item) if isinstance(item, dict) else item
                for item in val
            ]
        else:
            result[key] = val

    return result


def _normalize_nested_numbers(d: dict) -> dict:
    """ネストされた dict 内の number|null 型フィールドの空文字列を null に変換する。"""
    result = {}
    for key, val in d.items():
        if isinstance(val, dict):
            result[key] = _normalize_nested_numbers(val)
        elif key in _NUMBER_OR_NULL_FIELDS and val == "":
            result[key] = None
        else:
            result[key] = val
    return result


def update_post(
    post_id: int,
    service: str,
    status: str,
    price: Optional[float],
    updated_at: str,
    current_vod_term_ids: list[int],
) -> None:
    """投稿の ACF フィールドと vod タクソノミーを更新する。

    Args:
        post_id       : WordPress 投稿 ID。
        service       : サービス名（"amazon" / "netflix" / "hulu" / "unext"）。
        status        : 配信ステータス（"streaming" / "rental" / "purchase" / "unavailable" / "ended"）。
        price         : 価格（None の場合は 0 を設定）。
        updated_at    : 更新日時文字列（"YYYY-MM-DD HH:MM:SS" 形式）。
        current_vod_term_ids: 現在付与されている vod タクソノミーの term_id リスト。
    """
    url = f"{_base_url()}/posts/{post_id}"
    session = _session(wp_auth=True)

    # 既存の ACF データを取得して必須フィールドを保持したまま更新する
    get_resp = session.get(url, params={"_fields": "acf"}, timeout=30)
    get_resp.raise_for_status()
    existing_acf: dict = (get_resp.json().get("acf") or {}).copy()

    # 対象サービスのフィールドだけ上書き
    existing_acf[service] = {
        "scraping_url": existing_acf.get(service, {}).get("scraping_url", ""),
        "status": status,
        "price": price if price is not None else 0,
        "updated_at": updated_at,
    }

    # スキーマに基づいて ACF データを正規化（空文字列の数値→null、空配列の除外等）
    schema = _get_acf_schema()
    normalized_acf = _normalize_acf_for_patch(existing_acf, schema)

    acf_payload = {"acf": normalized_acf}
    resp = session.patch(url, json=acf_payload, timeout=30)
    if not resp.ok:
        logger.error("PATCH ACF failed: status=%d body=%s", resp.status_code, resp.text[:500])
    resp.raise_for_status()

    # vod タクソノミー更新
    term_id = VOD_TERM_IDS.get(service, 0)
    if term_id == 0:
        return  # term_id 未確定サービスは taxonomy 操作しない

    new_term_ids = set(current_vod_term_ids)
    if status == "streaming":
        new_term_ids.add(term_id)
    else:
        new_term_ids.discard(term_id)

    vod_payload = {"vod": list(new_term_ids)}
    resp = session.patch(url, json=vod_payload, timeout=30)
    if not resp.ok:
        logger.error("PATCH vod failed: status=%d body=%s", resp.status_code, resp.text[:500])
    resp.raise_for_status()


def get_vod_term_ids(post: dict) -> list[int]:
    """投稿データから現在の vod タクソノミー term_id リストを返す。

    Args:
        post: WordPress REST API の投稿データ。

    Returns:
        term_id の整数リスト（未設定の場合は空リスト）。
    """
    return post.get("vod") or []
