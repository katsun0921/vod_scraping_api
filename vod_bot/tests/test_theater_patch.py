"""theater_patch.py の判定ロジックのユニットテスト。

外部APIへのアクセスは一切行わない（HTTP は requests.Session のスタブで代替）。
"""

from datetime import date

import pytest

import theater_patch
from theater_patch import (
    DEFAULT_SHOWING_WEEKS,
    check_cinema_url,
    default_showing_weeks,
    elapsed_days,
    judge,
    run,
)


TODAY = date(2026, 9, 8)


# ---------------------------------------------------------------------------
# テスト用ヘルパー
# ---------------------------------------------------------------------------

def _item(
    *,
    post_id: int = 1,
    slug: str = "test-movie",
    title: str = "テスト作品",
    lang: str = "ja",
    release_date: str = "",
    cinema_url: str = "",
    category_slugs: list[str] | None = None,
) -> dict:
    """get_theater_showing_posts() が返す形式の記事1件を生成する。"""
    return {
        "id": post_id,
        "slug": slug,
        "title": title,
        "lang": lang,
        "release_date": release_date,
        "cinema_url": cinema_url,
        "category_slugs": category_slugs if category_slugs is not None else ["movie"],
    }


class _FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code

    def close(self) -> None:
        pass


class _FakeSession:
    """requests.Session の最小スタブ。URL → ステータス／例外を返す。"""

    def __init__(self, responses: dict) -> None:
        self.responses = responses
        self.requested: list[str] = []
        self.headers: dict[str, str] = {}

    def get(self, url, timeout=None, allow_redirects=None, stream=None):
        self.requested.append(url)
        value = self.responses.get(url, 200)
        if isinstance(value, Exception):
            raise value
        return _FakeResponse(value)


# ---------------------------------------------------------------------------
# elapsed_days
# ---------------------------------------------------------------------------

class TestElapsedDays:
    def test_公開日からの経過日数を返す(self):
        assert elapsed_days("2026-09-01", TODAY) == 7

    def test_未来の公開日は負数になる(self):
        assert elapsed_days("2026-09-15", TODAY) == -7

    def test_空文字はNone(self):
        assert elapsed_days("", TODAY) is None

    def test_不正な形式はNone(self):
        assert elapsed_days("20260901", TODAY) is None


# ---------------------------------------------------------------------------
# check_cinema_url
# ---------------------------------------------------------------------------

class TestCheckCinemaUrl:
    def test_404はgone(self):
        session = _FakeSession({"https://example.com/a": 404})
        assert check_cinema_url("https://example.com/a", session) == "gone"

    def test_410はgone(self):
        session = _FakeSession({"https://example.com/a": 410})
        assert check_cinema_url("https://example.com/a", session) == "gone"

    def test_200はalive(self):
        session = _FakeSession({"https://example.com/a": 200})
        assert check_cinema_url("https://example.com/a", session) == "alive"

    def test_500は判定不能(self):
        session = _FakeSession({"https://example.com/a": 500})
        assert check_cinema_url("https://example.com/a", session) == "unknown"

    def test_403は判定不能(self):
        # ボット対策で弾かれただけの可能性があるため上映終了にはしない
        session = _FakeSession({"https://example.com/a": 403})
        assert check_cinema_url("https://example.com/a", session) == "unknown"

    def test_接続失敗は判定不能(self):
        import requests
        session = _FakeSession({"https://example.com/a": requests.ConnectionError("boom")})
        assert check_cinema_url("https://example.com/a", session) == "unknown"


# ---------------------------------------------------------------------------
# judge
# ---------------------------------------------------------------------------

class TestJudge:
    def test_劇場URLが404なら上映終了(self):
        # 公開日が最近でも URL が消えていれば終了とみなす
        item = _item(release_date="2026-09-01")
        assert judge(item, TODAY, 8, "gone") == ("ended", "url_gone")

    def test_公開から8週間経過で上映終了(self):
        item = _item(release_date="2026-07-14")  # 56日前
        assert judge(item, TODAY, 8, "alive") == ("ended", "expired")

    def test_8週間未満なら上映中(self):
        item = _item(release_date="2026-07-15")  # 55日前
        assert judge(item, TODAY, 8, "alive") == ("showing", "url_alive")

    def test_しきい値を変更できる(self):
        item = _item(release_date="2026-07-14")  # 56日前
        assert judge(item, TODAY, 12, "alive") == ("showing", "url_alive")

    def test_URL判定不能でも公開日が範囲内なら上映中(self):
        item = _item(release_date="2026-09-01")
        assert judge(item, TODAY, 8, "unknown") == ("showing", "within_period")

    def test_公開日が未来なら上映中(self):
        item = _item(release_date="2026-09-20")
        assert judge(item, TODAY, 8, "unknown") == ("showing", "within_period")

    def test_公開日もURLも無ければ判定不能(self):
        item = _item()
        assert judge(item, TODAY, 8, "unknown") == ("unknown", "no_signal")


# ---------------------------------------------------------------------------
# default_showing_weeks
# ---------------------------------------------------------------------------

