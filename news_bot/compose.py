"""投稿文生成（本文＋リプライ分割）。

仕様書 4.5: メイン投稿はURLなし本文（$0.01/件）、リプライにURLを含める。
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
_MAX_HONBUN_LENGTH = 140

_SYSTEM_PROMPT = load_prompt("compose_system_prompt").replace(
    "{max_honbun_length}", str(_MAX_HONBUN_LENGTH)
)


def compose(entry: NewsEntry) -> dict:
    """投稿文（本文・リプライ）を生成する。

    Returns:
        {"honbun": str, "reply": str}
    """
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    user_content = f"タイトル: {entry.title}\n概要: {entry.summary}\nURL: {entry.url}"

    response = client.messages.create(
        model=_MODEL,
        max_tokens=400,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )
    text = response.content[0].text.strip()
    try:
        result = parse_json_response(text)
    except json.JSONDecodeError:
        logger.error("投稿文生成のJSONパース失敗: %s", text)
        raise

    if entry.url not in result["reply"]:
        result["reply"] = f"{result['reply']} {entry.url}"
    return result
