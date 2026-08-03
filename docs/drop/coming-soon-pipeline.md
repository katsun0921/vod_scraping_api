# Coming Soon パイプライン 実装仕様・手順書

作成: 2026-07-13
対象ブランチ: `feature/coming-soon`

> **スコープ**: このドキュメントは `vod_scraping_api` リポジトリで対応する  
> **Part 2（スクレイピング・API パイプライン）** のみを扱う。  
> Part 1（WordPress ACF設定）・Part 3（Next.js フロントエンド）は別リポジトリで対応。

---

## ⚠️ 廃止（2026-07-22、`docs/feature/` → `docs/drop/` へ移動）

**本パイプラインは規約上の理由で実装不可と判断し、`docs/feature/`（実装予定）から
`docs/drop/`（不採用・保留のまま凍結）へ移動した。**

本パイプラインは主に2つの取得手段（TMDb API・各VODサービス公式サイトのスクレイピング）に
依存していたが、`docs/feature/vod-release-calendar-spec.md`（VOD配信情報収集パイプライン）
策定にあたっての規約調査（[vod-sources-candidates.md](../feature/vod-sources-candidates.md)）で
**両方とも使用不可**と判明した。

1. **TMDb API（`tmdb_upcoming.py`・`config.py`のTMDB_API_KEY/PROVIDERS）**:
   KatsumascoreはGoogle AdSenseで収益化しており、TMDb APIの「Personal Use」申請フォームの
   非商用・無収益の誓約に反する。商用契約（Commercial APIプラン、年商$1M未満で$149/月〜）を
   契約しない限り使用できない（`theater-sources-candidates.md` A.節で確定済み）
2. **各VODサービス公式サイトのスクレイピング（`scrape_official.py`）**:
   対象3サービス（Netflix / U-NEXT / Prime Video）のうち、Netflix・Prime Videoは
   利用規約でロボット・スクレイパーによる自動アクセス／データ収集を明示的に禁止しており
   最初から不採用（`vod-sources-candidates.md` B節）。**唯一保留だったU-NEXTも、
   人間による規約原文確認の結果、同様に自動化手段を禁止していることが判明し不採用が確定した**
   （2026-07-22）。これにより本パイプラインが依存していた取得手段は全滅した

WP照合部分（`enrich_events.py`の`fetch_wp_post_by_tmdb_id()`/`build_auth()`）だけは
規約上の問題がないため、`docs/feature/vod-release-calendar-spec.md` 10.1節の方針で
（タイトル検索フォールバックを追加した上で）引き続き転用する。

**再開条件**: TMDb商用ライセンス契約が成立した場合のみ、本パイプラインをComing Soonの
取得レイヤーとして復活を検討する（`docs/feature/vod-release-calendar-spec.md` 16.将来拡張、
`docs/feature/theater-sources-candidates.md` A.節と同じ判断）。それまでは凍結する。

---

## 未決定事項（着手前に確認、凍結時点のスナップショット）

| # | 項目 | 影響範囲 |
|---|------|---------|
| 1 | **ストレージ選択**: Cloudflare R2 か Cloud Storage か | enrich_events.py アップロード処理、GitHub Actions、Next.js 環境変数 |
| 2 | **tmdb_id ACF フィールドの有無**: 既存WP投稿に `tmdb_id` メタが存在するか | enrich_events.py の WP照合クエリ |
| 3 | **U-NEXT TMDb プロバイダーID**: `84` で正しいか | config.py `PROVIDERS["unext"]` |
| 4 | **WP Application Password**: 発行済みか | enrich_events.py、GitHub Secrets |

---

## ディレクトリ構成

```
coming-soon/
  config.py              ← TMDb / WP / ストレージ等の設定値
  tmdb_upcoming.py       ← TMDb discover API から配信予定を取得
  scrape_official.py     ← 各VODサービス公式サイトをスクレイピング
  enrich_events.py       ← マージ・補完・WP照合・JSON出力（新規）
  requirements.txt       ← requests, beautifulsoup4, lxml
  output/
    coming-soon-netflix.json
    coming-soon-unext.json
    coming-soon-prime-video.json

.github/workflows/
  coming-soon-sync.yml   ← 1日4回定期実行
```

