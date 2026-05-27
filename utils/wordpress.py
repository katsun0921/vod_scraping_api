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
from datetime import date, timedelta
from typing import Optional

import requests
from requests.auth import HTTPBasicAuth

logger = logging.getLogger(__name__)

# vod taxonomy のサービス名 → term_id マッピング
# WordPress 管理画面で確認した値
VOD_TERM_IDS: dict[str, int] = {
    "amazon_prime_video": 433,
    "netflix": 161,
    "hulu": 72,
    "unext": 71,
    "disney_plus": 232,
    "dmm_tv": 838,
    "apple_tv": 1114,
    "youtube": 973,
    "crunchyroll": 3937,
}

# スクレイピング対象サービス一覧
SERVICES = ["amazon_prime_video", "netflix", "hulu", "unext", "disney_plus", "dmm_tv", "apple_tv", "youtube", "crunchyroll"]

# サービスごとの対応言語セット（post.acf.lang との照合に使用）
# 言語コード: "ja" = 日本語, "en" = 英語
SERVICE_SUPPORTED_LANGUAGES: dict[str, frozenset] = {
    "amazon_prime_video": frozenset({"ja", "en"}),
    "netflix":            frozenset({"ja", "en"}),
    "hulu":               frozenset({"ja", "en"}),
    "unext":              frozenset({"ja"}),
    "disney_plus":        frozenset({"ja", "en"}),
    "dmm_tv":             frozenset({"ja"}),
    "apple_tv":           frozenset({"ja", "en"}),
    "youtube":            frozenset({"ja", "en"}),
    "crunchyroll":        frozenset({"en"}),         # 英語作品（主に海外向けアニメ配信）
}

# サービスごとのカテゴリ制約（post の WordPress category term_id との照合に使用）
# 設定されている場合、投稿が指定 term_id のいずれかに属していないとスキップする
SERVICE_REQUIRED_CATEGORY_IDS: dict[str, frozenset] = {
    "crunchyroll": frozenset({3}),  # アニメ（anime）カテゴリのみ対象 term_id=3
}

PER_PAGE = 100


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


