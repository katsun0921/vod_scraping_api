# news_bot

Katsumascore（映画・アニメ・ドラマレビューメディア）のXニュース通知システム。映画・アニメ関連ニュースをRSSから収集し、Claude APIでランク判定（S/A/B/D）した上で、1回のrunでS/A判定になった記事をまとめて1つのXスレッド（連投）用テンプレートとしてSlackに送信する。投稿は現在手動運用（人間がSlackのテンプレートをコピーして①→②→③…の順にXへ連投する）。

このリポジトリはモノレポ構成で、`vod_bot/`（VOD配信状況スクレイピングAPI）とは独立したサブシステム。依存関係（`requirements.txt`）・実行環境・CIジョブは `news_bot/` 配下で完結し、`vod_bot/` には一切影響しない。データストア（Google Sheets）も `vod_bot/` のWordPressとは別物。

`vod_bot/` と共通化できる汎用コード（レート制御・User-Agent等）は [`../utils/`](../utils/) に置く。`python -m news_bot.main` はリポジトリルートから実行するため追加設定なしで `../utils/` をimportできる。現時点では `news_bot/` から未使用だが、RSS/HTMLスクレイピングの間隔制御が必要になった場合は `utils.rate_limit.RateLimiter` を流用できる。

詳細仕様 → [../docs/x-news-bot-spec.md](../docs/x-news-bot-spec.md)

## 責務

- 登録済みニュースソース（RSS）から映画・アニメ関連ニュースを収集
- URL完全一致による重複チェック（フェーズ1スコープ。タイトル類似度判定はフェーズ2）
- Google Sheets への記事保存
- Claude APIによるS/A/B/D判定
  - 精度比較テストのため、ChatGPT/Grokでも並列に判定させ結果をSheetsに記録できる（`NEWS_BOT_JUDGE_PROVIDERS`）
- 1回のrunでS/A判定になった記事をまとめて1つのXスレッド（連投）用テンプレートを生成し、Slackへ1回だけ送信する（`compose.compose_headline()` + `compose.pack_thread()` + `approval.notify_manual_thread()`）。投稿は人間が手動で行う（自動投稿は行わない）
- 自動投稿（X API v2への投稿、承認リアクションによる承認フロー）は予算状況次第で再開できるようコードは残しているが、現在はパイプラインから呼び出していない

## 実行方式

GitHub Actions（RSS: `.github/workflows/news-bot.yml`、X: `.github/workflows/news-bot-x.yml`）から `python -m news_bot.main`（RSS）/ `python -m news_bot.main x <地域>`（X）を実行する。Cloud Runは使用しない。

cronが有効化されており、`news-bot.yml`は1日1回（0:00 UTC。仕様書 3.は1〜2時間おき推奨だが運用判断で変更）、`news-bot-x.yml`は地域ごとに1日1回（日本 1:00 UTC / アメリカ 13:00 UTC）で自動実行される。Actionsタブからの手動実行（`workflow_dispatch`）も引き続き可能。

ChatGPT/Grokとの精度比較テストのため `NEWS_BOT_JUDGE_PROVIDERS: "claude,openai,grok"` を設定し、3プロバイダーを並列実行している。各プロバイダーの判定結果（rank/reason）は「ニュース取得」シートのClaude/ChatGPT/Grok列にそれぞれ記録される。最終的にどのランクを採用するかは `NEWS_BOT_JUDGE_DECISION`（既定 `primary` = 先頭プロバイダーのランクを採用）で制御し、比較結果を見た上で `majority`（多数決）への切り替えを検討する。本番投入時は `NEWS_BOT_JUDGE_PROVIDERS` を `claude` のみに戻す想定。

投稿は手動運用のため、実行するのは`fetch_cycle()`/`fetch_x_cycle()`のみ：

| 関数 | 処理内容 |
|---|---|
| `fetch_cycle()` | RSS取得 → 重複チェック → 保存 → AI判定 → S/A判定は投稿テンプレートをSlackに送信 |
| `fetch_x_cycle(region)` | 「公式X一覧」の指定地域の有効アカウントから投稿取得 → 上記と同じ処理（重複チェック以降はfetch_cycle()と共通） |
| `process_pending()`（**現在未使用**） | 承認キューを確認し、承認済み分をX投稿。自動投稿を再開する場合に使う（`main.py`の`__main__`でコメントアウト済み） |

