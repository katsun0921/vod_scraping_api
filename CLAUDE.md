# CLAUDE.md

**モノレポ構成。2つの独立したサブシステムを収容する。**

| サブシステム | 責務 | 実行環境 |
|---|---|---|
| `vod_bot/` | VOD 配信状況スクレイピング。WordPress の既存投稿に紐づく配信状況を確認・更新する | Cloud Run + Cloud Scheduler |
| `news_bot/` | ニュース・劇場公開・VOD配信情報の収集と記事化。人間の承認を経て WordPress 投稿と SNS 投稿案を生成する | GitHub Actions + Claudeルーティン |

依存関係（`requirements.txt`）・実行環境・CIジョブは分離されており、互いのデプロイに影響しない。

## AIエージェント運用

- 本ファイルをプロジェクト指示の正本とする。`AGENTS.md` はAgent/Codexから本ファイルを
  読み込むための入口であり、プロジェクト説明を重複させない。
- Claude Codeでは `/create-pr`、Agent/Codexでは `create-pr` または `$create-pr` で、
  `.agents/skills/create-pr/SKILL.md` の共通手順に従ってDraft PRを作成する。

## プロジェクト構成

```
vod_scraping_api/
├── vod_bot/                   # VOD配信状況スクレイピング（Cloud Run）
│   ├── main.py                # Flask エントリーポイント
│   ├── wordpress.py           # WP REST API クライアント
│   ├── justwatch.py           # JustWatch 経由の配信状況取得
│   ├── weekly_patch.py        # 既存投稿の配信状況を定期更新
│   ├── slack.py               # Slack 通知
│   ├── checkers/              # サービス別チェッカー（amazon/netflix/hulu/unext/
│   │                          #   disney_plus/dmm_tv/apple_tv/youtube/crunchyroll）
│   ├── acf/                   # ACF フィールド定義（WP管理画面からインポート用）
│   ├── Dockerfile             # Cloud Run 用コンテナ定義
│   └── tests/
├── news_bot/                  # 情報収集・記事化（GitHub Actions）
│   ├── main.py                # 全サイクルのエントリーポイント（サブコマンド方式）
│   ├── import_routine.py      # ルーティン成果物JSONの読み込み
│   ├── routine_data/          # ルーティンが週次で上書きコミットする成果物
│   ├── compose_theater.py     # 劇場公開まとめ本文・SNS投稿案の生成
│   ├── compose_vod.py         # VOD配信まとめ本文・Xスレッド案の生成
│   ├── theater_calendar.py    # 週範囲計算・正規化・重複キー + TheaterEntry
│   ├── vod_calendar.py        # 同上（VOD）+ SERVICES / VodEntry
│   ├── wp_client.py           # WP REST API クライアント（CPT投稿・既存記事照合）
│   ├── sheets.py              # Google Sheets I/O（gspread）
│   ├── approval.py            # Slack通知
│   ├── prompts/               # プロンプト本文（Markdown）
│   └── tests/
├── utils/                     # 両サブシステム共用（rate_limit / browser）
├── docs/
│   ├── feature/               # 機能仕様（劇場・VOD・ルーティン・戦略）
│   ├── drop/                  # 廃止した仕様の凍結保存
│   ├── archive/               # 過去バージョン
│   ├── x-news-bot-spec.md     # news_bot 実装仕様書
│   ├── relations.md           # データリレーション（ACF + taxonomy ER図）
│   ├── json-output.md         # WP REST API レスポンス仕様
│   ├── operations.md          # 運用フロー・アーキテクチャ・設計思想
│   ├── vod-scraping-api.md    # VODスクレイピングAPI仕様
│   ├── cache.md               # キャッシュ設計
│   ├── cloudflare-cache-setup.md  # Cloudflareキャッシュ設定
│   ├── cloud-run-deploy.md    # Cloud Run デプロイ手順
│   └── workload-identity-setup.md # Workload Identity Federation 設定手順
└── README.md                  # セットアップ・実行手順
```

> `utils/` には両サブシステムから使う汎用コードのみを置く。特定のサブシステムに閉じたコード
> （WordPress/JustWatch/Slack クライアント等）はそれぞれのディレクトリ直下に置く。

## news_bot のパイプライン

3段構成。**発見と公開の間に必ず人間の承認が入る。**

```
発見（ルーティン: Claudeサブスク内）→ PR
  ↓ 人間がレビュー（1次承認：情報が事実として妥当か）
取り込み（*_import）→ シートへ「承認待ち」で保存 + Slack通知
  ↓ 人間がシートで承認（2次承認：記事に載せるか）
公開（*_publish）→ WP CPT下書き投稿 + SNS投稿案をSlackへ
```

