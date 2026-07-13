"""justwatch.py / wordpress.patch_multi_service_fields のユニットテスト。

外部 API へのアクセスは一切行わない（requests をモックする）。
"""

from datetime import date
from unittest.mock import MagicMock, call, patch

import pytest

from justwatch import (
    _build_queries,
    _extract_urls_from_node,
    _pick_best_node,
    search_urls,
)


# ---------------------------------------------------------------------------
# _build_queries
# ---------------------------------------------------------------------------

class TestBuildQueries:

    def test_title_and_slug_differ(self):
        result = _build_queries("ジョン・ウィック", "john-wick")
        assert result == ["ジョン・ウィック", "john wick"]

    def test_title_same_as_slug_title(self):
        """title と slug 変換後が同じ（大文字小文字問わず）なら重複しない"""
        result = _build_queries("john wick", "john-wick")
        assert result == ["john wick"]

    def test_empty_title_uses_slug(self):
        result = _build_queries("", "john-wick")
        assert result == ["john wick"]

    def test_empty_both(self):
        result = _build_queries("", "")
        assert result == []

    def test_slug_with_multiple_hyphens(self):
        result = _build_queries("", "the-dark-knight-rises")
        assert result == ["the dark knight rises"]


# ---------------------------------------------------------------------------
# _extract_urls_from_node
# ---------------------------------------------------------------------------

def _make_offer(tech_name: str, url: str) -> dict:
    return {"package": {"technicalName": tech_name}, "standardWebURL": url, "monetizationType": "FLATRATE"}


class TestExtractUrlsFromNode:

    def test_known_services(self):
        node = {
            "offers": [
                _make_offer("netflix", "https://www.netflix.com/jp/title/12345"),
                _make_offer("amazonprime", "https://www.amazon.co.jp/gp/video/detail/B09ABC"),
                _make_offer("hulu", "https://www.hulu.jp/watch/99999"),
            ]
        }
        urls = _extract_urls_from_node(node)
        assert urls["netflix"] == "https://www.netflix.com/jp/title/12345"
        assert urls["amazon_prime_video"] == "https://www.amazon.co.jp/gp/video/detail/B09ABC"
        assert urls["hulu"] == "https://www.hulu.jp/watch/99999"

    def test_unknown_service_ignored(self):
        node = {"offers": [_make_offer("zzz", "https://unknown.com/")]}
        urls = _extract_urls_from_node(node)
        assert urls == {}

    def test_duplicate_service_takes_first(self):
        """同一サービスが複数 offer → 最初の URL のみ"""
        node = {
            "offers": [
                _make_offer("netflix", "https://www.netflix.com/jp/title/111"),
                _make_offer("netflix", "https://www.netflix.com/jp/title/222"),
            ]
        }
        urls = _extract_urls_from_node(node)
        assert urls["netflix"] == "https://www.netflix.com/jp/title/111"

    def test_empty_url_ignored(self):
        node = {"offers": [_make_offer("netflix", "")]}
        urls = _extract_urls_from_node(node)
        assert "netflix" not in urls

    def test_no_offers(self):
        assert _extract_urls_from_node({}) == {}
        assert _extract_urls_from_node({"offers": []}) == {}


# ---------------------------------------------------------------------------
# _pick_best_node
# ---------------------------------------------------------------------------

def _make_node(title: str, original_title: str = "") -> dict:
    return {
        "content": {"title": title, "originalTitle": original_title},
        "offers": [],
    }


class TestPickBestNode:

    def test_exact_match_wins(self):
        nodes = [
            _make_node("ジョン・ウィック：パラベラム"),
            _make_node("ジョン・ウィック"),
        ]
        result = _pick_best_node(nodes, "ジョン・ウィック")
        assert result["content"]["title"] == "ジョン・ウィック"

    def test_original_title_exact_match(self):
        nodes = [_make_node("ジョン・ウィック", "John Wick")]
        result = _pick_best_node(nodes, "John Wick")
        assert result["content"]["originalTitle"] == "John Wick"

    def test_prefix_match_fallback(self):
        nodes = [
            _make_node("The Dark Knight Rises"),
            _make_node("The Dark Knight"),
        ]
        result = _pick_best_node(nodes, "The Dark Knight")
        assert result["content"]["title"] == "The Dark Knight"

    def test_returns_first_if_no_match(self):
        nodes = [_make_node("全然違う映画"), _make_node("別の映画")]
        result = _pick_best_node(nodes, "ジョン・ウィック")
        assert result["content"]["title"] == "全然違う映画"

    def test_empty_nodes_returns_none(self):
        assert _pick_best_node([], "ジョン・ウィック") is None


