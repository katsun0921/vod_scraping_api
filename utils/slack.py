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


def notify_weekly_new_streaming_summary(items: list[dict]) -> None:
    """週次パッチで検知した新着配信の一覧を Slack に通知する。

    言語（ja / en）→ VOD サービスの順にグループ化して1通にまとめる。
    items が空の場合は通知しない。

    Args:
        items: 新着配信のリスト。各要素は以下のキーを持つ辞書:
            service: サービスキー名（例: "netflix"）
            lang   : 投稿の言語コード（"ja" / "en"）
            title  : 作品タイトル
            url    : WordPress 投稿の公開 URL（空文字可）
    """
    if not items:
        logger.info("Slack 通知スキップ: 今週の新着配信なし")
        return

    # lang → service → [(title, url), ...] にグループ化
    grouped: dict[str, dict[str, list[tuple[str, str]]]] = {}
    for item in items:
        lang = item.get("lang") or "ja"
        service = item.get("service", "")
        grouped.setdefault(lang, {}).setdefault(service, []).append(
            (item.get("title", ""), item.get("url", ""))
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
            for title, url in services[service]:
                lines.append(f"  • <{url}|{title}>" if url else f"  • {title}")

    _post({"text": "\n".join(lines)})
    logger.info("Slack 通知送信: 今週の新着配信一覧 %d件", len(items))