---

## 処理フロー

```
[tmdb_upcoming.py]        [scrape_official.py]
TMDb discover取得          公式サイトスクレイピング
       ↓                          ↓
       └──────────┬───────────────┘
                  ↓
        [enrich_events.py]
        event_id でマージ・重複排除
                  ↓
        TMDb /movie/{id} or /tv/{id} でメタ補完
        （title_ja, poster_path, genres, duration_min, overview_ja）
                  ↓
        WP REST API で tmdb_id → wp_post_id 照合
        coming_soon_hidden=true の投稿はスキップ
                  ↓
        status 判定（scheduled / available / expired）
                  ↓
        サービス別 JSON 出力 → ストレージ（R2 or GCS）
```

---

## config.py

```python
# coming-soon/config.py
import os

# TMDb
TMDB_API_KEY     = os.environ["TMDB_API_KEY"]
TMDB_BASE        = "https://api.themoviedb.org/3"
TMDB_IMG_BASE    = "https://image.tmdb.org/t/p"
TMDB_REGION      = "JP"
TMDB_LANGUAGE    = "ja-JP"
TMDB_WEEKS_AHEAD = 8

# JP provider IDs（未決定: U-NEXT は要確認）
PROVIDERS = {
    "netflix":     8,
    "prime-video": 9,
    "unext":       84,   # ← 要確認
}

# WordPress
WP_API_BASE = os.environ["WP_API_BASE"]   # https://katsumascore.conohawing.com
WP_APP_USER = os.environ["WP_APP_USER"]
WP_APP_PASS = os.environ["WP_APP_PASS"]

# 出力
OUTPUT_DIR = "coming-soon/output"
SERVICES   = ["netflix", "unext", "prime-video"]

# 取得ページ数上限（1ページ20件 → 最大200件/サービス/メディアタイプ）
MAX_PAGES = 10
```

---

## tmdb_upcoming.py

### 実装ポイント
- `/discover/movie` と `/discover/tv` を対象
- `with_watch_providers={provider_id}` でサービスを絞り込む
- `watch_region=JP`、`with_watch_monetization_types=flatrate`
- `primary_release_date.gte` / `primary_release_date.lte` で `TMDB_WEEKS_AHEAD` 週先まで取得
- `MAX_PAGES` ページ分ループ（20件/ページ）
- 出力: `StreamingEvent` のリスト（`source="tmdb"`）

### available_from の決定ルール
TMDb discover は配信日を直接返さないため:
1. `/movie/{id}/watch/providers` で JP のフラットレート開始日を確認
2. 取得できない場合は `release_date` を代替使用
3. ISO8601 JST フォーマット（例: `2026-07-05T00:00:00+09:00`）に変換

---

## scrape_official.py

### 対象サービスと取得先

| サービス | 取得先 |
|---|---|
| Netflix | https://www.netflix.com/jp/coming-soon |
| U-NEXT | https://video.unext.jp/browse/new-arrivals |
| Prime Video | https://www.amazon.co.jp/gp/video/offers（または TMDb データのみ使用） |

### 実装ポイント
- requests + BeautifulSoup4 でスクレイピング
- JS レンダリングが必要な場合は Playwright を使用（既存 `utils/browser.py` を流用）
- 出力: `StreamingEvent` のリスト（`source="official"`）
- `tmdb_id` は取得できない場合は `None`、`event_id` は `"{service}_official_{title_slug}"` など

---

## enrich_events.py

### StreamingEvent 型

```python
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class StreamingEvent:
    # 識別
    event_id:    str              # "{service}_{tmdb_id}"
    tmdb_id:     Optional[int]
    media_type:  str              # "movie" | "tv"

    # 配信情報
    service:         str          # "netflix" | "unext" | "prime-video"
    available_from:  str          # ISO8601 JST "2026-07-05T00:00:00+09:00"
    status:          str = "scheduled"  # "scheduled" | "available" | "expired"

    # メタデータ（TMDb）
    title_ja:     str = ""
    title_orig:   str = ""
    poster_path:  Optional[str] = None  # "/abc123.jpg"
    genres:       list = field(default_factory=list)
    duration_min: Optional[int] = None
    overview_ja:  str = ""

    # KatsumaScore 連携
    wp_post_id: Optional[int] = None

    # 管理
    source:            str = "tmdb"  # "tmdb" | "official" | "both"
    source_updated_at: str = ""
```

