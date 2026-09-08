"""wordpress.py の劇場公開（上映中フラグ）まわりのユニットテスト。

外部APIへのアクセスは一切行わない（_session をスタブに差し替える）。
"""

import pytest

import wordpress
from wordpress import (
    TheaterListError,
    _format_release_date,
    _v1_base_url,
    build_front_url,
    get_theater_showing_posts,
    patch_cinema_showing,
)


# ---------------------------------------------------------------------------
# テスト用ヘルパー
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, payload=None, status_code: int = 200) -> None:
        self._payload = payload if payload is not None else {}
        self.status_code = status_code
        self.ok = status_code < 400
        self.text = ""

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"status={self.status_code}")


class _FakeSession:
    """wordpress.py が使う GET / PATCH だけを持つスタブ。"""

    def __init__(self, get_responses: list, patch_response=None) -> None:
        self._get_responses = list(get_responses)
        self._patch_response = patch_response or _FakeResponse({})
        self.get_calls: list[tuple[str, dict]] = []
        self.patch_calls: list[tuple[str, dict]] = []

    def get(self, url, params=None, timeout=None):
        self.get_calls.append((url, params or {}))
        return self._get_responses.pop(0)

    def patch(self, url, json=None, timeout=None):
        self.patch_calls.append((url, json or {}))
        return self._patch_response


def _theater_list_page(items: list[dict], total_pages: int = 1) -> _FakeResponse:
    return _FakeResponse({"items": items, "meta": {"totalPages": total_pages}})


def _endpoint_item(post_id: int, slug: str, **overrides) -> dict:
    item = {
        "id": post_id,
        "slug": slug,
        "lang": "ja",
        "title": f"作品{post_id}",
        "releaseDate": "2026-08-01",
        "cinemaUrl": "https://example.com/theater",
        "categories": [{"id": 3, "slug": "movie", "name": "映画"}],
    }
    item.update(overrides)
    return item


@pytest.fixture(autouse=True)
def _wp_env(monkeypatch):
    monkeypatch.setenv("WP_API_URL", "https://cms.example.com/wp-json/wp/v2")
    monkeypatch.setenv("WP_USER", "user")
    monkeypatch.setenv("WP_APP_PASSWORD", "pass word")


# ---------------------------------------------------------------------------
# URL / 日付ヘルパー
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_v1ベースURLはwp_v2を差し替える(self):
        assert _v1_base_url() == "https://cms.example.com/wp-json/v1"

    def test_wp_v2で終わらないURLはそのまま_v1を足す(self, monkeypatch):
        monkeypatch.setenv("WP_API_URL", "https://cms.example.com/wp-json")
        assert _v1_base_url() == "https://cms.example.com/wp-json/v1"

    def test_公開日はYmdをY_m_dに整形する(self):
        assert _format_release_date("20260801") == "2026-08-01"

    def test_公開日が空や桁違いなら空文字(self):
        assert _format_release_date("") == ""
        assert _format_release_date("2026") == ""
        assert _format_release_date("abcdefgh") == ""

    def test_フロントURLはカテゴリ有無で切り替わる(self):
        assert build_front_url("john-wick", "ja", "movie") == "https://katsumascore.blog/ja/movie/john-wick"
        assert build_front_url("john-wick", "en", "") == "https://katsumascore.blog/en/john-wick"
        assert build_front_url("", "ja", "movie") == ""


# ---------------------------------------------------------------------------
# get_theater_showing_posts
# ---------------------------------------------------------------------------

