"""utils/new_titles.py のユニットテスト。

外部 API へのアクセスは一切行わない（requests をモックする）。
"""

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

from utils.new_titles import (
    NewTitle,
    ServiceOffer,
    _parse_date,
    _parse_node,
    fetch_new_titles,
    group_by_service,
    to_report,
)


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _make_offer(tech: str, url: str, avail_from: str = "", avail_to: str = "") -> dict:
    return {
        "package": {"technicalName": tech, "clearName": tech},
        "standardWebURL": url,
        "monetizationType": "FLATRATE",
        "availableFrom": avail_from or None,
        "availableTo": avail_to or None,
    }


def _make_node(
    node_id: str,
    title: str,
    original_title: str = "",
    full_path: str = "/jp/movie/test",
    genres: list[str] | None = None,
    offers: list[dict] | None = None,
) -> dict:
    return {
        "id": node_id,
        "content": {
            "title": title,
            "originalTitle": original_title,
            "fullPath": full_path,
            "posterUrl": f"https://images.justwatch.com/{node_id}.jpg",
            "genres": [{"shortName": g} for g in (genres or [])],
        },
        "offers": offers or [],
    }


def _make_graphql_response(nodes: list[dict], has_next: bool = False, end_cursor: str = "") -> dict:
    return {
        "data": {
            "popularTitles": {
                "pageInfo": {"endCursor": end_cursor, "hasNextPage": has_next},
                "edges": [{"node": n} for n in nodes],
            }
        }
    }


TODAY = date(2026, 6, 26)


# ---------------------------------------------------------------------------
# _parse_date
# ---------------------------------------------------------------------------

class TestParseDate:
    def test_iso_date(self):
        assert _parse_date("2026-06-20") == date(2026, 6, 20)

    def test_iso_datetime(self):
        assert _parse_date("2026-06-20T12:00:00") == date(2026, 6, 20)

    def test_none_returns_none(self):
        assert _parse_date(None) is None

    def test_empty_returns_none(self):
        assert _parse_date("") is None

    def test_invalid_returns_none(self):
        assert _parse_date("not-a-date") is None


# ---------------------------------------------------------------------------
# _parse_node
# ---------------------------------------------------------------------------

class TestParseNode:
    def test_netflix_node(self):
        node = _make_node(
            "123",
            "ジョン・ウィック",
            "John Wick",
            genres=["Action", "Thriller"],
            offers=[
                _make_offer("netflix", "https://www.netflix.com/jp/title/12345", "2026-06-20"),
            ],
        )
        result = _parse_node(node, frozenset({"netflix"}))
        assert result is not None
        assert result.title == "ジョン・ウィック"
        assert result.original_title == "John Wick"
        assert len(result.offers) == 1
        assert result.offers[0].service == "netflix"
        assert result.offers[0].available_from == date(2026, 6, 20)

    def test_unknown_service_filtered(self):
        node = _make_node(
            "456",
            "Test Movie",
            offers=[_make_offer("hulu", "https://www.hulu.jp/watch/999", "2026-06-20")],
        )
        result = _parse_node(node, frozenset({"netflix", "unext"}))
        assert result is None

    def test_empty_url_skipped(self):
        node = _make_node(
            "789",
            "Test Movie",
            offers=[_make_offer("netflix", "", "2026-06-20")],
        )
        result = _parse_node(node, frozenset({"netflix"}))
        assert result is None

    def test_amazon_tech_names_all_mapped(self):
        """amazonprime / amazon / amazonprimevideowithads → amazon_prime_video"""
        for tech in ("amazonprime", "amazon", "amazonprimevideowithads"):
            node = _make_node(
                "100",
                "Test",
                offers=[_make_offer(tech, f"https://www.amazon.co.jp/gp/video/detail/B001")],
            )
            result = _parse_node(node, frozenset({"amazon_prime_video"}))
            assert result is not None, f"{tech} should map to amazon_prime_video"
            assert result.offers[0].service == "amazon_prime_video"

    def test_multiple_services_in_one_node(self):
        node = _make_node(
            "200",
            "Multi Service Movie",
            offers=[
                _make_offer("netflix", "https://www.netflix.com/jp/title/1", "2026-06-15"),
                _make_offer("unext", "https://video.unext.jp/title/SID1", "2026-06-18"),
            ],
        )
        result = _parse_node(node, frozenset({"netflix", "unext"}))
        assert result is not None
        assert len(result.offers) == 2
        assert set(result.services) == {"netflix", "unext"}

    def test_no_title_returns_none(self):
        node = _make_node("300", "", offers=[_make_offer("netflix", "https://netflix.com/1", "2026-06-20")])
        result = _parse_node(node, frozenset({"netflix"}))
        assert result is None


# ---------------------------------------------------------------------------
# NewTitle ヘルパーメソッド
# ---------------------------------------------------------------------------

