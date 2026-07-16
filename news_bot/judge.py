"""AI判定（S/A/B/D ランク）。

仕様書 4.3: Claude APIへのプロンプトには過去の判定実例（few-shot）を含め、
判定のブレを抑える。出力は JSON: {rank, reason, confidence}。

他AI（ChatGPT/Grok）との精度比較テストのため、複数プロバイダーで並列判定できる。

環境変数:
    NEWS_BOT_JUDGE_PROVIDERS: 判定に使うプロバイダーをカンマ区切りで指定
        （既定 "claude"。指定可能: claude / openai / grok）
    NEWS_BOT_JUDGE_DECISION: 最終ランクの決定方式（既定 "primary"）
        primary  : NEWS_BOT_JUDGE_PROVIDERSの先頭プロバイダーの判定を採用
        majority : 有効プロバイダー間の多数決（同数の場合はランクが高い方を優先）
"""

import concurrent.futures
import json
import logging
import os

from news_bot import ai_clients
from news_bot.fetch import NewsEntry
from news_bot.json_response import parse as parse_json_response
from news_bot.prompt_loader import load as load_prompt

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = load_prompt("judge_system_prompt")
_VALID_RANKS = {"S", "A", "B", "D"}
_RANK_ORDER = {"D": 0, "B": 1, "A": 2, "S": 3}

_PROVIDER_CALLS = {
    "claude": ai_clients.call_claude,
    "openai": ai_clients.call_openai,
    "grok": ai_clients.call_grok,
}


def _judge_with_provider(provider: str, user_content: str) -> dict:
    text = _PROVIDER_CALLS[provider](_SYSTEM_PROMPT, user_content).strip()
    try:
        result = parse_json_response(text)
    except json.JSONDecodeError:
        logger.error("AI判定のJSONパース失敗(%s): %s", provider, text)
        return {"rank": "D", "reason": "判定結果のパース失敗", "confidence": 0.0}

    if result.get("rank") not in _VALID_RANKS:
        logger.error("AI判定の不正なrank(%s): %s", provider, result)
        return {"rank": "D", "reason": "不正な判定結果", "confidence": 0.0}
    return result


def _decide(providers: list[str], results: dict[str, dict]) -> dict:
    """最終的に採用する判定結果（rank/reason/confidence）を決める。"""
    if os.environ.get("NEWS_BOT_JUDGE_DECISION", "primary") != "majority":
        return results[providers[0]]

    counts: dict[str, int] = {}
    for provider in providers:
        rank = results[provider]["rank"]
        counts[rank] = counts.get(rank, 0) + 1
    final_rank = max(counts, key=lambda rank: (counts[rank], _RANK_ORDER[rank]))

    for provider in providers:
        if results[provider]["rank"] == final_rank:
            return results[provider]
    return results[providers[0]]


def judge(entry: NewsEntry) -> dict:
    """1件のニュースを設定されたAIプロバイダーで判定する。

    Returns:
        {"rank": str, "reason": str, "confidence": float, "providers": {name: {rank, reason, confidence}}}
    """
    providers = [p.strip() for p in os.environ.get("NEWS_BOT_JUDGE_PROVIDERS", "claude").split(",") if p.strip()]
    user_content = f"タイトル: {entry.title}\n概要: {entry.summary}\n媒体: {entry.source}"

    results: dict[str, dict] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(providers)) as executor:
        future_to_provider = {
            executor.submit(_judge_with_provider, provider, user_content): provider
            for provider in providers
        }
        for future in concurrent.futures.as_completed(future_to_provider):
            provider = future_to_provider[future]
            try:
                results[provider] = future.result()
            except Exception:
                logger.exception("AI判定失敗(%s): %s", provider, entry.url)
                results[provider] = {"rank": "D", "reason": "API呼び出し失敗", "confidence": 0.0}

    final = _decide(providers, results)
    return {
        "rank": final["rank"],
        "reason": final["reason"],
        "confidence": final.get("confidence", 0.0),
        "providers": results,
    }
