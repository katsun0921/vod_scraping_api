"""ルーティンが作成したPull RequestをSlackへ通知する。"""

import json
import os
import sys
from pathlib import Path
from typing import Mapping
from urllib.request import Request, urlopen

_SLACK_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"


def routine_kind(head_ref: str) -> str:
    """ルーティンのブランチ名から劇場/VODを判定する。"""
    if head_ref.startswith("routine/theater-"):
        return "theater"
    if head_ref.startswith("routine/vod-"):
        return "vod"
    raise ValueError(f"ルーティンPRではないブランチです: {head_ref}")


def notification_channel(kind: str, environ: Mapping[str, str] = os.environ) -> str:
    """種別ごとの専用チャンネルを返す。

    専用チャンネルが未設定なら承認チャンネルへフォールバックする。
    """
    channel_env = {
        "theater": "SLACK_THEATER_CHANNEL_ID",
        "vod": "SLACK_VOD_CHANNEL_ID",
    }
    if kind not in channel_env:
        raise ValueError(f"不明なルーティン種別です: {kind}")

    channel = environ.get(channel_env[kind]) or environ.get("SLACK_APPROVAL_CHANNEL_ID")
    if not channel:
        raise RuntimeError("Slack通知先チャンネルが設定されていません")
    return channel


def build_message(pull_request: Mapping[str, object], kind: str) -> str:
    """Slackへ送るPRレビュー依頼メッセージを生成する。"""
    label = {"theater": "劇場公開", "vod": "VOD配信"}[kind]
    number = pull_request["number"]
    title = pull_request["title"]
    url = pull_request["html_url"]
    author = pull_request.get("user") or {}
    author_name = author.get("login", "不明") if isinstance(author, Mapping) else "不明"
    draft_note = "\nステータス: Draft" if pull_request.get("draft") else ""
    return (
        f"*{label}ルーティンのPRが作成されました*\n"
        f"#{number} {title}\n"
        f"{url}\n"
        f"作成者: {author_name}{draft_note}\n"
        "内容を確認し、問題なければマージしてください。"
    )


def post_message(channel: str, text: str, token: str) -> tuple[str, str]:
    """Slack Web APIへメッセージを投稿する。"""
    request = Request(
        _SLACK_POST_MESSAGE_URL,
        data=json.dumps({"channel": channel, "text": text}, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    with urlopen(request, timeout=10) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(f"Slack通知失敗: {result}")
    return result["channel"], result["ts"]


def notify_from_event(event_path: Path, environ: Mapping[str, str] = os.environ) -> tuple[str, str]:
    """GitHubのpull_requestイベントを読み、対応するSlackチャンネルへ通知する。"""
    event = json.loads(event_path.read_text(encoding="utf-8"))
    pull_request = event["pull_request"]
    kind = routine_kind(pull_request["head"]["ref"])
    return post_message(
        notification_channel(kind, environ),
        build_message(pull_request, kind),
        environ["SLACK_BOT_TOKEN"],
    )


if __name__ == "__main__":
    path = Path(sys.argv[1] if len(sys.argv) > 1 else os.environ["GITHUB_EVENT_PATH"])
    notify_from_event(path)
