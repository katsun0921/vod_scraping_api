"""utils/wordpress.py の should_skip / update_cooldown ユニットテスト。

外部APIへのアクセスは一切行わない。
"""

from datetime import date, timedelta

import pytest

from utils.wordpress import SERVICE_REQUIRED_CATEGORY_IDS, SERVICE_SUPPORTED_LANGUAGES, should_skip, update_cooldown


# ---------------------------------------------------------------------------
# テスト用ヘルパー
# ---------------------------------------------------------------------------

def _make_post(
    *,
    scraping_disabled=False,
    cooldown_until: str = "",
    service: str = "netflix",
    scraping_url: str = "https://www.netflix.com/jp/title/12345",
    updated_at: str = "",
    status: str = "",
    release_year: int = 0,
    unavailable_check_count: int = 0,
    lang: str | None = None,
    is_exclusive: bool = False,
    exclusive_service: str = "",
    categories: list[int] | None = None,
) -> dict:
    """テスト用の最小限の post dict を生成する。"""
    acf: dict = {
        "scraping_disabled": scraping_disabled,
        "scraping_cooldown_until": cooldown_until,
        "release_year": release_year,
        "unavailable_check_count": unavailable_check_count,
        "is_exclusive": is_exclusive,
        "exclusive_service": exclusive_service,
        service: {
            "scraping_url": scraping_url,
            "status": status,
            "updated_at": updated_at,
        },
    }
    if lang is not None:
        acf["lang"] = lang
    post: dict = {"id": 1, "slug": "test-movie", "acf": acf}
    if categories is not None:
        post["categories"] = categories
    return post


TODAY = date(2026, 5, 25)


# ---------------------------------------------------------------------------
# should_skip のテスト
# ---------------------------------------------------------------------------

