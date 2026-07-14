"""AI判定（S/A/B/D ランク）。

仕様書 4.3: Claude APIへのプロンプトには過去の判定実例（few-shot）を含め、
判定のブレを抑える。出力は JSON: {rank, reason, confidence}。
"""

import json
import logging
import os

from anthropic import Anthropic

from news_bot.fetch import NewsEntry
from news_bot.json_response import parse as parse_json_response
from news_bot.prompt_loader import load as load_prompt

logger = logging.getLogger(__name__)

_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
_SYSTEM_PROMPT = load_prompt("judge_system_prompt")


def judge(entry: NewsEntry) -> dict:
    """1件のニュースをS/A/B/D判定する。

    Returns:
        {"rank": str, "reason": str, "confidence": float}
    """
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    user_content = f"タイトル: {entry.title}\n概要: {entry.summary}\n媒体: {entry.source}"

    response = client.messages.create(
        model=_MODEL,
        max_tokens=300,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )
    text = response.content[0].text.strip()
    try:
        result = parse_json_response(text)
    except json.JSONDecodeError:
        logger.error("AI判定のJSONパース失敗: %s", text)
        return {"rank": "D", "reason": "判定結果のパース失敗", "confidence": 0.0}

    if result.get("rank") not in {"S", "A", "B", "D"}:
        logger.error("AI判定の不正なrank: %s", result)
        return {"rank": "D", "reason": "不正な判定結果", "confidence": 0.0}
    return result
