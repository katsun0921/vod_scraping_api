"""パイプライン統合エントリーポイント。

GitHub Actions cron から呼び出す想定（仕様書 3.: 1〜2時間おき推奨）。

    fetch_cycle():        RSS取得 → 重複チェック → 保存 → AI判定 → S/A判定はスレッドにまとめてSlack送信
    fetch_x_cycle(region): 公式Xアカウント取得（地域ごとに1日1回）→ 上記と同じ処理
    theater_cycle():      「劇場情報源」シート巡回による劇場公開情報取得
                           （レイヤー1データソース撤回により現在シート未登録＝実質未使用）
    theater_discover_cycle(): AI Web検索（Claude/OpenAI併用）による劇場公開作品の発見（週次）
                           → 対象期間フィルタ → 重複チェック → 承認待ちとして保存
                           （docs/feature/theater-release-calendar-spec.md。人間の承認後の
                           下流処理（週次サマリー・Slack/WP投稿）は未実装、同spec 17.のTODO参照）
    vod_discover_cycle():  AI Web検索 + VOD公式Xアカウント投稿の構造化抽出によるVOD配信開始
                           作品の発見（週次、木曜）→ 重複マージ → Katsumascore照合 → 承認待ち
                           として保存（docs/feature/vod-release-calendar-spec.md 7./10.）
    vod_publish_cycle():   承認済みのVOD配信予定行から週次まとめを生成し、WP CPT投稿+
                           Xスレッド案のSlack通知まで行う（週次、月曜。同spec 11.）

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
from datetime import date

from news_bot import (
    approval,
    compose,
    compose_vod,
    dedupe,
    discover_theater,
    discover_vod,
    extract_vod,
    fetch_theater,
    fetch_vod_x,
    fetch_x,
    judge,
    post_x,
    theater_calendar,
    vod_calendar,
    wp_client,
)
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
    重複していない記事はjudge.judge_batch()でまとめて1〜数リクエストで判定する
    （記事ごとに1リクエストずつ叩くとsystemプロンプトの重複送信でコストが嵩むため）。
    """
    fetch_limit = os.environ.get("NEWS_BOT_FETCH_LIMIT")
    if fetch_limit:
        entries = entries[: int(fetch_limit)]
    stats = {"fetched": len(entries), "duplicate": 0, "judged": 0, "queued": 0, "errors": 0}

    new_entries: list[NewsEntry] = []
    for entry in entries:
        if dedupe.is_duplicate(entry, existing_urls):
            sheets.append_news_item(title=entry.title, url=entry.url, source=entry.source, is_duplicate=True)
            stats["duplicate"] += 1
        else:
            new_entries.append(entry)

    try:
        judge_results = judge.judge_batch(new_entries)
    except Exception:
        logger.exception("AI判定(バッチ)失敗: %d件", len(new_entries))
        stats["errors"] += len(new_entries)
        judge_results = []

    postable: list[tuple[NewsEntry, str]] = []
    for entry, result in zip(new_entries, judge_results):
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
    logger.info("公式X取得対象: region=%s accounts=%d", region, len(accounts))
    existing_urls = sheets.get_existing_urls()

    entries, updated_states = fetch_x.fetch_all_x(accounts)
    logger.info("公式X取得結果: region=%s entries=%d updated_accounts=%d", region, len(entries), len(updated_states))
    stats = _process_entries(entries, sheets, existing_urls)

    for handle, state in updated_states.items():
        if state["user_id"] is None:
            continue
        sheets.update_x_account_state(handle, user_id=state["user_id"], since_id=state["since_id"])

    logger.info("fetch_x_cycle(%s) 完了: %s", region, stats)
    return stats


