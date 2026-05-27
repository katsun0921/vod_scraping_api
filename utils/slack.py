"""Slack Webhook 通知ユーティリティ。

環境変数:
    SLACK_WEBHOOK_URL: Slack Incoming Webhook URL（未設定時は通知しない）
"""

import logging
import os
from typing import Optional

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


def notify_new_streaming(title: str, service: str, url: str) -> None:
    """新規配信検知を Slack に通知する。

    Args:
        title  : 作品タイトル。
        service: サービスキー名（例: "netflix"）。
        url    : scraping_url（配信ページの URL）。
    """
    service_label = _SERVICE_LABELS.get(service, service)
    text = f":clapper: 新規配信検知\n*{title}* が *{service_label}* で配信開始\n{url}"
    _post({"text": text})
    logger.info("Slack 通知送信: title=%s service=%s", title, service_label)


# ──────────────────────────────────────────────
# JustWatch 月次バッチ用通知
# ──────────────────────────────────────────────

def notify_justwatch_start(total: int, limit: Optional[int] = None) -> None:
    """JustWatch 月次バッチ開始を通知する。

    Args:
        total: 処理対象の投稿数。
        limit: limit 指定がある場合はその値。
    """
    limit_text = f"（limit={limit}）" if limit is not None else ""
    text = f":mag: *JustWatch 月次バッチ開始*{limit_text}\n処理対象: *{total}* 件"
    _post({"text": text})
    logger.info("Slack 通知送信: JustWatch バッチ開始 total=%d", total)


def notify_justwatch_post_result(
    title: str,
    slug: str,
    registered: dict[str, str],
    unavailable: list[str],
    error: bool = False,
) -> None:
    """投稿1件あたりの JustWatch バッチ結果を Slack に通知する。

    Args:
        title      : 作品タイトル。
        slug       : WordPress スラッグ。
        registered : {service_key: url} — 新規登録したサービスと URL。
        unavailable: unavailable にしたサービスキーのリスト。
        error      : PATCH 失敗など処理エラーが発生した場合 True。
    """
    if error:
        icon = ":x:"
        status_line = "PATCH エラー"
    elif registered:
        icon = ":white_check_mark:"
        status_line = f"URL 登録: {len(registered)} サービス"
    else:
        icon = ":white_circle:"
        status_line = f"unavailable: {len(unavailable)} サービス"

    lines = [f"{icon} *{title}* (`{slug}`) — {status_line}"]

    for svc, url in registered.items():
        label = _SERVICE_LABELS.get(svc, svc)
        lines.append(f"  • {label}: {url}")

    if unavailable:
        labels = [_SERVICE_LABELS.get(s, s) for s in unavailable]
        lines.append(f"  • 配信なし: {', '.join(labels)}")

    _post({"text": "\n".join(lines)})


def notify_justwatch_summary(result: dict) -> None:
    """JustWatch 月次バッチ完了サマリーを Slack に通知する。

    Args:
        result: {"registered": int, "unavailable": int, "skipped": int, "errors": int}
    """
    registered = result.get("registered", 0)
    unavailable = result.get("unavailable", 0)
    skipped = result.get("skipped", 0)
    errors = result.get("errors", 0)

    icon = ":white_check_mark:" if errors == 0 else ":warning:"
    lines = [
        f"{icon} *JustWatch 月次バッチ完了*",
        f"  • URL 登録: *{registered}* サービス",
        f"  • 配信なし: *{unavailable}* サービス",
        f"  • スキップ: {skipped} 件",
        f"  • エラー: {errors} 件",
    ]
    _post({"text": "\n".join(lines)})
    logger.info("Slack 通知送信: JustWatch バッチ完了 %s", result)
