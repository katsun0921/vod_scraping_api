"""パイプライン統合エントリーポイント。

GitHub Actions cron から呼び出す想定（仕様書 3.: 1〜2時間おき推奨）。
1回のcron実行で fetch_cycle() と process_pending() を両方実行する。

    fetch_cycle()    : ニュース取得 → 重複チェック → 保存 → AI判定 → S/A判定は承認依頼
    process_pending(): 承認キューを確認し、承認/猶予経過分をX投稿

環境変数:
    NEWS_BOT_FETCH_LIMIT: 1回のfetch_cycle()で処理する件数の上限（テスト用、未設定なら無制限）
"""

import logging
import os
import sys

from news_bot import approval, compose, dedupe, judge, post_x
from news_bot.fetch import fetch_all
from news_bot.sheets import NewsBotSheets

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
logger = logging.getLogger(__name__)

_POSTABLE_RANKS = {"S", "A"}


def fetch_cycle() -> dict:
    """ニュース取得〜AI判定〜承認依頼までを1サイクル実行する。"""
    sheets = NewsBotSheets()
    sources = sheets.get_active_sources()
    existing_urls = sheets.get_existing_urls()

    entries = fetch_all(sources)
    fetch_limit = os.environ.get("NEWS_BOT_FETCH_LIMIT")
    if fetch_limit:
        entries = entries[: int(fetch_limit)]
    stats = {"fetched": len(entries), "duplicate": 0, "judged": 0, "queued": 0, "errors": 0}

    for entry in entries:
        if dedupe.is_duplicate(entry, existing_urls):
            sheets.append_news_item(title=entry.title, url=entry.url, source=entry.source, is_duplicate=True)
            stats["duplicate"] += 1
            continue

        try:
            result = judge.judge(entry)
        except Exception:
            logger.exception("AI判定失敗: %s", entry.url)
            stats["errors"] += 1
            continue

        rank = result["rank"]
        sheets.append_news_item(
            title=entry.title,
            url=entry.url,
            source=entry.source,
            summary=entry.summary,
            rank=rank,
            post_status="保存のみ" if rank not in _POSTABLE_RANKS else "承認待ち",
            judge_reason=result["reason"],
        )
        stats["judged"] += 1
        existing_urls.add(entry.url)

        if rank not in _POSTABLE_RANKS:
            continue

        try:
            composed = compose.compose(entry)
            channel, ts = approval.notify_pending(entry, rank, composed["honbun"], composed["reply"])
            sheets.enqueue_approval(
                url=entry.url,
                rank=rank,
                honbun=composed["honbun"],
                reply=composed["reply"],
                slack_channel_id=channel,
                slack_ts=ts,
            )
            stats["queued"] += 1
        except Exception:
            logger.exception("承認依頼失敗: %s", entry.url)
            stats["errors"] += 1

    logger.info("fetch_cycle 完了: %s", stats)
    return stats


def process_pending() -> dict:
    """承認キューを確認し、承認済み/猶予経過分をX投稿する。"""
    sheets = NewsBotSheets()
    pending_items = sheets.get_pending_approvals()
    stats = {"checked": len(pending_items), "posted": 0, "cancelled": 0, "still_pending": 0, "errors": 0}

    for item in pending_items:
        url = item["ニュースURL"]
        try:
            decision = approval.resolve(item)
        except Exception:
            logger.exception("承認状態の確認失敗: %s", url)
            stats["errors"] += 1
            continue

        if decision == "pending":
            stats["still_pending"] += 1
            continue

        if decision == "cancelled":
            sheets.update_approval_status(url, status="cancelled")
            sheets.update_news_item_status(url, post_status="取消")
            stats["cancelled"] += 1
            continue

        # decision == "approved"
        try:
            post_x.post_with_reply(item["本文"], item["リプライ本文"])
            sheets.append_post_history(news_id=url, honbun=item["本文"], reply=item["リプライ本文"])
            sheets.update_approval_status(url, status="posted")
            sheets.update_news_item_status(url, post_status="投稿済み")
            stats["posted"] += 1
        except Exception:
            logger.exception("X投稿失敗: %s", url)
            stats["errors"] += 1

    logger.info("process_pending 完了: %s", stats)
    return stats


if __name__ == "__main__":
    fetch_cycle()
    process_pending()