| | 劇場公開 | VOD配信 |
|---|---|---|
| 取り込み | `theater_import` | `vod_import`（+ X公式アカウント抽出） |
| 公開 | `theater_publish`（木 07:00 JST） | `vod_publish`（月 07:00 JST） |
| CPT | `theater_release` | `vod_release` |
| 区切り方 | 公開日別 | サービス別 |

- **ルーティンはAIからは設定・起動できない。** `/schedule` から人間が設定する
  （プロンプトは [docs/feature/routine-prompts.md](docs/feature/routine-prompts.md)）
- `*_discover`（AI API方式）は cron 停止済みだがコードは残す。ルーティンが止まった際の
  手動フォールバック。**従量課金が発生する**ため実行時は注意する
- SNSへの自動投稿は行わない。Slackに投稿案を出し、人間が手動投稿する

## docs/ のメンテナンス

**`docs/` にドキュメントを追加・削除・大幅リライトしたら、Obsidian Vault側の [`../obsidian/vod_scraping_api/_関連マップ.md`](../obsidian/vod_scraping_api/_関連マップ.md) も同じタイミングで更新する**（新規ファイルの関連付け追加、削除ファイルの記載除去）。あわせてリポジトリ横断の関連がある場合は [`../obsidian/_横断マップ.md`](../obsidian/_横断マップ.md) も確認・更新する。これらのマップファイルは本リポジトリではなくObsidian Vault（`katsumscore/obsidian/`）側の管理物。

## 技術スタック

| レイヤー | 技術 |
|---|---|
| 実行環境 | Python 3.11 / Cloud Run（第2世代・`vod_bot`）/ GitHub Actions（`news_bot`） |
| Web フレームワーク | Flask + gunicorn（`vod_bot`） |
| スクレイピング | requests + BeautifulSoup / Playwright（U-NEXT / DMM TV / Crunchyroll） |
| データストア | Google Sheets（`news_bot` の情報源・収集データ管理、gspread） |
| AI | Claude API（判定・抽出）/ Claudeルーティン（週次のWeb検索） |
| 認証（GCP） | Workload Identity Federation（SA キー不要） |
| CI/CD | GitHub Actions（main push → Cloud Run 自動デプロイ） |

## 対応 VOD サービス（vod_bot）

| サービス | キー名 | URL形式 | 実装 |
|---|---|---|---|
| Amazon Prime Video | `amazon_prime_video` | `https://www.amazon.co.jp/gp/video/detail/{id}` | requests + BS4 |
| Netflix | `netflix` | `https://www.netflix.com/jp/title/{id}` | requests + BS4 |
| Hulu | `hulu` | `https://www.hulu.jp/watch/{id}` | requests + BS4 |
| U-NEXT | `unext` | `https://video.unext.jp/title/SID{id}` | Playwright |
| Disney+ | `disney_plus` | `https://www.disneyplus.com/ja-jp/movies/{slug}` | requests + BS4 |
| DMM TV | `dmm_tv` | `https://tv.dmm.com/vod/detail/?season={id}` | Playwright |
| Apple TV | `apple_tv` | `https://tv.apple.com/{region}/movie/{slug}/{id}` | requests + BS4 |
| YouTube | `youtube` | `https://www.youtube.com/watch?v={video_id}` | requests + BS4 |
| Crunchyroll | `crunchyroll` | `https://www.crunchyroll.com/series/{ID}/{slug}` | Playwright |

> Amazon: Cloud Run 環境では `/gp/video/detail/{id}` 形式を使用すること（`/dp/{asin}` はブロックされる場合あり）
> Crunchyroll: アニメカテゴリ（category slug: `anime`）の en 作品のみ対象

## ステータス値（vod_bot）

| status | 意味 |
|---|---|
| `streaming` | 見放題 |
| `rental` | レンタル（price に金額） |
| `purchase` | 購入（price に金額） |
| `unavailable` | 配信なし |
| `ended` | 配信終了（404等） |
| `''` | 未取得 |

## 投稿状態（news_bot）

「劇場公開予定」「VOD配信予定」シートの投稿状態列。**3値で運用する。**

| 値 | 意味 | 誰が設定するか |
|---|---|---|
| `承認待ち` | 発見直後。人間の確認前 | `*_import` / `*_discover` |
| `承認済み` | 人間が実在・日付を確認した | 人間（シート上で手動） |
| `投稿済み` | 週次まとめに掲載しWP投稿済み | `*_publish` |

