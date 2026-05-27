"""utils/justwatch.py / utils/wordpress.patch_multi_service_fields /
justwatch_batch のユニットテスト。

外部 API へのアクセスは一切行わない（requests をモックする）。
"""

from datetime import date
from unittest.mock import MagicMock, call, patch

import pytest

from utils.justwatch import (
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
                _make_offer("nfx", "https://www.netflix.com/jp/title/12345"),
                _make_offer("amp", "https://www.amazon.co.jp/gp/video/detail/B09ABC"),
                _make_offer("hlu", "https://www.hulu.jp/watch/99999"),
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
                _make_offer("nfx", "https://www.netflix.com/jp/title/111"),
                _make_offer("nfx", "https://www.netflix.com/jp/title/222"),
            ]
        }
        urls = _extract_urls_from_node(node)
        assert urls["netflix"] == "https://www.netflix.com/jp/title/111"

    def test_empty_url_ignored(self):
        node = {"offers": [_make_offer("nfx", "")]}
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

    @patch("utils.justwatch._post_graphql")
    def test_returns_urls_on_match(self, mock_graphql):
        node = {
            "content": {"title": "ジョン・ウィック", "originalTitle": "John Wick"},
            "offers": [
                _make_offer("nfx", "https://www.netflix.com/jp/title/70126666"),
                _make_offer("amp", "https://www.amazon.co.jp/gp/video/detail/B09ABC"),
            ],
        }
        mock_graphql.return_value = _make_graphql_response([node])

        result = search_urls("ジョン・ウィック", "john-wick")

        assert result["netflix"] == "https://www.netflix.com/jp/title/70126666"
        assert result["amazon_prime_video"] == "https://www.amazon.co.jp/gp/video/detail/B09ABC"

    @patch("utils.justwatch._post_graphql")
    def test_falls_back_to_slug_query(self, mock_graphql):
        """title で結果なし → slug クエリで再試行"""
        node = {
            "content": {"title": "John Wick", "originalTitle": ""},
            "offers": [_make_offer("nfx", "https://www.netflix.com/jp/title/111")],
        }
        # 1回目（title）は空、2回目（slug）はヒット
        mock_graphql.side_effect = [
            _make_graphql_response([]),
            _make_graphql_response([node]),
        ]

        result = search_urls("ジョン・ウィック", "john-wick")

        assert mock_graphql.call_count == 2
        assert "netflix" in result

    @patch("utils.justwatch._post_graphql")
    def test_returns_empty_if_no_results(self, mock_graphql):
        mock_graphql.return_value = _make_graphql_response([])
        result = search_urls("存在しない映画", "no-such-movie")
        assert result == {}

    @patch("utils.justwatch._post_graphql")
    def test_returns_empty_if_node_has_no_urls(self, mock_graphql):
        node = {
            "content": {"title": "ジョン・ウィック", "originalTitle": ""},
            "offers": [],  # offers が空
        }
        mock_graphql.return_value = _make_graphql_response([node])
        result = search_urls("ジョン・ウィック", "john-wick")
        assert result == {}

    @patch("utils.justwatch._post_graphql")
    def test_raises_runtime_error_propagated(self, mock_graphql):
        mock_graphql.side_effect = RuntimeError("HTTP 429")
        with pytest.raises(RuntimeError, match="HTTP 429"):
            search_urls("ジョン・ウィック", "john-wick")

    @patch("utils.justwatch._post_graphql")
    def test_all_8_services_extracted(self, mock_graphql):
        """全8サービスが同時に取れるケース"""
        node = {
            "content": {"title": "Test Movie", "originalTitle": ""},
            "offers": [
                _make_offer("amp", "https://www.amazon.co.jp/gp/video/detail/B001"),
                _make_offer("nfx", "https://www.netflix.com/jp/title/1"),
                _make_offer("hlu", "https://www.hulu.jp/watch/1"),
                _make_offer("unx", "https://video.unext.jp/title/SID1"),
                _make_offer("dnp", "https://www.disneyplus.com/ja-jp/movies/test/1"),
                _make_offer("dmt", "https://tv.dmm.com/vod/detail/?season=1"),
                _make_offer("atp", "https://tv.apple.com/jp/movie/test/id1"),
                _make_offer("yte", "https://www.youtube.com/watch?v=abc"),
            ],
        }
        mock_graphql.return_value = _make_graphql_response([node])
        result = search_urls("Test Movie", "test-movie")
        assert len(result) == 8
        assert "amazon_prime_video" in result
        assert "netflix" in result
        assert "hulu" in result
        assert "unext" in result
        assert "disney_plus" in result
        assert "dmm_tv" in result
        assert "apple_tv" in result
        assert "youtube" in result


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

    @patch("utils.wordpress.os.environ", {"WP_API_URL": "https://example.com/wp-json/wp/v2", "WP_USER": "u", "WP_APP_PASSWORD": "p"})
    @patch("utils.wordpress._get_acf_schema", return_value={})
    @patch("utils.wordpress._session")
    def test_single_patch_for_multiple_services(self, mock_session_fn, mock_schema):
        """複数サービスを渡しても PATCH は 1回だけ呼ばれる。"""
        from utils.wordpress import patch_multi_service_fields

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

    @patch("utils.wordpress.os.environ", {"WP_API_URL": "https://example.com/wp-json/wp/v2", "WP_USER": "u", "WP_APP_PASSWORD": "p"})
    @patch("utils.wordpress._get_acf_schema", return_value={})
    @patch("utils.wordpress._session")
    def test_existing_fields_preserved(self, mock_session_fn, mock_schema):
        """既存の scraping_url など、更新対象外フィールドは保持される。"""
        from utils.wordpress import patch_multi_service_fields

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

    @patch("utils.wordpress._get_acf_schema", return_value={})
    @patch("utils.wordpress._session")
    def test_empty_service_fields_does_nothing(self, mock_session_fn, mock_schema):
        """service_fields が空なら GET も PATCH も呼ばれない。"""
        from utils.wordpress import patch_multi_service_fields

        session = MagicMock()
        mock_session_fn.return_value = session

        patch_multi_service_fields(post_id=1, service_fields={})

        session.get.assert_not_called()
        session.patch.assert_not_called()