def get_posts(slug: Optional[str] = None, limit: Optional[int] = None) -> list[dict]:
    """publish 状態の投稿を取得して返す。

    scraping_url が 1 件以上設定されている投稿のみ返す。
    slug を指定した場合は該当する 1 件のみを取得する（全件ページネーションを省略）。
    limit を指定した場合は最大 limit 件でフィルタ後の結果を打ち切る。

    Args:
        slug : 指定した場合、該当 slug の投稿のみ取得する。
        limit: 返す最大件数。None の場合は全件返す。

    Returns:
        投稿データのリスト。各要素は WordPress REST API のレスポンス形式。
    """
    url = f"{_base_url()}/posts"
    posts: list[dict] = []
    page = 1
    session = _session()

    while True:
        params: dict = {
            "status": "publish",
            "_fields": "id,slug,title,acf,vod,categories",
        }
        if slug:
            # slug 指定時は 1 件取得で完結
            params["slug"] = slug
            params["per_page"] = 1
        else:
            params["per_page"] = PER_PAGE
            params["page"] = page

        # 502 等の一時的エラーに備えてリトライ
        for attempt in range(3):
            resp = session.get(url, params=params, timeout=30)
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
        # slug 指定時 or ページ末尾に達したら終了
        if slug or len(batch) < PER_PAGE:
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

    if limit is not None:
        filtered = filtered[:limit]

    logger.info("全投稿 %d 件中、scraping_url あり: %d 件（limit=%s）", len(posts), len(filtered), limit)
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
        max_length = field_schema.get("maxLength")

        # maxLength 制約があり文字列が超過している場合はスキップ（description 等）
        if max_length and isinstance(val, str) and len(val) > max_length:
            logger.debug("ACF normalize: skip %s (maxLength=%d, len=%d)", key, max_length, len(val))
            continue

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
) -> bool:
    """投稿の ACF フィールドと vod タクソノミーを更新する。

    Args:
        post_id       : WordPress 投稿 ID。
        service       : サービス名（"amazon_prime_video" / "netflix" / "hulu" / "unext" 等）。
        status        : 配信ステータス（"streaming" / "rental" / "purchase" / "unavailable" / "ended"）。
        price         : 価格（None の場合は 0 を設定）。
        updated_at    : 更新日時文字列（"YYYY-MM-DD HH:MM:SS" 形式）。
        current_vod_term_ids: 現在付与されている vod タクソノミーの term_id リスト。

    Returns:
        True の場合、未配信 → streaming への初回遷移（新規配信検知）。
    """
    url = f"{_base_url()}/posts/{post_id}"
    session = _session(wp_auth=True)

    # 既存の ACF データを取得して必須フィールドを保持したまま更新する
    get_resp = session.get(url, params={"_fields": "acf"}, timeout=30)
    get_resp.raise_for_status()
    existing_acf: dict = (get_resp.json().get("acf") or {}).copy()

    # streaming_started_at: 未取得→streaming への初回遷移時のみ更新
    prev_status = (existing_acf.get(service) or {}).get("status", "")
    prev_ssa = (existing_acf.get(service) or {}).get("streaming_started_at", "")
    is_new_streaming = status == "streaming" and prev_status != "streaming" and not prev_ssa
    if is_new_streaming:
        streaming_started_at = updated_at
        logger.info("post_id=%d service=%s: 新規配信検知 → streaming_started_at=%s", post_id, service, streaming_started_at)
    else:
        streaming_started_at = prev_ssa

    # 対象サービスのフィールドのみ PATCH する（他フィールドはバリデーションエラー回避のため送らない）
    # ただし REST API スキーマで required=True のフィールドは既存値をそのまま含める
    schema = _get_acf_schema()
    required_keys = [k for k, v in schema.items() if isinstance(v, dict) and v.get("required")]
    acf_patch: dict = {}
    for key in required_keys:
        if key in existing_acf:
            acf_patch[key] = existing_acf[key]
    acf_patch[service] = {
        "scraping_url": (existing_acf.get(service) or {}).get("scraping_url", ""),
        "status": status,
        "price": price if price is not None else 0,
        "updated_at": updated_at,
        "streaming_started_at": streaming_started_at,
    }
    acf_payload = {"acf": acf_patch}
    resp = session.patch(url, json=acf_payload, timeout=30)
    if not resp.ok:
        logger.error("PATCH ACF failed: status=%d body=%s", resp.status_code, resp.text[:500])
    resp.raise_for_status()

    # vod タクソノミー更新
    term_id = VOD_TERM_IDS.get(service, 0)
    if term_id == 0:
        return is_new_streaming  # term_id 未確定サービスは taxonomy 操作しない

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

    return is_new_streaming


def get_vod_term_ids(post: dict) -> list[int]:
    """投稿データから現在の vod タクソノミー term_id リストを返す。

    Args:
        post: WordPress REST API の投稿データ。

    Returns:
        term_id の整数リスト（未設定の場合は空リスト）。
    """
    return post.get("vod") or []


