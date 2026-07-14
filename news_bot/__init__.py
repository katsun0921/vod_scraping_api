"""Katsumascore Xニュース自動投稿システム。

VOD スクレイピング API（リポジトリルート）とは独立したサブシステム。
依存関係は news_bot/requirements.txt で管理し、ルートの requirements.txt
（Cloud Run イメージ）には影響しない。実行は GitHub Actions cron を想定。
"""