# ---------------------------------------------------------------------------
# justwatch_batch.run の統合テスト
# ---------------------------------------------------------------------------

class TestJustwatchBatchRun:

    def _make_post(self, post_id: int, slug: str, title: str, missing: list[str]) -> dict:
        """missing に含まれるサービスは scraping_url 空、それ以外は設定済みにする。"""
        from utils.wordpress import SERVICES
        acf: dict = {}
        for svc in SERVICES:
            if svc in missing:
                acf[svc] = {"scraping_url": ""}
            else:
                acf[svc] = {"scraping_url": f"https://example.com/{svc}/1"}
        return {"id": post_id, "slug": slug, "title": {"rendered": title}, "acf": acf}

    @patch("justwatch_batch.notify_justwatch_post_result")
    @patch("justwatch_batch.notify_justwatch_summary")
    @patch("justwatch_batch.notify_justwatch_start")
    @patch("justwatch_batch.time.sleep")
    @patch("justwatch_batch.patch_multi_service_fields")
    @patch("justwatch_batch.search_urls")
    @patch("justwatch_batch.get_posts_missing_url")
    def test_registered_and_unavailable_counted(
        self, mock_get_posts, mock_search, mock_patch, mock_sleep,
        mock_start, mock_summary, mock_post_result,
    ):
        """URL あり → registered / URL なし → unavailable がそれぞれカウントされる。"""
        mock_get_posts.return_value = [
            self._make_post(1, "john-wick", "ジョン・ウィック", ["netflix", "hulu"]),
        ]
        mock_search.return_value = {
            "netflix": "https://www.netflix.com/jp/title/1",
            # hulu は見つからない
        }

        import justwatch_batch
        result = justwatch_batch.run()

        assert result["registered"] == 1
        assert result["unavailable"] == 1
        assert result["errors"] == 0
        # PATCH は 1回だけ
        mock_patch.assert_called_once()
        patch_call_args = mock_patch.call_args[0]
        assert patch_call_args[0] == 1  # post_id
        service_fields = patch_call_args[1]
        assert "scraping_url" in service_fields["netflix"]
        assert service_fields["hulu"]["status"] == "unavailable"

    @patch("justwatch_batch.notify_justwatch_post_result")
    @patch("justwatch_batch.notify_justwatch_summary")
    @patch("justwatch_batch.notify_justwatch_start")
    @patch("justwatch_batch.time.sleep")
    @patch("justwatch_batch.patch_multi_service_fields")
    @patch("justwatch_batch.search_urls")
    @patch("justwatch_batch.get_posts_missing_url")
    def test_jw_error_skips_post(
        self, mock_get_posts, mock_search, mock_patch, mock_sleep,
        mock_start, mock_summary, mock_post_result,
    ):
        """JustWatch エラー時は PATCH せずエラーカウントを増やす。"""
        mock_get_posts.return_value = [
            self._make_post(1, "john-wick", "ジョン・ウィック", ["netflix"]),
        ]
        mock_search.side_effect = RuntimeError("HTTP 429")

        import justwatch_batch
        result = justwatch_batch.run()

        assert result["errors"] == 1
        mock_patch.assert_not_called()

    @patch("justwatch_batch.notify_justwatch_post_result")
    @patch("justwatch_batch.notify_justwatch_summary")
    @patch("justwatch_batch.notify_justwatch_start")
    @patch("justwatch_batch.time.sleep")
    @patch("justwatch_batch.patch_multi_service_fields")
    @patch("justwatch_batch.search_urls")
    @patch("justwatch_batch.get_posts_missing_url")
    def test_dry_run_skips_all(
        self, mock_get_posts, mock_search, mock_patch, mock_sleep,
        mock_start, mock_summary, mock_post_result,
    ):
        """dry_run=True では search も PATCH も呼ばれない。"""
        mock_get_posts.return_value = [
            self._make_post(1, "john-wick", "ジョン・ウィック", ["netflix"]),
        ]

        import justwatch_batch
        result = justwatch_batch.run(dry_run=True)

        assert result["skipped"] == 1
        mock_search.assert_not_called()
        mock_patch.assert_not_called()

    @patch("justwatch_batch.notify_justwatch_post_result")
    @patch("justwatch_batch.notify_justwatch_summary")
    @patch("justwatch_batch.notify_justwatch_start")
    @patch("justwatch_batch.time.sleep")
    @patch("justwatch_batch.patch_multi_service_fields")
    @patch("justwatch_batch.search_urls")
    @patch("justwatch_batch.get_posts_missing_url")
    def test_patch_error_rolls_back_count(
        self, mock_get_posts, mock_search, mock_patch, mock_sleep,
        mock_start, mock_summary, mock_post_result,
    ):
        """PATCH 失敗時は registered / unavailable を差し引いてエラーカウントを増やす。"""
        mock_get_posts.return_value = [
            self._make_post(1, "john-wick", "ジョン・ウィック", ["netflix", "hulu"]),
        ]
        mock_search.return_value = {
            "netflix": "https://www.netflix.com/jp/title/1",
        }
        mock_patch.side_effect = Exception("connection error")

        import justwatch_batch
        result = justwatch_batch.run()

        assert result["errors"] == 1
        assert result["registered"] == 0
        assert result["unavailable"] == 0


