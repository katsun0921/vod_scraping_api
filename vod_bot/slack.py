"""Slack Webhook 通知ユーティリティ。

環境変数:
    SLACK_WEBHOOK_URL: Slack Incoming Webhook URL（未設定時は通知しない）
"""

import logging
import os

import requests

logger = logging.getLogger(__name__)

_SERVICE_LABELS: dict[str, str] = {
    "amazon_prime_video": "Amazon Prime Video",
    "netflix": "Netflix",
    "hulu": "Hulu",
    "unext": "U-NEXT",
    "disney_plus": "Disney+",
    "dmm_tv": "DMM TV",
    "apple_tv": "Apple TV",
    "youtube": "YouTube",
    "crunchyroll": "Crunchyroll",
}

# 言語コード → セクション見出し（表示順もこの順）
_LANG_LABELS: dict[str, str] = {
    "ja": ":jp: 日本語",
    "en": ":us: English",
}


def _post(payload: dict) -> None:
    """Slack Webhook に POST する共通処理。

    SLACK_WEBHOOK_URL が未設定の場合は何もしない。
    失敗時は WARNING ログのみ出力し例外を raise しない。
    """
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook_url:
        return
    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        if not resp.ok:
            logger.warning("Slack 通知失敗: status=%d body=%s", resp.status_code, resp.text[:200])
    except Exception as e:
        logger.warning("Slack 通知エラー: %s", e)


def _format_item(title: str, url: str, scraping_url: str) -> str:
    """新着1件分の表示行を組み立てる。

    記事のフロント URL をタイトルにリンクし、その後ろにスクレイピングした
    配信ページ URL へのリンクを添える。

    Args:
        title       : 作品タイトル。
        url         : フロントエンド表示 URL（空なら title をそのまま表示）。
        scraping_url: スクレイピング対象の配信ページ URL（空なら省略）。

    Returns:
        Slack mrkdwn 形式の1行分の文字列（先頭の箇条書き記号は含まない）。
    """
    line = f"<{url}|{title}>" if url else title
    if scraping_url:
        line += f" ｜ <{scraping_url}|配信ページ>"
    return line


def notify_weekly_new_streaming_summary(items: list[dict]) -> None:
    """週次パッチで検知した新着配信の一覧を Slack に通知する。

    言語（ja / en）→ VOD サービスの順にグループ化して1通にまとめる。
    items が空の場合は通知しない。

    Args:
        items: 新着配信のリスト。各要素は以下のキーを持つ辞書:
            service     : サービスキー名（例: "netflix"）
            lang        : 投稿の言語コード（"ja" / "en"）
            title       : 作品タイトル
            url         : フロントエンド表示 URL（https://katsumascore.blog/{ja|en}/...）
            scraping_url: スクレイピング対象の配信ページ URL（空文字可）
    """
    if not items:
        logger.info("Slack 通知スキップ: 今週の新着配信なし")
        return

    # lang → service → [(title, url, scraping_url), ...] にグループ化
    grouped: dict[str, dict[str, list[tuple[str, str, str]]]] = {}
    for item in items:
        lang = item.get("lang") or "ja"
        service = item.get("service", "")
        grouped.setdefault(lang, {}).setdefault(service, []).append(
            (item.get("title", ""), item.get("url", ""), item.get("scraping_url", ""))
        )

    lines = [f":clapper: *今週の新着配信一覧* — 全{len(items)}件"]

    # ja / en を先頭に、それ以外の言語は末尾に
    lang_order = [l for l in _LANG_LABELS if l in grouped]
    lang_order += [l for l in grouped if l not in _LANG_LABELS]

    for lang in lang_order:
        services = grouped[lang]
        count = sum(len(v) for v in services.values())
        lines.append("")
        lines.append(f"{_LANG_LABELS.get(lang, lang)}（{count}件）")

        # サービスの表示順は _SERVICE_LABELS の定義順、未知のキーは末尾
        service_order = [s for s in _SERVICE_LABELS if s in services]
        service_order += [s for s in services if s not in _SERVICE_LABELS]

        for service in service_order:
            lines.append(f"*{_SERVICE_LABELS.get(service, service)}*")
            for title, url, scraping_url in services[service]:
                lines.append(f"  • {_format_item(title, url, scraping_url)}")

    _post({"text": "\n".join(lines)})
    logger.info("Slack 通知送信: 今週の新着配信一覧 %d件", len(items))


def _format_theater_item(item: dict, weeks: int) -> str:
    """劇場チェック結果1件分の表示行を組み立てる。

    Args:
        item : theater_patch.run() の ended / unknown 要素。
        weeks: 上映終了とみなす経過週数（理由ラベルの埋め込みに使用）。

    Returns:
        Slack mrkdwn 形式の1行分の文字列（先頭の箇条書き記号は含まない）。
    """
    title = item.get("title") or item.get("slug", "")
    url = item.get("url") or ""
    line = f"<{url}|{title}>" if url else title

    reason = item.get("reason", "")
    if reason == "url_gone":
        detail = "劇場URLが404/410"
    elif reason == "expired":
        days = item.get("elapsed_days")
        release_date = item.get("release_date") or ""
        elapsed = f"公開から{days // 7}週間経過" if isinstance(days, int) else f"公開から{weeks}週間以上経過"
        detail = f"{elapsed}（{release_date}）" if release_date else elapsed
    else:
        detail = "公開日・劇場URLが未入力で判定できず"

    line += f" ｜ {detail}"
    cinema_url = item.get("cinema_url") or ""
    if cinema_url:
        line += f" ｜ <{cinema_url}|劇場ページ>"
    return line


def notify_theater_showing_result(ended: list[dict], unknown: list[dict], weeks: int) -> None:
    """劇場公開チェックの結果を Slack に通知する。

    上映終了として自動でフラグを外した記事と、判定できず据え置いた記事を
    1通にまとめる。どちらも空の場合は通知しない。

    Args:
        ended  : 上映終了と判定しフラグを OFF にした記事のリスト。
        unknown: 判定不能で据え置いた記事のリスト。
        weeks  : 上映終了とみなす経過週数。
    """
    if not ended and not unknown:
        logger.info("Slack 通知スキップ: 劇場公開チェックの報告対象なし")
        return

    lines = [
        f":performing_arts: *劇場公開の週次チェック* — 上映終了 {len(ended)}件 / 判定不能 {len(unknown)}件"
    ]

    if ended:
        lines.append("")
        lines.append("*上映終了（現在上映中フラグを自動OFF）*")
        for item in ended:
            lines.append(f"  • {_format_theater_item(item, weeks)}")

    if unknown:
        lines.append("")
        lines.append("*判定不能（フラグはそのまま）*")
        for item in unknown:
            lines.append(f"  • {_format_theater_item(item, weeks)}")

    _post({"text": "\n".join(lines)})
    logger.info("Slack 通知送信: 劇場公開チェック 上映終了%d件 / 判定不能%d件", len(ended), len(unknown))