class TestShouldSkip:

    # --- 1. scraping_disabled ---

    def test_skip_if_scraping_disabled(self):
        post = _make_post(scraping_disabled=True)
        skip, reason = should_skip(post, "netflix", TODAY)
        assert skip is True
        assert reason == "scraping_disabled=true"

    def test_no_skip_if_scraping_enabled(self):
        post = _make_post(scraping_disabled=False, scraping_url="https://example.com", updated_at="")
        skip, _ = should_skip(post, "netflix", TODAY)
        assert skip is False

    # --- 2. scraping_cooldown_until ---

    def test_skip_if_cooldown_today(self):
        """cooldown_until が今日と同じ日 → スキップ（>= today）"""
        post = _make_post(cooldown_until=TODAY.isoformat())
        skip, reason = should_skip(post, "netflix", TODAY)
        assert skip is True
        assert "cooldown_until" in reason

    def test_skip_if_cooldown_future(self):
        future = (TODAY + timedelta(days=30)).isoformat()
        post = _make_post(cooldown_until=future)
        skip, reason = should_skip(post, "netflix", TODAY)
        assert skip is True
        assert "cooldown_until" in reason

    def test_no_skip_if_cooldown_past(self):
        past = (TODAY - timedelta(days=1)).isoformat()
        post = _make_post(cooldown_until=past, scraping_url="https://example.com", updated_at="")
        skip, _ = should_skip(post, "netflix", TODAY)
        assert skip is False

    def test_no_skip_if_cooldown_empty(self):
        post = _make_post(cooldown_until="", scraping_url="https://example.com", updated_at="")
        skip, _ = should_skip(post, "netflix", TODAY)
        assert skip is False

    def test_skip_ignores_invalid_cooldown_date(self):
        """不正な日付はスキップせずに続行する（WarningログのみでエラーにしないL）"""
        post = _make_post(cooldown_until="invalid-date", scraping_url="https://example.com", updated_at="")
        skip, _ = should_skip(post, "netflix", TODAY)
        assert skip is False

    # --- 3. scraping_url 空 ---

    def test_skip_if_scraping_url_empty(self):
        post = _make_post(scraping_url="")
        skip, reason = should_skip(post, "netflix", TODAY)
        assert skip is True
        assert reason == "scraping_url=empty"

    def test_no_skip_if_scraping_url_set(self):
        post = _make_post(scraping_url="https://example.com", updated_at="")
        skip, _ = should_skip(post, "netflix", TODAY)
        assert skip is False

    # --- 4. updated_at 30日以内 ---

    def test_skip_if_updated_today(self):
        post = _make_post(scraping_url="https://example.com", updated_at=TODAY.isoformat())
        skip, reason = should_skip(post, "netflix", TODAY)
        assert skip is True
        assert "updated_within_30d" in reason

    def test_skip_if_updated_yesterday(self):
        yesterday = (TODAY - timedelta(days=1)).isoformat()
        post = _make_post(scraping_url="https://example.com", updated_at=yesterday)
        skip, reason = should_skip(post, "netflix", TODAY)
        assert skip is True

    def test_skip_if_updated_29_days_ago(self):
        d = (TODAY - timedelta(days=29)).isoformat()
        post = _make_post(scraping_url="https://example.com", updated_at=d)
        skip, _ = should_skip(post, "netflix", TODAY)
        assert skip is True

    def test_no_skip_if_updated_30_days_ago(self):
        """30日前（ちょうど30日）はスキップしない（< 30 の判定）"""
        d = (TODAY - timedelta(days=30)).isoformat()
        post = _make_post(scraping_url="https://example.com", updated_at=d)
        skip, _ = should_skip(post, "netflix", TODAY)
        assert skip is False

    def test_no_skip_if_updated_at_empty(self):
        post = _make_post(scraping_url="https://example.com", updated_at="")
        skip, _ = should_skip(post, "netflix", TODAY)
        assert skip is False

    def test_no_skip_if_updated_at_datetime_format(self):
        """'YYYY-MM-DD HH:MM:SS' 形式でも正しく判定する"""
        old = (TODAY - timedelta(days=60)).strftime("%Y-%m-%d %H:%M:%S")
        post = _make_post(scraping_url="https://example.com", updated_at=old)
        skip, _ = should_skip(post, "netflix", TODAY)
        assert skip is False

    # --- 優先順の確認 ---

    def test_disabled_takes_priority_over_cooldown(self):
        """scraping_disabled は cooldown より優先される"""
        future = (TODAY + timedelta(days=30)).isoformat()
        post = _make_post(scraping_disabled=True, cooldown_until=future)
        skip, reason = should_skip(post, "netflix", TODAY)
        assert reason == "scraping_disabled=true"

    def test_cooldown_takes_priority_over_url_empty(self):
        """cooldown は scraping_url 空より優先される"""
        future = (TODAY + timedelta(days=30)).isoformat()
        post = _make_post(cooldown_until=future, scraping_url="")
        skip, reason = should_skip(post, "netflix", TODAY)
        assert "cooldown_until" in reason

    # --- 3. 独占配信スキップ ---

    def test_skip_if_exclusive_and_different_service(self):
        """is_exclusive=True かつ exclusive_service が別サービス → スキップ"""
        post = _make_post(is_exclusive=True, exclusive_service="netflix")
        skip, reason = should_skip(post, "amazon_prime_video", TODAY)
        assert skip is True
        assert reason == "exclusive=netflix"

    def test_no_skip_if_exclusive_and_same_service(self):
        """is_exclusive=True かつ exclusive_service が一致 → スキップしない"""
        post = _make_post(is_exclusive=True, exclusive_service="netflix")
        skip, _ = should_skip(post, "netflix", TODAY)
        assert skip is False

    def test_no_skip_if_not_exclusive(self):
        """is_exclusive=False → スキップしない"""
        post = _make_post(
            service="amazon_prime_video",
            scraping_url="https://www.amazon.co.jp/gp/video/detail/B09ABC",
            is_exclusive=False,
            exclusive_service="netflix",
        )
        skip, _ = should_skip(post, "amazon_prime_video", TODAY)
        assert skip is False

    def test_no_skip_if_exclusive_service_empty(self):
        """is_exclusive=True でも exclusive_service が未設定 → スキップしない"""
        post = _make_post(
            service="amazon_prime_video",
            scraping_url="https://www.amazon.co.jp/gp/video/detail/B09ABC",
            is_exclusive=True,
            exclusive_service="",
        )
        skip, _ = should_skip(post, "amazon_prime_video", TODAY)
        assert skip is False

    def test_exclusive_takes_priority_over_scraping_url_empty(self):
        """独占判定は scraping_url 空チェックより優先される"""
        post = _make_post(is_exclusive=True, exclusive_service="netflix", scraping_url="")
        skip, reason = should_skip(post, "amazon_prime_video", TODAY)
        assert skip is True
        assert "exclusive" in reason

    # --- 5. 言語ミスマッチ ---

    def test_skip_if_en_post_and_unext(self):
        """lang=en の作品 → unext（ja のみ対応）はスキップ"""
        post = _make_post(service="unext", scraping_url="https://video.unext.jp/title/SID123", lang="en")
        skip, reason = should_skip(post, "unext", TODAY)
        assert skip is True
        assert reason == "language_mismatch=en"

    def test_skip_if_en_post_and_dmm_tv(self):
        """lang=en の作品 → dmm_tv（ja のみ対応）はスキップ"""
        post = _make_post(service="dmm_tv", scraping_url="https://tv.dmm.com/vod/detail/?season=123", lang="en")
        skip, reason = should_skip(post, "dmm_tv", TODAY)
        assert skip is True
        assert reason == "language_mismatch=en"

    def test_no_skip_if_lang_not_set(self):
        """lang フィールド未設定のときはスキップしない"""
        post = _make_post(service="unext", scraping_url="https://video.unext.jp/title/SID123", lang=None)
        skip, _ = should_skip(post, "unext", TODAY)
        assert skip is False

    def test_no_skip_en_post_on_netflix(self):
        """lang=en の作品でも netflix（ja + en 対応）はスキップしない"""
        post = _make_post(lang="en")
        skip, _ = should_skip(post, "netflix", TODAY)
        assert skip is False

    def test_no_skip_ja_post_on_netflix(self):
        """lang=ja の作品でも netflix（ja + en 対応）はスキップしない"""
        post = _make_post(lang="ja")
        skip, _ = should_skip(post, "netflix", TODAY)
        assert skip is False

    def test_language_mismatch_priority_after_updated_at(self):
        """言語ミスマッチは updated_at チェックより後（優先順5）"""
        # updated_at が古く（スキップ対象外）、言語ミスマッチ
        old = (TODAY - timedelta(days=60)).isoformat()
        post = _make_post(
            service="unext",
            scraping_url="https://video.unext.jp/title/SID123",
            updated_at=old,
            lang="en",
        )
        skip, reason = should_skip(post, "unext", TODAY)
        assert skip is True
        assert "language_mismatch" in reason