### status 判定ロジック

```python
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))

def determine_status(available_from: str) -> str:
    try:
        dt = datetime.fromisoformat(available_from)
    except ValueError:
        return "scheduled"
    now = datetime.now(JST)
    if dt > now:
        return "scheduled"
    if dt > now - timedelta(days=90):
        return "available"
    return "expired"
```

### WP照合ロジック

```python
import requests
from base64 import b64encode

def fetch_wp_post_by_tmdb_id(tmdb_id: int, wp_base: str, auth: str) -> dict | None:
    """tmdb_id で WP 投稿を検索。coming_soon_hidden=true は None を返す。"""
    url = f"{wp_base}/wp-json/wp/v2/posts"
    params = {
        "meta_key":   "tmdb_id",
        "meta_value": tmdb_id,
        "acf_format": "standard",
        "per_page":   1,
    }
    res = requests.get(url, params=params, headers={"Authorization": f"Basic {auth}"})
    if res.status_code != 200 or not res.json():
        return None

    post = res.json()[0]
    if post.get("acf", {}).get("coming_soon_hidden"):
        return None
    return post


def build_auth(user: str, app_pass: str) -> str:
    return b64encode(f"{user}:{app_pass}".encode()).decode()
```

### JSON 出力仕様

ファイル名: `coming-soon-{service}.json`

```json
{
  "generated_at": "2026-07-13T06:00:00+09:00",
  "service": "netflix",
  "events": [
    {
      "event_id": "netflix_12345",
      "tmdb_id": 12345,
      "media_type": "movie",
      "service": "netflix",
      "available_from": "2026-07-05T00:00:00+09:00",
      "status": "scheduled",
      "title_ja": "タイトル",
      "title_orig": "Original Title",
      "poster_path": "/abc123.jpg",
      "poster_url_w500": "https://image.tmdb.org/t/p/w500/abc123.jpg",
      "genres": ["アクション", "SF"],
      "duration_min": 120,
      "overview_ja": "あらすじ...",
      "wp_post_id": 12725,
      "coming_soon_comment": "シリーズ最高評価の最新作。",
      "coming_soon_highlight": true,
      "coming_soon_priority": 8,
      "source": "both",
      "source_updated_at": "2026-07-13T06:00:00+09:00"
    }
  ]
}
```

> `coming_soon_comment` / `coming_soon_highlight` / `coming_soon_priority` は  
> `wp_post_id` が存在する場合のみ WP ACF から取得する。存在しない場合は `null` / `false` / `0`。

---

## GitHub Actions: coming-soon-sync.yml

```yaml
# .github/workflows/coming-soon-sync.yml
name: Coming Soon Sync

on:
  schedule:
    - cron: '0 21 * * *'   # JST 06:00
    - cron: '0  3 * * *'   # JST 12:00
    - cron: '0  9 * * *'   # JST 18:00
    - cron: '0 14 * * *'   # JST 23:00
  workflow_dispatch:

jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install dependencies
        run: pip install -r coming-soon/requirements.txt

      - name: Run pipeline
        env:
          TMDB_API_KEY: ${{ secrets.TMDB_API_KEY }}
          WP_API_BASE:  ${{ secrets.WP_API_BASE }}
          WP_APP_USER:  ${{ secrets.WP_APP_USER }}
          WP_APP_PASS:  ${{ secrets.WP_APP_PASS }}
          STORAGE_DEST: ${{ secrets.STORAGE_DEST }}
        run: |
          python coming-soon/tmdb_upcoming.py
          python coming-soon/scrape_official.py
          python coming-soon/enrich_events.py

      - name: Upload to storage
        # R2: wrangler r2 object put {STORAGE_DEST}/coming-soon-{service}.json
        # GCS: gsutil cp coming-soon/output/*.json gs://{STORAGE_DEST}/
        run: |
          echo "TODO: ストレージ確定後に実装"
```

