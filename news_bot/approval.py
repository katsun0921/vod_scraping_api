"""Slack承認フロー（仕様書 4.4）。

S/A判定とも、投稿するには :white_check_mark: での承認リアクションが必須。
自動投稿は行わない。:x: で反応すると取り消し、リアクションが無ければ保留のまま。

Incoming Webhookはメッセージts（タイムスタンプ）を返さずリアクションも読めないため、
Slack Web API（Bot Token）で chat.postMessage / reactions.get を使用する。

環境変数:
    SLACK_BOT_TOKEN           : リアクション読み取り・投稿に使うBot Token
    SLACK_APPROVAL_CHANNEL_ID : 承認依頼を投稿するチャンネルID
"""

import logging
import os

import requests

from news_bot.fetch import NewsEntry

logger = logging.getLogger(__name__)

_SLACK_API_BASE = "https://slack.com/api"
CANCEL_EMOJI = "x"
APPROVE_EMOJI = "white_check_mark"


def _headers() -> dict:
    return {"Authorization": f"Bearer {os.environ['SLACK_BOT_TOKEN']}"}


def notify_pending(entry: NewsEntry, rank: str, honbun: str, reply: str) -> tuple[str, str]:
    """承認依頼をSlackに投稿する。

    Returns:
        (channel_id, ts)
    """
    channel = os.environ["SLACK_APPROVAL_CHANNEL_ID"]
    instruction = f"*{rank}判定*：投稿するには :{APPROVE_EMOJI}: で反応してください。取り消す場合は :{CANCEL_EMOJI}: で反応してください。"

    text = (
        f"{instruction}\n\n"
        f"*{entry.title}*\n"
        f"媒体: {entry.source}\n"
        f"本文: {honbun}\n"
        f"リプライ: {reply}"
    )
    resp = requests.post(
        f"{_SLACK_API_BASE}/chat.postMessage",
        headers=_headers(),
        json={"channel": channel, "text": text},
        timeout=10,
    )
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack通知失敗: {data}")
    return data["channel"], data["ts"]


def _get_reaction_names(channel: str, ts: str) -> set[str]:
    resp = requests.get(
        f"{_SLACK_API_BASE}/reactions.get",
        headers=_headers(),
        params={"channel": channel, "timestamp": ts},
        timeout=10,
    )
    data = resp.json()
    if not data.get("ok"):
        logger.warning("リアクション取得失敗: %s", data)
        return set()
    reactions = data.get("message", {}).get("reactions", [])
    return {r["name"] for r in reactions}


def resolve(pending: dict) -> str:
    """承認キューの1行を解決する。

    Args:
        pending: sheets.get_pending_approvals() の1行
            （ランク / SlackチャンネルID / Slackメッセージts / 通知日時 を含む）

    Returns:
        "approved" | "cancelled" | "pending"
    """
    channel = pending["SlackチャンネルID"]
    ts = pending["Slackメッセージts"]
    reactions = _get_reaction_names(channel, ts)

    if CANCEL_EMOJI in reactions:
        return "cancelled"
    return "approved" if APPROVE_EMOJI in reactions else "pending"
