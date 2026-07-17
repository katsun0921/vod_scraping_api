# AGENTS.md

VOD 配信状況スクレイピング API。
WordPress REST API から投稿データを取得し、各 VOD サービスの配信状況を確認・更新する。

## プロジェクト構成

```
vod_scraping_api/
├── main.py                    # Flask エントリーポイント（Cloud Run）
├── checker.py                 # メイン処理（取得・スクレイピング・更新）
├── checkers/
│   ├── __init__.py            # 共通定数（HEADERS / NOT_FOUND_INDICATORS）
│   ├── amazon.py              # Amazon Prime Video チェッカー
│   ├── netflix.py             # Netflix チェッカー
│   ├── hulu.py                # Hulu チェッカー
│   ├── unext.py               # U-NEXT チェッカー（Playwright）
│   ├── disney_plus.py         # Disney+ チェッカー
│   ├── dmm_tv.py              # DMM TV チェッカー（Playwright）
│   ├── apple_tv.py            # Apple TV チェッカー（実装予定）
│   └── youtube.py             # YouTube チェッカー
├── utils/
│   ├── wordpress.py           # WP REST API クライアント
│   └── rate_limit.py          # リクエスト間隔制御
├── docs/
│   ├── relations.md           # データリレーション（ACF + taxonomy ER図）
│   ├── json-output.md         # WP REST API レスポンス仕様
│   ├── operations.md          # 運用フロー・アーキテクチャ・設計思想
│   ├── vod-scraping-api.md    # VODスクレイピングAPI仕様
│   ├── cache.md               # キャッシュ設計
│   └── cloudflare-cache-setup.md  # Cloudflareキャッシュ設定
├── acf/
│   └── acf-vod-status.json    # ACF フィールド定義（WP管理画面からインポート用）
├── Dockerfile                 # Cloud Run 用コンテナ定義
├── requirements.txt           # Python 依存ライブラリ
├── cloud-run-deploy.md        # Cloud Run デプロイ手順
├── workload-identity-setup.md # Workload Identity Federation 設定手順
└── README.md                  # セットアップ・実行手順
```

## 技術スタック

| レイヤー | 技術 |
|---|---|
| 実行環境 | Python 3.11 / Cloud Run（第2世代） |
| Web フレームワーク | Flask + gunicorn |
| スクレイピング | requests + BeautifulSoup / Playwright（U-NEXT / DMM TV） |
| 認証（GCP） | Workload Identity Federation（SA キー不要） |
| CI/CD | GitHub Actions（main push → 自動デプロイ） |

## 対応 VOD サービス

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

## ステータス値

| status | 意味 |
|---|---|
| `streaming` | 見放題 |
| `rental` | レンタル（price に金額） |
| `purchase` | 購入（price に金額） |
| `unavailable` | 配信なし |
| `ended` | 配信終了（404等） |
| `''` | 未取得 |

## コーディング規約

- チェッカーは `checkers/` に追加し `check(url: str) -> dict` を実装する
- 戻り値は `{"status": str, "price": float | None}` に統一する
- ロボット検出・サーバーエラー時は `RuntimeError` を raise する（呼び出し元でスキップ）
- 新規チェッカーを追加したら `checker.py` の `_SERVICE_KEYWORDS` にも追加する
- JS レンダリングが必要なサービスは Playwright を使用する
- 環境変数はすべて `os.environ` 経由で参照し、ハードコードしない

## 環境変数

| 変数名 | 用途 | 必須 |
|---|---|---|
| `WP_API_URL` | WordPress REST API ベース URL | ○ |
| `WP_USER` | WordPress ユーザー名 | ○ |
| `WP_APP_PASSWORD` | WordPress Application Password | ○ |
| `WP_BASIC_USER` | サーバー Basic 認証ユーザー名 | △ |
| `WP_BASIC_PASSWORD` | サーバー Basic 認証パスワード | △ |
| `SLACK_WEBHOOK_URL` | Slack 通知 Webhook URL | △ |

## セキュリティ規約

- WordPress パスワード・API キーはコードにハードコードしない
- `.env` ファイルは `.gitignore` で除外済み（コミット厳禁）
- Cloud Run では環境変数または Secret Manager で管理する
- ドキュメントに記載する場合は `YOUR_PASSWORD` などのプレースホルダーを使用する
