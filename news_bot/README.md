# news_bot

Katsumascore（映画・アニメ・ドラマレビューメディア）のXニュース自動投稿システム。映画・アニメ関連ニュースをRSSから収集し、Claude APIでランク判定（S/A/B/D）した上で、人間の承認を経てXへ投稿する。

このリポジトリはモノレポ構成で、`vod_bot/`（VOD配信状況スクレイピングAPI）とは完全に独立したサブシステム。依存関係（`requirements.txt`）・実行環境・CIジョブはすべて `news_bot/` 配下で完結し、`vod_bot/` には一切影響しない。データストア（Google Sheets）も `vod_bot/` のWordPressとは別物。

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

GitHub Actions cron（2時間おき、`.github/workflows/news-bot.yml`）から `python -m news_bot.main` を実行する。Cloud Runは使用しない。

1回の実行で以下の2つを行う：

| 関数 | 処理内容 |
|---|---|
| `fetch_cycle()` | ニュース取得 → 重複チェック → 保存 → AI判定 → S/A判定は承認依頼をSlackに投稿 |
| `process_pending()` | 承認キューを確認し、承認済み/猶予経過分をX投稿 |

## ディレクトリ構成

```
news_bot/
├── main.py       # fetch_cycle() / process_pending() エントリーポイント
├── fetch.py      # RSS取得（feedparser）
├── dedupe.py     # URL完全一致の重複チェック
├── judge.py      # Claude APIでS/A/B/D判定
├── compose.py    # 投稿文生成（本文＋リプライ）
├── approval.py   # Slack承認フロー（Bot Token使用）
├── post_x.py     # X API v2投稿（tweepy）
├── sheets.py     # Google Sheets I/O（gspread）
└── requirements.txt
```

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
