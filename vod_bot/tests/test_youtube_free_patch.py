"""YouTube 無料配信チェック（checkers/youtube.py・youtube_free_patch.py）のユニットテスト。

外部APIへのアクセスは一切行わない（HTTP は requests.get のスタブで代替）。
"""

import pytest

import youtube_free_patch
from checkers import youtube as youtube_checker
from checkers.youtube import YoutubeChecker, extract_channel_name, extract_playability_status
from youtube_free_patch import run


# ---------------------------------------------------------------------------
# テスト用の HTML フィクスチャ
# ---------------------------------------------------------------------------

def _html(
    *,
    playability: str = "OK",
    channel: str = "【公式】プレシディオチャンネル",
    offer: str = "",
    og_title: str = "着信アリ",
) -> str:
    """watch ページの HTML を判定に必要な部分だけ再現する。"""
    parts = ['<html><head>']
    if og_title:
        parts.append(f'<meta property="og:title" content="{og_title}">')
    if channel:
        parts.append(f'<link itemprop="name" content="{channel}">')
    parts.append('</head><body><script>var ytInitialPlayerResponse = {')
    if playability:
        parts.append(f'"playabilityStatus":{{"status":"{playability}","reason":"test"}},')
    if channel:
        parts.append(f'"videoDetails":{{"ownerChannelName":"{channel}"}},')
    if offer:
        parts.append(offer)
    parts.append('};</script></body></html>')
    return "".join(parts)


_RENTAL_OFFER = (
    '"errorScreen":{"ytOfferModuleRenderer":{"offerHeadline":{"simpleText":"この動画を視聴するには"},'
    '"offerButtonText":{"simpleText":"¥407 でレンタル"}}},'
)

_PURCHASE_OFFER = (
    '"errorScreen":{"ytOfferModuleRenderer":{"offerHeadline":{"simpleText":"この動画を視聴するには"},'
    '"offerButtonText":{"simpleText":"¥2,500 で購入"}}},'
)


class _StubResponse:
    """requests.get の戻り値のスタブ。"""

    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code


@pytest.fixture
def stub_get(monkeypatch):
    """requests.get を差し替え、任意の HTML を返させるフィクスチャ。"""

    def _install(text: str, status_code: int = 200):
        def _fake_get(url, **kwargs):
            return _StubResponse(text, status_code)

        monkeypatch.setattr(youtube_checker.requests, "get", _fake_get)

    return _install


# ---------------------------------------------------------------------------
# extract_playability_status / extract_channel_name
# ---------------------------------------------------------------------------

def test_extract_playability_status_ok():
    assert extract_playability_status(_html(playability="OK")) == "OK"


def test_extract_playability_status_missing():
    assert extract_playability_status("<html></html>") == ""


def test_extract_channel_name_from_player_response():
    html = _html(channel="【公式】プレシディオチャンネル")
    assert extract_channel_name(html) == "【公式】プレシディオチャンネル"


def test_extract_channel_name_falls_back_to_microformat():
    """ownerChannelName が無くても microformat の link から取れる。"""
    html = (
        '<html><head><link itemprop="name" content="東宝MOVIEチャンネル"></head>'
        '<body><script>var x = {"playabilityStatus":{"status":"OK"}};</script></body></html>'
    )
    assert extract_channel_name(html) == "東宝MOVIEチャンネル"


def test_extract_channel_name_missing():
    assert extract_channel_name("<html></html>") == ""


# ---------------------------------------------------------------------------
# YoutubeChecker.check
# ---------------------------------------------------------------------------

def test_check_free_video_returns_streaming(stub_get):
    """再生可能なら無料公開（streaming / price 0）とみなす。"""
    stub_get(_html(playability="OK"))
    result = YoutubeChecker().check("https://www.youtube.com/watch?v=abc")
    assert result["status"] == "streaming"
    assert result["price"] == 0
    assert result["channel_name"] == "【公式】プレシディオチャンネル"


def test_check_rental_offer_is_not_free(stub_get):
    """有料オファーが出ていたら無料と判定しない（このテストが本改修の要）。"""
    stub_get(_html(playability="UNPLAYABLE", offer=_RENTAL_OFFER))
    result = YoutubeChecker().check("https://www.youtube.com/watch?v=abc")
    assert result["status"] == "rental"
    assert result["price"] == 407.0


def test_check_purchase_offer(stub_get):
    stub_get(_html(playability="UNPLAYABLE", offer=_PURCHASE_OFFER))
    result = YoutubeChecker().check("https://www.youtube.com/watch?v=abc")
    assert result["status"] == "purchase"
    assert result["price"] == 2500.0


def test_check_deleted_video_returns_ended(stub_get):
    stub_get(_html(playability="ERROR", og_title=""))
    result = YoutubeChecker().check("https://www.youtube.com/watch?v=abc")
    assert result["status"] == "ended"
    assert result["price"] is None


def test_check_login_required_raises(stub_get):
    """年齢制限か限定公開か区別できないため据え置く。"""
    stub_get(_html(playability="LOGIN_REQUIRED"))
    with pytest.raises(RuntimeError):
        YoutubeChecker().check("https://www.youtube.com/watch?v=abc")


def test_check_unreadable_page_raises(stub_get):
    """同意ページ等で playabilityStatus が読めない場合、無料に倒さず判定不能にする。"""
    stub_get('<html><head><meta property="og:title" content="着信アリ"></head><body></body></html>')
    with pytest.raises(RuntimeError):
        YoutubeChecker().check("https://www.youtube.com/watch?v=abc")


