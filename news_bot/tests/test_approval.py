import sys
import types

# feedparserは本番依存だが、テストで使うのはVOD承認ロジック（Slack API呼び出しは
# monkeypatchで差し替え）だけなので、import_routineのテストと同様スタブで十分
sys.modules.setdefault("feedparser", types.ModuleType("feedparser"))

from news_bot import approval


def test_resolve_vod_approvals_returns_keys_with_approve_emoji(monkeypatch):
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

    assert approval.resolve_vod_approvals(pending) == ["a"]


def test_resolve_vod_approvals_skips_rows_without_slack_ref(monkeypatch):
    monkeypatch.setattr(
        approval, "_get_reaction_names", lambda channel, ts: {approval.APPROVE_EMOJI}
    )

    pending = [
        {"重複キー": "a", "SlackチャンネルID": "", "Slackメッセージts": ""},
        {"重複キー": "", "SlackチャンネルID": "C1", "Slackメッセージts": "111"},
    ]

    assert approval.resolve_vod_approvals(pending) == []