class TestServiceSupportedLanguages:
    """SERVICE_SUPPORTED_LANGUAGES 定数の整合性チェック。"""

    def test_all_services_defined(self):
        """全 SERVICES がマッピングに含まれている"""
        from utils.wordpress import SERVICES
        for svc in SERVICES:
            assert svc in SERVICE_SUPPORTED_LANGUAGES, f"{svc} がマッピングに未定義"

    def test_dmm_tv_ja_only(self):
        assert SERVICE_SUPPORTED_LANGUAGES["dmm_tv"] == frozenset({"ja"})

    def test_apple_tv_ja_and_en(self):
        """apple_tv は ja/en 両対応"""
        assert SERVICE_SUPPORTED_LANGUAGES["apple_tv"] == frozenset({"ja", "en"})

    def test_unext_ja_only(self):
        assert SERVICE_SUPPORTED_LANGUAGES["unext"] == frozenset({"ja"})

    def test_netflix_both(self):
        assert "ja" in SERVICE_SUPPORTED_LANGUAGES["netflix"]
        assert "en" in SERVICE_SUPPORTED_LANGUAGES["netflix"]

    def test_crunchyroll_en_only(self):
        """crunchyroll は en のみ対応"""
        assert SERVICE_SUPPORTED_LANGUAGES["crunchyroll"] == frozenset({"en"})


# ---------------------------------------------------------------------------
# crunchyroll スキップ条件のテスト（lang=en かつ anime カテゴリのみ対象）
# ---------------------------------------------------------------------------

CRUNCHYROLL_URL = "https://www.crunchyroll.com/series/GRDQPM1ZY/attack-on-titan"
ANIME_CATEGORY_ID = 3  # WordPress category: anime (term_id=3)


