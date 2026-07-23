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
    SLACK_THEATER_CHANNEL_ID  : 劇場公開通知(notify_theater_discovered)専用のチャンネルID
                                （任意。未設定ならSLACK_APPROVAL_CHANNEL_IDに送る。
                                Botを対象チャンネルに招待しておくこと）
    SLACK_VOD_CHANNEL_ID      : VOD配信情報通知(notify_vod_discovered/
                                notify_vod_weekly_summary)専用のチャンネルID
                                （任意。未設定ならSLACK_APPROVAL_CHANNEL_IDに送る）
"""

import logging
import os
from datetime import date

import requests

from news_bot.fetch import NewsEntry

logger = logging.getLogger(__name__)

_SLACK_API_BASE = "https://slack.com/api"
CANCEL_EMOJI = "x"
APPROVE_EMOJI = "white_check_mark"


def _headers() -> dict:
    return {"Authorization": f"Bearer {os.environ['SLACK_BOT_TOKEN']}"}


def _post_message(text: str, thread_ts: str | None = None, channel: str | None = None) -> tuple[str, str]:
    channel = channel or os.environ["SLACK_APPROVAL_CHANNEL_ID"]
    payload = {"channel": channel, "text": text}
    if thread_ts is not None:
        payload["thread_ts"] = thread_ts
    resp = requests.post(
        f"{_SLACK_API_BASE}/chat.postMessage",
        headers=_headers(),
        json=payload,
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


def notify_theater_discovered(start: date, end: date, entries: list) -> tuple[str, str]:
    """AI発見した劇場公開作品を、親メッセージ+作品ごとのスレッド返信でSlackに通知する。

    theater_discover_cycle()が「劇場公開予定」シートへ保存した直後に呼ばれる。
    通知はあくまで確認依頼であり、承認そのものはシート上で行う（仕様書17. TODO#1）。

    Args:
        start, end: 対象期間（theater_calendar.week_range()）
        entries: 保存済みのfetch_theater.TheaterEntry一覧（release_dateは必ず設定済み）

    Returns:
        親メッセージの (channel_id, ts)
    """
    # 劇場公開専用チャンネルが設定されていればそちらへ、なければ承認チャンネルへ送る
    theater_channel = os.environ.get("SLACK_THEATER_CHANNEL_ID") or None

    parent_text = (
        f"*劇場公開予定 {start.isoformat()}〜{end.isoformat()}: "
        f"{len(entries)}件を発見し、シートに`承認待ち`で保存しました*\n"
        f"作品の詳細はこのスレッドに続きます。AIの検索結果のため誤り得ます — "
        f"「劇場公開予定」シートで実在・公開日を確認し、修正・不要行の削除をお願いします"
        f"（情報源が`AI検索(claude+openai)`の作品は両AIが一致しており確度高め）。"
    )
    channel, ts = _post_message(parent_text, channel=theater_channel)

    for entry in entries:
        detail = (
            f"*{entry.title}*\n"
            f"公開日: {entry.release_date.isoformat()} / 配給: {entry.distributor or '不明'}\n"
            f"公式URL: {entry.url or 'なし'}\n"
            f"情報源: {entry.source}"
        )
        try:
            # 親メッセージの投稿先チャンネル（解決済みID）に返信をぶら下げる
            _post_message(detail, thread_ts=ts, channel=channel)
        except Exception:
            # スレッド返信の1件失敗で残りの作品通知を止めない
            logger.exception("劇場公開スレッド返信失敗: %s", entry.title)

    return channel, ts


def notify_theater_added(entry, input_url: str, duplicate: bool = False) -> tuple[str, str]:
    """人間が指定したURLからの追記結果を劇場公開チャンネルに通知する（1件・スレッドなし）。

    Args:
        entry: fetch_theater.TheaterEntry（release_dateはNoneの場合あり）
        input_url: 人間が入力したURL（抽出結果の確認用に表示する）
        duplicate: 既にシートに存在したため追記しなかった場合True
    """
    theater_channel = os.environ.get("SLACK_THEATER_CHANNEL_ID") or None
    release = entry.release_date.isoformat() if entry.release_date else "抽出できず（シートで補完してください）"
    if duplicate:
        headline = "*URLの作品は既に「劇場公開予定」シートに存在するため追記しませんでした*"
    else:
        headline = "*URLから「劇場公開予定」シートに`承認待ち`で追記しました*（AI抽出のため内容の確認をお願いします）"
    text = (
        f"{headline}\n"
        f"*{entry.title}*\n"
        f"公開日: {release} / 配給: {entry.distributor or '不明'}\n"
        f"公式URL: {entry.url or 'なし'}\n"
        f"入力URL: {input_url}"
    )
    return _post_message(text, channel=theater_channel)


def notify_vod_discovered(start: date, end: date, entries: list) -> tuple[str, str]:
    """AI Web検索・X抽出で発見したVOD配信開始作品を、親メッセージ+作品ごとのスレッド返信で
    Slackに通知する（仕様書11.1）。

    vod_discover_cycle()が「VOD配信予定」シートへ保存した直後に呼ばれる。
    notify_theater_discovered()と同型。通知はあくまで確認依頼であり、承認そのものは
    シート上（投稿状態列の手動書き換え、または編集部おすすめ列への手動チェック）で行う。

    Args:
        start, end: 対象期間（vod_calendar.next_week_range()）
        entries: 保存済みのdiscover_vod.VodEntry一覧（available_fromは必ず設定済み）
    """
    vod_channel = os.environ.get("SLACK_VOD_CHANNEL_ID") or None

    parent_text = (
        f"*VOD配信予定 {start.isoformat()}〜{end.isoformat()}: "
        f"{len(entries)}件を発見し、シートに`承認待ち`で保存しました*\n"
        f"作品の詳細はこのスレッドに続きます。AI検索・X投稿からの抽出のため誤り得ます — "
        f"「VOD配信予定」シートで実在・配信開始日を確認し、修正・不要行の削除をお願いします"
        f"（情報源に複数ソースが含まれる作品は確度高め）。編集部おすすめにしたい作品は"
        f"`編集部おすすめ`列にチェックを入れ、`編集部コメント`列に一言コメントを入れてください。"
    )
    channel, ts = _post_message(parent_text, channel=vod_channel)

    for entry in entries:
        detail = (
            f"*{entry.title}*（{entry.service}）\n"
            f"配信開始日: {entry.available_from.isoformat()} / 配信種別: {entry.availability_type or '不明'}\n"
            f"公式URL: {entry.url or 'なし'}\n"
            f"情報源: {entry.source}"
        )
        try:
            _post_message(detail, thread_ts=ts, channel=channel)
        except Exception:
            # スレッド返信の1件失敗で残りの作品通知を止めない
            logger.exception("VOD配信予定スレッド返信失敗: %s", entry.title)

    return channel, ts


def notify_vod_weekly_summary(item_count: int, wp_post_url: str, x_thread_parts: list[str]) -> tuple[str, str]:
    """週次まとめ（`vod_publish`）の結果をSlackへ通知する（仕様書11.1）。

    WP投稿結果（下書きURL）とXスレッド投稿案テンプレートを1回のメッセージで送る。
    Xへの実際の投稿はx-news-bot仕様書4.4と同じく人間が手動で行う（自動投稿はしない）。
    """
    thread_sections = "\n\n".join(
        f"――― 投稿 {i}/{len(x_thread_parts)} ―――\n{part}" for i, part in enumerate(x_thread_parts, 1)
    )
    text = (
        f"*週次VODまとめを生成しました（{item_count}件）*\n"
        f"WP投稿（下書き）: {wp_post_url or '投稿失敗（ログを確認してください）'}\n\n"
        f"以下を1つのXスレッドとして1→2の順に手動で投稿してください"
        f"（2件目は1件目への返信）。\n\n{thread_sections}"
    )
    return _post_message(text, channel=os.environ.get("SLACK_VOD_CHANNEL_ID") or None)


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