def should_skip(post: dict, service: str, today: date) -> tuple[bool, str]:
    """投稿・サービスの組み合わせをスキップすべきか判定する。

    スキップ条件（優先順）:
      1. scraping_disabled が True（管理者停止）
      2. scraping_cooldown_until が today 以降（クールダウン中）
      3. 独占配信かつ対象サービスが exclusive_service と不一致
      4. scraping_url が空（探索対象外）
      5. 直近30日以内に updated_at が更新済み
      6. 言語ミスマッチ（lang が設定されておりサービス対応言語に含まれない）
      7. カテゴリ制約（SERVICE_REQUIRED_CATEGORY_IDS に定義されたサービスは指定 category term_id 以外をスキップ）

    Args:
        post   : WordPress REST API の投稿データ（categories フィールドを含む）。
        service: サービス名（例: "netflix"）。
        today  : 判定基準日（date オブジェクト）。

    Returns:
        (skip: bool, reason: str) のタプル。
        skip が True のときはスキップ、False のときはチェック対象。
    """
    acf = post.get("acf") or {}

    # 1. 管理者停止フラグ
    if acf.get("scraping_disabled"):
        return True, "scraping_disabled=true"

    # 2. クールダウン期間中
    cooldown_str = acf.get("scraping_cooldown_until") or ""
    if cooldown_str:
        try:
            cooldown_date = date.fromisoformat(cooldown_str)
            if cooldown_date >= today:
                return True, f"cooldown_until={cooldown_str}"
        except ValueError:
            logger.warning("post_id=%d scraping_cooldown_until の形式が不正: %r", post.get("id"), cooldown_str)

    # 3. 独占配信スキップ（is_exclusive=True かつ exclusive_service が別サービスの場合）
    if acf.get("is_exclusive"):
        exclusive_svc = (acf.get("exclusive_service") or "").strip()
        if exclusive_svc and exclusive_svc != service:
            return True, f"exclusive={exclusive_svc}"

    # 4. scraping_url が空
    service_acf = acf.get(service) or {}
    scraping_url = service_acf.get("scraping_url") or ""
    if not scraping_url:
        return True, "scraping_url=empty"

    # 5. 直近30日以内に更新済み
    updated_at_str = service_acf.get("updated_at") or ""
    if updated_at_str:
        try:
            # "YYYY-MM-DD HH:MM:SS" または "YYYY-MM-DD" 形式に対応
            updated_date = date.fromisoformat(updated_at_str[:10])
            if (today - updated_date).days < 30:
                return True, f"updated_within_30d={updated_at_str[:10]}"
        except ValueError:
            logger.warning("post_id=%d service=%s updated_at の形式が不正: %r", post.get("id"), service, updated_at_str)

    # 6. 言語ミスマッチ（lang が未設定の場合はスキップしない）
    post_lang = acf.get("lang") or ""
    if post_lang:
        supported = SERVICE_SUPPORTED_LANGUAGES.get(service, frozenset({"ja", "en"}))
        if post_lang not in supported:
            return True, f"language_mismatch={post_lang}"

    # 7. カテゴリ制約（SERVICE_REQUIRED_CATEGORY_IDS に定義されたサービスのみ）
    required_cat_ids = SERVICE_REQUIRED_CATEGORY_IDS.get(service)
    if required_cat_ids:
        post_category_ids: set[int] = set(post.get("categories") or [])
        if not post_category_ids & required_cat_ids:
            # カテゴリ未設定、または必要カテゴリを含まない投稿はスキップ
            return True, f"category_mismatch(required_ids={','.join(str(i) for i in sorted(required_cat_ids))})"

    return False, ""


def update_cooldown(post: dict, today: date, acf_payload: dict) -> None:
    """スクレイピング完了後にクールダウンを計算して acf_payload へ追記する。

    配信中サービスが1つでもある場合: 30日後にリセット + カウントリセット。
    全サービス未配信の場合: 指数バックオフ + 年齢補正。

    Args:
        post       : WordPress REST API の投稿データ（最新の ACF を含む）。
        today      : 基準日（date オブジェクト）。
        acf_payload: PATCH 送信用の ACF dict。この dict に cooldown フィールドを追記する。
    """
    acf = post.get("acf") or {}

    has_streaming = any(
        (acf.get(s) or {}).get("status") == "streaming"
        for s in SERVICES
    )

    if has_streaming:
        cooldown_until = today + timedelta(days=30)
        acf_payload["scraping_cooldown_until"] = cooldown_until.isoformat()
        acf_payload["unavailable_check_count"] = 0
        logger.info(
            "post_id=%d: 配信中あり → cooldown_until=%s count=0",
            post.get("id"),
            cooldown_until.isoformat(),
        )
        return

    # 全サービス未配信 → 指数バックオフ + 年齢補正
    try:
        count = int(acf.get("unavailable_check_count") or 0) + 1
    except (ValueError, TypeError):
        count = 1

    base_days_table = [30, 60, 120, 240, 360]
    base_days = base_days_table[min(count - 1, 4)]

    # 年齢補正
    try:
        release_year = int(acf.get("release_year") or 0)
    except (ValueError, TypeError):
        release_year = 0

    years_old = today.year - release_year if release_year else 0

    if years_old >= 5:
        next_days = 360
    elif years_old >= 3:
        next_days = max(base_days, 180)
    else:
        next_days = base_days

    cooldown_until = today + timedelta(days=next_days)
    acf_payload["scraping_cooldown_until"] = cooldown_until.isoformat()
    acf_payload["unavailable_check_count"] = count
    logger.info(
        "post_id=%d: 全未配信 count=%d years_old=%d → next_days=%d cooldown_until=%s",
        post.get("id"),
        count,
        years_old,
        next_days,
        cooldown_until.isoformat(),
    )


