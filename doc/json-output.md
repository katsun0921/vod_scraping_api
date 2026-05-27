# WP REST API レスポンス仕様

WordPress REST API (`/wp/v2/posts`) で返される ACF フィールドの形式。

---

## エンドポイント

```
GET /wp-json/wp/v2/posts/{id}?_fields=id,slug,acf,vod
```

---

## レスポンス例

```json
{
  "id": 12345,
  "slug": "john-wick-chapter-4",
  "acf": {
    "amazon_prime_video_status": "rental",
    "amazon_prime_video_scraping_url": "https://www.amazon.co.jp/gp/video/detail/B0XXXXXX",
    "amazon_prime_video_price": 500,
    "amazon_prime_video_updated_at": "2026-05-24 10:00:00",
    "amazon_prime_video_streaming_started_at": "",

    "netflix_status": "streaming",
    "netflix_scraping_url": "https://www.netflix.com/jp/title/81XXXXXX",
    "netflix_price": null,
    "netflix_updated_at": "2026-05-24 10:00:00",
    "netflix_streaming_started_at": "2026-04-01 00:00:00",

    "hulu_status": "unavailable",
    "hulu_scraping_url": "https://www.hulu.jp/watch/XXXXXX",
    "hulu_price": null,
    "hulu_updated_at": "2026-05-24 10:00:00",
    "hulu_streaming_started_at": "",

    "unext_status": "",
    "unext_scraping_url": "",
    "unext_price": null,
    "unext_updated_at": "",
    "unext_streaming_started_at": "",

    "disney_plus_status": "",
    "disney_plus_scraping_url": "",
    "disney_plus_price": null,
    "disney_plus_updated_at": "",
    "disney_plus_streaming_started_at": "",

    "dmm_tv_status": "",
    "dmm_tv_scraping_url": "",
    "dmm_tv_price": null,
    "dmm_tv_updated_at": "",
    "dmm_tv_streaming_started_at": "",

    "apple_tv_status": "",
    "apple_tv_scraping_url": "",
    "apple_tv_price": null,
    "apple_tv_updated_at": "",
    "apple_tv_streaming_started_at": "",

    "youtube_status": "",
    "youtube_scraping_url": "",
    "youtube_price": null,
    "youtube_updated_at": "",
    "youtube_streaming_started_at": "",

    "is_exclusive": false,
    "exclusive_service": null,
    "lang": "ja",
    "scraping_disabled": false,
    "scraping_cooldown_until": "",
    "unavailable_check_count": 0
  },
  "vod": [161]
}
```

---

## フィールド型定義

| フィールド | 型 | 備考 |
|---|---|---|
| `{service}_status` | string | `streaming` / `rental` / `purchase` / `unavailable` / `ended` / `''` |
| `{service}_scraping_url` | string | 空文字許容 |
| `{service}_price` | number\|null | `rental` / `purchase` のみ値あり |
| `{service}_updated_at` | string | `"YYYY-MM-DD HH:MM:SS"` または `''` |
| `{service}_streaming_started_at` | string | `"YYYY-MM-DD HH:MM:SS"` または `''`。初回 streaming 検知時のみセット |
| `is_exclusive` | boolean | 独占配信フラグ |
| `exclusive_service` | integer\|null | vod term_id |
| `lang` | string | `"ja"` / `"en"` |
| `scraping_disabled` | boolean | 管理者による探索停止フラグ |
| `scraping_cooldown_until` | string | `"YYYY-MM-DD"` または `''`。次回チェック予定日 |
| `unavailable_check_count` | integer | 連続未配信カウント（クールダウン計算用） |
| `vod` | integer[] | 現在 streaming 中サービスの term_id リスト |

---

## 全件取得（スクレイピング API 用）

```
GET /wp-json/wp/v2/posts?per_page=20&page=1&_fields=id,slug,acf,vod&post_status=publish
```

- ページネーションで全件取得（`per_page=20` でループ）
- `scraping_url` が 1 件以上設定されている投稿のみ処理対象
- `post_status=publish` のみ対象
