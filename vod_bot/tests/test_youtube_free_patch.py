"""YouTube 無料配信チェック（checkers/youtube.py・youtube_free_patch.py）のユニットテスト。

外部APIへのアクセスは一切行わない（HTTP は requests.get のスタブで代替）。
"""

import pytest

import youtube_free_patch
from checkers import youtube as youtube_checker
from checkers.youtube import (
    YoutubeChecker,
    classify_page,
    extract_channel_name,
    extract_playability_status,
    probe,
)
from youtube_free_patch import run, run_probe


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

    def __init__(self, text: str, status_code: int = 200, url: str = "") -> None:
        self.text = text
        self.status_code = status_code
        self.url = url


@pytest.fixture
def stub_get(monkeypatch):
    """requests.get を差し替え、任意の HTML を返させるフィクスチャ。"""

    def _install(text: str, status_code: int = 200, final_url: str = ""):
        def _fake_get(url, **kwargs):
            return _StubResponse(text, status_code, final_url or url)

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


def test_check_content_check_required_is_free(stub_get):
    """閲覧注意の確認を挟むだけの動画は無料で観られる。ended に倒さない。"""
    stub_get(_html(playability="CONTENT_CHECK_REQUIRED"))
    result = YoutubeChecker().check("https://www.youtube.com/watch?v=abc")
    assert result["status"] == "streaming"
    assert result["price"] == 0


def test_check_content_check_required_with_offer_is_paid(stub_get):
    """確認付きでも有料オファーがあれば無料ではない（オファー判定が先）。"""
    stub_get(_html(playability="CONTENT_CHECK_REQUIRED", offer=_RENTAL_OFFER))
    result = YoutubeChecker().check("https://www.youtube.com/watch?v=abc")
    assert result["status"] == "rental"


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
    calls: dict = {"patched": [], "notified": [], "failed": []}

    monkeypatch.setattr(youtube_free_patch, "get_category_slug_map", lambda: {3: "movie"})
    monkeypatch.setattr(youtube_free_patch, "get_vod_term_ids", lambda post: [])
    monkeypatch.setattr(youtube_free_patch._RATE_LIMITER, "wait", lambda: None)

    def _fake_patch(**kwargs):
        calls["patched"].append(kwargs)
        return kwargs["status"] == "streaming"

    def _fake_notify(started, ended, skipped, errors=None):
        calls["notified"].append((started, ended, skipped, errors or []))

    def _fake_notify_failure(message):
        calls["failed"].append(message)

    monkeypatch.setattr(youtube_free_patch, "patch_youtube_status", _fake_patch)
    monkeypatch.setattr(youtube_free_patch, "notify_youtube_free_result", _fake_notify)
    monkeypatch.setattr(youtube_free_patch, "notify_youtube_free_failure", _fake_notify_failure)
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
    """対象一覧が取れないときは1件も更新せず、中断を Slack に知らせる。"""

    def _boom(slug=None, post_id=None):
        raise RuntimeError("WP 取得失敗")

    monkeypatch.setattr(youtube_free_patch, "get_youtube_url_posts", _boom)

    result = run()

    assert result["error"] == "WP 取得失敗"
    assert patched_run["patched"] == []
    # 無通知だと「変化が無かった日」と区別できないため、中断は必ず通知する
    assert patched_run["failed"] == ["WP 取得失敗"]


def test_run_notifies_started_ended_skipped_and_errors(monkeypatch, patched_run):
    """Slack 通知には開始・終了・判定不能・更新失敗の4種が渡る。"""
    monkeypatch.setattr(
        youtube_free_patch, "get_youtube_url_posts",
        lambda slug=None, post_id=None: [
            _post(post_id=1, slug="became-free", status="rental", url="https://youtu.be/a"),
            _post(post_id=2, slug="became-paid", status="streaming", url="https://youtu.be/b"),
            _post(post_id=3, slug="undecidable", status="streaming", url="https://youtu.be/c"),
            _post(post_id=4, slug="patch-fails", status="rental", url="https://youtu.be/d"),
        ],
    )
    _stub_checker(monkeypatch, {
        "https://youtu.be/a": {"status": "streaming", "price": 0, "channel_name": "公式"},
        "https://youtu.be/b": {"status": "rental", "price": 407.0, "channel_name": ""},
        "https://youtu.be/c": RuntimeError("ログインが必要"),
        "https://youtu.be/d": {"status": "streaming", "price": 0, "channel_name": ""},
    })

    def _patch(**kwargs):
        if kwargs["post_id"] == 4:
            raise RuntimeError("PATCH 失敗")
        return True

    monkeypatch.setattr(youtube_free_patch, "patch_youtube_status", _patch)

    result = run()

    started, ended, skipped, errors = patched_run["notified"][0]
    assert [i["slug"] for i in started] == ["became-free"]
    assert [i["slug"] for i in ended] == ["became-paid"]
    assert [i["slug"] for i in skipped] == ["undecidable"]
    assert [i["slug"] for i in errors] == ["patch-fails"]
    assert errors[0]["reason"] == "PATCH 失敗"
    assert result["posts"]["errors"] == 1


