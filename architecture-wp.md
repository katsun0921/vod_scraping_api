# アーキテクチャ設計

## 全体像

```
WordPress（データ管理・REST API）
├── Post
│   ├── ACF Group: VOD配信状況
│   │   ├── amazon / netflix / hulu / unext
│   │   └── 各 Group: scraping_url / status / price / updated_at
│   └── Taxonomy: vod_service（配信中サービスを紐付け）
│
└── Taxonomy: vod_service
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

### Taxonomy: `vod_service`

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
GET /wp-json/wp/v2/posts?vod_service=amazon-prime&per_page=100

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
  "vod_service": [term_id]
}
```

---

## vod-scraping-api 処理フロー

```
① GET /wp-json/wp/v2/posts（全件取得、200件 → 2回リクエスト）
   scraping_url が入っているサービスのみ処理対象

② 各サービスの scraping_url をスクレイピング
   status / price を取得

③ PATCH /wp-json/wp/v2/posts/{id}
   ACF の status / price / updated_at を更新

④ status = streaming → vod_service taxonomy を付与
   status ≠ streaming → vod_service taxonomy を削除
```

---

## Next.js 構成

| ページ | エンドポイント |
|---|---|
| `/ja/movie/{slug}` | `GET /wp-json/wp/v2/posts?slug={slug}` |
| `/vod/{service}` | `GET /wp-json/wp/v2/posts?vod_service={service}&per_page=100` |
