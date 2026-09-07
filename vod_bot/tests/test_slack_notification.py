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
