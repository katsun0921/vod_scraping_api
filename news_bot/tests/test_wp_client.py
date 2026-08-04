import sys
import types
from unittest.mock import MagicMock, patch

# requests は本番依存だがテストでは実通信しないためスタブで十分
sys.modules.setdefault("requests", types.ModuleType("requests"))

from news_bot import wp_client


def _response(posts: list[dict]) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = posts
    resp.raise_for_status.return_value = None
    return resp


def _post(post_id: int, title: str) -> dict:
    return {"id": post_id, "title": {"rendered": title}, "link": f"https://example.com/{post_id}/"}


@patch.dict(
    "os.environ",
    {"WP_API_URL": "https://example.com/wp-json/wp/v2", "WP_USER": "u", "WP_APP_PASSWORD": "p"},
)
def test_rejects_fuzzy_search_hit_of_unrelated_post():
    """WP REST の search はあいまい検索のため無関係な記事を返す。

    実運用で実際に起きた誤照合の再現: 「モリア」（Netflixドラマ）の検索で
    『映画大好きポンポさん』がヒットし、週次まとめ記事に無関係なレビューURLが
    貼られる状態だった。タイトルが一致しない場合はリンクを付けない。
    """
    with patch.object(wp_client, "requests") as req:
        req.get.return_value = _response([_post(11744, "映画大好きポンポさん")])
        assert wp_client.find_post_by_title("モリア") is None


@patch.dict(
    "os.environ",
    {"WP_API_URL": "https://example.com/wp-json/wp/v2", "WP_USER": "u", "WP_APP_PASSWORD": "p"},
)
def test_rejects_partial_match_of_series_title():
    """「リーチャー ～正義のアウトロー～: シーズン4」で『ウォーマシン』がヒットした事例。"""
    with patch.object(wp_client, "requests") as req:
        req.get.return_value = _response([_post(20286, "ウォーマシン")])
        assert wp_client.find_post_by_title("リーチャー ～正義のアウトロー～: シーズン4") is None


@patch.dict(
    "os.environ",
    {"WP_API_URL": "https://example.com/wp-json/wp/v2", "WP_USER": "u", "WP_APP_PASSWORD": "p"},
)
def test_accepts_exact_title_match():
    with patch.object(wp_client, "requests") as req:
        req.get.return_value = _response([_post(123, "インターステラー")])
        post = wp_client.find_post_by_title("インターステラー")
        assert post is not None and post["id"] == 123


@patch.dict(
    "os.environ",
    {"WP_API_URL": "https://example.com/wp-json/wp/v2", "WP_USER": "u", "WP_APP_PASSWORD": "p"},
)
def test_accepts_match_after_normalization():
    """全角/半角・波ダッシュの揺れは normalize_title が吸収する。"""
    with patch.object(wp_client, "requests") as req:
        req.get.return_value = _response([_post(5, "リーチャー ～正義のアウトロー～")])
        post = wp_client.find_post_by_title("リーチャー ~正義のアウトロー~")
        assert post is not None and post["id"] == 5


@patch.dict(
    "os.environ",
    {"WP_API_URL": "https://example.com/wp-json/wp/v2", "WP_USER": "u", "WP_APP_PASSWORD": "p"},
)
def test_finds_exact_match_beyond_first_candidate():
    """目的の記事が検索結果の2位以降にいても取りこぼさない。"""
    with patch.object(wp_client, "requests") as req:
        req.get.return_value = _response(
            [_post(1, "全然ちがう記事"), _post(2, "似ているが別の記事"), _post(3, "モリア")]
        )
        post = wp_client.find_post_by_title("モリア")
        assert post is not None and post["id"] == 3


@patch.dict(
    "os.environ",
    {"WP_API_URL": "https://example.com/wp-json/wp/v2", "WP_USER": "u", "WP_APP_PASSWORD": "p"},
)
def test_falls_back_to_original_title():
    """邦題で一致しない場合は原題で再検索する。"""
    with patch.object(wp_client, "requests") as req:
        req.get.side_effect = [_response([]), _response([_post(9, "Reacher")])]
        post = wp_client.find_post_by_title("リーチャー", "Reacher")
        assert post is not None and post["id"] == 9


@patch.dict(
    "os.environ",
    {"WP_API_URL": "https://example.com/wp-json/wp/v2", "WP_USER": "u", "WP_APP_PASSWORD": "p"},
)
def test_returns_none_when_no_results():
    with patch.object(wp_client, "requests") as req:
        req.get.return_value = _response([])
        assert wp_client.find_post_by_title("存在しない作品") is None