def patch_cooldown(post_id: int, acf_payload: dict) -> None:
    """クールダウンフィールドだけを PATCH する。

    Args:
        post_id    : WordPress 投稿 ID。
        acf_payload: {"scraping_cooldown_until": "YYYY-MM-DD", "unavailable_check_count": N} 形式。
    """
    if not acf_payload:
        return
    url = f"{_base_url()}/posts/{post_id}"
    session = _session(wp_auth=True)

    # required フィールドを既存 ACF から取得して含める
    get_resp = session.get(url, params={"_fields": "acf"}, timeout=30)
    get_resp.raise_for_status()
    existing_acf: dict = (get_resp.json().get("acf") or {})
    schema = _get_acf_schema()
    required_keys = [k for k, v in schema.items() if isinstance(v, dict) and v.get("required")]
    patch: dict = {k: existing_acf[k] for k in required_keys if k in existing_acf}
    patch.update(acf_payload)

    resp = session.patch(url, json={"acf": patch}, timeout=30)
    if not resp.ok:
        logger.error(
            "PATCH cooldown failed: post_id=%d status=%d body=%s",
            post_id, resp.status_code, resp.text[:300],
        )
    resp.raise_for_status()


def patch_multi_service_fields(
    post_id: int,
    service_fields: dict[str, dict],
    top_level_fields: dict | None = None,
) -> None:
    """複数サービスの ACF サブフィールドを 1回の GET + 1回の PATCH でまとめて更新する。

    scraping_url の登録や unavailable ステータスの書き込みに使用する。
    1サービスずつ個別 PATCH するより GET/PATCH 回数を大幅に削減できる。

    Args:
        post_id          : WordPress 投稿 ID。
        service_fields   : {service_key: {field: value, ...}} の辞書。
                           例: {
                               "netflix":  {"scraping_url": "https://..."},
                               "hulu":     {"status": "unavailable", "updated_at": "2026-05-27 ..."},
                           }
        top_level_fields : ACF トップレベルフィールドの更新辞書（任意）。
                           例: {"scraping_disabled": True}

    Raises:
        requests.HTTPError: PATCH 失敗時。
    """
    if not service_fields and not top_level_fields:
        return

    url = f"{_base_url()}/posts/{post_id}"
    session = _session(wp_auth=True)

    # 既存 ACF を 1回だけ取得
    get_resp = session.get(url, params={"_fields": "acf"}, timeout=30)
    get_resp.raise_for_status()
    existing_acf: dict = (get_resp.json().get("acf") or {}).copy()

    schema = _get_acf_schema()
    required_keys = [k for k, v in schema.items() if isinstance(v, dict) and v.get("required")]
    acf_patch: dict = {k: existing_acf[k] for k in required_keys if k in existing_acf}

    # 全対象サービスを 1つの PATCH ペイロードに積む
    for service, fields in service_fields.items():
        service_base = dict(existing_acf.get(service) or {})
        service_base.update(fields)
        # number|null フィールドが空文字列の場合は None (JSON null) に変換
        for sub_key in _NUMBER_OR_NULL_FIELDS:
            if sub_key in service_base and service_base[sub_key] == "":
                service_base[sub_key] = None
        acf_patch[service] = service_base

    # トップレベル ACF フィールドを上書き（scraping_disabled 等）
    if top_level_fields:
        acf_patch.update(top_level_fields)

    resp = session.patch(url, json={"acf": acf_patch}, timeout=30)
    if not resp.ok:
        logger.error(
            "PATCH multi service fields failed: post_id=%d services=%s status=%d body=%s",
            post_id, list(service_fields.keys()), resp.status_code, resp.text[:300],
        )
    resp.raise_for_status()