class TestNewTitle:
    def _make_title(self, offers: list[ServiceOffer]) -> NewTitle:
        return NewTitle(
            jw_id="1",
            title="Test",
            original_title="Test",
            full_path="/jp/movie/test",
            poster_url="",
            offers=offers,
        )

    def test_earliest_available_from(self):
        offers = [
            ServiceOffer("netflix", "https://n.com", "FLATRATE", date(2026, 6, 10), None),
            ServiceOffer("unext", "https://u.com", "FLATRATE", date(2026, 6, 5), None),
        ]
        t = self._make_title(offers)
        assert t.earliest_available_from() == date(2026, 6, 5)

    def test_earliest_available_from_with_none(self):
        offers = [
            ServiceOffer("netflix", "https://n.com", "FLATRATE", None, None),
            ServiceOffer("unext", "https://u.com", "FLATRATE", date(2026, 6, 15), None),
        ]
        t = self._make_title(offers)
        assert t.earliest_available_from() == date(2026, 6, 15)

    def test_earliest_available_from_all_none(self):
        offers = [
            ServiceOffer("netflix", "https://n.com", "FLATRATE", None, None),
        ]
        t = self._make_title(offers)
        assert t.earliest_available_from() is None

    def test_services_no_duplicates(self):
        offers = [
            ServiceOffer("netflix", "https://n1.com", "FLATRATE", None, None),
            ServiceOffer("netflix", "https://n2.com", "FLATRATE", None, None),  # 重複
            ServiceOffer("unext", "https://u.com", "FLATRATE", None, None),
        ]
        t = self._make_title(offers)
        assert t.services == ["netflix", "unext"]

    def test_get_offer_url(self):
        offers = [
            ServiceOffer("netflix", "https://n.com/title/1", "FLATRATE", None, None),
            ServiceOffer("unext", "https://u.com/title/2", "FLATRATE", None, None),
        ]
        t = self._make_title(offers)
        assert t.get_offer_url("netflix") == "https://n.com/title/1"
        assert t.get_offer_url("unext") == "https://u.com/title/2"
        assert t.get_offer_url("hulu") is None


# ---------------------------------------------------------------------------
# fetch_new_titles（モックあり）
# ---------------------------------------------------------------------------

def _make_netflix_node(node_id: str, title: str, avail_from: str) -> dict:
    return _make_node(
        node_id,
        title,
        offers=[_make_offer("netflix", f"https://www.netflix.com/jp/title/{node_id}", avail_from)],
    )


class TestFetchNewTitles:

    @patch("utils.new_titles._post_graphql")
    def test_returns_titles_within_days_back(self, mock_gql):
        """days_back 以内の avail_from を持つタイトルが返る。"""
        cutoff = date.today() - timedelta(days=14)
        recent = (date.today() - timedelta(days=7)).isoformat()
        old = (cutoff - timedelta(days=1)).isoformat()

        nodes = [
            _make_netflix_node("1", "新着映画A", recent),
            _make_netflix_node("2", "古い映画B", old),
        ]
        mock_gql.return_value = _make_graphql_response(nodes)

        result = fetch_new_titles(["netflix"], days_back=14)

        # 古い映画は日付が cutoff より前のため stop_early で打ち切り → 含まれない
        assert len(result) == 1
        assert result[0].title == "新着映画A"

    @patch("utils.new_titles._post_graphql")
    def test_titles_sorted_newest_first(self, mock_gql):
        """返り値は available_from の降順（新しい順）。"""
        nodes = [
            _make_netflix_node("1", "映画A", "2026-06-20"),
            _make_netflix_node("2", "映画B", "2026-06-25"),
            _make_netflix_node("3", "映画C", "2026-06-15"),
        ]
        mock_gql.return_value = _make_graphql_response(nodes)

        result = fetch_new_titles(["netflix"], days_back=30)

        dates = [t.earliest_available_from() for t in result]
        assert dates == sorted(dates, reverse=True), "新しい順になっていない"

    @patch("utils.new_titles._post_graphql")
    def test_limit_respected(self, mock_gql):
        """limit を超えない。"""
        nodes = [_make_netflix_node(str(i), f"映画{i}", "2026-06-20") for i in range(20)]
        mock_gql.return_value = _make_graphql_response(nodes)

        result = fetch_new_titles(["netflix"], limit=5, days_back=30)
        assert len(result) <= 5

    @patch("utils.new_titles._post_graphql")
    def test_empty_services_returns_empty(self, mock_gql):
        """services が空なら API を呼ばずに空リストを返す。"""
        result = fetch_new_titles([])
        mock_gql.assert_not_called()
        assert result == []

    @patch("utils.new_titles._post_graphql")
    def test_unknown_service_returns_empty(self, mock_gql):
        """_SERVICE_TO_JW_PACKAGE にないサービスのみなら空リスト。"""
        result = fetch_new_titles(["dmm_tv"])  # dmm_tv は JW 未対応
        mock_gql.assert_not_called()
        assert result == []

    @patch("utils.new_titles._post_graphql")
    def test_avail_from_none_included(self, mock_gql):
        """availableFrom が null のタイトルも含める（日付不明）。"""
        nodes = [
            _make_node(
                "99",
                "日付不明映画",
                offers=[_make_offer("netflix", "https://www.netflix.com/jp/title/99")],
            ),
        ]
        mock_gql.return_value = _make_graphql_response(nodes)

        result = fetch_new_titles(["netflix"], days_back=14)
        assert any(t.title == "日付不明映画" for t in result)

    @patch("utils.new_titles._post_graphql")
    def test_pagination_stops_on_old_title(self, mock_gql):
        """古いタイトルが出てきた時点でページネーションを打ち切る。"""
        recent = (date.today() - timedelta(days=7)).isoformat()
        old = (date.today() - timedelta(days=30)).isoformat()

        page1_nodes = [_make_netflix_node("1", "新着", recent)]
        page2_nodes = [_make_netflix_node("2", "古い", old)]

        # page1 は hasNextPage=True、page2 で古いタイトルが出る
        mock_gql.side_effect = [
            _make_graphql_response(page1_nodes, has_next=True, end_cursor="cur1"),
            _make_graphql_response(page2_nodes, has_next=False),
        ]

        result = fetch_new_titles(["netflix"], days_back=14, page_size=1, sleep_between_pages=0)
        # page2 の「古い」は cutoff より前なので含まれない
        assert len(result) == 1
        assert result[0].title == "新着"

    @patch("utils.new_titles._post_graphql")
    def test_runtime_error_propagated(self, mock_gql):
        mock_gql.side_effect = RuntimeError("API down")
        with pytest.raises(RuntimeError, match="API down"):
            fetch_new_titles(["netflix"])

    @patch("utils.new_titles._post_graphql")
    def test_multi_service_query(self, mock_gql):
        """複数サービスを渡すと packages リストに変換して1回のリクエストで取得できる。"""
        nodes = [
            _make_node(
                "10",
                "マルチサービス映画",
                offers=[
                    _make_offer("netflix", "https://n.com/1", "2026-06-20"),
                    _make_offer("unext", "https://u.com/1", "2026-06-18"),
                ],
            ),
        ]
        mock_gql.return_value = _make_graphql_response(nodes)

        result = fetch_new_titles(["netflix", "unext"], days_back=14)

        assert len(result) == 1
        assert set(result[0].services) == {"netflix", "unext"}
        # API は 1回だけ呼ばれる（ページネーションなし）
        assert mock_gql.call_count == 1

        # packages パラメータに netflix と unext が含まれていることを確認
        call_vars = mock_gql.call_args[0][1]
        assert "netflix" in call_vars["packages"]
        assert "unext" in call_vars["packages"]


