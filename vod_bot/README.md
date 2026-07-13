# vod_bot

VOD配信状況スクレイピングAPI。WordPress REST API から投稿データを取得し、各VODサービス（Netflix・Amazon Prime Video・Hulu・U-NEXT・Disney+・DMM TV・Apple TV・YouTube・Crunchyroll）の配信状況（`status` / `price`）を確認し、WordPressへ書き戻す。

このリポジトリはモノレポ構成で、`news_bot/`（Xニュース自動投稿システム）とは独立したサブシステム。依存関係（`requirements.txt`）・CIジョブは `vod_bot/` 配下で完結し、`news_bot/` には一切影響しない。ただし [`../utils/`](../utils/) はVOD/news両方から使う汎用コード（レート制御・User-Agent等）を置く共有ディレクトリで、`vod_bot/Dockerfile` のビルドコンテキストにはリポジトリルートを使用しこれを取り込む（詳細は「デプロイ」参照）。

## 責務

- WordPress REST API から VOD 関連投稿を取得
- 各VODサービスのURLをスクレイピング（`requests`+`BeautifulSoup` / Playwright）し、配信状況を判定
- JustWatch API を使った未登録URLの検索
- 判定結果を ACF フィールド・taxonomy として WordPress に書き戻す
- 新着配信の検知結果を Slack に通知

## 実行方式

| エントリーポイント | 用途 | 実行環境 |
|---|---|---|
| `main.py`（Flask） | `POST /weekly-patch` を Cloud Run 上で待ち受け、Cloud Scheduler から起動 | Cloud Run（本番） |
| `weekly_patch.py`（CLI） | 同じ処理をコマンドラインから直接実行 | GitHub Actions cron / ローカル |

## ローカル実行手順

### 1. 仮想環境の作成

```bash
cd vod_bot
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium  # U-NEXT / DMM TV / Crunchyroll 用ブラウザのインストール
```

### 2. 環境変数の設定

```bash
cp .env.example .env
# .env を編集して WP_API_URL / WP_USER / WP_APP_PASSWORD を設定
```

### 3. 実行

`checkers/` `weekly_patch.py` などは `vod_bot/` 直下がsys.pathに乗る前提のフラット構成だが、
共有の `utils/`（レート制御・User-Agent）はリポジトリルートにあるため、`vod_bot/` から実行する際は
`PYTHONPATH` にリポジトリルートを追加する。

```bash
export PYTHONPATH=..

# 今週のバッチ内全件を処理（投稿数増加に自動追従）
python weekly_patch.py

# バッチ0を強制実行
python weekly_patch.py --batch 0

# 対象確認のみ（更新しない）
python weekly_patch.py --dry-run

# 7日以内の更新済みもスキップせず強制処理
python weekly_patch.py --force

# 特定の slug のみ処理
python weekly_patch.py --slug john-wick

# 上限を指定（デバッグ用）
python weekly_patch.py --limit 50
```

## ステータス値

| status | 意味 |
|---|---|
| `streaming` | 見放題 |
| `rental` | レンタル（price に金額） |
| `purchase` | 購入（price に金額） |
| `unavailable` | 配信なし |
| `ended` | 配信終了（404等） |

## 対応VODサービス

| サービス名 | URL形式 | 備考 |
|---|---|---|
| `Netflix` | `https://www.netflix.com/jp/title/{id}` | |
| `Amazon Prime Video` | `https://www.amazon.co.jp/gp/video/detail/{id}` | `/dp/{asin}` 形式は Cloud Run からブロックされる場合あり |
| `Hulu` | `https://www.hulu.jp/watch/{id}` | |
| `U-NEXT` | `https://video.unext.jp/title/SID{id}` | SPA のため Playwright で取得 |
| `DMM TV` | `https://tv.dmm.com/vod/detail/?season={id}` | Playwright で取得 |
| `Disney+` | `https://www.disneyplus.com/ja-jp/movies/{slug}` | |
| `Apple TV` | `https://tv.apple.com/{region}/movie/{slug}/{id}` | |
| `YouTube` | `https://www.youtube.com/watch?v={video_id}` | |
| `Crunchyroll` | `https://www.crunchyroll.com/series/{ID}/{slug}` | アニメカテゴリの en 作品のみ |

## ディレクトリ構成

```
vod_bot/
├── main.py           # Flask エントリーポイント（Cloud Run）
├── weekly_patch.py   # 週次パッチ統合ランナー（URLチェック + JustWatch検索）
├── justwatch.py      # JustWatch APIクライアント
├── slack.py          # Slack Webhook通知
├── wordpress.py      # WordPress REST APIクライアント
├── checkers/         # VODサービスごとのスクレイピングロジック
├── tests/
├── acf/              # ACF フィールド定義（WP管理画面からインポート用）
├── requirements.txt
└── Dockerfile        # ビルドコンテキストはリポジトリルート（../utils/ を含めるため）

../utils/             # vod_bot / news_bot 共有の汎用コード（レート制御・User-Agent）
```

## デプロイ

- Cloud Run へのデプロイ手順 → [../docs/cloud-run-deploy.md](../docs/cloud-run-deploy.md)
- Workload Identity Federation の設定 → [../docs/workload-identity-setup.md](../docs/workload-identity-setup.md)
- CI/CD は GitHub Actions（`.github/workflows/deploy.yml`）で自動化済み。`../utils/` を含めるため Docker のビルドコンテキストはリポジトリルート（`docker build --file vod_bot/Dockerfile .`）
