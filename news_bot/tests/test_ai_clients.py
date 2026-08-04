import sys
import types

import pytest

# anthropic / openai は本番依存だがテストでは実通信しないためスタブで十分
for _name in ("anthropic", "openai"):
    _mod = sys.modules.setdefault(_name, types.ModuleType(_name))
setattr(sys.modules["anthropic"], "Anthropic", object)
setattr(sys.modules["openai"], "OpenAI", object)

from news_bot import ai_clients


def _block(block_type: str, text: str | None = None):
    block = types.SimpleNamespace(type=block_type)
    if text is not None:
        block.text = text
    return block


def _response(*blocks):
    return types.SimpleNamespace(content=list(blocks))


def test_returns_text_when_thinking_block_comes_first():
    """extended thinking 有効時は content[0] が ThinkingBlock になる。

    実運用で `AttributeError: 'ThinkingBlock' object has no attribute 'text'` が発生し、
    X投稿の構造化抽出が全バッチ失敗した（保存された作品がAI検索由来のみになった）。
    ThinkingBlock は .text を持たないため、type が "text" のブロックを探す必要がある。
    """
    thinking = types.SimpleNamespace(type="thinking", thinking="考え中...")
    response = _response(thinking, _block("text", '[{"index": 0}]'))
    assert ai_clients._first_text_block(response) == '[{"index": 0}]'


def test_returns_text_when_text_block_is_first():
    response = _response(_block("text", "本文"), _block("thinking"))
    assert ai_clients._first_text_block(response) == "本文"


def test_raises_when_no_text_block_exists():
    """テキストが1つも無い応答は、静かに空文字を返さず明示的に失敗させる。"""
    response = _response(types.SimpleNamespace(type="thinking", thinking="..."))
    with pytest.raises(ValueError, match="テキストブロックがありません"):
        ai_clients._first_text_block(response)


def test_call_claude_disables_thinking(monkeypatch):
    """thinkingを無効化して呼ぶ（max_tokensを思考で使い切る事故を防ぐ）。

    claude-sonnet-5 はthinking省略時にadaptive thinkingで動作し、max_tokensは
    thinking+テキストの合計に効く。実運用では思考だけで4096を使い切り、応答が
    types=['thinking']のみになってX投稿の構造化抽出が全バッチ失敗した。
    """
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        resp = _response(_block("text", "[]"))
        resp.usage = types.SimpleNamespace(
            cache_read_input_tokens=0, cache_creation_input_tokens=0, input_tokens=10
        )
        return resp

    client = types.SimpleNamespace(messages=types.SimpleNamespace(create=fake_create))
    monkeypatch.setattr(ai_clients, "Anthropic", lambda **_: client)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    ai_clients.call_claude("system", "user")

    assert captured["thinking"] == {"type": "disabled"}
