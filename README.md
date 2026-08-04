# vod_scraping_api

モノレポ構成。Katsumascore（映画・アニメ・ドラマレビューメディア）向けの2つの独立したサブシステムを収容する。

| サブシステム | 責務 | 詳細 |
|---|---|---|
| [`vod_bot/`](vod_bot/README.md) | VOD配信状況スクレイピングAPI。WordPressの投稿データを取得し、各VODサービスの配信状況を確認・更新する | Cloud Run + Cloud Scheduler |
| [`news_bot/`](news_bot/README.md) | ニュース・劇場公開・VOD配信情報の収集と記事化。AI判定/Web検索で収集し、人間の承認を経てWordPress投稿とSNS投稿案を生成する | GitHub Actions cron + Claudeルーティン |

両サブシステムは依存関係（`requirements.txt`）・実行環境・CIジョブが分離されており、互いのデプロイに影響しない。それぞれの責務・セットアップ手順は各ディレクトリの README を参照。

## utils/

[`utils/`](utils/) には `vod_bot/` `news_bot/` の両方から使う汎用コード（レート制御 `RateLimiter`、共通User-Agent）を置く。特定のサブシステムに閉じたコード（WordPress/JustWatch/Slack クライアントなど）はそれぞれのディレクトリ直下に置き、ここには置かない。

## ドキュメント

[docs/](docs/) に運用・設計ドキュメント一式を置く。主要なものは以下。

### 仕様

| ドキュメント | 内容 |
|---|---|
| [x-news-bot-spec.md](docs/x-news-bot-spec.md) | news_bot 実装仕様書（ニュース収集・AI判定・X投稿） |
| [feature/theater-release-calendar-spec.md](docs/feature/theater-release-calendar-spec.md) | 劇場公開カレンダー収集パイプライン |
| [feature/vod-release-calendar-spec.md](docs/feature/vod-release-calendar-spec.md) | VOD配信情報収集パイプライン |
| [feature/routine-discovery.md](docs/feature/routine-discovery.md) | 情報収集のルーティン方式（従量課金→サブスク内） |
| [feature/routine-prompts.md](docs/feature/routine-prompts.md) | ルーティンに登録するプロンプト本体・レビュー観点 |
| [vod-scraping-api.md](docs/vod-scraping-api.md) | VODスクレイピングAPI仕様（vod_bot） |

### 戦略・調査

| ドキュメント | 内容 |
|---|---|
| [feature/strategy-overview.md](docs/feature/strategy-overview.md) | パイプライン全体の役割分担・方針 |
| [feature/growth-strategy.md](docs/feature/growth-strategy.md) | 流入拡大戦略（フロントSEO・導線設計） |
| [feature/theater-sources-candidates.md](docs/feature/theater-sources-candidates.md) | 劇場情報源の規約調査 |
| [feature/vod-sources-candidates.md](docs/feature/vod-sources-candidates.md) | VOD情報源の規約調査・コスト試算 |

### 運用

| ドキュメント | 内容 |
|---|---|
| [operations.md](docs/operations.md) | 運用フロー・アーキテクチャ・設計思想 |
| [relations.md](docs/relations.md) | データリレーション（ACF + taxonomy ER図） |
| [json-output.md](docs/json-output.md) | WP REST API レスポンス仕様 |
| [cache.md](docs/cache.md) / [cloudflare-cache-setup.md](docs/cloudflare-cache-setup.md) | キャッシュ設計・設定 |

> `docs/drop/` には規約上の理由等で**廃止した**仕様を凍結保存している。参照する際は
> 廃止理由（各ファイル冒頭）を必ず確認すること。