def get_posts_missing_url(
    services: list[str] | None = None,
    slug: Optional[str] = None,
    limit: Optional[int] = None,
) -> list[dict]:
    """scraping_url が 1 件以上 空のサービスを持つ publish 投稿を返す。

    月次 JustWatch バッチ用。
    slug を指定した場合は該当する 1 件のみを取得する。
    limit を指定した場合は最大 limit 件でフィルタ後の結果を打ち切る。

    Args:
        services: 対象サービスリスト。None の場合は SERVICES 全体。
        slug    : 指定した場合、該当 slug の投稿のみ取得する。
        limit   : 返す最大件数。None の場合は全件返す。

    Returns:
        投稿データのリスト。
    """
    target_services = services or SERVICES
    url = f"{_base_url()}/posts"
    posts: list[dict] = []
    page = 1
    session = _session()

    while True:
        params: dict = {
            "status": "publish",
            "_fields": "id,slug,title,acf,vod,categories",
        }
        if slug:
            params["slug"] = slug
            params["per_page"] = 1
        else:
            params["per_page"] = PER_PAGE
            params["page"] = page

        for attempt in range(3):
            resp = session.get(url, params=params, timeout=30)
            logger.info("GET posts(missing_url) page=%d status=%d (attempt=%d)", page, resp.status_code, attempt + 1)
            if resp.status_code < 500:
                break
            logger.warning("GET posts(missing_url) page=%d server error, retrying in 5s...", page)
            time.sleep(5)
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        posts.extend(batch)
        if slug or len(batch) < PER_PAGE:
            break
        page += 1

    # scraping_disabled / cooldown のポストを除外し、scraping_url が空のサービスを 1 件以上持つ投稿のみ返す
    today = date.today()
    filtered = []
    for post in posts:
        acf = post.get("acf") or {}

        # scraping_disabled=true はスキップ
        if acf.get("scraping_disabled"):
            logger.debug("SKIP [%s] scraping_disabled=true", post.get("slug"))
            continue

        # クールダウン中はスキップ
        cooldown_str = acf.get("scraping_cooldown_until") or ""
        if cooldown_str:
            try:
                if date.fromisoformat(cooldown_str) >= today:
                    logger.debug("SKIP [%s] cooldown_until=%s", post.get("slug"), cooldown_str)
                    continue
            except ValueError:
                pass

        # scraping_url が空のサービスが1件以上あるか判定
        # TODO: 全件再取得のため updated_at フィルターを一時無効化
        # one_month_ago = today.replace(month=today.month - 1) if today.month > 1 else today.replace(year=today.year - 1, month=12)
        has_target = False
        for svc in target_services:
            svc_data = acf.get(svc) or {}
            if svc_data.get("scraping_url"):
                continue  # scraping_url 設定済みはスキップ対象外
            # updated_at_str = (svc_data.get("updated_at") or "")[:10]
            # if updated_at_str:
            #     try:
            #         if date.fromisoformat(updated_at_str) > one_month_ago:
            #             continue  # 1か月未満はスキップ
            #     except ValueError:
            #         pass
            has_target = True
            break
        if has_target:
            filtered.append(post)

    if limit is not None:
        filtered = filtered[:limit]

    logger.info("全投稿 %d 件中、scraping_url 未設定サービスあり: %d 件（limit=%s）", len(posts), len(filtered), limit)
    return filtered