def theater_cycle() -> dict:
    """劇場公開情報取得〜対象期間フィルタ〜重複チェック〜保存までを1サイクル実行する。

    取得元は「劇場情報源」シートに登録された行（fetch_theater.fetch_all）。
    Katsumascore照合・SNS優先度判定・投稿案生成・Slack通知は未実装のため、
    保存時は投稿状態="未判定"のまま（docs/feature/theater-release-calendar-spec.md 17.のTODO参照）。
    """
    sheets = NewsBotSheets()
    start, end = theater_calendar.week_range(date.today())
    sources = sheets.get_active_theater_sources()
    existing_keys = sheets.get_existing_theater_keys()

    entries = fetch_theater.fetch_all(sources, start, end)
    stats = {"fetched": len(entries), "no_date": 0, "out_of_range": 0, "duplicate": 0, "saved": 0}

    for entry in entries:
        if entry.release_date is None:
            stats["no_date"] += 1
            continue
        if not theater_calendar.in_range(entry.release_date, start, end):
            stats["out_of_range"] += 1
            continue

        release_date_str = entry.release_date.isoformat()
        key = theater_calendar.dedupe_key(release_date_str, entry.title)
        if key in existing_keys:
            stats["duplicate"] += 1
            continue

        sheets.append_theater_item(
            release_date=release_date_str,
            title=entry.title,
            dedupe_key=key,
            original_title=entry.original_title,
            category=entry.category,
            official_url=entry.url,
            source=entry.source,
        )
        existing_keys.add(key)
        stats["saved"] += 1

    logger.info("theater_cycle(%s〜%s) 完了: %s", start, end, stats)
    return stats


def theater_discover_cycle() -> dict:
    """AIのWeb検索で対象週の劇場公開作品を発見し、承認待ちとして保存する。

    レイヤー1データソース（特定サイトの自動取得）が規約上すべて撤回されたため、
    Claude/OpenAIのWeb検索併用で事実情報のみを収集する方式（discover_theater.py）。
    AIの結果は誤り得るため投稿状態="承認待ち"で保存し、人間がシートを確認・
    修正・承認する。承認後の下流処理（週次サマリー・Slack/WP投稿）は未実装。
    """
    sheets = NewsBotSheets()
    start, end = theater_calendar.week_range(date.today())
    existing_keys = sheets.get_existing_theater_keys()

    entries = discover_theater.discover_all(start, end)
    stats = {"discovered": len(entries), "out_of_range": 0, "duplicate": 0, "saved": 0, "notified": 0}

    saved_entries = []
    for entry in entries:
        if not theater_calendar.in_range(entry.release_date, start, end):
            stats["out_of_range"] += 1
            continue

        release_date_str = entry.release_date.isoformat()
        key = theater_calendar.dedupe_key(release_date_str, entry.title)
        if key in existing_keys:
            stats["duplicate"] += 1
            continue

        sheets.append_theater_item(
            release_date=release_date_str,
            title=entry.title,
            dedupe_key=key,
            distributor=entry.distributor,
            official_url=entry.url,
            source=entry.source,
            post_status="承認待ち",
        )
        existing_keys.add(key)
        saved_entries.append(entry)
        stats["saved"] += 1

    # 新規保存分があればSlackに親メッセージ+作品ごとのスレッド返信で確認依頼を送る。
    # 通知失敗でもシート保存は完了しているためサイクル自体は失敗させない。
    if saved_entries:
        try:
            approval.notify_theater_discovered(start, end, saved_entries)
            stats["notified"] = len(saved_entries)
        except Exception:
            logger.exception("劇場公開Slack通知失敗（%d件）", len(saved_entries))

    logger.info("theater_discover_cycle(%s〜%s) 完了: %s", start, end, stats)
    return stats