> 各仕様書8.の投稿状態テーブル（`投稿候補`/`保存のみ`等）は初期設計のもので実装では使っていない。

## コーディング規約

### 共通

- 環境変数はすべて `os.environ` 経由で参照し、ハードコードしない
- **未登録のGitHub Secretは空文字で渡る。** 既定値へのフォールバックは
  `os.environ.get(k, default)` ではなく `os.environ.get(k) or default` と書く

### vod_bot

- チェッカーは `vod_bot/checkers/` に追加し、`check(self, url: str) -> dict` を持つ
  `*Checker` クラスとして実装する
- 戻り値は `{"status": str, "price": float | None}` に統一する
- ロボット検出・サーバーエラー時は `RuntimeError` を raise する（呼び出し元でスキップ）
- 新規チェッカーを追加したら `vod_bot/weekly_patch.py` の `_CHECKER_MAP` にも追加する
- JS レンダリングが必要なサービスは Playwright を使用する

### news_bot

- **収集した情報は必ず `承認待ち` で保存する。** 人間の承認を経ずに公開へ流さない
- **Katsumascore照合はタイトル完全一致のみ。** 一致しなければリンクを付けない
  （`search` はあいまい検索のため、誤リンクを作るくらいなら空欄にする）
- SNSへの自動投稿は行わない。Slackに投稿案を出して人間が手動投稿する
- あらすじ・紹介文などの表現は収集・保存しない（事実情報のみ）
- データクラス・定数（`TheaterEntry` / `VodEntry` / `SERVICES`）は `*_calendar.py` に置く。
  `discover_*.py` に置くとAI SDK非依存のモジュールまで `anthropic`/`openai` を要求してしまう
- プロンプトはコードにハードコードせず `news_bot/prompts/*.md` で管理する

## 環境変数

### 共通（WordPress）

| 変数名 | 用途 | 必須 |
|---|---|---|
| `WP_API_URL` | WordPress REST API ベース URL | ○ |
| `WP_USER` | WordPress ユーザー名 | ○ |
| `WP_APP_PASSWORD` | WordPress Application Password | ○ |

### vod_bot

| 変数名 | 用途 | 必須 |
|---|---|---|
| `WP_BASIC_USER` | サーバー Basic 認証ユーザー名 | △ |
| `WP_BASIC_PASSWORD` | サーバー Basic 認証パスワード | △ |
| `SLACK_WEBHOOK_URL` | Slack 通知 Webhook URL | △ |

### news_bot

| 変数名 | 用途 | 必須 |
|---|---|---|
| `GOOGLE_SHEETS_SPREADSHEET_ID` / `GOOGLE_SHEETS_CREDENTIALS_JSON` | Google Sheets 接続 | ○ |
| `ANTHROPIC_API_KEY` | AI判定・X投稿の構造化抽出 | ○ |
| `OPENAI_API_KEY` | `*_discover` のWeb検索併用（フォールバック時のみ） | △ |
| `X_BEARER_TOKEN` | 公式Xアカウントの投稿取得 | ○ |
| `NEWS_BOT_SLACK_BOT_TOKEN` / `NEWS_BOT_SLACK_APPROVAL_CHANNEL_ID` | Slack通知 | ○ |
| `NEWS_BOT_SLACK_THEATER_CHANNEL_ID` / `NEWS_BOT_SLACK_VOD_CHANNEL_ID` | 専用チャンネル（未設定なら承認チャンネル） | △ |
| `VOD_NEWS_CPT_SLUG` | VOD投稿先CPT（既定 `vod_release`） | △ |
| `THEATER_NEWS_CPT_SLUG` | 劇場投稿先CPT（既定 `theater_release`） | △ |
| `VOD_NEWS_WP_STATUS` / `THEATER_NEWS_WP_STATUS` | 投稿ステータス（既定 `draft`） | △ |

> Slack Bot は**対象チャンネルに招待**しておくこと（`/invite @bot名`）。
> 招待し忘れると `chat.postMessage` が `not_in_channel` で失敗する。

## セキュリティ規約

- WordPress パスワード・API キーはコードにハードコードしない
- `.env` ファイルは `.gitignore` で除外済み（コミット厳禁）
- Cloud Run では環境変数または Secret Manager で管理する
- ドキュメントに記載する場合は `YOUR_PASSWORD` などのプレースホルダーを使用する