class TestJustwatchBatchSlackNotify:
    """Slack 通知が正しいタイミング・内容で呼ばれることを確認する。"""

    def _make_post(self, post_id: int, slug: str, title: str, missing: list[str]) -> dict:
        from utils.wordpress import SERVICES
        acf: dict = {}
        for svc in SERVICES:
            if svc in missing:
                acf[svc] = {"scraping_url": ""}
            else:
                acf[svc] = {"scraping_url": f"https://example.com/{svc}/1"}
        return {"id": post_id, "slug": slug, "title": {"rendered": title}, "acf": acf}

    @patch("justwatch_batch.notify_justwatch_post_result")
    @patch("justwatch_batch.notify_justwatch_summary")
    @patch("justwatch_batch.notify_justwatch_start")
    @patch("justwatch_batch.time.sleep")
    @patch("justwatch_batch.patch_multi_service_fields")
    @patch("justwatch_batch.search_urls")
    @patch("justwatch_batch.get_posts_missing_url")
    def test_start_and_summary_called(
        self, mock_get_posts, mock_search, mock_patch, mock_sleep,
        mock_start, mock_summary, mock_post_result,
    ):
        """dry_run=False では開始・完了通知が 1回ずつ呼ばれる。"""
        mock_get_posts.return_value = [
            self._make_post(1, "john-wick", "ジョン・ウィック", ["netflix"]),
        ]
        mock_search.return_value = {}

        import justwatch_batch
        justwatch_batch.run()

        mock_start.assert_called_once_with(total=1, limit=None)
        mock_summary.assert_called_once()
        summary_arg = mock_summary.call_args[0][0]
        assert "registered" in summary_arg
        assert "unavailable" in summary_arg

    @patch("justwatch_batch.notify_justwatch_post_result")
    @patch("justwatch_batch.notify_justwatch_summary")
    @patch("justwatch_batch.notify_justwatch_start")
    @patch("justwatch_batch.time.sleep")
    @patch("justwatch_batch.patch_multi_service_fields")
    @patch("justwatch_batch.search_urls")
    @patch("justwatch_batch.get_posts_missing_url")
    def test_dry_run_no_slack(
        self, mock_get_posts, mock_search, mock_patch, mock_sleep,
        mock_start, mock_summary, mock_post_result,
    ):
        """dry_run=True では Slack 通知が一切呼ばれない。"""
        mock_get_posts.return_value = [
            self._make_post(1, "john-wick", "ジョン・ウィック", ["netflix"]),
        ]

        import justwatch_batch
        justwatch_batch.run(dry_run=True)

        mock_start.assert_not_called()
        mock_summary.assert_not_called()
        mock_post_result.assert_not_called()

    @patch("justwatch_batch.notify_justwatch_post_result")
    @patch("justwatch_batch.notify_justwatch_summary")
    @patch("justwatch_batch.notify_justwatch_start")
    @patch("justwatch_batch.time.sleep")
    @patch("justwatch_batch.patch_multi_service_fields")
    @patch("justwatch_batch.search_urls")
    @patch("justwatch_batch.get_posts_missing_url")
    def test_post_result_registered(
        self, mock_get_posts, mock_search, mock_patch, mock_sleep,
        mock_start, mock_summary, mock_post_result,
    ):
        """URL 登録時は registered に URL が渡される。"""
        mock_get_posts.return_value = [
            self._make_post(1, "john-wick", "ジョン・ウィック", ["netflix", "hulu"]),
        ]
        mock_search.return_value = {
            "netflix": "https://www.netflix.com/jp/title/1",
        }

        import justwatch_batch
        justwatch_batch.run()

        mock_post_result.assert_called_once()
        kwargs = mock_post_result.call_args[1] if mock_post_result.call_args[1] else {}
        args = mock_post_result.call_args[0]
        # キーワード引数 or 位置引数で呼ばれる
        call_kwargs = mock_post_result.call_args.kwargs if hasattr(mock_post_result.call_args, 'kwargs') else {}
        # positional fallback
        if not call_kwargs:
            call_kwargs = {
                "title": args[0], "slug": args[1],
                "registered": args[2], "unavailable": args[3], "error": args[4],
            }
        assert call_kwargs["registered"] == {"netflix": "https://www.netflix.com/jp/title/1"}
        assert "hulu" in call_kwargs["unavailable"]
        assert call_kwargs["error"] is False

    @patch("justwatch_batch.notify_justwatch_post_result")
    @patch("justwatch_batch.notify_justwatch_summary")
    @patch("justwatch_batch.notify_justwatch_start")
    @patch("justwatch_batch.time.sleep")
    @patch("justwatch_batch.patch_multi_service_fields")
    @patch("justwatch_batch.search_urls")
    @patch("justwatch_batch.get_posts_missing_url")
    def test_post_result_error_flag(
        self, mock_get_posts, mock_search, mock_patch, mock_sleep,
        mock_start, mock_summary, mock_post_result,
    ):
        """PATCH 失敗時は error=True で通知される。"""
        mock_get_posts.return_value = [
            self._make_post(1, "john-wick", "ジョン・ウィック", ["netflix"]),
        ]
        mock_search.return_value = {}
        mock_patch.side_effect = Exception("connection error")

        import justwatch_batch
        justwatch_batch.run()

        mock_post_result.assert_called_once()
        call_kwargs = mock_post_result.call_args.kwargs
        assert call_kwargs["error"] is True