def theater_add_url(url: str) -> dict:
    """人間が見つけた劇場公開情報のURLから事実を抽出し、シートに承認待ちで追記する。

    週次のAI発見（theater_discover_cycle）と違い対象期間ではフィルタしない
    （人間は来月公開の作品のURLを入れることもあるため）。公開日が抽出できなかった
    場合もメモ付きで保存し、人間がシートで補完する。抽出失敗（タイトルすら取れない）
    は例外で落とし、Actionsの実行失敗として入力者に見えるようにする。
    """
    sheets = NewsBotSheets()
    entry = discover_theater.extract_from_url(url)
    stats = {"duplicate": 0, "saved": 0, "notified": 0}

    dedupe_key = ""
    memo = ""
    if entry.release_date is not None:
        release_date_str = entry.release_date.isoformat()
        dedupe_key = theater_calendar.dedupe_key(release_date_str, entry.title)
        if dedupe_key in sheets.get_existing_theater_keys():
            stats["duplicate"] = 1
            logger.info("既存のためスキップ: %s (%s)", entry.title, release_date_str)
    else:
        release_date_str = ""
        memo = "公開日をAI抽出できず。シートで補完してください"

    if not stats["duplicate"]:
        sheets.append_theater_item(
            release_date=release_date_str,
            title=entry.title,
            dedupe_key=dedupe_key,
            distributor=entry.distributor,
            official_url=entry.url,
            source=entry.source,
            post_status="承認待ち",
            memo=memo,
        )
        stats["saved"] = 1

    try:
        approval.notify_theater_added(entry, input_url=url, duplicate=bool(stats["duplicate"]))
        stats["notified"] = 1
    except Exception:
        logger.exception("URL追記Slack通知失敗: %s", url)

    logger.info("theater_add_url(%s) 完了: %s", url, stats)
    return stats


def vod_discover_cycle() -> dict:
    """AI Web検索 + VOD公式Xアカウント投稿の構造化抽出で対象週のVOD配信開始作品を発見し、
    承認待ちとして保存する（docs/feature/vod-release-calendar-spec.md 7./9./10.）。

    discover_vod.discover_all()（Claude/OpenAI併用のAI Web検索）と
    fetch_vod_x.fetch_all_vod_x() + extract_vod.extract_from_x_posts()（X公式アカウント投稿の
    構造化抽出）の両方から収集し、extract_vod.merge_all()で重複キーにより統合する（仕様書7.5）。
    保存前にKatsumascore照合（10.）を行い、既存レビュー記事が見つかればURL/post_idを付与する。
    1ソースの失敗は他方に伝播させない。
    """
    sheets = NewsBotSheets()
    start, end = vod_calendar.next_week_range(date.today())
    existing_keys = sheets.get_existing_vod_keys()

    ai_entries: list = []
    try:
        ai_entries = discover_vod.discover_all(start, end)
    except Exception:
        logger.exception("VOD AI Web検索失敗")

    x_entries: list = []
    try:
        x_accounts = sheets.get_active_vod_x_accounts()
        x_posts, updated_states = fetch_vod_x.fetch_all_vod_x(x_accounts)
        x_entries = extract_vod.extract_from_x_posts(x_posts)
        for handle, state in updated_states.items():
            if state["user_id"] is None:
                continue
            sheets.update_vod_x_account_state(handle, user_id=state["user_id"], since_id=state["since_id"])
    except Exception:
        logger.exception("VOD公式Xアカウント取得・抽出失敗")

    merged = extract_vod.merge_all(x_entries, ai_entries)
    stats = {"discovered": len(merged), "out_of_range": 0, "duplicate": 0, "saved": 0, "notified": 0}

    saved_entries = []
    for entry in merged:
        if not vod_calendar.in_range(entry.available_from, start, end):
            stats["out_of_range"] += 1
            continue

        key = vod_calendar.dedupe_key(entry.available_from.isoformat(), entry.service, entry.title)
        if key in existing_keys:
            stats["duplicate"] += 1
            continue

        katsumascore_url = ""
        wp_post_id = ""
        try:
            post = wp_client.find_post_by_title(entry.title, entry.title_orig)
            if post:
                katsumascore_url = post.get("link", "")
                wp_post_id = str(post.get("id", "")) if post.get("id") else ""
        except Exception:
            logger.exception("Katsumascore照合失敗: %s", entry.title)

        sheets.append_vod_item(
            release_date=entry.available_from.isoformat(),
            title=entry.title,
            title_orig=entry.title_orig,
            service=entry.service,
            category=entry.category,
            availability_type=entry.availability_type,
            official_url=entry.url,
            source=entry.source,
            katsumascore_url=katsumascore_url,
            wp_post_id=wp_post_id,
            dedupe_key=key,
            post_status="承認待ち",
        )
        existing_keys.add(key)
        saved_entries.append(entry)
        stats["saved"] += 1

    # 新規保存分があればSlackに確認依頼を送る。通知失敗でもシート保存は完了しているため
    # サイクル自体は失敗させない（theater_discover_cycle()と同じ方針）。
    if saved_entries:
        try:
            approval.notify_vod_discovered(start, end, saved_entries)
            stats["notified"] = len(saved_entries)
        except Exception:
            logger.exception("VOD配信予定Slack通知失敗（%d件）", len(saved_entries))

    logger.info("vod_discover_cycle(%s〜%s) 完了: %s", start, end, stats)
    return stats


