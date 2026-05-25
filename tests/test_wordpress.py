"""utils/wordpress.py の should_skip / update_cooldown ユニットテスト。

外部APIへのアクセスは一切行わない。
"""

from datetime import date, timedelta

import pytest

from utils.wordpress import SERVICE_SUPPORTED_LANGUAGES, should_skip, update_cooldown


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
    languages: list | None = None,
) -> dict:
    """テスト用の最小限の post dict を生成する。"""
    acf: dict = {
        "scraping_disabled": scraping_disabled,
        "scraping_cooldown_until": cooldown_until,
        "release_year": release_year,
        "unavailable_check_count": unavailable_check_count,
        service: {
            "scraping_url": scraping_url,
            "status": status,
            "updated_at": updated_at,
        },
    }
    if languages is not None:
        acf["languages"] = languages
    return {"id": 1, "slug": "test-movie", "acf": acf}


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


    # --- 5. 言語ミスマッチ ---

    def test_skip_if_ja_only_post_and_apple_tv(self):
        """ja のみの作品 → apple_tv（en のみ対応）はスキップ"""
        post = _make_post(service="apple_tv", scraping_url="https://tv.apple.com/jp/movie/test/id1", languages=["ja"])
        skip, reason = should_skip(post, "apple_tv", TODAY)
        assert skip is True
        assert reason == "language_mismatch=ja"

    def test_skip_if_en_only_post_and_dmm_tv(self):
        """en のみの作品 → dmm_tv（ja のみ対応）はスキップ"""
        post = _make_post(service="dmm_tv", scraping_url="https://tv.dmm.com/vod/detail/?season=123", languages=["en"])
        skip, reason = should_skip(post, "dmm_tv", TODAY)
        assert skip is True
        assert reason == "language_mismatch=en"

    def test_no_skip_if_ja_en_both(self):
        """ja + en の両方が設定されていれば全サービスをスキップしない"""
        post = _make_post(service="apple_tv", scraping_url="https://tv.apple.com/jp/movie/test/id1", languages=["ja", "en"])
        skip, _ = should_skip(post, "apple_tv", TODAY)
        assert skip is False

    def test_no_skip_if_languages_empty(self):
        """languages が空リストのときはスキップしない"""
        post = _make_post(service="apple_tv", scraping_url="https://tv.apple.com/jp/movie/test/id1", languages=[])
        skip, _ = should_skip(post, "apple_tv", TODAY)
        assert skip is False

    def test_no_skip_if_languages_not_set(self):
        """languages フィールド未設定（None）のときはスキップしない"""
        post = _make_post(service="apple_tv", scraping_url="https://tv.apple.com/jp/movie/test/id1", languages=None)
        skip, _ = should_skip(post, "apple_tv", TODAY)
        assert skip is False

    def test_no_skip_en_post_on_netflix(self):
        """en のみの作品でも netflix（ja + en 対応）はスキップしない"""
        post = _make_post(languages=["en"])
        skip, _ = should_skip(post, "netflix", TODAY)
        assert skip is False

    def test_no_skip_ja_post_on_netflix(self):
        """ja のみの作品でも netflix（ja + en 対応）はスキップしない"""
        post = _make_post(languages=["ja"])
        skip, _ = should_skip(post, "netflix", TODAY)
        assert skip is False

    def test_language_mismatch_priority_after_updated_at(self):
        """言語ミスマッチは updated_at チェックより後（優先順5）"""
        # updated_at が古く（スキップ対象外）、言語ミスマッチ
        old = (TODAY - timedelta(days=60)).isoformat()
        post = _make_post(
            service="apple_tv",
            scraping_url="https://tv.apple.com/jp/movie/test/id1",
            updated_at=old,
            languages=["ja"],
        )
        skip, reason = should_skip(post, "apple_tv", TODAY)
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

    def test_apple_tv_en_only(self):
        assert SERVICE_SUPPORTED_LANGUAGES["apple_tv"] == frozenset({"en"})

    def test_netflix_both(self):
        assert "ja" in SERVICE_SUPPORTED_LANGUAGES["netflix"]
        assert "en" in SERVICE_SUPPORTED_LANGUAGES["netflix"]


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
