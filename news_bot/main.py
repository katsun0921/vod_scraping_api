"""パイプライン統合エントリーポイント。

GitHub Actions cron から呼び出す想定（仕様書 3.: 1〜2時間おき推奨）。

    fetch_cycle():        RSS取得 → 重複チェック → 保存 → AI判定 → S/A判定はスレッドにまとめてSlack送信
    fetch_x_cycle(region): 公式Xアカウント取得（地域ごとに1日1回）→ 上記と同じ処理

1回のrunでS/A判定になった記事は個別に投稿する代わりに1つのXスレッド（連投）にまとめる
（`compose.compose_headline()` + `compose.pack_thread()` + `approval.notify_manual_thread()`）。
投稿は現在手動運用のため、両サイクルともSlackへテンプレートを送るところまでで終わる。
X APIへの自動投稿（process_pending() + post_x.post_with_reply）は予算状況次第で
再開できるようコードは残してあるが、__main__からは呼び出していない。

環境変数:
    NEWS_BOT_FETCH_LIMIT: 1回のサイクルで処理する件数の上限（テスト用、未設定なら無制限）
"""

import logging
import os
import sys

from news_bot import approval, compose, dedupe, fetch_x, judge, post_x
from news_bot.fetch import NewsEntry, fetch_all
from news_bot.sheets import NewsBotSheets

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
logger = logging.getLogger(__name__)

_POSTABLE_RANKS = {"S", "A"}


def _notify_thread(postable: list[tuple[NewsEntry, str]]) -> None:
    """S/A判定記事一覧を1つのXスレッド（連投）用テンプレートにまとめてSlackへ送信する。"""
    lines = []
    for entry, rank in postable:
        headline = compose.compose_headline(entry, rank)
        lines.append(f"【{rank}】{headline} {entry.url}")
    thread_parts = compose.pack_thread(lines)
    approval.notify_manual_thread(postable, thread_parts)
    # 自動承認フロー（自動投稿）を再開する場合は、記事ごとにcompose.compose()で
    # 本文/リプライを生成し、approval.notify_pending() + sheets.enqueue_approval()
    # に切り替えること（このスレッドまとめ機能とは別立てで実装する）。


def _process_entries(entries: list[NewsEntry], sheets: NewsBotSheets, existing_urls: set[str]) -> dict:
    """記事一覧を重複チェック→AI判定まで処理し、S/A判定分はまとめて1回だけSlack通知する。

    fetch_cycle() / fetch_x_cycle() の共通処理。取得元（RSS/X）に依存しない。
    """
    fetch_limit = os.environ.get("NEWS_BOT_FETCH_LIMIT")
    if fetch_limit:
        entries = entries[: int(fetch_limit)]
    stats = {"fetched": len(entries), "duplicate": 0, "judged": 0, "queued": 0, "errors": 0}
    postable: list[tuple[NewsEntry, str]] = []

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
            post_status="保存のみ" if rank not in _POSTABLE_RANKS else "手動投稿待ち",
            judge_reason=result["reason"],
            provider_results=result["providers"],
        )
        stats["judged"] += 1
        existing_urls.add(entry.url)

        if rank in _POSTABLE_RANKS:
            postable.append((entry, rank))

    if postable:
        try:
            _notify_thread(postable)
            stats["queued"] = len(postable)
        except Exception:
            logger.exception("スレッドテンプレート送信失敗（%d件）", len(postable))
            stats["errors"] += 1

    return stats


def fetch_cycle() -> dict:
    """RSSニュース取得〜AI判定〜投稿テンプレートのSlack送信までを1サイクル実行する。"""
    sheets = NewsBotSheets()
    sources = sheets.get_active_sources()
    existing_urls = sheets.get_existing_urls()

    entries = fetch_all(sources)
    stats = _process_entries(entries, sheets, existing_urls)
    logger.info("fetch_cycle 完了: %s", stats)
    return stats


def fetch_x_cycle(region: str) -> dict:
    """公式Xアカウントの投稿取得〜AI判定〜投稿テンプレートのSlack送信までを1サイクル実行する。

    日本メディア・アメリカメディアを分けてコスト管理するため、地域ごとに1日1回の実行を想定
    （仕様書「Xポストのニュースソース化」）。

    Args:
        region: 「公式X一覧」シートの"地域"列の値（例: "日本" / "アメリカ"）
    """
    sheets = NewsBotSheets()
    accounts = sheets.get_active_x_accounts(region)
    existing_urls = sheets.get_existing_urls()

    entries, updated_states = fetch_x.fetch_all_x(accounts)
    stats = _process_entries(entries, sheets, existing_urls)

    for handle, state in updated_states.items():
        if state["user_id"] is None:
            continue
        sheets.update_x_account_state(handle, user_id=state["user_id"], since_id=state["since_id"])

    logger.info("fetch_x_cycle(%s) 完了: %s", region, stats)
    return stats


def process_pending() -> dict:
    """[未使用・自動投稿再開用] 承認キューを確認し、承認済み分をX投稿する。

    投稿を手動運用にしたため現在は__main__から呼ばれていない。自動投稿を
    再開する場合は、fetch_cycle()内をnotify_pending/enqueue_approval経由に
    戻した上で、__main__のコメントを外すこと。
    """
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
    # 引数無し: RSS取得（fetch_cycle）。 "x <地域>": 公式Xアカウント取得（fetch_x_cycle）。
    if len(sys.argv) > 1 and sys.argv[1] == "x":
        fetch_x_cycle(sys.argv[2] if len(sys.argv) > 2 else "日本")
    else:
        fetch_cycle()
    # 投稿は手動運用のため自動投稿は呼ばない。自動化を再開する場合はコメントを外す。
    # process_pending()
