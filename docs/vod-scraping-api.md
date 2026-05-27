# VOD スクレイピング API 仕様

Cloud Run 上で動作する Python サービス。
WordPress REST API から投稿一覧を取得し、各 VOD サービスの配信状況をスクレイピングして ACF を更新する。

---

## アーキテクチャ

```
Cloud Scheduler（定期起動）
    ↓ HTTP POST（IAM 認証）
Cloud Run（vod-scraping-api）
    ↓ REST API GET
WordPress（投稿一覧 + ACF）
    ↓ 各サービス URL をスクレイピング
Amazon / Netflix / Hulu / U-NEXT / Disney+ / DMM TV / Apple TV / YouTube
    ↓ REST API PATCH
WordPress（ACF 更新 + vod taxonomy 同期）
    ↓ Slack Webhook
Slack（新規配信通知）
```

---

## ディレクトリ構成

```
vod_scraping_api/
├── main.py                # Flask エントリーポイント（Cloud Run）
├── checker.py             # メイン処理（取得・スクレイピング・更新）
├── checkers/
│   ├── __init__.py        # 共通定数（HEADERS / NOT_FOUND_INDICATORS）
│   ├── amazon.py          # Amazon Prime Video チェッカー
│   ├── netflix.py         # Netflix チェッカー
│   ├── hulu.py            # Hulu チェッカー
│   ├── unext.py           # U-NEXT チェッカー（Playwright）
│   ├── disney_plus.py     # Disney+ チェッカー
│   ├── dmm_tv.py          # DMM TV チェッカー（Playwright）
│   ├── apple_tv.py        # Apple TV チェッカー（実装予定）
│   └── youtube.py         # YouTube チェッカー
└── utils/
    ├── wordpress.py       # WP REST API クライアント
    └── rate_limit.py      # リクエスト間隔制御
```

---

## HTTP エンドポイント

| メソッド | パス | 概要 |
|---|---|---|
| POST | `/` | VOD 配信状況チェックを全件実行 |
| GET | `/health` | ヘルスチェック |

認証は Cloud Run IAM の Bearer トークンで管理する。

---

## チェッカー仕様

各チェッカーは `check(url: str) -> dict` 関数として実装する。

### 戻り値

```python
{"status": str, "price": float | None}
```

### ステータス値

| status | 意味 |
|---|---|
| `streaming` | 見放題（サブスクリプション） |
| `rental` | レンタル（price に金額） |
| `purchase` | 購入（price に金額） |
| `unavailable` | 配信なし |
| `ended` | 配信終了（404 等） |

### サービス別実装

| サービス | URL 形式 | 実装方式 |
|---|---|---|
| Amazon Prime Video | `https://www.amazon.co.jp/gp/video/detail/{id}` | requests + BeautifulSoup |
| Netflix | `https://www.netflix.com/jp/title/{id}` | requests + BeautifulSoup |
| Hulu | `https://www.hulu.jp/watch/{id}` | requests + BeautifulSoup |
| U-NEXT | `https://video.unext.jp/title/SID{id}` | Playwright（Chromium） |
| Disney+ | `https://www.disneyplus.com/ja-jp/movies/{slug}` | requests + BeautifulSoup |
| DMM TV | `https://tv.dmm.com/vod/detail/?season={id}` | Playwright（Chromium） |
| Apple TV | `https://tv.apple.com/jp/movie/{slug}` | requests + BeautifulSoup（実装予定） |
| YouTube | `https://www.youtube.com/watch?v={video_id}` | requests + BeautifulSoup |

> **Amazon について**: Cloud Run 環境では `/gp/video/detail/{id}` 形式を使用すること。`/dp/{asin}` 形式はブロックされる場合がある。

---

## 共通定数（checkers/__init__.py）

### HEADERS

ブラウザを模倣してブロックを回避するリクエストヘッダー。

```python
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ...",
    "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8",
    "Accept": "text/html,application/xhtml+xml,...",
    "Accept-Encoding": "gzip, deflate, br",
}
```

### NOT_FOUND_INDICATORS

配信終了と判定するキーワード一覧。

```python
NOT_FOUND_INDICATORS = [
    "404",
    "ページが見つかりません",
    "page not found",
    "not found",
    "this title is not available",
    "お探しのページは見つかりませんでした",
]
```

---

## エラーハンドリング

| エラー種別 | 処理 |
|---|---|
| `requests.RequestException` | `RuntimeError` に変換、該当投稿をスキップ |
| ロボット検出（Amazon 等） | `RuntimeError`、該当投稿をスキップ |
| HTTP 5xx | `RuntimeError`、該当投稿をスキップ |
| HTTP 404 | `ended` として正常処理 |

スキップした件数は `errors` カウントに加算される。

### レスポンス例

```json
{"total": 100, "updated": 85, "skipped": 10, "errors": 5}
```

---

## ACF 更新ロジック（utils/wordpress.py）

### update_post() の処理フロー

```
1. GET /posts/{id}?_fields=acf で既存 ACF を取得
2. 対象サービスのフィールドを上書き
   - status / price / updated_at を更新
   - streaming_started_at: 前回 streaming 以外 → 今回 streaming の場合のみ updated_at と同値でセット
3. スキーマ正規化（number|null フィールドの空文字 → null 等）
4. PATCH /posts/{id} で ACF を更新
5. vod taxonomy を同期
   - streaming → term_id を追加
   - streaming 以外 → term_id を削除
6. streaming_started_at を新規セットした場合、Slack 通知（実装予定）
```

### スキーマ正規化

| 対象 | 変換 |
|---|---|
| `number\|null` 型フィールドの空文字 | → `null` |
| `array` 型で `minItems >= 1` の空値 | → フィールドをスキップ |
| `array\|null` 型の `null` 値 | → フィールドをスキップ |

---

## コーディング規約

- チェッカーは `checkers/` に追加し `check(url: str) -> dict` を実装する
- 戻り値は `{"status": str, "price": float | None}` に統一する
- ロボット検出・サーバーエラー時は `RuntimeError` を raise する（呼び出し元でスキップ）
- 新規チェッカーを追加したら `checker.py` の `_SERVICE_KEYWORDS` にも追加する
- JS レンダリングが必要なサービス（U-NEXT / DMM TV）は Playwright を使用する
- 環境変数はすべて `os.environ` 経由で参照し、ハードコードしない
