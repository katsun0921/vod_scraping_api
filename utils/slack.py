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
}


def notify_new_streaming(title: str, service: str, url: str) -> None:
    """新規配信検知を Slack に通知する。

    SLACK_WEBHOOK_URL が未設定の場合は何もしない。
    通知失敗時は WARNING ログを出力するだけで例外を raise しない。

    Args:
        title  : 作品タイトル。
        service: サービスキー名（例: "netflix"）。
        url    : scraping_url（配信ページの URL）。
    """
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook_url:
        return

    service_label = _SERVICE_LABELS.get(service, service)
    text = f":clapper: 新規配信検知\n*{title}* が *{service_label}* で配信開始\n{url}"

    try:
        resp = requests.post(webhook_url, json={"text": text}, timeout=10)
        if not resp.ok:
            logger.warning("Slack 通知失敗: status=%d body=%s", resp.status_code, resp.text[:200])
        else:
            logger.info("Slack 通知送信: title=%s service=%s", title, service_label)
    except Exception as e:
        logger.warning("Slack 通知エラー: %s", e)