### GitHub Secrets 登録値

| Secret | 値 |
|---|---|
| `TMDB_API_KEY` | TMDb API キー |
| `WP_API_BASE` | `https://katsumascore.conohawing.com` |
| `WP_APP_USER` | WordPress Application Password ユーザー名 |
| `WP_APP_PASS` | WordPress Application Password |
| `STORAGE_DEST` | R2 バケット名 or GCS バケット URL（確定後） |

---

## coming-soon/requirements.txt

```
requests>=2.31
beautifulsoup4>=4.12
lxml>=5.0
```

---

## ストレージ選択肢

### 選択肢A: Cloudflare R2（推奨）
- Next.js on Workers と同一 Cloudflare アカウントで管理
- `wrangler r2 object put` でアップロード
- Public URL: `https://pub-{hash}.r2.dev/coming-soon-{service}.json`

### 選択肢B: Cloud Storage + Cloudflare CDN
- `gsutil cp` でアップロード
- 既存インフラ（Cloud Run）との親和性が高い

**確定後にすること**:
1. `enrich_events.py` にアップロード処理を追加
2. GitHub Actions の "Upload to storage" ステップを実装
3. Next.js の `NEXT_PUBLIC_STORAGE_BASE` 環境変数を設定

---

## 実装ステップ

```
Step 1: 未決定事項 4 項目を確定する
         ↓
Step 2: coming-soon/ ディレクトリ作成
        config.py 作成
         ↓
Step 3: tmdb_upcoming.py 実装
        ローカルで TMDB_API_KEY をセットして動作確認
        output/*.json の件数・内容を確認
         ↓
Step 4: scrape_official.py 実装
        Netflix / U-NEXT / Prime Video ページのパーサー精度調整
         ↓
Step 5: enrich_events.py 実装
        マージ・WP照合・JSON出力をローカルでエンドツーエンドテスト
         ↓
Step 6: ストレージ選択・アップロード処理を追加
         ↓
Step 7: GitHub Actions workflow 作成
        GitHub Secrets を登録
        workflow_dispatch で手動実行して動作確認
         ↓
Step 8: スケジュール実行を確認して完了
```

---

## WordPress 側で必要な対応（このリポジトリ外）

WP REST API で `tmdb_id` による絞り込みを使うために、  
WordPress テーマの `functions.php` に以下を追加する必要がある。

```php
// /inc/rest-coming-soon.php として作成し functions.php で require する

// coming_soon_active=1 で coming_soon_hidden=false の投稿のみ返す
add_filter( 'rest_post_query', function( $args, $request ) {
    if ( $request->get_param( 'coming_soon_active' ) ) {
        $args['meta_query'] = array_merge(
            $args['meta_query'] ?? [],
            [
                'relation' => 'AND',
                [
                    'relation' => 'OR',
                    [ 'key' => 'coming_soon_hidden', 'compare' => 'NOT EXISTS' ],
                    [ 'key' => 'coming_soon_hidden', 'value' => '0', 'compare' => '=' ],
                ],
            ]
        );
    }
    return $args;
}, 10, 2 );

// tmdb_id / event_id による meta_query 検索を許可
add_filter( 'rest_post_query', function( $args, $request ) {
    $meta_key   = $request->get_param( 'meta_key' );
    $meta_value = $request->get_param( 'meta_value' );
    $allowed    = [ 'tmdb_id', 'event_id' ];

    if ( $meta_key && in_array( $meta_key, $allowed, true ) && $meta_value !== null ) {
        $args['meta_query'][] = [
            'key'     => sanitize_key( $meta_key ),
            'value'   => sanitize_text_field( $meta_value ),
            'compare' => '=',
        ];
    }
    return $args;
}, 10, 2 );
```

動作確認URL:
```
https://katsumascore.conohawing.com/wp-json/wp/v2/posts
  ?coming_soon_active=1&acf_format=standard&per_page=5
```
