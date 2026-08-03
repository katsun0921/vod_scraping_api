"""AIのWeb検索によるVOD配信開始作品の発見（取得方式=ai、仕様書7.4）。

discover_theater.pyと同じ設計思想: 特定VODサービス公式サイトの自動取得はすべて
規約上不採用のため（docs/feature/vod-sources-candidates.md B節）、Claude/OpenAIの
Web検索サーバーツールで対象週の配信開始作品を調べさせ、事実情報のみを構造化して返す。

- 保存するのは事実情報のみ（タイトル・原題・サービス・配信開始日・配信種別・公式URL）。
  あらすじ等の表現は保存しない
- AIの検索結果は誤り得るため、呼び出し元は投稿状態="承認待ち"で保存する

環境変数:
    ANTHROPIC_API_KEY / ANTHROPIC_MODEL（既定 claude-sonnet-5。ai_clients.pyと共通。
        Web検索ツール web_search_20260209 対応モデルであること）
    OPENAI_API_KEY    / OPENAI_MODEL（既定 gpt-4o。Responses APIのweb_search対応モデル）
"""

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import date

from anthropic import Anthropic
from openai import OpenAI

from news_bot.vod_calendar import dedupe_key

logger = logging.getLogger(__name__)

_ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
_OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")
_MAX_TOKENS = 8192
_MAX_WEB_SEARCHES = 8  # 週次1回の実行なので検索回数を絞ってコストを抑える
_MAX_CONTINUATIONS = 3  # サーバー側ツールのpause_turn継続上限

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

# 対象サービス（仕様書6.）。キーは vod_bot/wordpress.py の VOD_TERM_IDS と揃えた命名規則。
# 表示名はAIへの検索プロンプト用、_service_key()で応答テキストをキーへ逆変換する。
SERVICES = {
    "netflix": "Netflix",
    "amazon_prime_video": "Amazon Prime Video",
    "unext": "U-NEXT",
    "disney_plus": "Disney+",
    "hulu": "Hulu",
    "dmm_tv": "DMM TV",
}


@dataclass
class VodEntry:
    title: str
    service: str  # SERVICESのキー（netflix / unext 等）
    available_from: date
    source: str
    title_orig: str = ""
    availability_type: str = ""  # 見放題・レンタル・購入・独占 等
    url: str = ""
    category: str = ""  # 映画・ドラマ・アニメ


def _build_prompt(start: date, end: date) -> str:
    """両プロバイダー共通の調査プロンプトを組み立てる。

    事実情報のみをJSONで返させる（あらすじ等の表現をコピーさせない）。
    """
    service_list = "、".join(SERVICES.values())
    return f"""\
{start.isoformat()}から{end.isoformat()}までの期間に、日本国内の以下のVODサービスで新たに配信開始（見放題・レンタル・購入・独占配信のいずれか）される映画・ドラマ・アニメをWeb検索で調べてください。

対象サービス: {service_list}

以下のルールに従って、結果をJSON配列のみで出力してください（前置き・後書き・説明文は不要です）:

- 各要素は {{"title": "邦題", "title_orig": "原題", "service": "サービス名（{service_list}のいずれか）", "available_from": "YYYY-MM-DD", "availability_type": "見放題|レンタル|購入|独占", "category": "映画|ドラマ|アニメ", "official_url": "配信ページの公式URL"}} の形式
- 事実情報のみを含めること。あらすじ・紹介文・レビューなどの文章は一切含めない
- available_from が上記期間内で、日付が確認できた作品のみを含める
- title_orig / official_url / availability_type / category が不明な場合は空文字 "" にする
- 期間内の該当作品が見つからない場合は空配列 [] を出力する

出力例:
[{{"title": "作品A", "title_orig": "Title A", "service": "Netflix", "available_from": "{start.isoformat()}", "availability_type": "見放題", "category": "映画", "official_url": "https://example.com"}}]"""


def _service_key(service_name: str) -> str:
    """AIが返すサービス表示名をSERVICESのキーへ変換する。一致しなければ空文字。"""
    normalized = (service_name or "").strip().lower()
    for key, name in SERVICES.items():
        if name.lower() == normalized:
            return key
    return ""


def _parse_entries(text: str, source: str) -> list[VodEntry]:
    """AIの応答テキストからJSON配列を取り出しVodEntry一覧にする。

    コードフェンスや前後の文章が混ざる場合に備え、最初の "[" から最後の "]"
    までを抽出してパースする。日付が不正・サービス名が一致しない要素はスキップする。
    """
    cleaned = _CODE_FENCE_RE.sub("", text.strip()).strip()
    begin = cleaned.find("[")
    close = cleaned.rfind("]")
    if begin == -1 or close == -1 or close < begin:
        raise ValueError(f"JSON配列が見つかりません: {cleaned[:200]!r}")
    items = json.loads(cleaned[begin : close + 1])

    entries: list[VodEntry] = []
    for item in items:
        title = (item.get("title") or "").strip()
        raw_date = (item.get("available_from") or "").strip()
        service_key = _service_key(item.get("service", ""))
        if not title or not raw_date or not service_key:
            continue
        try:
            available_from = date.fromisoformat(raw_date)
        except ValueError:
            logger.warning("配信開始日が不正のためスキップ: %s (%r)", title, raw_date)
            continue
        entries.append(
            VodEntry(
                title=title,
                title_orig=(item.get("title_orig") or "").strip(),
                service=service_key,
                available_from=available_from,
                availability_type=(item.get("availability_type") or "").strip(),
                category=(item.get("category") or "").strip(),
                url=(item.get("official_url") or "").strip(),
                source=source,
            )
        )
    return entries


def discover_claude(start: date, end: date) -> list[VodEntry]:
    """Claude APIのWeb検索サーバーツールで対象週の配信開始作品を調べる。

    サーバー側ツールはstop_reason="pause_turn"で中断されることがあるため、
    その場合はassistant応答を積んで再送し継続する（discover_theater.pyと同型）。
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


def discover_openai(start: date, end: date) -> list[VodEntry]:
    """OpenAI Responses APIのweb_searchツールで対象週の配信開始作品を調べる。"""
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    response = client.responses.create(
        model=_OPENAI_MODEL,
        tools=[{"type": "web_search"}],
        input=_build_prompt(start, end),
    )
    return _parse_entries(response.output_text, source="AI検索(openai)")


def discover_all(start: date, end: date) -> list[VodEntry]:
    """Claude/OpenAIの両方で調査し、重複キーでマージした一覧を返す。

    片方の失敗は他方に伝播させない。両方が同じ作品を見つけた場合は
    情報源を "AI検索(claude+openai)" とし、空のフィールドを相互補完する
    （discover_theater.discover_all()と同じロジック）。
    """
    merged: dict[str, VodEntry] = {}
    for discover in (discover_claude, discover_openai):
        try:
            entries = discover(start, end)
        except Exception:
            logger.exception("AI Web検索失敗: %s", discover.__name__)
            continue
        for entry in entries:
            key = dedupe_key(entry.available_from.isoformat(), entry.service, entry.title)
            if key not in merged:
                merged[key] = entry
                continue
            existing = merged[key]
            existing.source = "AI検索(claude+openai)"
            existing.url = existing.url or entry.url
            existing.title_orig = existing.title_orig or entry.title_orig
            existing.availability_type = existing.availability_type or entry.availability_type
            existing.category = existing.category or entry.category
    return list(merged.values())
