"""重複チェック。

仕様書 4.2 / 6.: フェーズ1はURL完全一致のみ。
タイトル類似度による二次チェックはフェーズ2以降（既存記事とのembedding比較 or
Claude APIによる同一ニュース判定）で追加する。
"""

from news_bot.fetch import NewsEntry


def is_duplicate(entry: NewsEntry, existing_urls: set[str]) -> bool:
    """URL完全一致による重複判定（一次チェックのみ）。"""
    return entry.url in existing_urls