## Xポストのニュースソース化

RSSに続く追加ニュースソースとして、公式Xアカウントの投稿を`fetch_x.py`で取得し、`main.py`の`fetch_x_cycle(region)`から通常のRSSパイプライン（重複チェック→AI判定→S/A判定はSlackテンプレート送信）と同じ処理に流す。

- **取得方針**：「公式X一覧」シートの"地域"列（`日本`/`アメリカ`）で取得元を分け、それぞれ1日1回（合計2回/日）のcronで取得する（`fetch_x_cycle("日本")` / `fetch_x_cycle("アメリカ")`）
- **認証**：投稿用のOAuth1.0aキー（`X_API_KEY`等）とは別に、読み取り専用の`X_BEARER_TOKEN`（OAuth2.0 App-Only）を発行して使う
- **コスト**：Pay-Per-Useで投稿の読み取りは$0.005/件。`since_id`（前回取得した最新投稿ID）を「公式X一覧」シートにキャッシュし、次回はそれ以降の新着分のみ取得することで課金対象を抑える（`sheets.get_active_x_accounts()`/`update_x_account_state()`）
- **「公式X一覧」シートの列**：`ID / アカウント名 / Xハンドル / URL / 種別（作品/配給/制作会社/配信サービス/メディア） / 地域 / 有効/無効（チェックボックス） / user_id / since_id / 最終取得日時`（`user_id`・`since_id`は19桁のsnowflake IDのため、Sheets側の数値変換による桁落ちを避けてraw書き込みしている。読み込み側の`get_active_x_accounts()`も`gspread`の自動数値変換を`numericise_ignore`で無効化し、明示的に文字列化している）。「有効/無効」列は**Sheetsのチェックボックスのみ**対応（`row.get("有効/無効") is True`で判定）。テキストで"有効"等と入力しても対象にならない

### 実行方法

`.github/workflows/news-bot-x.yml`が地域ごとに1日1回自動実行する（日本 1:00 UTC / アメリカ 13:00 UTC）。手動実行したい場合はActionsタブから`workflow_dispatch`（`region`入力で`日本`/`アメリカ`を選択、既定は`日本`）で`python -m news_bot.main x <地域>`を実行できる。「公式X一覧」シートに登録済みの有効なアカウントを取得し、AI判定・Slack通知まで行う。

cronのschedule実行では`workflow_dispatch`の`region`入力が存在しないため、どちらのcron式（`0 1 * * *` / `0 13 * * *`）が発火したか（`github.event.schedule`）で地域を判定している（workflow内の`REGION`環境変数の式を参照）。

## 劇場公開カレンダー収集パイプライン（実装中）

詳細仕様 → [../docs/feature/theater-release-calendar-spec.md](../docs/feature/theater-release-calendar-spec.md)（17.に未実装・未確定事項のTODOをまとめてある）

毎週の劇場公開作品を収集し「劇場公開予定」シートに保存するパイプライン。`.github/workflows/theater-calendar.yml`が毎週月曜 06:00 JST（`0 21 * * 0` UTC）に`python -m news_bot.main theater_discover`（`theater_discover_cycle()`）を自動実行する。

