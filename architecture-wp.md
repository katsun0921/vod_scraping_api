# WordPress 移行アーキテクチャ設計

スプレッドシート + GAS 管理から WordPress 管理への移行設計。

---

## 移行の背景と目的

### 現状（スプレッドシート構成）

```
Google Sheets（Works / VODs / Services）
    ↓ GAS で補完・JSON生成
JSON API
    ↓
Next.js（表示）
```

### 課題

- GAS・スプレッドシート・JSON API・WordPress が分散していて管理が複雑
- VOD 一覧ページ（`/vod/{service}`）を作るためのフィルタリングが困難
- スクレイピング結果の書き戻し先がスプレッドシートで Next.js と連携しにくい

### 移行後の目標

- WordPress を唯一のデータソースにする
- `vod/{service}` 一覧ページを WordPress タクソノミーで実現
- スクレイピング結果を WordPress に直接書き戻す

---

## 新アーキテクチャ全体像

```
WordPress（データ管理・REST API）
├── Post（通常投稿）※ 現状維持
│   ├── slug: equalizer-2014
│   ├── ACF: 既存フィールド（鑑賞VOD・アフィリエイト）※ 変更なし
│   ├── ACF: VOD配信状況（新規追加）← スクレイピング結果
│   └── Taxonomy: vod_service（新規追加）← 配信中サービスを紐付け
│
└── Taxonomy: vod_service
    ├── amazon-prime
    ├── netflix
    ├── hulu
    └── u-next

        ↓ wp-json REST API

┌─────────────────┐      ┌──────────────────────────┐
│   Next.js        │      │   vod-scraping-api        │
│                 │      │   (Cloud Run)             │
│ /ja/movie/slug  │      │                          │
│ /vod/{service}  │      │ ① GET /wp-json/wp/v2/posts│
│                 │      │   scraping_url を取得     │
└─────────────────┘      │ ② 各URLをスクレイピング   │
                         │ ③ PATCH /wp-json/wp/v2/  │
                         │   posts/{id}             │
                         │   ACF と taxonomy を更新  │
                         └──────────────────────────┘
```

---

## WordPress 構成

### Post（通常投稿）

現状の Post をそのまま使用。URL 構造は変更なし。

```
https://katsumascore.blog/ja/movie/equalizer-2014
    ↕
WordPress Post: slug = equalizer-2014
```

### 新規追加: Taxonomy `vod_service`

「現在配信中のサービス」を Post に紐付けるタクソノミー。
スクレイピングで `status=streaming` になったサービスを付与し、
`streaming` 以外になったら削除する。

| slug | label |
|---|---|
| `amazon-prime` | Amazon Prime Video |
| `netflix` | Netflix |
| `hulu` | Hulu |
| `u-next` | U-NEXT |

### 新規追加: ACF フィールドグループ `VOD配信状況`

スクレイピング結果を保持する。配信していないサービスは空のままにする。

```
post
└── ACF Group: VOD配信状況（show_in_rest: true）
    │
    ├── amazon（group）
    │   ├── scraping_url（url）    ← スクレイピング用直リンク
    │   ├── status（select）       ← streaming / rental / purchase / unavailable / ended
    │   ├── price（number）        ← 価格（円）。見放題は0、なしはnull
    │   └── updated_at（text）     ← 最終更新日時 "YYYY-MM-DD HH:MM:SS"
    │
    ├── netflix（group）
    │   ├── scraping_url（url）
    │   ├── status（select）
    │   ├── price（number）
    │   └── updated_at（text）
    │
    ├── hulu（group）
    │   ├── scraping_url（url）
    │   ├── status（select）
    │   ├── price（number）
    │   └── updated_at（text）
    │
    └── unext（group）
        ├── scraping_url（url）
        ├── status（select）
        ├── price（number）
        └── updated_at（text）
```

#### status の選択肢

| value | label |
|---|---|
| `streaming` | 見放題 |
| `rental` | レンタル |
| `purchase` | 購入 |
| `unavailable` | 配信なし |
| `ended` | 配信終了 |

---

## REST API

### Next.js からの取得

