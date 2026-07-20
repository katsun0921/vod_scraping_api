"""AI判定（S/A/B/D ランク）。

仕様書 4.3: Claude APIへのプロンプトには過去の判定実例（few-shot）を含め、
判定のブレを抑える。出力は JSON: {rank, reason, confidence}。

他AI（ChatGPT）との精度比較テストのため、複数プロバイダーで並列判定できる。

1件ずつAPIを叩くとsystemプロンプトの重複送信でコストが嵩むため、
`judge_batch()`は複数記事を`_BATCH_SIZE`件ごとにまとめて1リクエストで判定する
（記事本文自体の入出力トークンは減らないが、システムプロンプトの重複回数を
1/`_BATCH_SIZE`に減らせる。あわせて同一バッチ内の記事同士を比較できるため、
重複記事（D判定）の精度も上がる）。

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
_BATCH_SIZE = 15

_PROVIDER_CALLS = {
    "claude": ai_clients.call_claude,
    "openai": ai_clients.call_openai,
    "grok": ai_clients.call_grok,
}


def _build_batch_user_content(entries: list[NewsEntry]) -> str:
    articles = [
        {"index": i, "title": e.title, "summary": e.summary, "媒体": e.source}
        for i, e in enumerate(entries)
    ]
    return (
        f"以下の{len(entries)}件のニュース記事をそれぞれ判定してください。\n"
        f"{json.dumps(articles, ensure_ascii=False)}"
    )


def _fallback_results(count: int, reason: str) -> list[dict]:
    return [{"rank": "D", "reason": reason, "confidence": 0.0} for _ in range(count)]


def _judge_batch_with_provider(provider: str, entries: list[NewsEntry]) -> list[dict]:
    """1プロバイダーで`entries`をまとめて判定し、entriesと同じ順序・件数のリストを返す。"""
    user_content = _build_batch_user_content(entries)
    text = _PROVIDER_CALLS[provider](_SYSTEM_PROMPT, user_content).strip()
    try:
        results = parse_json_response(text)
    except json.JSONDecodeError:
        logger.error("AI判定(バッチ)のJSONパース失敗(%s): %s", provider, text)
        return _fallback_results(len(entries), "判定結果のパース失敗")

    if not isinstance(results, list) or len(results) != len(entries):
        logger.error(
            "AI判定(バッチ)の件数不一致(%s): 期待%d件, 応答=%s", provider, len(entries), results
        )
        return _fallback_results(len(entries), "判定結果の件数不一致")

    validated = []
    for result in results:
        if not isinstance(result, dict) or result.get("rank") not in _VALID_RANKS:
            logger.error("AI判定(バッチ)の不正なrank(%s): %s", provider, result)
            validated.append({"rank": "D", "reason": "不正な判定結果", "confidence": 0.0})
        else:
            validated.append(result)
    return validated


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


def judge_batch(entries: list[NewsEntry]) -> list[dict]:
    """複数記事をまとめて設定されたAIプロバイダーで判定する。

    `_BATCH_SIZE`件ごとにチャンク分割して1プロバイダーにつき1リクエストで判定する。

    Returns:
        entriesと同じ順序・件数のリスト。各要素は
        {"rank": str, "reason": str, "confidence": float, "providers": {name: {rank, reason, confidence}}}
    """
    if not entries:
        return []

    providers = [p.strip() for p in os.environ.get("NEWS_BOT_JUDGE_PROVIDERS", "claude").split(",") if p.strip()]
    final_results: list[dict] = []

    for start in range(0, len(entries), _BATCH_SIZE):
        chunk = entries[start : start + _BATCH_SIZE]
        provider_results: dict[str, list[dict]] = {}

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(providers)) as executor:
            future_to_provider = {
                executor.submit(_judge_batch_with_provider, provider, chunk): provider
                for provider in providers
            }
            for future in concurrent.futures.as_completed(future_to_provider):
                provider = future_to_provider[future]
                try:
                    provider_results[provider] = future.result()
                except Exception:
                    logger.exception("AI判定(バッチ)失敗(%s): %d件", provider, len(chunk))
                    provider_results[provider] = _fallback_results(len(chunk), "API呼び出し失敗")

        for i in range(len(chunk)):
            per_provider = {provider: provider_results[provider][i] for provider in providers}
            final = _decide(providers, per_provider)
            final_results.append(
                {
                    "rank": final["rank"],
                    "reason": final["reason"],
                    "confidence": final.get("confidence", 0.0),
                    "providers": per_provider,
                }
            )

    return final_results