- **AI Web検索による発見（現行方式）**：特定サイトの自動取得はすべて規約上撤回されたため（下記）、Claude API（`web_search_20260209`サーバーツール）とOpenAI API（Responses APIの`web_search`）を**併用**して対象週の公開作品を調べさせる方式にした（`discover_theater.py`）。保存するのは**事実情報のみ**（タイトル・公開日・配給会社名・公式URL）で、あらすじ等の表現はコピーしない。両AIが同じ作品を挙げた場合は情報源が`AI検索(claude+openai)`になり、実在確度のシグナルとして人間の承認時に使える。
- **人間の承認が必須**：AIの検索結果は誤り得るため、保存時は投稿状態=`承認待ち`。新規保存分は**Slackに親メッセージ+作品ごとのスレッド返信で確認依頼を通知**する（`approval.notify_theater_discovered()`。通知失敗してもシート保存は完了しているためサイクルは失敗しない）。人間がシートを確認・修正・承認するまで下流処理（週次サマリー・X/WP投稿、いずれも未実装）には流れない。
- **レイヤー1データソース（特定サイトの自動取得）はすべて撤回**：配給会社公式サイト（東宝・東映等）と映画.comRSSは利用規約の複製・転載禁止により除外。**TMDb API**はKatsumascoreのAdSense収益化がPersonal Use申請（非商用・無収益の誓約）に反するため撤回。**PR TIMES企業別RSS**も一般規約第6条④「有償目的で企業コンテンツを利用する行為」の禁止に抵触するリスクが高く撤回（詳細は[theater-sources-candidates.md](../docs/feature/theater-sources-candidates.md)）。`fetch_theater.py`（RSS/TMDb取得）と「劇場情報源」シート巡回の`theater`コマンドはコードとして残っているが、cronからは外した。
- **対象期間**：`theater_calendar.week_range()`が実行日から「直近の金曜日〜その翌週木曜日」を計算する（仕様書6.）。AI検索のプロンプトにこの期間を直接渡す。
- **重複判定**：「公開日 + 正規化タイトル」（`theater_calendar.normalize_title()` / `dedupe_key()`）の完全一致のみ（仕様書9.）。Claude/OpenAI間のマージにも同じキーを使う。
- **現時点でのスコープ**：AI発見→期間フィルタ→重複チェック→承認待ちとして保存→Slack確認依頼通知まで。Katsumascore照合・SNS優先度判定・投稿案生成・Googleカレンダー同期は未実装。

## ディレクトリ構成

```
news_bot/
├── main.py              # fetch_cycle() / fetch_x_cycle() / theater_discover_cycle() / theater_cycle() / process_pending() エントリーポイント
├── fetch.py             # RSS取得（feedparser）
├── fetch_x.py           # 公式Xアカウントの投稿取得（RSSに続く第2のニュースソース）
├── fetch_theater.py     # 劇場公開情報の取得（RSS/TMDb。規約上の理由で現在未使用）
├── discover_theater.py  # AI Web検索（Claude/OpenAI併用）による劇場公開作品の発見
├── theater_calendar.py  # 対象期間計算・タイトル正規化・重複キー生成
├── dedupe.py            # URL完全一致の重複チェック
├── judge.py             # S/A/B/D判定（複数AIプロバイダーの並列実行・複数記事のバッチ判定に対応）
├── ai_clients.py        # Claude/ChatGPT/Grokへの個別API呼び出しラッパー
├── compose.py           # 投稿文生成（スレッド見出し生成 + パッキング / 単独投稿版は未使用で保持）
├── approval.py          # Slack通知（スレッドまとめテンプレート送信）
├── post_x.py            # X API v2投稿（tweepy、現在未使用で保持）
├── sheets.py            # Google Sheets I/O（gspread）
├── prompt_loader.py     # prompts/*.md を読み込むローダー
├── prompts/             # プロンプト本文（Markdown）を専用管理
│   ├── judge_system_prompt.md
│   ├── compose_system_prompt.md
│   └── thread_headline_system_prompt.md
└── requirements.txt
```

プロンプトは`judge.py` / `compose.py`にハードコードせず、`prompts/*.md`で管理する。judge/compose用のfew-shot例やトーンの調整はコードを触らずMarkdownファイルの編集だけで完結する。

## 必要なアカウント

本番投入前に、以下のアカウント・認証情報を用意する必要がある。