```
# VOD一覧ページ（/vod/amazon-prime）
GET /wp-json/wp/v2/posts?vod_service=amazon-prime&per_page=100

# 記事詳細（ACF の VOD データも含む）
GET /wp-json/wp/v2/posts?slug=equalizer-2014
```

レスポンス例：

```json
{
  "id": 123,
  "slug": "equalizer-2014",
  "acf": {
    "amazon": {
      "scraping_url": "https://www.amazon.co.jp/gp/video/detail/xxx",
      "status": "streaming",
      "price": 0,
      "updated_at": "2026-05-24 10:00:00"
    },
    "netflix": {
      "scraping_url": "",
      "status": "",
      "price": null,
      "updated_at": ""
    },
    "hulu": {
      "scraping_url": "https://www.hulu.jp/watch/xxx",
      "status": "streaming",
      "price": 0,
      "updated_at": "2026-05-24 10:00:00"
    },
    "unext": {
      "scraping_url": "",
      "status": "",
      "price": null,
      "updated_at": ""
    }
  }
}
```

### vod-scraping-api からの更新

WP の Application Password で認証し、PATCH で更新する。

```
# ACF フィールドの更新
PATCH /wp-json/wp/v2/posts/{id}
Authorization: Basic base64(user:application_password)

{
  "acf": {
    "amazon": {
      "status": "streaming",
      "price": 0,
      "updated_at": "2026-05-24 10:00:00"
    }
  }
}

# taxonomy の更新（streaming になったら付与、それ以外は削除）
PATCH /wp-json/wp/v2/posts/{id}
{
  "vod_service": [term_id]
}
```

---

## vod-scraping-api の改修内容

### 変更点

| 項目 | 現状 | 移行後 |
|---|---|---|
| 入力源 | Google Sheets（gspread） | WordPress REST API |
| 出力先 | Google Sheets（gspread） | WordPress REST API |
| 認証 | サービスアカウント JSON | Application Password |
| 環境変数 | `SPREADSHEET_ID` | `WP_API_URL` / `WP_USER` / `WP_APP_PASSWORD` |

### 処理フロー

```
① GET /wp-json/wp/v2/posts?per_page=100&page=N
   全 post を取得（200件 → 2回リクエスト）
   scraping_url が入っているサービスのみ処理対象

② 各サービスの scraping_url をスクレイピング
   status / price を取得

③ PATCH /wp-json/wp/v2/posts/{id}
   ACF の status / price / updated_at を更新

④ status = streaming → vod_service taxonomy を付与
   status ≠ streaming → vod_service taxonomy を削除
```

---

## Next.js の変更内容

### 新規: `/vod/{service}` 一覧ページ

```typescript
// 例: /vod/amazon-prime
const res = await fetch(
  `${WP_API_URL}/wp-json/wp/v2/posts?vod_service=amazon-prime&per_page=100`
)
const posts = await res.json()
```

### 既存: `/ja/movie/{slug}` 詳細ページ

ACF フィールドに `amazon` / `netflix` / `hulu` / `unext` が追加されるため、
VOD 配信状況の表示ロジックを追加する。

---

## 移行ステップ

| ステップ | 内容 | 担当 |
|---|---|---|
| 1 | WP に `vod_service` タクソノミーを追加 | WP 管理画面 or functions.php |
| 2 | ACF フィールドグループ `VOD配信状況` を作成 | ACF 管理画面 |
| 3 | WP Application Password を発行 | WP 管理画面 |
| 4 | `vod-scraping-api` を WP API 対応に改修 | Python |
| 5 | Cloud Run の環境変数を更新 | GCP |
| 6 | Next.js に `/vod/{service}` 一覧ページを追加 | Next.js |
| 7 | 既存スプレッドシートをアーカイブ・GAS を廃止 | - |

---

## 廃止するもの

| 廃止対象 | 理由 |
|---|---|
| Google Sheets（Works / VODs / Services / Countries） | WordPress に一元化 |
| Google Apps Script | WP 管理画面で代替 |
| サービスアカウント JSON（Sheets 用） | 不要になる |
| `SPREADSHEET_ID` 環境変数 | `WP_API_URL` 等に置き換え |