class TestCrunchyrollSkip:
    """crunchyroll の言語・カテゴリ制約スキップ条件のテスト。"""

    # --- 言語制約 ---

    def test_skip_if_ja_post(self):
        """lang=ja の作品 → crunchyroll（en のみ対応）はスキップ"""
        post = _make_post(
            service="crunchyroll", scraping_url=CRUNCHYROLL_URL,
            lang="ja", categories=[ANIME_CATEGORY_ID],
        )
        skip, reason = should_skip(post, "crunchyroll", TODAY)
        assert skip is True
        assert reason == "language_mismatch=ja"

    def test_no_skip_if_en_post_with_anime(self):
        """lang=en かつ anime カテゴリ → スキップしない"""
        post = _make_post(
            service="crunchyroll", scraping_url=CRUNCHYROLL_URL,
            lang="en", categories=[ANIME_CATEGORY_ID],
        )
        skip, _ = should_skip(post, "crunchyroll", TODAY)
        assert skip is False

    # --- カテゴリ制約 ---

    def test_skip_if_no_anime_category(self):
        """anime カテゴリなし（別カテゴリのみ）→ スキップ"""
        post = _make_post(
            service="crunchyroll", scraping_url=CRUNCHYROLL_URL,
            lang="en", categories=[99],  # anime 以外のカテゴリ
        )
        skip, reason = should_skip(post, "crunchyroll", TODAY)
        assert skip is True
        assert "category_mismatch" in reason

    def test_skip_if_no_categories(self):
        """カテゴリ未設定 → スキップ"""
        post = _make_post(
            service="crunchyroll", scraping_url=CRUNCHYROLL_URL,
            lang="en", categories=[],
        )
        skip, reason = should_skip(post, "crunchyroll", TODAY)
        assert skip is True
        assert "category_mismatch" in reason

    def test_skip_if_categories_field_absent(self):
        """categories フィールド自体がない → スキップ"""
        post = _make_post(
            service="crunchyroll", scraping_url=CRUNCHYROLL_URL,
            lang="en",
            # categories=None → post に "categories" キーを含まない
        )
        skip, reason = should_skip(post, "crunchyroll", TODAY)
        assert skip is True
        assert "category_mismatch" in reason

    def test_no_skip_if_anime_among_multiple_categories(self):
        """複数カテゴリのうち anime を含む → スキップしない"""
        post = _make_post(
            service="crunchyroll", scraping_url=CRUNCHYROLL_URL,
            lang="en", categories=[5, ANIME_CATEGORY_ID, 20],
        )
        skip, _ = should_skip(post, "crunchyroll", TODAY)
        assert skip is False

    # --- 定数の整合性 ---

    def test_required_category_ids_defined(self):
        """SERVICE_REQUIRED_CATEGORY_IDS に crunchyroll が定義されている"""
        assert "crunchyroll" in SERVICE_REQUIRED_CATEGORY_IDS

    def test_required_category_ids_anime(self):
        """crunchyroll の必須カテゴリは anime (term_id=3)"""
        assert SERVICE_REQUIRED_CATEGORY_IDS["crunchyroll"] == frozenset({3})


# ---------------------------------------------------------------------------
# update_cooldown のテスト
# ---------------------------------------------------------------------------

def _make_post_with_services(statuses: dict, release_year: int = 0, count: int = 0) -> dict:
    """複数サービスのステータスを持つ post を生成する。"""
    acf: dict = {
        "release_year": release_year,
        "unavailable_check_count": count,
    }
    for svc, st in statuses.items():
        acf[svc] = {"status": st}
    return {"id": 1, "slug": "test", "acf": acf}


