"""AI判定（S/A/B/D ランク）。

仕様書 4.3: Claude APIへのプロンプトには過去の判定実例（few-shot）を含め、
判定のブレを抑える。出力は JSON: {rank, reason, confidence}。
"""

import json
import logging
import os

from anthropic import Anthropic

from news_bot.fetch import NewsEntry

logger = logging.getLogger(__name__)

_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")

_SYSTEM_PROMPT = """\
あなたは映画・アニメ・ドラマレビューメディア「Katsumascore」のニュース編集者です。
入力されたニュース記事のタイトルと概要を読み、以下の基準でランク付けしてください。

- S: 新作発表・続編決定・公開日決定・予告公開・キャスト発表・配信開始など、速報性が高く読者の関心が強いもの
- A: インタビュー・興行収入・制作情報など、投稿対象だが速報性は中程度のもの
- B: グッズ・キャンペーン・小規模ニュースなど、記録はするが投稿は見送るもの
- D: 未確認情報・噂・重複記事など、除外すべきもの

# 判定例

## S判定の例
- 「映画『○○』続編の制作が決定、20XX年公開へ」→ S（続編決定・公開日決定）
- 「アニメ『○○』第2期の放送が決定、キービジュアル公開」→ S（続編決定）
- 「『○○』本予告編が解禁、主題歌はアーティストXが担当」→ S（予告公開）

## A判定の例
- 「監督○○が語る、映画『○○』制作秘話インタビュー」→ A（インタビュー）
- 「映画『○○』が公開3日間で興行収入10億円突破」→ A（興行収入）

## B判定の例
- 「映画『○○』とカフェのコラボメニューが期間限定販売」→ B（グッズ・キャンペーン）
- 「アニメ『○○』グッズの受注販売開始」→ B（グッズ）

## D判定の例
- 「【未確認】『○○』続編制作か、SNSで噂が拡散」→ D（未確認情報）
- 「映画『○○』続編制作決定、20XX年公開へ」（直近24時間の既出記事と同一内容）→ D（重複）

# 出力フォーマット
必ず以下のJSON形式のみで出力してください。前後に説明文を付けないこと。
{"rank": "S|A|B|D", "reason": "判定理由を1文で", "confidence": 0.0から1.0の数値}
"""


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
        result = json.loads(text)
    except json.JSONDecodeError:
        logger.error("AI判定のJSONパース失敗: %s", text)
        return {"rank": "D", "reason": "判定結果のパース失敗", "confidence": 0.0}

    if result.get("rank") not in {"S", "A", "B", "D"}:
        logger.error("AI判定の不正なrank: %s", result)
        return {"rank": "D", "reason": "不正な判定結果", "confidence": 0.0}
    return result