def vod_publish_cycle() -> dict:
    """承認済みのVOD配信予定行から週次まとめを生成し、WP CPT投稿+Xスレッド案の
    Slack通知まで行う（docs/feature/vod-release-calendar-spec.md 11.）。

    対象は当週月曜〜日曜が配信開始日、かつ投稿状態=承認済みの行。対象0件の場合は
    何もしない（空のWP投稿・Slack通知を出さない）。
    """
    sheets = NewsBotSheets()
    start, end = vod_calendar.current_week_range(date.today())
    items = sheets.get_approved_vod_items(start, end)
    stats = {"target": len(items), "posted": 0, "notified": 0}

    if not items:
        logger.info("vod_publish_cycle(%s〜%s): 対象0件", start, end)
        return stats

    label = compose_vod.week_label(start.year, start.month, start.day)
    title = compose_vod.build_wp_title(label)
    content_html = compose_vod.build_wp_content(items)

    wp_post_url = ""
    try:
        wp_post = wp_client.create_post(title, content_html)
        wp_post_url = wp_post.get("link", "")
        stats["posted"] = 1
    except Exception:
        logger.exception("VOD週次まとめWP投稿失敗")

    x_thread_parts = compose_vod.build_x_thread(items, wp_post_url)
    try:
        approval.notify_vod_weekly_summary(len(items), wp_post_url, x_thread_parts)
        stats["notified"] = 1
    except Exception:
        logger.exception("VOD週次まとめSlack通知失敗")

    for item in items:
        key = item.get("重複キー")
        if key:
            sheets.update_vod_item_status(key, post_status="投稿済み")

    logger.info("vod_publish_cycle(%s〜%s) 完了: %s", start, end, stats)
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
    # "theater": 劇場情報源シート巡回（theater_cycle、現在シート未登録のため実質未使用）。
    # "theater_discover": AI Web検索による劇場公開作品の発見（theater_discover_cycle）。
    # "theater_add <URL>": 人間が見つけたURLからの追記（theater_add_url）。
    # "vod_discover": AI Web検索+VOD公式X投稿抽出によるVOD配信開始作品の発見（vod_discover_cycle）。
    # "vod_publish": 承認済みVOD配信予定の週次まとめ生成・WP投稿・Slack通知（vod_publish_cycle）。
    if len(sys.argv) > 1 and sys.argv[1] == "x":
        fetch_x_cycle(sys.argv[2] if len(sys.argv) > 2 else "日本")
    elif len(sys.argv) > 1 and sys.argv[1] == "theater":
        theater_cycle()
    elif len(sys.argv) > 1 and sys.argv[1] == "theater_discover":
        theater_discover_cycle()
    elif len(sys.argv) > 1 and sys.argv[1] == "theater_add":
        if len(sys.argv) < 3:
            raise SystemExit("使い方: python -m news_bot.main theater_add <URL>")
        theater_add_url(sys.argv[2])
    elif len(sys.argv) > 1 and sys.argv[1] == "vod_discover":
        vod_discover_cycle()
    elif len(sys.argv) > 1 and sys.argv[1] == "vod_publish":
        vod_publish_cycle()
    else:
        fetch_cycle()
    # 投稿は手動運用のため自動投稿は呼ばない。自動化を再開する場合はコメントを外す。
    # process_pending()