class TestUpdateCooldown:

    # --- 配信中サービスあり → 30日後・カウントリセット ---

    def test_has_streaming_sets_30_days(self):
        post = _make_post_with_services({"netflix": "streaming", "amazon_prime_video": "unavailable"})
        payload: dict = {}
        update_cooldown(post, TODAY, payload)
        expected = (TODAY + timedelta(days=30)).isoformat()
        assert payload["scraping_cooldown_until"] == expected
        assert payload["unavailable_check_count"] == 0

    def test_all_streaming_sets_30_days(self):
        post = _make_post_with_services({"netflix": "streaming", "hulu": "streaming"})
        payload: dict = {}
        update_cooldown(post, TODAY, payload)
        assert payload["unavailable_check_count"] == 0

    # --- 全未配信 → 指数バックオフ ---

    def test_all_unavailable_count0_base30(self):
        """count=0（初回）→ count+1=1 → base_days=30"""
        post = _make_post_with_services({"netflix": "unavailable"}, release_year=2025, count=0)
        payload: dict = {}
        update_cooldown(post, TODAY, payload)
        expected = (TODAY + timedelta(days=30)).isoformat()
        assert payload["scraping_cooldown_until"] == expected
        assert payload["unavailable_check_count"] == 1

    def test_all_unavailable_count1_base60(self):
        post = _make_post_with_services({"netflix": "unavailable"}, release_year=2025, count=1)
        payload: dict = {}
        update_cooldown(post, TODAY, payload)
        expected = (TODAY + timedelta(days=60)).isoformat()
        assert payload["scraping_cooldown_until"] == expected
        assert payload["unavailable_check_count"] == 2

    def test_all_unavailable_count2_base120(self):
        post = _make_post_with_services({"netflix": "unavailable"}, release_year=2025, count=2)
        payload: dict = {}
        update_cooldown(post, TODAY, payload)
        expected = (TODAY + timedelta(days=120)).isoformat()
        assert payload["scraping_cooldown_until"] == expected

    def test_all_unavailable_count3_base240(self):
        post = _make_post_with_services({"netflix": "unavailable"}, release_year=2025, count=3)
        payload: dict = {}
        update_cooldown(post, TODAY, payload)
        expected = (TODAY + timedelta(days=240)).isoformat()
        assert payload["scraping_cooldown_until"] == expected

    def test_all_unavailable_count4_base360(self):
        post = _make_post_with_services({"netflix": "unavailable"}, release_year=2025, count=4)
        payload: dict = {}
        update_cooldown(post, TODAY, payload)
        expected = (TODAY + timedelta(days=360)).isoformat()
        assert payload["scraping_cooldown_until"] == expected

    def test_all_unavailable_count10_caps_at_360(self):
        """count が 4 を超えても base_days は 360 が上限"""
        post = _make_post_with_services({"netflix": "unavailable"}, release_year=2025, count=10)
        payload: dict = {}
        update_cooldown(post, TODAY, payload)
        expected = (TODAY + timedelta(days=360)).isoformat()
        assert payload["scraping_cooldown_until"] == expected
        assert payload["unavailable_check_count"] == 11

    # --- 年齢補正 ---

    def test_age_correction_3_to_5_years(self):
        """3〜5年の作品: base_days と 180 の大きい方"""
        release_year = TODAY.year - 4  # 4年前
        # count=0 → base=30, years_old=4 → max(30, 180)=180
        post = _make_post_with_services({"netflix": "unavailable"}, release_year=release_year, count=0)
        payload: dict = {}
        update_cooldown(post, TODAY, payload)
        expected = (TODAY + timedelta(days=180)).isoformat()
        assert payload["scraping_cooldown_until"] == expected

    def test_age_correction_5_plus_years_fixed_360(self):
        """5年以上の作品: count に関わらず 360 日固定"""
        release_year = TODAY.year - 6
        for count in [0, 1, 3]:
            post = _make_post_with_services({"netflix": "unavailable"}, release_year=release_year, count=count)
            payload: dict = {}
            update_cooldown(post, TODAY, payload)
            expected = (TODAY + timedelta(days=360)).isoformat()
            assert payload["scraping_cooldown_until"] == expected, f"count={count} failed"

    def test_age_correction_new_work_under_3_years(self):
        """3年未満の新作: 補正なし（base_days そのまま）"""
        release_year = TODAY.year - 1  # 1年前
        post = _make_post_with_services({"netflix": "unavailable"}, release_year=release_year, count=0)
        payload: dict = {}
        update_cooldown(post, TODAY, payload)
        expected = (TODAY + timedelta(days=30)).isoformat()
        assert payload["scraping_cooldown_until"] == expected

    def test_age_correction_3_years_base_240_exceeds_180(self):
        """3〜5年 かつ base_days > 180 のとき base_days が使われる"""
        release_year = TODAY.year - 4
        # count=3 → base=240, years_old=4 → max(240, 180)=240
        post = _make_post_with_services({"netflix": "unavailable"}, release_year=release_year, count=3)
        payload: dict = {}
        update_cooldown(post, TODAY, payload)
        expected = (TODAY + timedelta(days=240)).isoformat()
        assert payload["scraping_cooldown_until"] == expected

    # --- エッジケース ---

    def test_release_year_zero_treated_as_new(self):
        """release_year=0（未設定）は新作扱い → 補正なし"""
        post = _make_post_with_services({"netflix": "unavailable"}, release_year=0, count=0)
        payload: dict = {}
        update_cooldown(post, TODAY, payload)
        expected = (TODAY + timedelta(days=30)).isoformat()
        assert payload["scraping_cooldown_until"] == expected

    def test_invalid_count_treated_as_zero(self):
        """unavailable_check_count が不正値のとき count=0 として扱う"""
        post = _make_post_with_services({"netflix": "unavailable"}, count=0)
        post["acf"]["unavailable_check_count"] = "invalid"
        payload: dict = {}
        update_cooldown(post, TODAY, payload)
        # count=0 → count+1=1 → base=30
        expected = (TODAY + timedelta(days=30)).isoformat()
        assert payload["scraping_cooldown_until"] == expected
        assert payload["unavailable_check_count"] == 1

    def test_payload_is_empty_if_no_check_done(self):
        """streaming サービスが1つもなく全サービスが空なら unavailable 扱いになる"""
        post = _make_post_with_services({"netflix": "unavailable"}, count=0)
        payload: dict = {}
        update_cooldown(post, TODAY, payload)
        assert "scraping_cooldown_until" in payload
        assert "unavailable_check_count" in payload