def test_run_skips_notify_when_nothing_changed(monkeypatch, patched_run):
    """変化が無い日は通知内容が空で渡る（slack 側で送信を抑止する）。"""
    monkeypatch.setattr(
        youtube_free_patch, "get_youtube_url_posts",
        lambda slug=None, post_id=None: [_post(status="streaming")],
    )
    _stub_checker(monkeypatch, {
        "https://www.youtube.com/watch?v=abc": {"status": "streaming", "price": 0, "channel_name": ""},
    })

    run()

    assert patched_run["notified"][0] == ([], [], [], [])


# ---------------------------------------------------------------------------
# probe / classify_page（診断モード）
# ---------------------------------------------------------------------------

def test_classify_page_detects_consent():
    assert classify_page("<html>Before you continue</html>") == "consent"
    assert classify_page("", "https://consent.youtube.com/m?continue=...") == "consent"


def test_classify_page_detects_bot_check():
    assert classify_page("<html>Our systems have detected unusual traffic</html>") == "bot_check"


def test_classify_page_normal():
    assert classify_page(_html()) == "normal"


def test_probe_reports_signals_for_free_video(stub_get):
    """無料公開の動画では、観測値と判定の両方が揃って返る。"""
    stub_get(_html(playability="OK"))
    result = probe("https://www.youtube.com/watch?v=abc")

    assert result["http_status"] == 200
    assert result["page_type"] == "normal"
    assert result["has_player_response"] is True
    assert result["playability_status"] == "OK"
    assert result["has_offer_marker"] is False
    assert result["channel_name"] == "【公式】プレシディオチャンネル"
    assert result["og_title"] == "着信アリ"
    assert result["verdict"]["status"] == "streaming"
    assert result["error"] == ""


def test_probe_reports_offer_excerpt(stub_get):
    """有料オファーのときは抜粋も返し、マーカーが当たったか目視できる。"""
    stub_get(_html(playability="UNPLAYABLE", offer=_RENTAL_OFFER))
    result = probe("https://www.youtube.com/watch?v=abc")

    assert result["has_offer_marker"] is True
    assert "ytOfferModuleRenderer" in result["offer_excerpt"]
    assert result["verdict"]["status"] == "rental"


def test_probe_records_error_instead_of_raising(stub_get):
    """判定不能でも例外にせず error に載せる（複数URLをまとめて見るため）。"""
    stub_get(_html(playability="LOGIN_REQUIRED"))
    result = probe("https://www.youtube.com/watch?v=abc")

    assert result["verdict"] is None
    assert "ログインが必要" in result["error"]
    # 判定できなくても観測値は返る。これが診断モードの目的
    assert result["playability_status"] == "LOGIN_REQUIRED"


def test_probe_survives_network_error(monkeypatch):
    """通信エラーでも例外を投げず、error だけ埋めて返す。"""

    def _boom(url, **kwargs):
        raise youtube_checker.requests.RequestException("tunnel blocked")

    monkeypatch.setattr(youtube_checker.requests, "get", _boom)
    result = probe("https://www.youtube.com/watch?v=abc")

    assert result["http_status"] == 0
    assert result["verdict"] is None
    assert "リクエスト失敗" in result["error"]


def test_run_probe_does_not_touch_wordpress(monkeypatch, patched_run, stub_get):
    """診断モードは WordPress も Slack も触らない。"""
    monkeypatch.setattr(
        youtube_free_patch, "get_youtube_url_posts",
        lambda slug=None, post_id=None, limit=None: [_post(status="rental")],
    )
    stub_get(_html(playability="OK"))

    result = run_probe()

    assert result["posts"]["total"] == 1
    assert result["posts"]["judged"] == 1
    assert patched_run["patched"] == []
    assert patched_run["notified"] == []


def test_run_probe_with_url_skips_wordpress(monkeypatch, patched_run, stub_get):
    """--url 指定時は記事一覧を取りに行かない（記事の無い動画も試せる）。"""

    def _must_not_be_called(**kwargs):
        raise AssertionError("get_youtube_url_posts を呼んではいけない")

    monkeypatch.setattr(youtube_free_patch, "get_youtube_url_posts", _must_not_be_called)
    stub_get(_html(playability="OK"))

    result = run_probe(url="https://youtu.be/abc")

    assert result["posts"]["total"] == 1
    assert result["probe"][0]["url"] == "https://youtu.be/abc"


def test_run_probe_separates_fetch_failure_from_consent(monkeypatch, patched_run):
    """通信できなかった件を「同意ページ」に数えない。原因の取り違えを防ぐ。"""

    def _boom(url, **kwargs):
        raise youtube_checker.requests.RequestException("tunnel blocked")

    monkeypatch.setattr(youtube_checker.requests, "get", _boom)

    result = run_probe(url="https://youtu.be/abc")

    assert result["posts"]["fetch_failed"] == 1
    assert result["posts"]["consent_or_bot"] == 0
    assert result["posts"]["no_player_response"] == 0
