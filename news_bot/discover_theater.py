"""AIのWeb検索による劇場公開作品の発見（レイヤー1データソースの代替）。

仕様書: `docs/feature/theater-release-calendar-spec.md`
経緯: `docs/feature/theater-sources-candidates.md`
（映画.com/PR TIMES/TMDb等の特定サイトからの機械的な自動取得は、各サービスの
利用規約（複製・転載禁止、有償目的利用の禁止、非商用誓約）とKatsumascoreの
AdSense収益化が衝突するためすべて撤回した）

代わりに、Claude APIとOpenAI APIのWeb検索ツールを併用して対象週の日本の
劇場公開作品を調べさせ、**事実情報のみ**（タイトル・公開日・配給会社名・
公式URL）を構造化して返す方式を採る。事実は著作権の保護対象ではなく、
特定サイトのフィード/API利用者として規約に拘束される構造でもないため、
撤回した各方式とはリスクの性質が異なる。以下を設計上の制約とする:

- 保存するのは事実情報のみ。記事本文・あらすじ・紹介文などの表現は保存しない
- AIの検索結果は誤り得るため、保存時は投稿状態="承認待ち"とし、下流
  （X投稿・WP登録等）に流す前に人間が確認する（main.theater_discover_cycle()）

環境変数:
    ANTHROPIC_API_KEY / ANTHROPIC_MODEL（既定 claude-sonnet-5。ai_clients.pyと共通。
        Web検索ツール web_search_20260209 対応モデルであること）
    OPENAI_API_KEY    / OPENAI_MODEL（既定 gpt-4o。Responses APIのweb_search対応モデル）
"""

import json
import logging
import os
import re
from datetime import date

from anthropic import Anthropic
from openai import OpenAI

from news_bot.fetch_theater import TheaterEntry
from news_bot.theater_calendar import dedupe_key

logger = logging.getLogger(__name__)

_ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
_OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")
_MAX_TOKENS = 8192
_MAX_WEB_SEARCHES = 8  # 週次1回の実行なので検索回数を絞ってコストを抑える
_MAX_CONTINUATIONS = 3  # サーバー側ツールのpause_turn継続上限

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _build_prompt(start: date, end: date) -> str:
    """両プロバイダー共通の調査プロンプトを組み立てる。

    事実情報のみをJSONで返させる（あらすじ等の表現をコピーさせない）。
    """
    return f"""\
{start.isoformat()}から{end.isoformat()}までの期間に、日本国内の映画館で劇場公開（初公開）される映画をWeb検索で調べてください。

以下のルールに従って、結果をJSON配列のみで出力してください（前置き・後書き・説明文は不要です）:

- 各要素は {{"title": "邦題", "release_date": "YYYY-MM-DD", "distributor": "配給会社名", "official_url": "公式サイトURL"}} の形式
- 事実情報のみを含めること。あらすじ・紹介文・レビューなどの文章は一切含めない
- release_date が上記期間内で、日付が確認できた作品のみを含める
- distributor / official_url が不明な場合は空文字 "" にする
- 期間内の公開作品が見つからない場合は空配列 [] を出力する

出力例:
[{{"title": "作品A", "release_date": "{start.isoformat()}", "distributor": "東宝", "official_url": "https://example.com"}}]"""


def _parse_entries(text: str, source: str) -> list[TheaterEntry]:
    """AIの応答テキストからJSON配列を取り出しTheaterEntry一覧にする。

    コードフェンスや前後の文章が混ざる場合に備え、最初の "[" から最後の "]"
    までを抽出してパースする。日付が不正な要素はスキップする。
    """
    cleaned = _CODE_FENCE_RE.sub("", text.strip()).strip()
    begin = cleaned.find("[")
    close = cleaned.rfind("]")
    if begin == -1 or close == -1 or close < begin:
        raise ValueError(f"JSON配列が見つかりません: {cleaned[:200]!r}")
    items = json.loads(cleaned[begin : close + 1])

    entries: list[TheaterEntry] = []
    for item in items:
        title = (item.get("title") or "").strip()
        raw_date = (item.get("release_date") or "").strip()
        if not title or not raw_date:
            continue
        try:
            release_date = date.fromisoformat(raw_date)
        except ValueError:
            logger.warning("公開日が不正のためスキップ: %s (%r)", title, raw_date)
            continue
        entries.append(
            TheaterEntry(
                title=title,
                url=(item.get("official_url") or "").strip(),
                source=source,
                release_date=release_date,
                distributor=(item.get("distributor") or "").strip(),
            )
        )
    return entries


def discover_claude(start: date, end: date) -> list[TheaterEntry]:
    """Claude APIのWeb検索サーバーツールで対象週の劇場公開作品を調べる。

    サーバー側ツールはstop_reason="pause_turn"で中断されることがあるため、
    その場合はassistant応答を積んで再送し継続する。
    """
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    messages = [{"role": "user", "content": _build_prompt(start, end)}]

    text_parts: list[str] = []
    for _ in range(1 + _MAX_CONTINUATIONS):
        response = client.messages.create(
            model=_ANTHROPIC_MODEL,
            max_tokens=_MAX_TOKENS,
            tools=[
                {
                    "type": "web_search_20260209",
                    "name": "web_search",
                    "max_uses": _MAX_WEB_SEARCHES,
                }
            ],
            messages=messages,
        )
        text_parts.extend(block.text for block in response.content if block.type == "text")
        if response.stop_reason != "pause_turn":
            break
        messages.append({"role": "assistant", "content": response.content})
    else:
        logger.warning("Claude Web検索がpause_turn継続上限に達しました")

    return _parse_entries("\n".join(text_parts), source="AI検索(claude)")


def discover_openai(start: date, end: date) -> list[TheaterEntry]:
    """OpenAI Responses APIのweb_searchツールで対象週の劇場公開作品を調べる。"""
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    response = client.responses.create(
        model=_OPENAI_MODEL,
        tools=[{"type": "web_search"}],
        input=_build_prompt(start, end),
    )
    return _parse_entries(response.output_text, source="AI検索(openai)")


def discover_all(start: date, end: date) -> list[TheaterEntry]:
    """Claude/OpenAIの両方で調査し、重複キーでマージした一覧を返す。

    片方の失敗は他方に伝播させない。両方が同じ作品を見つけた場合は
    情報源を "AI検索(claude+openai)" とし、空のフィールドを相互補完する
    （両方が挙げた作品は実在の確度が高い、という人間の承認時のシグナルになる）。
    """
    merged: dict[str, TheaterEntry] = {}
    for discover in (discover_claude, discover_openai):
        try:
            entries = discover(start, end)
        except Exception:
            logger.exception("AI Web検索失敗: %s", discover.__name__)
            continue
        for entry in entries:
            key = dedupe_key(entry.release_date.isoformat(), entry.title)
            if key not in merged:
                merged[key] = entry
                continue
            existing = merged[key]
            existing.source = "AI検索(claude+openai)"
            existing.url = existing.url or entry.url
            existing.distributor = existing.distributor or entry.distributor
    return list(merged.values())