# ---------------------------------------------------------------------------
# get_posts_missing_url の scraping_disabled / cooldown フィルタテスト
# ---------------------------------------------------------------------------

from unittest.mock import MagicMock, patch


def _make_full_post(
    post_id: int,
    slug: str,
    *,
    scraping_disabled: bool = False,
    cooldown_until: str = "",
    missing_services: list | None = None,
) -> dict:
    """get_posts_missing_url 用のテスト投稿を生成する。

    missing_services に含まれるサービスは scraping_url 空、それ以外は設定済み。
    """
    from utils.wordpress import SERVICES
    missing_services = missing_services or []
    acf: dict = {
        "scraping_disabled": scraping_disabled,
        "scraping_cooldown_until": cooldown_until,
    }
    for svc in SERVICES:
        if svc in missing_services:
            acf[svc] = {"scraping_url": ""}
        else:
            acf[svc] = {"scraping_url": f"https://example.com/{svc}/1"}
    return {"id": post_id, "slug": slug, "title": {"rendered": slug}, "acf": acf}


class TestGetPostsMissingUrlFilter:
    """get_posts_missing_url の scraping_disabled / cooldown フィルタのテスト。"""

    def _mock_session_get(self, posts: list[dict]):
        """requests.Session.get をモックして posts を返す。"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.ok = True
        mock_resp.json.return_value = posts
        mock_resp.raise_for_status = MagicMock()
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        return mock_session

    @patch("utils.wordpress.os.environ", {
        "WP_API_URL": "https://example.com/wp-json/wp/v2",
        "WP_USER": "u", "WP_APP_PASSWORD": "p",
    })
    @patch("utils.wordpress._session")
    def test_scraping_disabled_excluded(self, mock_session_fn):
        """scraping_disabled=True の投稿は除外される。"""
        posts = [
            _make_full_post(1, "movie-a", scraping_disabled=True, missing_services=["netflix"]),
            _make_full_post(2, "movie-b", missing_services=["netflix"]),
        ]
        mock_session_fn.return_value = self._mock_session_get(posts)

        from utils.wordpress import get_posts_missing_url
        result = get_posts_missing_url()

        slugs = [p["slug"] for p in result]
        assert "movie-a" not in slugs
        assert "movie-b" in slugs

    @patch("utils.wordpress.os.environ", {
        "WP_API_URL": "https://example.com/wp-json/wp/v2",
        "WP_USER": "u", "WP_APP_PASSWORD": "p",
    })
    @patch("utils.wordpress._session")
    def test_cooldown_active_excluded(self, mock_session_fn):
        """cooldown_until が今日以降の投稿は除外される。"""
        from datetime import date, timedelta
        future = (date.today() + timedelta(days=10)).isoformat()
        past = (date.today() - timedelta(days=1)).isoformat()
        posts = [
            _make_full_post(1, "movie-cooldown", cooldown_until=future, missing_services=["netflix"]),
            _make_full_post(2, "movie-ok", cooldown_until=past, missing_services=["netflix"]),
        ]
        mock_session_fn.return_value = self._mock_session_get(posts)

        from utils.wordpress import get_posts_missing_url
        result = get_posts_missing_url()

        slugs = [p["slug"] for p in result]
        assert "movie-cooldown" not in slugs
        assert "movie-ok" in slugs

    @patch("utils.wordpress.os.environ", {
        "WP_API_URL": "https://example.com/wp-json/wp/v2",
        "WP_USER": "u", "WP_APP_PASSWORD": "p",
    })
    @patch("utils.wordpress._session")
    def test_no_missing_url_excluded(self, mock_session_fn):
        """全サービスの scraping_url が設定済みの投稿は除外される。"""
        posts = [
            _make_full_post(1, "movie-full", missing_services=[]),  # 全サービス設定済み
            _make_full_post(2, "movie-missing", missing_services=["netflix"]),
        ]
        mock_session_fn.return_value = self._mock_session_get(posts)

        from utils.wordpress import get_posts_missing_url
        result = get_posts_missing_url()

        slugs = [p["slug"] for p in result]
        assert "movie-full" not in slugs
        assert "movie-missing" in slugs

    @patch("utils.wordpress.os.environ", {
        "WP_API_URL": "https://example.com/wp-json/wp/v2",
        "WP_USER": "u", "WP_APP_PASSWORD": "p",
    })
    @patch("utils.wordpress._session")
    @pytest.mark.skip(reason="全件再取得のため updated_at フィルターを一時無効化中")
    def test_updated_at_within_one_month_excluded(self, mock_session_fn):
        """updated_at が1か月未満のサービスのみ空の投稿は除外される。"""
        from datetime import date, timedelta
        recent = (date.today() - timedelta(days=10)).isoformat()
        from utils.wordpress import SERVICES
        # netflix のみ scraping_url 空・updated_at が10日前
        acf: dict = {"scraping_disabled": False, "scraping_cooldown_until": ""}
        for svc in SERVICES:
            if svc == "netflix":
                acf[svc] = {"scraping_url": "", "updated_at": recent}
            else:
                acf[svc] = {"scraping_url": f"https://example.com/{svc}/1"}
        posts = [{"id": 1, "slug": "movie-recent", "title": {"rendered": "movie-recent"}, "acf": acf}]
        mock_session_fn.return_value = self._mock_session_get(posts)

        from utils.wordpress import get_posts_missing_url
        result = get_posts_missing_url()

        assert result == []  # 1か月未満なので除外

    @patch("utils.wordpress.os.environ", {
        "WP_API_URL": "https://example.com/wp-json/wp/v2",
        "WP_USER": "u", "WP_APP_PASSWORD": "p",
    })
    @patch("utils.wordpress._session")
    def test_updated_at_over_one_month_included(self, mock_session_fn):
        """updated_at が1か月以上前のサービスがある投稿は対象に含まれる。"""
        from datetime import date, timedelta
        old = (date.today() - timedelta(days=40)).isoformat()
        from utils.wordpress import SERVICES
        acf: dict = {"scraping_disabled": False, "scraping_cooldown_until": ""}
        for svc in SERVICES:
            if svc == "netflix":
                acf[svc] = {"scraping_url": "", "updated_at": old}
            else:
                acf[svc] = {"scraping_url": f"https://example.com/{svc}/1"}
        posts = [{"id": 1, "slug": "movie-old", "title": {"rendered": "movie-old"}, "acf": acf}]
        mock_session_fn.return_value = self._mock_session_get(posts)

        from utils.wordpress import get_posts_missing_url
        result = get_posts_missing_url()

        assert len(result) == 1
        assert result[0]["slug"] == "movie-old"

    @patch("utils.wordpress.os.environ", {
        "WP_API_URL": "https://example.com/wp-json/wp/v2",
        "WP_USER": "u", "WP_APP_PASSWORD": "p",
    })
    @patch("utils.wordpress._session")
    def test_updated_at_empty_included(self, mock_session_fn):
        """updated_at が未設定（初回）のサービスがある投稿は対象に含まれる。"""
        from utils.wordpress import SERVICES
        acf: dict = {"scraping_disabled": False, "scraping_cooldown_until": ""}
        for svc in SERVICES:
            if svc == "netflix":
                acf[svc] = {"scraping_url": "", "updated_at": ""}
            else:
                acf[svc] = {"scraping_url": f"https://example.com/{svc}/1"}
        posts = [{"id": 1, "slug": "movie-new", "title": {"rendered": "movie-new"}, "acf": acf}]
        mock_session_fn.return_value = self._mock_session_get(posts)

        from utils.wordpress import get_posts_missing_url
        result = get_posts_missing_url()

        assert len(result) == 1
