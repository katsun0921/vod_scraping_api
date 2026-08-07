import sys
import types
from datetime import date

# feedparserは本番依存だが、テストで使うのは承認ロジックと通知の組み立て
# （Slack API呼び出しはmonkeypatchで差し替え）だけなので、
# import_routineのテストと同様スタブで十分
sys.modules.setdefault("feedparser", types.ModuleType("feedparser"))

from news_bot import approval
from news_bot.theater_calendar import TheaterEntry


def test_resolve_approvals_returns_keys_with_approve_emoji(monkeypatch):
    reactions_by_ts = {
        "111": {approval.APPROVE_EMOJI},
        "222": {"eyes"},
    }
    monkeypatch.setattr(
        approval, "_get_reaction_names", lambda channel, ts: reactions_by_ts[ts]
    )

    pending = [
        {"重複キー": "a", "SlackチャンネルID": "C1", "Slackメッセージts": "111"},
        {"重複キー": "b", "SlackチャンネルID": "C1", "Slackメッセージts": "222"},
    ]

    assert approval.resolve_approvals(pending) == ["a"]


def test_resolve_approvals_skips_rows_without_slack_ref(monkeypatch):
    monkeypatch.setattr(
        approval, "_get_reaction_names", lambda channel, ts: {approval.APPROVE_EMOJI}
    )

    pending = [
        {"重複キー": "a", "SlackチャンネルID": "", "Slackメッセージts": ""},
        {"重複キー": "", "SlackチャンネルID": "C1", "Slackメッセージts": "111"},
    ]

    assert approval.resolve_approvals(pending) == []


def _stub_post_message(monkeypatch, *, fail_titles=()):
    """_post_messageを差し替え、親メッセージ→スレッド返信の順にtsを振る。

    fail_titlesに含まれるタイトルの返信だけ例外にして、
    「1件失敗しても残りは通知される」経路を再現する。
    """
    posted = {"n": 0}

    def fake_post_message(text, thread_ts=None, channel=None):
        if any(title in text for title in fail_titles):
            raise RuntimeError("Slack通知失敗")
        posted["n"] += 1
        return "C1", f"ts{posted['n']}"

    monkeypatch.setattr(approval, "_post_message", fake_post_message)
    return posted


def test_notify_theater_discovered_returns_ref_per_entry(monkeypatch):
    """スレッド返信のchannel/tsを重複キー単位で返す（シートのSlack参照列に書くため）。"""
    _stub_post_message(monkeypatch)
    entries = [
        TheaterEntry(title="ゴースト・オブ・ウエノ", url="", source="ルーティン(claude)",
                     release_date=date(2026, 8, 8), distributor="NAKACHIKA"),
    ]

    refs = approval.notify_theater_discovered(date(2026, 8, 7), date(2026, 8, 13), entries)

    # 親メッセージ(ts1)ではなくスレッド返信(ts2)を指していること
    assert refs == {"2026-08-08|ゴースト・オブ・ウエノ": ("C1", "ts2")}


def test_notify_theater_discovered_omits_entry_whose_reply_failed(monkeypatch):
    """返信に失敗した作品はrefsに載せない（ワンクリック承認の対象外になるだけ）。"""
    _stub_post_message(monkeypatch, fail_titles=("作品B",))
    entries = [
        TheaterEntry(title="作品A", url="", source="s", release_date=date(2026, 8, 8)),
        TheaterEntry(title="作品B", url="", source="s", release_date=date(2026, 8, 9)),
    ]

    refs = approval.notify_theater_discovered(date(2026, 8, 7), date(2026, 8, 13), entries)

    assert list(refs) == ["2026-08-08|作品A"]