class TestDefaultShowingWeeks:
    def test_未設定なら既定値(self, monkeypatch):
        monkeypatch.delenv("THEATER_SHOWING_WEEKS", raising=False)
        assert default_showing_weeks() == DEFAULT_SHOWING_WEEKS

    def test_空文字なら既定値(self, monkeypatch):
        # 未登録の GitHub Secret / vars は空文字で渡る
        monkeypatch.setenv("THEATER_SHOWING_WEEKS", "")
        assert default_showing_weeks() == DEFAULT_SHOWING_WEEKS

    def test_数値を反映する(self, monkeypatch):
        monkeypatch.setenv("THEATER_SHOWING_WEEKS", "12")
        assert default_showing_weeks() == 12

    def test_不正値なら既定値(self, monkeypatch):
        monkeypatch.setenv("THEATER_SHOWING_WEEKS", "abc")
        assert default_showing_weeks() == DEFAULT_SHOWING_WEEKS

    def test_ゼロ以下なら既定値(self, monkeypatch):
        monkeypatch.setenv("THEATER_SHOWING_WEEKS", "0")
        assert default_showing_weeks() == DEFAULT_SHOWING_WEEKS


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

@pytest.fixture
def patched_run(monkeypatch):
    """run() の外部依存（WP取得・PATCH・Slack・待機）を差し替えるフィクスチャ。"""
    state: dict = {"items": [], "patched": [], "notified": [], "responses": {}}

    monkeypatch.setattr(theater_patch, "get_theater_showing_posts", lambda: list(state["items"]))
    monkeypatch.setattr(
        theater_patch,
        "patch_cinema_showing",
        lambda post_id, is_showing: state["patched"].append((post_id, is_showing)),
    )
    monkeypatch.setattr(
        theater_patch,
        "notify_theater_showing_result",
        lambda ended, unknown, weeks: state["notified"].append((ended, unknown, weeks)),
    )
    monkeypatch.setattr(
        theater_patch.requests, "Session", lambda: _FakeSession(state["responses"])
    )
    monkeypatch.setattr(theater_patch.RateLimiter, "wait", lambda self: None)
    return state


class TestRun:
    def test_上映終了を自動でOFFにする(self, patched_run):
        patched_run["items"] = [
            _item(post_id=1, slug="ended-movie", release_date="2026-06-01"),   # 99日前
            _item(post_id=2, slug="showing-movie", release_date="2026-09-01"),  # 7日前
        ]
        result = run(today=TODAY)

        assert patched_run["patched"] == [(1, False)]
        assert result["posts"] == {"total": 2, "showing": 1, "ended": 1, "unknown": 0, "errors": 0}
        assert result["ended"][0]["slug"] == "ended-movie"
        assert result["ended"][0]["reason"] == "expired"
        assert result["ended"][0]["url"] == "https://katsumascore.blog/ja/movie/ended-movie"

    def test_劇場URLが404なら公開直後でもOFFにする(self, patched_run):
        patched_run["items"] = [
            _item(post_id=3, release_date="2026-09-01", cinema_url="https://example.com/gone"),
        ]
        patched_run["responses"] = {"https://example.com/gone": 404}
        result = run(today=TODAY)

        assert patched_run["patched"] == [(3, False)]
        assert result["ended"][0]["reason"] == "url_gone"

    def test_判定不能は据え置く(self, patched_run):
        patched_run["items"] = [_item(post_id=4)]
        result = run(today=TODAY)

        assert patched_run["patched"] == []
        assert result["posts"]["unknown"] == 1
        assert result["unknown"][0]["reason"] == "no_signal"

    def test_dry_runは更新もSlack通知もしない(self, patched_run):
        patched_run["items"] = [_item(post_id=5, release_date="2026-06-01")]
        result = run(today=TODAY, dry_run=True)

        assert patched_run["patched"] == []
        assert patched_run["notified"] == []
        assert result["posts"]["ended"] == 1

    def test_PATCH失敗はerrorsに数える(self, patched_run, monkeypatch):
        def _boom(post_id, is_showing):
            raise RuntimeError("patch failed")

        monkeypatch.setattr(theater_patch, "patch_cinema_showing", _boom)
        patched_run["items"] = [_item(post_id=6, release_date="2026-06-01")]
        result = run(today=TODAY)

        assert result["posts"] == {"total": 1, "showing": 0, "ended": 0, "unknown": 0, "errors": 1}

    def test_一覧取得に失敗したら何も更新しない(self, patched_run, monkeypatch):
        def _boom():
            raise theater_patch.TheaterListError("501 showing_meta_key_missing")

        monkeypatch.setattr(theater_patch, "get_theater_showing_posts", _boom)
        result = run(today=TODAY)

        assert patched_run["patched"] == []
        assert patched_run["notified"] == []
        assert "501" in result["error"]

    def test_slug指定で絞り込む(self, patched_run):
        patched_run["items"] = [
            _item(post_id=1, slug="a", release_date="2026-06-01"),
            _item(post_id=2, slug="b", release_date="2026-06-01"),
        ]
        result = run(today=TODAY, slug="b")

        assert patched_run["patched"] == [(2, False)]
        assert result["posts"]["total"] == 1

    def test_post_idはslugより優先される(self, patched_run):
        patched_run["items"] = [
            _item(post_id=1, slug="a", release_date="2026-06-01"),
            _item(post_id=2, slug="b", release_date="2026-06-01"),
        ]
        run(today=TODAY, slug="b", post_id=1)

        assert patched_run["patched"] == [(1, False)]
