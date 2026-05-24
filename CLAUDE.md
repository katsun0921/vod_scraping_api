# CLAUDE.md

このリポジトリは VOD 配信状況スクレイピング API です。
Google Sheets または WordPress の投稿データを入力源とし、
各 VOD サービスの URL にアクセスして配信状況を確認・更新します。

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
│   ├── unext.py               # U-NEXT チェッカー（Playwright 使用）
│   ├── disney_plus.py         # Disney+ チェッカー
│   └── dmm_tv.py              # DMM TV チェッカー（Playwright 使用）
├── utils/
│   ├── sheets.py              # Google Sheets 読み書きユーティリティ（現行）
│   └── rate_limit.py          # リクエスト間隔制御
├── Dockerfile                 # Cloud Run 用コンテナ定義
├── requirements.txt           # Python 依存ライブラリ
├── .env.example               # 環境変数テンプレート
├── cloud-run-deploy.md        # Cloud Run デプロイ手順
├── workload-identity-setup.md # Workload Identity Federation 設定手順
├── architecture-wp.md         # WordPress 移行アーキテクチャ設計
└── README.md                  # セットアップ・実行手順
```

## 技術スタック

| レイヤー | 技術 |
|---|---|
| 実行環境 | Python 3.11 / Cloud Run（第2世代） |
| Web フレームワーク | Flask + gunicorn |
| スクレイピング | requests + BeautifulSoup / Playwright（U-NEXT） |
| 認証（GCP） | Workload Identity Federation（SA キー不要） |
| CI/CD | GitHub Actions（main push → 自動デプロイ） |

## 対応 VOD サービス

| サービス | URL形式 | 実装 |
|---|---|---|
| Amazon Prime Video | `https://www.amazon.co.jp/gp/video/detail/{id}` | requests + BeautifulSoup |
| Netflix | `https://www.netflix.com/jp/title/{id}` | requests + BeautifulSoup |
| Hulu | `https://www.hulu.jp/watch/{id}` | requests + BeautifulSoup |
| U-NEXT | `https://video.unext.jp/title/SID{id}` | Playwright（Chromium） |
| Disney+ | `https://www.disneyplus.com/ja-jp/movies/{slug}` | requests + BeautifulSoup |
| DMM TV | `https://tv.dmm.com/vod/detail/?season={id}` | Playwright（Chromium） |

### Amazon Prime Video URL について

Cloud Run 環境では `/gp/video/detail/{id}` 形式を使用すること。
`/dp/{asin}` 形式はブロックされる場合がある。

## ステータス値

| status | 意味 |
|---|---|
| `streaming` | 見放題 |
| `rental` | レンタル（price に金額） |
| `purchase` | 購入（price に金額） |
| `unavailable` | 配信なし |
| `ended` | 配信終了（404等） |

## コーディング規約

- チェッカーは `checkers/` に追加し `check(url: str) -> dict` メソッドを実装する
- 戻り値は `{"status": str, "price": float | None}` に統一する
- ロボット検出・サーバーエラー時は `RuntimeError` を raise する（呼び出し元でスキップ処理）
- 新規チェッカーを追加したら `checker.py` の `_SERVICE_KEYWORDS` に追加する
- U-NEXT のように JS レンダリングが必要なサービスは Playwright を使用する
- 環境変数はすべて `os.environ` 経由で参照し、ハードコードしない

## 環境変数

| 変数名 | 用途 | 必須 |
|---|---|---|
| `WP_API_URL` | WordPress REST API のベース URL（例: `https://example.com/wp-json/wp/v2`） | ○ |
| `WP_USER` | WordPress ユーザー名 | ○ |
| `WP_APP_PASSWORD` | WordPress Application Password | ○ |

## セキュリティ規約

- スプレッドシート ID・WordPress パスワード・API キーなどはコードにハードコードしない
- `.env` ファイルは `.gitignore` で除外済み（コミット厳禁）
- Cloud Run では環境変数または Secret Manager で管理する
- ドキュメントに記載する場合は `YOUR_SPREADSHEET_ID` などのプレースホルダーを使用する

## アーキテクチャ

入力源・出力先ともに WordPress REST API を使用する。
詳細は [architecture-wp.md](architecture-wp.md) を参照。

| 項目 | 内容 |
|---|---|
| 入力源 | WordPress REST API（ACF: scraping_url） |
| 出力先 | WordPress REST API（ACF: status / price / updated_at、taxonomy: vod） |
| 認証 | Application Password（Basic 認証） |
