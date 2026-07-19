"""投稿文生成（本文＋リプライ分割 / 複数記事のスレッド化）。

仕様書 4.5: メイン投稿はURLなし本文（$0.01/件）、リプライにURLを含める。

1回のrunでS/A判定になった記事は、個別に投稿する代わりに1つのスレッド
（連投）にまとめる。各記事は「見出し文＋URL」の1行になり、`pack_thread()`が
1ツイート分の文字数上限（既定150字）ごとに機械的にパッキングする
（行の途中では分割しないため、URLが分断されることはない）。
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
_MAX_THREAD_PART_LENGTH = 150

_SYSTEM_PROMPT = load_prompt("compose_system_prompt").replace(
    "{max_honbun_length}", str(_MAX_HONBUN_LENGTH)
)
_THREAD_HEADLINE_SYSTEM_PROMPT = load_prompt("thread_headline_system_prompt")


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


def compose_headline(entry: NewsEntry) -> str:
    """スレッド用の1行見出し文（URLなし）を生成する。呼び出し側でURLを付与する。"""
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    user_content = f"タイトル: {entry.title}\n概要: {entry.summary}"

    response = client.messages.create(
        model=_MODEL,
        max_tokens=150,
        system=_THREAD_HEADLINE_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )
    return response.content[0].text.strip()


def pack_thread(lines: list[str], limit: int = _MAX_THREAD_PART_LENGTH) -> list[str]:
    """複数行を連投（スレッド）用のパーツにパッキングする。

    各行を1単位として扱い、行の途中では分割しない（URLが分断されるのを防ぐ）。
    1行だけでlimitを超える場合はその行単独で1パーツにする（それ以上は分割できない）。
    """
    parts: list[str] = []
    current: list[str] = []
    current_len = 0

    for line in lines:
        added_len = len(line) if not current else len(line) + 1  # +1: 改行分
        if current and current_len + added_len > limit:
            parts.append("\n".join(current))
            current = []
            current_len = 0
            added_len = len(line)
        current.append(line)
        current_len += added_len

    if current:
        parts.append("\n".join(current))
    return parts
