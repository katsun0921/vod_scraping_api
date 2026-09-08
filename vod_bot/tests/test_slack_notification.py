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
