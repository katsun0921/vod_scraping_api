"""投稿文生成（本文＋リプライ分割）。

仕様書 4.5: メイン投稿はURLなし本文（$0.01/件）、リプライにURLを含める。
"""

import json
import logging
import os

from anthropic import Anthropic

from news_bot.fetch import NewsEntry

logger = logging.getLogger(__name__)

_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
_MAX_HONBUN_LENGTH = 140

_SYSTEM_PROMPT = f"""\
あなたはKatsumascore（映画・アニメ・ドラマレビューメディア）のX運用担当です。
入力されたニュースをもとに、X投稿用の文章を2つ生成してください。

- 本文: URLを含めない。{_MAX_HONBUN_LENGTH}文字以内。ニュースの要点を簡潔に、
  メディアの公式トーン（丁寧語・煽りすぎない）で。ハッシュタグは1〜2個まで。
- リプライ本文: 「詳細はこちら」等の一言＋URL。本文と合わせて読める内容にする。

出力は必ず以下のJSON形式のみ。前後に説明文を付けないこと。
{{"honbun": "...", "reply": "..."}}
"""


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
        result = json.loads(text)
    except json.JSONDecodeError:
        logger.error("投稿文生成のJSONパース失敗: %s", text)
        raise

    if entry.url not in result["reply"]:
        result["reply"] = f"{result['reply']} {entry.url}"
    return result