class TestGetTheaterShowingPosts:
    def test_ページングして全件取得する(self, monkeypatch):
        session = _FakeSession([
            _theater_list_page([_endpoint_item(1, "a")], total_pages=2),   # ja page1
            _theater_list_page([_endpoint_item(2, "b")], total_pages=2),   # ja page2
            _theater_list_page([], total_pages=0),                          # en page1
        ])
        monkeypatch.setattr(wordpress, "_session", lambda wp_auth=False: session)

        items = get_theater_showing_posts()

        assert [i["id"] for i in items] == [1, 2]
        assert items[0] == {
            "id": 1,
            "slug": "a",
            "title": "作品1",
            "lang": "ja",
            "release_date": "2026-08-01",
            "cinema_url": "https://example.com/theater",
            "category_slugs": ["movie"],
        }
        assert len(session.get_calls) == 3

    def test_言語をまたいだ重複はidで排除する(self, monkeypatch):
        session = _FakeSession([
            _theater_list_page([_endpoint_item(1, "a")]),
            _theater_list_page([_endpoint_item(1, "a")]),
        ])
        monkeypatch.setattr(wordpress, "_session", lambda wp_auth=False: session)

        assert len(get_theater_showing_posts()) == 1

    def test_未入力の公開日と劇場URLは空文字になる(self, monkeypatch):
        session = _FakeSession([
            _theater_list_page([_endpoint_item(1, "a", releaseDate=None, cinemaUrl=None)]),
            _theater_list_page([]),
        ])
        monkeypatch.setattr(wordpress, "_session", lambda wp_auth=False: session)

        item = get_theater_showing_posts()[0]
        assert item["release_date"] == ""
        assert item["cinema_url"] == ""

    def test_404なら投稿の全件走査にフォールバックする(self, monkeypatch):
        session = _FakeSession([_FakeResponse({}, status_code=404)])
        monkeypatch.setattr(wordpress, "_session", lambda wp_auth=False: session)
        monkeypatch.setattr(wordpress, "_scan_theater_showing_posts", lambda: [{"id": 99}])

        assert get_theater_showing_posts() == [{"id": 99}]

    def test_501はエラーにする(self, monkeypatch):
        # postmeta キー不一致。空一覧として扱うと全記事のフラグを外しかねない
        session = _FakeSession([_FakeResponse({}, status_code=501)])
        monkeypatch.setattr(wordpress, "_session", lambda wp_auth=False: session)

        with pytest.raises(TheaterListError):
            get_theater_showing_posts()


# ---------------------------------------------------------------------------
# _scan_theater_showing_posts（フォールバック）
# ---------------------------------------------------------------------------

class TestScanTheaterShowingPosts:
    def test_上映中フラグONの投稿だけ抽出する(self, monkeypatch):
        posts = [
            {
                "id": 1,
                "slug": "showing",
                "title": {"rendered": "上映中の作品"},
                "categories": [3],
                "acf": {
                    "lang": "ja",
                    "release": {"release_date": "20260801"},
                    "cinema_info_filed": {
                        "is_cinema_showing": True,
                        "cinema_list_filed": "https://example.com/theater",
                    },
                },
            },
            {
                "id": 2,
                "slug": "not-showing",
                "title": {"rendered": "対象外"},
                "categories": [3],
                "acf": {"cinema_info_filed": {"is_cinema_showing": False}},
            },
            {"id": 3, "slug": "no-acf", "title": {"rendered": "ACFなし"}, "acf": {}},
        ]
        session = _FakeSession([_FakeResponse(posts)])
        monkeypatch.setattr(wordpress, "_session", lambda wp_auth=False: session)
        monkeypatch.setattr(wordpress, "get_category_slug_map", lambda: {3: "movie"})

        items = wordpress._scan_theater_showing_posts()

        assert len(items) == 1
        assert items[0]["id"] == 1
        assert items[0]["release_date"] == "2026-08-01"
        assert items[0]["category_slugs"] == ["movie"]


# ---------------------------------------------------------------------------
# patch_cinema_showing
# ---------------------------------------------------------------------------

class TestPatchCinemaShowing:
    def test_同グループの他サブフィールドと必須フィールドを保持する(self, monkeypatch):
        existing = {
            "acf": {
                "lang": "ja",
                "review_score": 4,
                "cinema_info_filed": {
                    "is_cinema_watched": True,
                    "cinema_list_filed": "https://example.com/theater",
                    "is_cinema_showing": True,
                },
            }
        }
        session = _FakeSession([_FakeResponse(existing)])
        monkeypatch.setattr(wordpress, "_session", lambda wp_auth=False: session)
        monkeypatch.setattr(wordpress, "_get_acf_schema", lambda: {"lang": {"required": True}})

        patch_cinema_showing(123, False)

        url, payload = session.patch_calls[0]
        assert url.endswith("/posts/123")
        assert payload["acf"]["cinema_info_filed"] == {
            "is_cinema_watched": True,
            "cinema_list_filed": "https://example.com/theater",
            "is_cinema_showing": False,
        }
        # 必須フィールドは引き継ぐ / 必須でないフィールドは送らない
        assert payload["acf"]["lang"] == "ja"
        assert "review_score" not in payload["acf"]

    def test_グループ未設定でもフラグを立てられる(self, monkeypatch):
        session = _FakeSession([_FakeResponse({"acf": {}})])
        monkeypatch.setattr(wordpress, "_session", lambda wp_auth=False: session)
        monkeypatch.setattr(wordpress, "_get_acf_schema", lambda: {})

        patch_cinema_showing(1, False)

        assert session.patch_calls[0][1]["acf"]["cinema_info_filed"] == {"is_cinema_showing": False}