# ---------------------------------------------------------------------------
# group_by_service
# ---------------------------------------------------------------------------

class TestGroupByService:
    def _make_title(self, title: str, services: list[str]) -> NewTitle:
        return NewTitle(
            jw_id="1",
            title=title,
            original_title="",
            full_path="",
            poster_url="",
            offers=[
                ServiceOffer(svc, f"https://example.com/{svc}", "FLATRATE", None, None)
                for svc in services
            ],
        )

    def test_single_service(self):
        titles = [self._make_title("映画A", ["netflix"])]
        result = group_by_service(titles)
        assert list(result.keys()) == ["netflix"]
        assert result["netflix"][0].title == "映画A"

    def test_multi_service_title_appears_in_both(self):
        titles = [self._make_title("映画A", ["netflix", "unext"])]
        result = group_by_service(titles)
        assert "netflix" in result
        assert "unext" in result
        assert result["netflix"][0].title == "映画A"
        assert result["unext"][0].title == "映画A"

    def test_empty_titles(self):
        assert group_by_service([]) == {}


# ---------------------------------------------------------------------------
# to_report
# ---------------------------------------------------------------------------

class TestToReport:
    def test_report_structure(self):
        offers = [
            ServiceOffer("netflix", "https://n.com", "FLATRATE", date(2026, 6, 20), None),
        ]
        titles = [
            NewTitle("1", "映画A", "Movie A", "/jp/movie/a", "https://poster.jpg",
                     genres=["Action"], offers=offers),
        ]
        report = to_report(titles)
        assert report["total"] == 1
        assert report["by_service"]["netflix"] == 1
        assert len(report["titles"]) == 1
        t = report["titles"][0]
        assert t["title"] == "映画A"
        assert t["original_title"] == "Movie A"
        assert t["genres"] == ["Action"]
        assert t["services"] == ["netflix"]
        assert t["available_from"] == "2026-06-20"
        assert len(t["offers"]) == 1
        assert t["offers"][0]["service"] == "netflix"
        assert t["offers"][0]["type"] == "FLATRATE"

    def test_empty_titles(self):
        report = to_report([])
        assert report["total"] == 0
        assert report["by_service"] == {}
        assert report["titles"] == []

    def test_by_service_counts(self):
        make_offer = lambda svc: ServiceOffer(svc, "https://url", "FLATRATE", None, None)
        titles = [
            NewTitle("1", "A", "", "", "", offers=[make_offer("netflix")]),
            NewTitle("2", "B", "", "", "", offers=[make_offer("netflix"), make_offer("unext")]),
            NewTitle("3", "C", "", "", "", offers=[make_offer("unext")]),
        ]
        report = to_report(titles)
        assert report["by_service"]["netflix"] == 2
        assert report["by_service"]["unext"] == 2
