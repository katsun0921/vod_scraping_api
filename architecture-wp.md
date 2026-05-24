# アーキテクチャ設計

## 全体像

```
WordPress（データ管理・REST API）
├── Post
│   ├── ACF Group: VOD配信状況
│   │   ├── amazon / netflix / hulu / unext
│   │   └── 各 Group: scraping_url / status / price / updated_at
│   └── Taxonomy: vod（配信中サービスを紐付け）
│
└── Taxonomy: vod
    ├── amazon-prime
    ├── netflix
    ├── hulu
    └── u-next

          ↓ wp-json REST API

┌─────────────────┐        ┌──────────────────────────┐
│   Next.js        │        │   vod-scraping-api        │
│                 │        │   (Cloud Run)             │
│ /ja/movie/slug  │        │                          │
│ /vod/{service}  │        │ ① GET /wp-json/wp/v2/posts│
└─────────────────┘        │ ② 各URLをスクレイピング   │
                           │ ③ PATCH で ACF・taxonomy  │
                           │   を更新                  │
                           └──────────────────────────┘
```

---

## WordPress 構成

### Post

| 項目 | 内容 |
|---|---|
| 投稿タイプ | Post（通常投稿） |
| slug | 映画識別子（例: `equalizer-2014`） |
| URL | `https://katsumascore.blog/ja/movie/{slug}` |

### Taxonomy: `vod`

「現在配信中のサービス」を Post に紐付ける。
`status=streaming` のサービスのみ付与し、それ以外は削除する。

| slug | label |
|---|---|
| `amazon-prime` | Amazon Prime Video |
| `netflix` | Netflix |
| `hulu` | Hulu |
| `u-next` | U-NEXT |

### ACF フィールドグループ: `VOD配信状況`

スクレイピング結果を保持する。配信していないサービスは空のままにする。

```
post
└── ACF Group: VOD配信状況（show_in_rest: true）
    ├── amazon（group）
    │   ├── scraping_url（url）
    │   ├── status（select）
    │   ├── price（number）
    │   └── updated_at（text）
    ├── netflix（group）
    ├── hulu（group）
    └── unext（group）
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
GET /wp-json/wp/v2/posts?vod=amazon-prime&per_page=30

# 記事詳細
GET /wp-json/wp/v2/posts?slug=equalizer-2014
```

レスポンス例：

```json
{
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

Application Password で認証し PATCH で更新する。

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

# taxonomy の更新
PATCH /wp-json/wp/v2/posts/{id}
{
  "vod": [term_id]
}
```

---

## vod-scraping-api 処理フロー

```
① GET /wp-json/wp/v2/posts（per_page=20 でページネーション全件取得）
   scraping_url が入っているサービスのみ処理対象

② 各サービスの scraping_url をスクレイピング
   status / price を取得

③ GET /wp-json/wp/v2/posts/{id}?_fields=acf
   既存 ACF データを取得（PATCH 時に必須フィールドを保持するため）

④ PATCH /wp-json/wp/v2/posts/{id}
   ACF の status / price / updated_at を更新

⑤ status = streaming → vod taxonomy を付与
   status ≠ streaming → vod taxonomy を削除
```

---

## Cloud Run デプロイ手順

### 環境変数

Cloud Run の環境変数は GCP コンソールから設定する（gcloud CLI でのシェルエスケープ問題を避けるため）。

| 変数名 | 内容 |
|---|---|
| `WP_API_URL` | `https://YOUR_DOMAIN/wp-json/wp/v2` |
| `WP_USER` | WordPress ユーザー名 |
| `WP_APP_PASSWORD` | WordPress Application Password（スペースなし） |
| `WP_BASIC_USER` | サーバー Basic 認証ユーザー名 |
| `WP_BASIC_PASSWORD` | サーバー Basic 認証パスワード |

### ビルド・デプロイ

```bash
# イメージビルド & プッシュ
gcloud builds submit \
  --tag asia-northeast1-docker.pkg.dev/YOUR_GCP_PROJECT/vod-scraping-api/app:latest .

# デプロイ
gcloud run deploy vod-scraping-api \
  --image asia-northeast1-docker.pkg.dev/YOUR_GCP_PROJECT/vod-scraping-api/app:latest \
  --region asia-northeast1
```

### 動作テスト

```bash
# 特定 slug のみテスト
curl -s -X POST \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  -H "Content-Type: application/json" \
  -d '{"slug": "equalizer-2014", "force": true}' \
  https://YOUR_CLOUD_RUN_URL/

# 全件実行（30日以内更新済みはスキップ）
curl -s -X POST \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  -H "Content-Type: application/json" \
  -d '{}' \
  https://YOUR_CLOUD_RUN_URL/
```

---

## ACF PATCH の注意点

### なぜ既存データを取得してから PATCH するか

WordPress REST API は ACF フィールド更新時にグループ全体のバリデーションを行う。
`acf.amazon.status` だけ送っても、同じ ACF グループの `lang`（required）や `genre`（required, minItems=1）が
ないとエラーになるため、**既存データを取得して必要フィールドを保持したまま更新**する必要がある。

### データ正規化が必要な理由

WordPress 管理画面で未入力のフィールドは空文字列 `""` で保存されるが、
REST API バリデーションは型チェックを行うため、そのまま送り返すとエラーになる。

| フィールド型 | 空文字列の挙動 | 対処 |
|---|---|---|
| `number\|null` | バリデーションエラー | `null` に変換（`score`, `price` 等） |
| `array` (minItems=1) | バリデーションエラー | ペイロードから除外 |
| `array\|null` で `null` | バリデーションエラー | ペイロードから除外 |

スキーマは起動時に `OPTIONS /wp-json/wp/v2/posts` で取得し、インメモリキャッシュする。

### 認証の分離

| 操作 | 認証方式 |
|---|---|
| GET（投稿取得・スキーマ取得） | サーバー Basic 認証（`WP_BASIC_USER` / `WP_BASIC_PASSWORD`） |
| PATCH（ACF・taxonomy 更新） | WordPress Application Password（`Authorization: Basic` ヘッダー） |

PATCH 時はサーバー Basic 認証を使わない（`requests` の `auth=` パラメータと `Authorization` ヘッダーが競合するため）。

---

## Next.js 構成

| ページ | エンドポイント |
|---|---|
| `/ja/movie/{slug}` | `GET /wp-json/wp/v2/posts?slug={slug}` |
| `/vod/{service}` | `GET /wp-json/wp/v2/posts?vod={service}&per_page=100` |