def test_check_server_error_raises(stub_get):
    stub_get("", status_code=503)
    with pytest.raises(RuntimeError):
        YoutubeChecker().check("https://www.youtube.com/watch?v=abc")


def test_check_no_content_returns_ended(stub_get):
    stub_get("<html><head></head><body></body></html>")
    result = YoutubeChecker().check("https://www.youtube.com/watch?v=abc")
    assert result["status"] == "ended"


# ---------------------------------------------------------------------------
# youtube_free_patch.run
# ---------------------------------------------------------------------------

def _post(
    *,
    post_id: int = 1,
    slug: str = "one-missed-call-2003",
    status: str = "",
    url: str = "https://www.youtube.com/watch?v=abc",
    channel_name: str = "",
) -> dict:
    """get_youtube_url_posts() が返す形式の投稿1件を生成する。"""
    return {
        "id": post_id,
        "slug": slug,
        "title": {"rendered": "着信アリ"},
        "categories": [3],
        "vod": [],
        "acf": {
            "lang": "ja",
            "youtube": {
                "scraping_url": url,
                "status": status,
                "price": 0,
                "channel_name": channel_name,
            },
        },
    }


@pytest.fixture
def patched_run(monkeypatch):
    """run() の外部依存（WP・Slack・待機）を差し替える。"""
    calls: dict = {"patched": [], "notified": []}

    monkeypatch.setattr(youtube_free_patch, "get_category_slug_map", lambda: {3: "movie"})
    monkeypatch.setattr(youtube_free_patch, "get_vod_term_ids", lambda post: [])
    monkeypatch.setattr(youtube_free_patch._RATE_LIMITER, "wait", lambda: None)

    def _fake_patch(**kwargs):
        calls["patched"].append(kwargs)
        return kwargs["status"] == "streaming"

    def _fake_notify(started, ended, skipped):
        calls["notified"].append((started, ended, skipped))

    monkeypatch.setattr(youtube_free_patch, "patch_youtube_status", _fake_patch)
    monkeypatch.setattr(youtube_free_patch, "notify_youtube_free_result", _fake_notify)
    return calls


def _stub_checker(monkeypatch, results: dict):
    """slug ごとの判定結果を返すチェッカーに差し替える。"""

    class _Checker:
        def check(self, url: str) -> dict:
            value = results[url]
            if isinstance(value, Exception):
                raise value
            return value

    monkeypatch.setattr(youtube_free_patch, "YoutubeChecker", _Checker)


def test_run_detects_newly_free(monkeypatch, patched_run):
    """有料だった作品が無料になったら started に載り、ACF が更新される。"""
    monkeypatch.setattr(
        youtube_free_patch, "get_youtube_url_posts",
        lambda slug=None, post_id=None: [_post(status="rental")],
    )
    _stub_checker(monkeypatch, {
        "https://www.youtube.com/watch?v=abc": {
            "status": "streaming", "price": 0, "channel_name": "【公式】プレシディオチャンネル",
        },
    })

    result = run()

    assert result["posts"]["started"] == 1
    assert result["posts"]["free"] == 1
    assert result["started"][0]["channel_name"] == "【公式】プレシディオチャンネル"
    assert patched_run["patched"][0]["status"] == "streaming"


def test_run_detects_free_ended(monkeypatch, patched_run):
    """無料だった作品が有料化したら ended に載る（TOP から外れる）。"""
    monkeypatch.setattr(
        youtube_free_patch, "get_youtube_url_posts",
        lambda slug=None, post_id=None: [_post(status="streaming")],
    )
    _stub_checker(monkeypatch, {
        "https://www.youtube.com/watch?v=abc": {"status": "rental", "price": 407.0, "channel_name": ""},
    })

    result = run()

    assert result["posts"]["ended"] == 1
    assert result["ended"][0]["status"] == "rental"
    assert patched_run["patched"][0]["status"] == "rental"


def test_run_skips_undecidable_without_patching(monkeypatch, patched_run):
    """判定不能な記事は WordPress を更新しない（無料枠から誤って落とさない）。"""
    monkeypatch.setattr(
        youtube_free_patch, "get_youtube_url_posts",
        lambda slug=None, post_id=None: [_post(status="streaming")],
    )
    _stub_checker(monkeypatch, {
        "https://www.youtube.com/watch?v=abc": RuntimeError("判定不能"),
    })

    result = run()

    assert result["posts"]["skipped"] == 1
    assert patched_run["patched"] == []


def test_run_dry_run_does_not_patch(monkeypatch, patched_run):
    monkeypatch.setattr(
        youtube_free_patch, "get_youtube_url_posts",
        lambda slug=None, post_id=None: [_post(status="rental")],
    )
    _stub_checker(monkeypatch, {
        "https://www.youtube.com/watch?v=abc": {"status": "streaming", "price": 0, "channel_name": ""},
    })

    result = run(dry_run=True)

    assert result["posts"]["started"] == 1
    assert patched_run["patched"] == []
    assert patched_run["notified"] == []


def test_run_returns_error_when_fetch_fails(monkeypatch, patched_run):
    """対象一覧が取れないときは1件も更新しない。"""

    def _boom(slug=None, post_id=None):
        raise RuntimeError("WP 取得失敗")

    monkeypatch.setattr(youtube_free_patch, "get_youtube_url_posts", _boom)

    result = run()

    assert result["error"] == "WP 取得失敗"
    assert patched_run["patched"] == []