| # | アカウント/サービス | 用途 | 必要な認証情報 | GitHub Secret名 |
|---|---|---|---|---|
| 1 | Google Cloud サービスアカウント（**news_bot専用に新規発行**） | Google Sheets APIを有効化し、news_bot専用のサービスアカウントを発行する | サービスアカウントJSON、対象スプレッドシートをそのサービスアカウントのメールアドレスに共有 | `GOOGLE_SHEETS_CREDENTIALS_JSON`, `GOOGLE_SHEETS_SPREADSHEET_ID` |
| 2 | Anthropic Console アカウント | Claude APIでAI判定・投稿文生成を行う | APIキー | `ANTHROPIC_API_KEY` |
| 3 | OpenAI Platform アカウント（**AI判定の精度比較テスト用**） | ChatGPTでのAI判定（`NEWS_BOT_JUDGE_PROVIDERS`に`openai`を含めた場合のみ） | APIキー | `OPENAI_API_KEY` |
| 4 | xAI Developer アカウント（**AI判定の精度比較テスト用**） | Grokでの AI判定（`NEWS_BOT_JUDGE_PROVIDERS`に`grok`を含めた場合のみ） | APIキー | `GROK_API_KEY` |
| 5 | X (Twitter) Developer アカウント + katsumascore運用アカウント | Xへの投稿（Pay-Per-Use課金の有効化・支出上限設定も必要、仕様書4.6） | App の Consumer Key/Secret、投稿アカウントのAccess Token/Secret（OAuth1.0a、Read and Write権限） | `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_TOKEN_SECRET` |
| 6 | 同上のX Developerアカウント（**Xポストのニュースソース化用**） | 公式Xアカウントの投稿読み取り（`fetch_x.py` / `fetch_x_cycle()`） | 同一App内で発行するOAuth2.0 App-Only Bearer Token | `X_BEARER_TOKEN` |
| 7 | Slackワークスペース + Slack App（Bot） | 承認フロー（S/A判定の通知・リアクション検知） | Bot Token（`chat:write` / `reactions:read` スコープ）、承認依頼を投稿するチャンネルのID | `NEWS_BOT_SLACK_BOT_TOKEN`, `NEWS_BOT_SLACK_APPROVAL_CHANNEL_ID` |
| 8 | GitHubリポジトリの管理権限 | 上記の認証情報をActions Secretsに登録する | - | - |

> **長期有効な認証情報の運用方針**：`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GROK_API_KEY` / `X_API_*` / `NEWS_BOT_SLACK_BOT_TOKEN`は発行元サービスがWorkload Identity Federation等の短期認証に対応していないため、無期限キーとしてGitHub Actions Secretsで管理する。漏洩の通知・兆候（想定外の使用量急増、GitHubのsecret scanningアラート等）を検知した場合は各サービスのコンソールで即座にRevoke（失効）する。

> Slack Botはワークスペースにインストールし、承認を行うチャンネルに招待（`/invite @bot名`）しておく必要がある。招待し忘れると`chat.postMessage`が失敗する。

> **既存スプレッドシートを使っている場合の注意**：「ニュース取得」シートは既に作成済みだとヘッダー行が自動更新されない（`_ensure_sheets_exist()`はシートが無い場合のみ作成する）。ChatGPT/Grok列を使う場合は、既存シートのヘッダー行末尾に手動で `Claude判定` / `Claude理由` / `ChatGPT判定` / `ChatGPT理由` / `Grok判定` / `Grok理由` を追加しておくこと。

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
# Google Sheetsサービスアカウント / ANTHROPIC_API_KEY（+ 比較テスト時はOPENAI_API_KEY/GROK_API_KEY） / X API資格情報 / Slack Bot Tokenを設定
```

### 3. 実行

```bash
python -m news_bot.main
```

## 実装上の注意（仕様書からの補足）

- Slack通知には **Slack Bot Token**（`chat.postMessage`）を使用する。
- 投稿は手動運用のため、Slackへのスレッドまとめテンプレート送信までがパイプラインの終着点（`approval.notify_manual_thread`）。1回のrunでS/A判定になった記事は個別投稿ではなく1つのXスレッドにまとめる。記事1件だけの単独投稿版（`approval.notify_manual_post` / `compose.compose`）と、承認リアクション（:white_check_mark:=承認 / :x:=取り消し）による自動投稿フロー（`approval.notify_pending` / `approval.resolve` / `process_pending()` / `post_x.post_with_reply`）はいずれもコードを残したまま無効化している。自動化を再開する場合は`main.py`内のコメントを参照。
- 承認キュー・投稿状態をcron実行をまたいで追跡するための内部管理用シート「**承認キュー**」は、自動投稿再開時に備えて仕様書のシート構成（[x-news-bot-spec.md](../docs/x-news-bot-spec.md) 5.）に追加済み。
- タイトル一覧・YouTube Shortsシート（関連タイトル紐付け等）はMVPスコープ外のため未実装。「公式X一覧」は`fetch_x_cycle()`用に実装済み（`sheets.py`の`_AUTO_CREATED_HEADERS`で自動作成対象）。
- Google Sheets/X API/Claude API/Slack Web APIへの実接続はネットワーク制限のある開発環境では未検証。本番投入前に疎通確認が必要。