# ---------------------------------------------------------------------------
# search_urls（モックあり）
# ---------------------------------------------------------------------------

def _make_graphql_response(nodes: list[dict]) -> dict:
    return {
        "data": {
            "popularTitles": {
                "edges": [{"node": n} for n in nodes]
            }
        }
    }


class TestSearchUrls:

    @patch("justwatch._post_graphql")
    def test_returns_urls_on_match(self, mock_graphql):
        node = {
            "content": {"title": "ジョン・ウィック", "originalTitle": "John Wick"},
            "offers": [
                _make_offer("netflix", "https://www.netflix.com/jp/title/70126666"),
                _make_offer("amazonprime", "https://www.amazon.co.jp/gp/video/detail/B09ABC"),
            ],
        }
        mock_graphql.return_value = _make_graphql_response([node])

        result = search_urls("ジョン・ウィック", "john-wick")

        assert result["netflix"] == "https://www.netflix.com/jp/title/70126666"
        assert result["amazon_prime_video"] == "https://www.amazon.co.jp/gp/video/detail/B09ABC"

    @patch("justwatch._post_graphql")
    def test_falls_back_to_slug_query(self, mock_graphql):
        """title で結果なし → slug クエリで再試行"""
        node = {
            "content": {"title": "John Wick", "originalTitle": ""},
            "offers": [_make_offer("netflix", "https://www.netflix.com/jp/title/111")],
        }
        # 1回目（title）は空、2回目（slug）はヒット
        mock_graphql.side_effect = [
            _make_graphql_response([]),
            _make_graphql_response([node]),
        ]

        result = search_urls("ジョン・ウィック", "john-wick")

        assert mock_graphql.call_count == 2
        assert "netflix" in result

    @patch("justwatch._post_graphql")
    def test_returns_empty_if_no_results(self, mock_graphql):
        mock_graphql.return_value = _make_graphql_response([])
        result = search_urls("存在しない映画", "no-such-movie")
        assert result == {}

    @patch("justwatch._post_graphql")
    def test_returns_empty_if_node_has_no_urls(self, mock_graphql):
        node = {
            "content": {"title": "ジョン・ウィック", "originalTitle": ""},
            "offers": [],  # offers が空
        }
        mock_graphql.return_value = _make_graphql_response([node])
        result = search_urls("ジョン・ウィック", "john-wick")
        assert result == {}

    @patch("justwatch._post_graphql")
    def test_raises_runtime_error_propagated(self, mock_graphql):
        mock_graphql.side_effect = RuntimeError("HTTP 429")
        with pytest.raises(RuntimeError, match="HTTP 429"):
            search_urls("ジョン・ウィック", "john-wick")

    @patch("justwatch._post_graphql")
    def test_all_mapped_services_extracted(self, mock_graphql):
        """マッピング済みサービスが同時に取れるケース（DMM TV / YouTube は JustWatch JP 未対応）"""
        node = {
            "content": {"title": "Test Movie", "originalTitle": ""},
            "offers": [
                _make_offer("amazonprime", "https://www.amazon.co.jp/gp/video/detail/B001"),
                _make_offer("netflix", "https://www.netflix.com/jp/title/1"),
                _make_offer("hulu", "https://www.hulu.jp/watch/1"),
                _make_offer("unext", "https://video.unext.jp/title/SID1"),
                _make_offer("disneyplus", "https://www.disneyplus.com/ja-jp/movies/test/1"),
                _make_offer("appletvplus", "https://tv.apple.com/jp/movie/test/id1"),
            ],
        }
        mock_graphql.return_value = _make_graphql_response([node])
        result = search_urls("Test Movie", "test-movie")
        assert len(result) == 6
        assert "amazon_prime_video" in result
        assert "netflix" in result
        assert "hulu" in result
        assert "unext" in result
        assert "disney_plus" in result
        assert "apple_tv" in result


