"""プロンプト（system prompt）ロードユーティリティ。

プロンプト本文は news_bot/prompts/*.md で専用管理し、コードから分離する。
"""

from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def load(name: str) -> str:
    """news_bot/prompts/{name}.md の内容を返す。"""
    return (_PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
