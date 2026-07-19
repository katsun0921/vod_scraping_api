"""Slack通知（仕様書 4.4）。

現在は投稿を手動運用としているため、S/A判定記事は投稿用テンプレートを
Slackに送るだけで、リアクションによる承認・自動投稿は行わない
（notify_manual_thread）。1回のrunでS/A判定になった記事は個別投稿ではなく
1つのXスレッド（連投）にまとめる。`notify_manual_post`は1記事単独版として
コードを残してあるが、main.pyの現在のフローからは呼ばれていない。

:white_check_mark: リアクションによる承認フロー＋自動投稿（notify_pending/
resolve）は、予算状況次第で自動化を再開できるようコードは残してある。
main.pyからは呼ばれていないため、再開時はfetch_cycle()内の呼び出し箇所の
コメントを参照して切り替えること。

Incoming Webhookはメッセージts（タイムスタンプ）を返さずリアクションも読めないため、
Slack Web API（Bot Token）で chat.postMessage / reactions.get を使用する。

環境変数:
    SLACK_BOT_TOKEN           : Slack投稿・リアクション読み取りに使うBot Token
    SLACK_APPROVAL_CHANNEL_ID : 通知を投稿するチャンネルID
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


def _post_message(text: str) -> tuple[str, str]:
    channel = os.environ["SLACK_APPROVAL_CHANNEL_ID"]
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


def notify_manual_thread(postable: list[tuple[NewsEntry, str]], thread_parts: list[str]) -> tuple[str, str]:
    """1回のrunでS/A判定になった記事をまとめたスレッド（連投）用テンプレートをSlackに送信する。

    Args:
        postable: [(NewsEntry, rank), ...]（対象記事一覧の表示用）
        thread_parts: compose.pack_thread() が返す、Xへの連投用テキストのリスト
            （①→②→③…の順に、②は①への返信、というように手動で連投する）

    Returns:
        (channel_id, ts)
    """
    article_lines = "\n".join(f"[{rank}] {entry.title}（{entry.source}）" for entry, rank in postable)
    thread_sections = "\n\n".join(
        f"――― 投稿 {i}/{len(thread_parts)} ―――\n{part}" for i, part in enumerate(thread_parts, 1)
    )
    text = (
        f"*S/A判定 {len(postable)}件をスレッドにまとめました*："
        f"以下を1つのXスレッドとして1→2→3…の順に手動で連投してください"
        f"（2件目以降は直前の投稿への返信として投稿）。\n\n"
        f"――― 対象記事 ―――\n{article_lines}\n\n"
        f"{thread_sections}"
    )
    return _post_message(text)


def notify_manual_post(entry: NewsEntry, rank: str, honbun: str, reply: str) -> tuple[str, str]:
    """[未使用・1記事単独投稿用] 手動投稿用のテンプレートをSlackに送信する（自動投稿は行わない）。

    Returns:
        (channel_id, ts)
    """
    text = (
        f"*{rank}判定*：以下を手動でXに投稿してください。\n\n"
        f"*{entry.title}*\n"
        f"媒体: {entry.source}\n\n"
        f"――― 本文 ―――\n{honbun}\n\n"
        f"――― リプライ ―――\n{reply}"
    )
    return _post_message(text)


def notify_pending(entry: NewsEntry, rank: str, honbun: str, reply: str) -> tuple[str, str]:
    """[未使用・自動投稿再開用] 承認依頼をSlackに投稿する。

    Returns:
        (channel_id, ts)
    """
    instruction = f"*{rank}判定*：投稿するには :{APPROVE_EMOJI}: で反応してください。取り消す場合は :{CANCEL_EMOJI}: で反応してください。"
    text = (
        f"{instruction}\n\n"
        f"*{entry.title}*\n"
        f"媒体: {entry.source}\n"
        f"本文: {honbun}\n"
        f"リプライ: {reply}"
    )
    return _post_message(text)


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
    """[未使用・自動投稿再開用] 承認キューの1行を解決する。

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
