"""AI統合レイヤー: X投稿の構造化抽出 + AI Web検索結果との重複マージ（仕様書7.5）。

X公式アカウント（fetch_vod_x.py）とAI Web検索（discover_vod.py）はそれぞれ生テキスト/
個別に構造化された結果を返すだけなので、本モジュールがXの生テキストを同じVodEntry
スキーマに変換し、両者を重複キーで統合する。

新規のAI APIプロバイダーは追加しない。既存の`news_bot/ai_clients.py`
（judge.pyと同じClaude Messages API呼び出しラッパー、prompt caching対応）を再利用する。
"""

import json
import logging
from datetime import date

from news_bot import ai_clients
from news_bot.discover_vod import SERVICES, VodEntry
from news_bot.fetch import NewsEntry
from news_bot.json_response import parse as parse_json_response
from news_bot.prompt_loader import load as load_prompt
from news_bot.vod_calendar import dedupe_key

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = load_prompt("vod_extract_system_prompt")
_BATCH_SIZE = 15  # judge.judge_batch()と同じくバッチ化してsystemプロンプトの重複送信を抑える


def _build_batch_user_content(posts: list[NewsEntry]) -> str:
    items = [
        {"index": i, "text": post.title, "url": post.url, "account": post.source}
        for i, post in enumerate(posts)
    ]
    return (
        f"以下の{len(posts)}件のX投稿からVOD配信開始情報を抽出してください。\n"
        f"{json.dumps(items, ensure_ascii=False)}"
    )


def _extract_batch(posts: list[NewsEntry]) -> list[VodEntry]:
    """1バッチ分のX投稿をまとめてClaudeに送り、配信開始告知のみVodEntryへ変換する。"""
    user_content = _build_batch_user_content(posts)
    text = ai_clients.call_claude(_SYSTEM_PROMPT, user_content).strip()
    try:
        results = parse_json_response(text)
    except json.JSONDecodeError:
        logger.error("X投稿構造化抽出のJSONパース失敗: %s", text)
        return []

    if not isinstance(results, list):
        logger.error("X投稿構造化抽出の応答形式が不正: %s", results)
        return []

    entries: list[VodEntry] = []
    for item in results:
        if not isinstance(item, dict) or not item.get("is_release"):
            continue  # 配信開始情報でないポスト（宣伝・キャンペーン等）は破棄
        index = item.get("index")
        if not isinstance(index, int) or not (0 <= index < len(posts)):
            continue

        title = (item.get("title") or "").strip()
        raw_date = (item.get("available_from") or "").strip()
        service_key = item.get("service") or ""
        if service_key not in SERVICES or not title or not raw_date:
            continue
        try:
            available_from = date.fromisoformat(raw_date)
        except ValueError:
            logger.warning("X抽出: 配信開始日が不正のためスキップ: %s (%r)", title, raw_date)
            continue

        entries.append(
            VodEntry(
                title=title,
                title_orig=(item.get("title_orig") or "").strip(),
                service=service_key,
                available_from=available_from,
                availability_type=(item.get("availability_type") or "").strip(),
                category=(item.get("category") or "").strip(),
                url=(item.get("official_url") or "").strip() or posts[index].url,
                source=f"X({posts[index].source})",
            )
        )
    return entries


def extract_from_x_posts(posts: list[NewsEntry]) -> list[VodEntry]:
    """取得済みのX投稿一覧をVodEntryへ構造化抽出する（Web検索は使わない）。

    `_BATCH_SIZE`件ごとにチャンク化し、1リクエストで判定する
    （judge.judge_batch()と同じくsystemプロンプトの重複送信回数を抑える）。
    1バッチの失敗は他バッチに伝播させない。
    """
    entries: list[VodEntry] = []
    for start in range(0, len(posts), _BATCH_SIZE):
        chunk = posts[start : start + _BATCH_SIZE]
        try:
            entries.extend(_extract_batch(chunk))
        except Exception:
            logger.exception("X投稿構造化抽出失敗（%d件）", len(chunk))
    return entries


def merge_all(x_entries: list[VodEntry], ai_entries: list[VodEntry]) -> list[VodEntry]:
    """X抽出結果とAI Web検索結果を重複キー（9.）でマージする。

    discover_theater.discover_all() / discover_vod.discover_all() と同じロジック:
    両ソースが同じ作品を挙げた場合は情報源を連結し、承認時の実在確度シグナルにする。
    空のフィールドは他方の値で補完する。
    """
    merged: dict[str, VodEntry] = {}
    for entry in [*x_entries, *ai_entries]:
        key = dedupe_key(entry.available_from.isoformat(), entry.service, entry.title)
        if key not in merged:
            merged[key] = entry
            continue
        existing = merged[key]
        if entry.source not in existing.source:
            existing.source = f"{existing.source}+{entry.source}"
        existing.url = existing.url or entry.url
        existing.title_orig = existing.title_orig or entry.title_orig
        existing.availability_type = existing.availability_type or entry.availability_type
        existing.category = existing.category or entry.category
    return list(merged.values())