class TestJustwatchAutoDisable:
    """release_year 10年超・全サービス URL 未発見で scraping_disabled=true になるテスト。"""

    def _make_post(self, post_id, slug, title, missing, release_year=0):
        from utils.wordpress import SERVICES
        acf = {"release_year": release_year}
        for svc in SERVICES:
            if svc in missing:
                acf[svc] = {"scraping_url": ""}
            else:
                acf[svc] = {"scraping_url": f"https://example.com/{svc}/1"}
        return {"id": post_id, "slug": slug, "title": {"rendered": title}, "acf": acf}

    @patch("justwatch_batch.notify_justwatch_post_result")
    @patch("justwatch_batch.notify_justwatch_summary")
    @patch("justwatch_batch.notify_justwatch_start")
    @patch("justwatch_batch.time.sleep")
    @patch("justwatch_batch.patch_multi_service_fields")
    @patch("justwatch_batch.search_urls")
    @patch("justwatch_batch.get_posts_missing_url")
    def test_auto_disable_when_old_and_no_urls(
        self, mock_get_posts, mock_search, mock_patch, mock_sleep,
        mock_start, mock_summary, mock_post_result,
    ):
        """10年超・全サービス URL 未発見 → scraping_disabled=true で PATCH される。"""
        old_year = date.today().year - 11
        mock_get_posts.return_value = [
            self._make_post(1, "old-movie", "古い映画", ["netflix", "hulu"], release_year=old_year),
        ]
        mock_search.return_value = {}

        import justwatch_batch
        result = justwatch_batch.run()

        assert result["disabled"] == 1
        call_kwargs = mock_patch.call_args.kwargs
        assert call_kwargs.get("top_level_fields") == {"scraping_disabled": True}

    @patch("justwatch_batch.notify_justwatch_post_result")
    @patch("justwatch_batch.notify_justwatch_summary")
    @patch("justwatch_batch.notify_justwatch_start")
    @patch("justwatch_batch.time.sleep")
    @patch("justwatch_batch.patch_multi_service_fields")
    @patch("justwatch_batch.search_urls")
    @patch("justwatch_batch.get_posts_missing_url")
    def test_no_auto_disable_when_recent(
        self, mock_get_posts, mock_search, mock_patch, mock_sleep,
        mock_start, mock_summary, mock_post_result,
    ):
        """10年未満の作品は URL 未発見でも scraping_disabled にならない。"""
        recent_year = date.today().year - 5
        mock_get_posts.return_value = [
            self._make_post(1, "recent-movie", "新しい映画", ["netflix"], release_year=recent_year),
        ]
        mock_search.return_value = {}

        import justwatch_batch
        result = justwatch_batch.run()

        assert result["disabled"] == 0
        call_kwargs = mock_patch.call_args.kwargs
        assert call_kwargs.get("top_level_fields") is None

    @patch("justwatch_batch.notify_justwatch_post_result")
    @patch("justwatch_batch.notify_justwatch_summary")
    @patch("justwatch_batch.notify_justwatch_start")
    @patch("justwatch_batch.time.sleep")
    @patch("justwatch_batch.patch_multi_service_fields")
    @patch("justwatch_batch.search_urls")
    @patch("justwatch_batch.get_posts_missing_url")
    def test_no_auto_disable_when_url_registered(
        self, mock_get_posts, mock_search, mock_patch, mock_sleep,
        mock_start, mock_summary, mock_post_result,
    ):
        """10年超でも URL が1件でも登録されれば scraping_disabled にならない。"""
        old_year = date.today().year - 11
        mock_get_posts.return_value = [
            self._make_post(1, "old-movie", "古い映画", ["netflix", "hulu"], release_year=old_year),
        ]
        mock_search.return_value = {"netflix": "https://www.netflix.com/jp/title/1"}

        import justwatch_batch
        result = justwatch_batch.run()

        assert result["disabled"] == 0
        call_kwargs = mock_patch.call_args.kwargs
        assert call_kwargs.get("top_level_fields") is None
