# vod_scraping_api

モノレポ構成。Katsumascore（映画・アニメ・ドラマレビューメディア）向けの2つの独立したサブシステムを収容する。

| サブシステム | 責務 | 詳細 |
|---|---|---|
| [`vod_bot/`](vod_bot/README.md) | VOD配信状況スクレイピングAPI。WordPressの投稿データを取得し、各VODサービスの配信状況を確認・更新する | Cloud Run + Cloud Scheduler |
| [`news_bot/`](news_bot/README.md) | Xニュース自動投稿システム。映画・アニメニュースを収集しAI判定を経てXへ投稿する | GitHub Actions cron |

両サブシステムは依存関係（`requirements.txt`）・実行環境・CIジョブが分離されており、互いのデプロイに影響しない。それぞれの責務・セットアップ手順は各ディレクトリの README を参照。

## utils/

[`utils/`](utils/) には `vod_bot/` `news_bot/` の両方から使う汎用コード（レート制御 `RateLimiter`、共通User-Agent）を置く。特定のサブシステムに閉じたコード（WordPress/JustWatch/Slack クライアントなど）はそれぞれのディレクトリ直下に置き、ここには置かない。

## ドキュメント

- [doc/](doc/) / [docs/](docs/) — 運用・設計ドキュメント一式
- [docs/x-news-bot-spec.md](docs/x-news-bot-spec.md) — news_bot 実装仕様書
