import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from news_bot import routine_pr_notify


def _pull_request(**overrides):
    data = {
        "number": 55,
        "title": "劇場公開情報 2026-08-14〜2026-08-20（6件）",
        "html_url": "https://github.com/example/repo/pull/55",
        "draft": False,
        "user": {"login": "routine-user"},
        "head": {"ref": "routine/theater-2026-08-14"},
    }
    data.update(overrides)
    return data


@pytest.mark.parametrize(
    ("head_ref", "expected"),
    [
        ("routine/theater-2026-08-14", "theater"),
        ("routine/vod-2026-08-12", "vod"),
    ],
)
def test_routine_kind_from_branch(head_ref, expected):
    assert routine_pr_notify.routine_kind(head_ref) == expected


def test_non_routine_branch_is_rejected():
    with pytest.raises(ValueError, match="ルーティンPRではない"):
        routine_pr_notify.routine_kind("feature/example")


def test_notification_channel_uses_kind_specific_channel():
    env = {
        "SLACK_APPROVAL_CHANNEL_ID": "C-approval",
        "SLACK_THEATER_CHANNEL_ID": "C-theater",
        "SLACK_VOD_CHANNEL_ID": "C-vod",
    }
    assert routine_pr_notify.notification_channel("theater", env) == "C-theater"
    assert routine_pr_notify.notification_channel("vod", env) == "C-vod"


def test_notification_channel_falls_back_to_approval_channel():
    assert routine_pr_notify.notification_channel("vod", {"SLACK_APPROVAL_CHANNEL_ID": "C-approval"}) == (
        "C-approval"
    )


def test_build_message_contains_pr_information():
    message = routine_pr_notify.build_message(_pull_request(), "theater")
    assert "劇場公開ルーティンのPRが作成されました" in message
    assert "#55 劇場公開情報" in message
    assert "https://github.com/example/repo/pull/55" in message
    assert "routine-user" in message
    assert "検証後に自動マージされます" in message


def test_post_message_calls_slack_api():
    response = MagicMock()
    response.__enter__.return_value.read.return_value = b'{"ok":true,"channel":"C1","ts":"123.45"}'
    with patch.object(routine_pr_notify, "urlopen", return_value=response) as mocked_urlopen:
        result = routine_pr_notify.post_message("C1", "通知本文", "xoxb-test")

    assert result == ("C1", "123.45")
    request = mocked_urlopen.call_args.args[0]
    assert request.full_url == "https://slack.com/api/chat.postMessage"
    assert json.loads(request.data) == {"channel": "C1", "text": "通知本文"}
    assert request.get_header("Authorization") == "Bearer xoxb-test"


def test_notify_from_event_selects_theater_channel(tmp_path: Path):
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps({"pull_request": _pull_request()}, ensure_ascii=False), encoding="utf-8")
    env = {
        "SLACK_BOT_TOKEN": "xoxb-test",
        "SLACK_APPROVAL_CHANNEL_ID": "C-approval",
        "SLACK_THEATER_CHANNEL_ID": "C-theater",
    }
    with patch.object(routine_pr_notify, "post_message", return_value=("C-theater", "123")) as post:
        result = routine_pr_notify.notify_from_event(event_path, env)

    assert result == ("C-theater", "123")
    assert post.call_args.args[0] == "C-theater"
    assert "#55" in post.call_args.args[1]
    assert post.call_args.args[2] == "xoxb-test"
