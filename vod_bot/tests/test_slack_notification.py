"""週次パッチの Slack 通知フォーマットのユニットテスト。

Slack への実 POST は行わない（_post をモンキーパッチで差し替える）。
フロントURL の組み立て（_build_front_url）は tests/test_quota.py 側で検証する。
"""

import pytest

import slack
from weekly_patch import _FRONT_BASE_URL


# ---------------------------------------------------------------------------
# notify_weekly_new_streaming_summary
# ---------------------------------------------------------------------------

@pytest.fixture
def sent(monkeypatch):
    """_post をキャプチャに差し替え、送信ペイロードのリストを返す。"""
    payloads: list[dict] = []
    monkeypatch.setattr(slack, "_post", payloads.append)
    return payloads


def test_notify_skips_when_empty(sent):
    slack.notify_weekly_new_streaming_summary([])
    assert sent == []


def test_notify_includes_front_url_and_scraping_url(sent):
    slack.notify_weekly_new_streaming_summary([
        {
            "service": "netflix",
            "lang": "ja",
            "title": "作品A",
            "url": f"{_FRONT_BASE_URL}/ja/anime/sakuhin-a",
            "scraping_url": "https://www.netflix.com/jp/title/12345",
        },
    ])
    text = sent[0]["text"]
    assert (
        f"  • <{_FRONT_BASE_URL}/ja/anime/sakuhin-a|作品A> "
        "｜ <https://www.netflix.com/jp/title/12345|配信ページ>"
    ) in text
    assert "*Netflix*" in text


def test_notify_omits_scraping_link_when_missing(sent):
    slack.notify_weekly_new_streaming_summary([
        {"service": "unext", "lang": "ja", "title": "作品C", "url": "", "scraping_url": ""},
    ])
    assert "  • 作品C" in sent[0]["text"]


def test_notify_groups_by_lang_then_service(sent):
    slack.notify_weekly_new_streaming_summary([
        {
            "service": "crunchyroll",
            "lang": "en",
            "title": "Title D",
            "url": f"{_FRONT_BASE_URL}/en/anime/title-d",
            "scraping_url": "https://www.crunchyroll.com/series/ABC/title-d",
        },
        {
            "service": "netflix",
            "lang": "ja",
            "title": "作品A",
            "url": f"{_FRONT_BASE_URL}/ja/anime/sakuhin-a",
            "scraping_url": "https://www.netflix.com/jp/title/12345",
        },
    ])
    text = sent[0]["text"]
    assert "全2件" in text
    # ja セクションが en セクションより前に出る
    assert text.index(":jp: 日本語") < text.index(":us: English")
    assert text.index("*Netflix*") < text.index("*Crunchyroll*")


# ---------------------------------------------------------------------------
# notify_theater_showing_result（劇場公開の週次チェック）
# ---------------------------------------------------------------------------

def test_theater_notify_skips_when_empty(sent):
    slack.notify_theater_showing_result([], [], 8)
    assert sent == []


def test_theater_notify_lists_ended_and_unknown(sent):
    slack.notify_theater_showing_result(
        ended=[
            {
                "title": "作品A",
                "url": f"{_FRONT_BASE_URL}/ja/movie/sakuhin-a",
                "reason": "expired",
                "release_date": "2026-06-01",
                "elapsed_days": 99,
                "cinema_url": "",
            },
            {
                "title": "作品B",
                "url": f"{_FRONT_BASE_URL}/ja/movie/sakuhin-b",
                "reason": "url_gone",
                "release_date": "2026-08-01",
                "elapsed_days": 38,
                "cinema_url": "https://example.com/theater-b",
            },
        ],
        unknown=[
            {
                "title": "作品C",
                "url": f"{_FRONT_BASE_URL}/ja/movie/sakuhin-c",
                "reason": "no_signal",
                "release_date": "",
                "elapsed_days": None,
                "cinema_url": "",
            },
        ],
        weeks=8,
    )
    text = sent[0]["text"]

    assert "上映終了 2件 / 判定不能 1件" in text
    assert f"<{_FRONT_BASE_URL}/ja/movie/sakuhin-a|作品A> ｜ 公開から14週間経過（2026-06-01）" in text
    assert "作品B> ｜ 劇場URLが404/410 ｜ <https://example.com/theater-b|劇場ページ>" in text
    assert "作品C> ｜ 公開日・劇場URLが未入力で判定できず" in text


# ---------------------------------------------------------------------------
# notify_youtube_free_result / notify_youtube_free_failure
# ---------------------------------------------------------------------------

def _youtube_item(**overrides) -> dict:
    """youtube_free_patch.run() が返す記事1件分の形。"""
    return {
        "id": 1,
        "slug": "one-missed-call-2003",
        "title": "着信アリ",
        "prev_status": "rental",
        "status": "streaming",
        "price": 0,
        "channel_name": "【公式】プレシディオチャンネル",
        "youtube_url": "https://www.youtube.com/watch?v=abc",
        "url": f"{_FRONT_BASE_URL}/ja/movie/one-missed-call-2003",
        **overrides,
    }


def test_youtube_notify_skips_when_nothing_changed(sent):
    """毎日実行するため、変化が無い日は通知しない。"""
    slack.notify_youtube_free_result([], [], [])
    assert sent == []


def test_youtube_notify_lists_started_with_channel(sent):
    slack.notify_youtube_free_result([_youtube_item()], [], [])
    text = sent[0]["text"]
    assert "*無料公開が始まった作品*" in text
    assert f"<{_FRONT_BASE_URL}/ja/movie/one-missed-call-2003|着信アリ>" in text
    assert "【公式】プレシディオチャンネル" in text
    assert "<https://www.youtube.com/watch?v=abc|YouTube>" in text


def test_youtube_notify_shows_price_when_free_ended(sent):
    """無料が終わったときは、いくらになったのかまで出す。"""
    slack.notify_youtube_free_result(
        [],
        [_youtube_item(prev_status="streaming", status="rental", price=407.0, channel_name="")],
        [],
    )
    text = sent[0]["text"]
    assert "*無料公開が終わった作品（TOP から自動的に外れる）*" in text
    assert "streaming → レンタル（407円）" in text


def test_youtube_notify_includes_update_failures(sent):
    """更新失敗は件数と理由まで出す。値が古いまま残るため見逃せない。"""
    slack.notify_youtube_free_result(
        [], [], [],
        [_youtube_item(reason="PATCH 失敗")],
    )
    text = sent[0]["text"]
    assert "更新失敗 1件" in text
    assert "*WordPress の更新に失敗（値が古いまま残っている）*" in text
    assert "PATCH 失敗" in text


def test_youtube_notify_lists_skipped_with_reason(sent):
    slack.notify_youtube_free_result(
        [], [],
        [_youtube_item(status="streaming", reason="ログインが必要")],
    )
    text = sent[0]["text"]
    assert "*判定不能（値はそのまま）*" in text
    assert "ログインが必要" in text


def test_youtube_notify_failure_always_posts(sent):
    """中断は無条件で通知する（無通知だと変化が無かった日と区別できない）。"""
    slack.notify_youtube_free_failure("WP 取得失敗")
    text = sent[0]["text"]
    assert "中断" in text
    assert "WP 取得失敗" in text
