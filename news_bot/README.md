# news_bot

Katsumascore（映画・アニメ・ドラマレビューメディア）のXニュース自動投稿システム。映画・アニメ関連ニュースをRSSから収集し、Claude APIでランク判定（S/A/B/D）した上で、人間の承認を経てXへ投稿する。

このリポジトリはモノレポ構成で、`vod_bot/`（VOD配信状況スクレイピングAPI）とは独立したサブシステム。依存関係（`requirements.txt`）・実行環境・CIジョブは `news_bot/` 配下で完結し、`vod_bot/` には一切影響しない。データストア（Google Sheets）も `vod_bot/` のWordPressとは別物。

`vod_bot/` と共通化できる汎用コード（レート制御・User-Agent等）は [`../utils/`](../utils/) に置く。`python -m news_bot.main` はリポジトリルートから実行するため追加設定なしで `../utils/` をimportできる。現時点では `news_bot/` から未使用だが、RSS/HTMLスクレイピングの間隔制御が必要になった場合は `utils.rate_limit.RateLimiter` を流用できる。

詳細仕様 → [../docs/x-news-bot-spec.md](../docs/x-news-bot-spec.md)

## 責務

- 登録済みニュースソース（RSS）から映画・アニメ関連ニュースを収集
- URL完全一致による重複チェック（フェーズ1スコープ。タイトル類似度判定はフェーズ2）
- Google Sheets への記事保存
- Claude APIによるS/A/B/D判定と、S/A判定記事の投稿文生成（本文＋リプライ分割）
- Slack通知による人間承認フロー（S判定は15分猶予後自動投稿、A判定はリアクション承認後投稿）
- X API v2への投稿（本文はURLなし、リプライにURLを含めてコスト最適化）
- 投稿履歴のGoogle Sheetsへの記録

## 実行方式

GitHub Actions（`.github/workflows/news-bot.yml`）から `python -m news_bot.main` を実行する。Cloud Runは使用しない。

現在はテスト段階のため、Actionsタブからの手動実行（`workflow_dispatch`）のみ有効。本番投入時は2時間おきのcron（仕様書 3.: 1〜2時間おき推奨）をworkflow内のコメントアウトを解除して有効化する。

1回の実行で以下の2つを行う：

| 関数 | 処理内容 |
|---|---|
| `fetch_cycle()` | ニュース取得 → 重複チェック → 保存 → AI判定 → S/A判定は承認依頼をSlackに投稿 |
| `process_pending()` | 承認キューを確認し、承認済み/猶予経過分をX投稿 |

## ディレクトリ構成

```
news_bot/
├── main.py           # fetch_cycle() / process_pending() エントリーポイント
├── fetch.py          # RSS取得（feedparser）
├── dedupe.py         # URL完全一致の重複チェック
├── judge.py          # Claude APIでS/A/B/D判定
├── compose.py        # 投稿文生成（本文＋リプライ）
├── approval.py       # Slack承認フロー（Bot Token使用）
├── post_x.py         # X API v2投稿（tweepy）
├── sheets.py         # Google Sheets I/O（gspread）
├── prompt_loader.py  # prompts/*.md を読み込むローダー
├── prompts/          # プロンプト本文（Markdown）を専用管理
│   ├── judge_system_prompt.md
│   └── compose_system_prompt.md
└── requirements.txt
```

プロンプトは`judge.py` / `compose.py`にハードコードせず、`prompts/*.md`で管理する。judge/compose用のfew-shot例やトーンの調整はコードを触らずMarkdownファイルの編集だけで完結する。

## 必要なアカウント

本番投入前に、以下のアカウント・認証情報を用意する必要がある。

| # | アカウント/サービス | 用途 | 必要な認証情報 | GitHub Secret名 |
|---|---|---|---|---|
| 1 | Google Cloud サービスアカウント（**news_bot専用に新規発行**） | Google Sheets APIを有効化し、news_bot専用のサービスアカウントを発行する | サービスアカウントJSON、対象スプレッドシートをそのサービスアカウントのメールアドレスに共有 | `GOOGLE_SHEETS_CREDENTIALS_JSON`, `GOOGLE_SHEETS_SPREADSHEET_ID` |
| 2 | Anthropic Console アカウント | Claude APIでAI判定・投稿文生成を行う | APIキー | `ANTHROPIC_API_KEY` |
| 3 | X (Twitter) Developer アカウント + katsumascore運用アカウント | Xへの投稿（Pay-Per-Use課金の有効化・支出上限設定も必要、仕様書4.6） | App の Consumer Key/Secret、投稿アカウントのAccess Token/Secret（OAuth1.0a、Read and Write権限） | `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_TOKEN_SECRET` |
| 4 | Slackワークスペース + Slack App（Bot） | 承認フロー（S/A判定の通知・リアクション検知） | Bot Token（`chat:write` / `reactions:read` スコープ）、承認依頼を投稿するチャンネルのID | `NEWS_BOT_SLACK_BOT_TOKEN`, `NEWS_BOT_SLACK_APPROVAL_CHANNEL_ID` |
| 5 | GitHubリポジトリの管理権限 | 上記の認証情報をActions Secretsに登録する | - | - |

> Slack Botはワークスペースにインストールし、承認を行うチャンネルに招待（`/invite @bot名`）しておく必要がある。招待し忘れると`chat.postMessage`が失敗する。

## セットアップ

### 1. 依存関係のインストール

```bash
cd news_bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 環境変数の設定

```bash
cp .env.example .env
# Google Sheetsサービスアカウント / ANTHROPIC_API_KEY / X API資格情報 / Slack Bot Tokenを設定
```

### 3. 実行

```bash
python -m news_bot.main
```

## 実装上の注意（仕様書からの補足）

- Slack Incoming Webhookはメッセージのタイムスタンプやリアクションを取得できないため、承認フローには **Slack Bot Token**（`chat.postMessage` / `reactions.get`）を使用する。
- 承認状態をcron実行をまたいで追跡するため、仕様書のシート構成（[x-news-bot-spec.md](../docs/x-news-bot-spec.md) 5.）に加えて内部管理用シート「**承認キュー**」を追加している。
- リアクションは `:white_check_mark:`=承認、`:x:`=取り消し、を割り当てている。
- タイトル一覧・公式X一覧・YouTube Shortsシート（関連タイトル紐付け等）はMVPスコープ外のため未実装。
- Google Sheets/X API/Claude API/Slack Web APIへの実接続はネットワーク制限のある開発環境では未検証。本番投入前に疎通確認が必要。
