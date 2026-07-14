"""Slack承認フロー（仕様書 4.4）。

- S判定: 投稿文生成後Slack通知し、15分間の取り消し猶予後に自動投稿
- A判定: Slack通知し、承認リアクションがあってから投稿

Incoming Webhookはメッセージts（タイムスタンプ）を返さずリアクションも読めないため、
Slack Web API（Bot Token）で chat.postMessage / reactions.get を使用する。

環境変数:
    SLACK_BOT_TOKEN           : リアクション読み取り・投稿に使うBot Token
    SLACK_APPROVAL_CHANNEL_ID : 承認依頼を投稿するチャンネルID
    NEWS_BOT_S_RANK_WAIT_MINUTES: S判定の自動投稿までの猶予分数（既定15）
"""

import logging
import os
from datetime import datetime, timedelta, timezone

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
    wait_minutes = os.environ.get("NEWS_BOT_S_RANK_WAIT_MINUTES", "15")

    if rank == "S":
        instruction = f"*S判定*：{wait_minutes}分後に自動投稿されます。取り消す場合は :{CANCEL_EMOJI}: で反応してください。"
    else:
        instruction = f"*A判定*：投稿するには :{APPROVE_EMOJI}: で反応してください。取り消す場合は :{CANCEL_EMOJI}: で反応してください。"

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

    rank = pending["ランク"]
    if rank == "A":
        return "approved" if APPROVE_EMOJI in reactions else "pending"

    # S判定: 猶予時間経過で自動承認
    wait_minutes = int(os.environ.get("NEWS_BOT_S_RANK_WAIT_MINUTES", "15"))
    notified_at = datetime.fromisoformat(pending["通知日時"])
    if datetime.now(timezone.utc) >= notified_at + timedelta(minutes=wait_minutes):
        return "approved"
    return "pending"