# ---------------------------------------------------------------------------
# patch_multi_service_fields
# ---------------------------------------------------------------------------

class TestPatchMultiServiceFields:

    def _make_session_mock(self, existing_acf: dict):
        """GET → existing_acf を返す、PATCH → 200 を返すセッションモック。"""
        get_resp = MagicMock()
        get_resp.ok = True
        get_resp.json.return_value = {"acf": existing_acf}

        patch_resp = MagicMock()
        patch_resp.ok = True
        patch_resp.raise_for_status = MagicMock()

        session = MagicMock()
        session.get.return_value = get_resp
        session.patch.return_value = patch_resp
        get_resp.raise_for_status = MagicMock()
        return session

    @patch("wordpress.os.environ", {"WP_API_URL": "https://example.com/wp-json/wp/v2", "WP_USER": "u", "WP_APP_PASSWORD": "p"})
    @patch("wordpress._get_acf_schema", return_value={})
    @patch("wordpress._session")
    def test_single_patch_for_multiple_services(self, mock_session_fn, mock_schema):
        """複数サービスを渡しても PATCH は 1回だけ呼ばれる。"""
        from wordpress import patch_multi_service_fields

        existing = {
            "netflix": {"scraping_url": "", "status": ""},
            "hulu":    {"scraping_url": "", "status": ""},
        }
        session = self._make_session_mock(existing)
        mock_session_fn.return_value = session

        patch_multi_service_fields(
            post_id=1,
            service_fields={
                "netflix": {"scraping_url": "https://www.netflix.com/jp/title/1"},
                "hulu":    {"status": "unavailable", "updated_at": "2026-06-01 00:00:00"},
            },
        )

        assert session.get.call_count == 1
        assert session.patch.call_count == 1

        patched_acf = session.patch.call_args[1]["json"]["acf"]
        assert patched_acf["netflix"]["scraping_url"] == "https://www.netflix.com/jp/title/1"
        assert patched_acf["hulu"]["status"] == "unavailable"

    @patch("wordpress.os.environ", {"WP_API_URL": "https://example.com/wp-json/wp/v2", "WP_USER": "u", "WP_APP_PASSWORD": "p"})
    @patch("wordpress._get_acf_schema", return_value={})
    @patch("wordpress._session")
    def test_existing_fields_preserved(self, mock_session_fn, mock_schema):
        """既存の scraping_url など、更新対象外フィールドは保持される。"""
        from wordpress import patch_multi_service_fields

        existing = {
            "netflix": {
                "scraping_url": "https://www.netflix.com/jp/title/999",
                "status": "streaming",
                "updated_at": "2026-01-01 00:00:00",
            },
        }
        session = self._make_session_mock(existing)
        mock_session_fn.return_value = session

        patch_multi_service_fields(
            post_id=1,
            service_fields={"netflix": {"status": "unavailable"}},
        )

        patched = session.patch.call_args[1]["json"]["acf"]["netflix"]
        # status は上書きされる
        assert patched["status"] == "unavailable"
        # scraping_url と updated_at は既存値を保持
        assert patched["scraping_url"] == "https://www.netflix.com/jp/title/999"
        assert patched["updated_at"] == "2026-01-01 00:00:00"

    @patch("wordpress._get_acf_schema", return_value={})
    @patch("wordpress._session")
    def test_empty_service_fields_does_nothing(self, mock_session_fn, mock_schema):
        """service_fields が空なら GET も PATCH も呼ばれない。"""
        from wordpress import patch_multi_service_fields

        session = MagicMock()
        mock_session_fn.return_value = session

        patch_multi_service_fields(post_id=1, service_fields={})

        session.get.assert_not_called()
        session.patch.assert_not_called()


