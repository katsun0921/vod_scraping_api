"""AI判定で使う各プロバイダーへのAPI呼び出しラッパー。

judge.py から呼ばれる。system prompt + user content を渡し、
テキストのレスポンスをそのまま返す（JSONパースは呼び出し側で行う）。

Grok（xAI）はOpenAI互換のChat Completions APIを公開しているため、
openaiパッケージに base_url を変えて流用する。

環境変数:
    ANTHROPIC_API_KEY / ANTHROPIC_MODEL（既定 claude-sonnet-5）
    OPENAI_API_KEY    / OPENAI_MODEL（既定 gpt-4o）
    GROK_API_KEY      / GROK_MODEL（既定 grok-4）
"""

import logging
import os

from anthropic import Anthropic
from openai import OpenAI

logger = logging.getLogger(__name__)

_ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
_OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")
_GROK_MODEL = os.environ.get("GROK_MODEL", "grok-4")
_GROK_BASE_URL = "https://api.x.ai/v1"
_MAX_TOKENS = 4096  # judge.judge_batch()が最大_BATCH_SIZE件分のJSON配列を1レスポンスで返すため


def call_claude(system_prompt: str, user_content: str) -> str:
    """systemプロンプトにprompt cachingを効かせる。

    1回のrunでjudge()が記事ごとに同じsystem_prompt（judge_system_prompt.md）を
    繰り返し送るため、cache_controlを付けて2件目以降の入力コストを圧縮する
    （ヒットは`usage.cache_read_input_tokens`で確認できる）。
    """
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    response = client.messages.create(
        model=_ANTHROPIC_MODEL,
        max_tokens=_MAX_TOKENS,
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_content}],
    )
    logger.info(
        "Claude cache: read=%d create=%d input=%d",
        response.usage.cache_read_input_tokens,
        response.usage.cache_creation_input_tokens,
        response.usage.input_tokens,
    )
    return response.content[0].text


def call_openai(system_prompt: str, user_content: str) -> str:
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    response = client.chat.completions.create(
        model=_OPENAI_MODEL,
        max_tokens=_MAX_TOKENS,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    )
    return response.choices[0].message.content


def call_grok(system_prompt: str, user_content: str) -> str:
    client = OpenAI(api_key=os.environ["GROK_API_KEY"], base_url=_GROK_BASE_URL)
    response = client.chat.completions.create(
        model=_GROK_MODEL,
        max_tokens=_MAX_TOKENS,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    )
    return response.choices[0].message.content
