# 週次パッチ 運用プラン

## 全体像

投稿全件を **4週間で1周** するサイクル。毎週月曜に100件を処理し、月末には全投稿が最新状態になる。

```
     第1月曜          第2月曜          第3月曜          第4月曜
      Week 1           Week 2           Week 3           Week 4
   ┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐
   │ batch 0  │     │ batch 1  │     │ batch 2  │     │ batch 3  │
   │  100件   │ ──▶ │  100件   │ ──▶ │  100件   │ ──▶ │  100件   │
   │id % 4 = 0│     │id % 4 = 1│     │id % 4 = 2│     │id % 4 = 3│
   └──────────┘     └──────────┘     └──────────┘     └──────────┘
        ↓                ↓                ↓                ↓
   1ヶ月で最大 400件 をカバー（翌月また Week 1 から繰り返す）
```

---

## スケジューリングバッジ

各投稿は `post_id % 4` で **バッチ番号（0〜3）** に静的割り当てされる。

| バッジ | 割り当て条件 | 処理タイミング |
|--------|------------|---------------|
| badge 0 | post_id % 4 == 0 | 毎月 1〜7 日の月曜 |
| badge 1 | post_id % 4 == 1 | 毎月 8〜14 日の月曜 |
| badge 2 | post_id % 4 == 2 | 毎月 15〜21 日の月曜 |
| badge 3 | post_id % 4 == 3 | 毎月 22〜31 日の月曜 |

- ACF フィールドの追加は **不要**（WordPress 側の変更なし）
- 投稿を追加・削除しても他バッジへの影響なし
- `batch` パラメータを省略すると実行日から自動判定される

---

## 1週間の処理フロー（100件/回）

```
実行日（月曜 02:00）
  │
  ├─ 全投稿を取得（scraping_disabled=true を除外）
  │
  ├─ バッジフィルタ（今週のバッチ番号に一致する投稿を抽出）
  │
  ├─ 優先度ソート
  │    Phase 1: 未スクレイピング投稿（全サービス updated_at 空）
  │             → release_year 古い順
  │    Phase 2: スクレイピング済み投稿
  │             → 配信中サービスありを優先、次に release_year 新しい順
  │
  └─ 上位 100件を処理
       │
       ├─ [Phase 1] URL なしサービス → JustWatch GraphQL で一括検索
       │    見つかった  → scraping_url を登録（次フェーズでチェック）
       │    見つからない → status=unavailable を書き込み
       │
       ├─ [Phase 2] URL ありサービス → 各チェッカーで確認
       │    requests ベース : Amazon / Netflix / Hulu / Disney+ / Apple TV / YouTube
       │    Playwright ベース: U-NEXT / DMM TV / Crunchyroll
       │
       └─ [Phase 3] クールダウン更新
            配信中あり → 30日後にリセット
            全未配信   → 指数バックオフ（30/60/120/240/360日）+ 年齢補正
```

---

## 週次予算

### 1回（100件）の予算試算

| 処理 | 件数（目安） | 単位コスト | 小計 |
|------|------------|-----------|------|
| WordPress GET/PATCH | ~700 回 | 0.5秒/回 | ~5.8分 |
| JustWatch GraphQL | ~100 回 | 3秒/回 | ~5分 |
| URL スクレイピング（requests） | ~210 回 | 3秒/回 | ~10.5分 |
| URL スクレイピング（Playwright）| ~65 回 | 15秒/回 | ~16分 |
| **合計** | | | **約 37〜42 分** |

> `budget.estimated_minutes` として API レスポンスに含まれる。

### 月次合計（4週 × 100件）

| 指標 | 推定値 |
|------|--------|
| 処理投稿数 | 最大 400件/月 |
| JustWatch API | 400〜800 calls/月 |
| URL スクレイピング | 1,200〜2,400 calls/月 |
| WordPress API | 2,800〜4,000 calls/月 |
| Cloud Run 処理時間 | 約 2.5〜3 時間/月 |

---

## Cloud Scheduler 設定

### 最小構成（推奨）

毎週月曜 02:00 JST に実行し、`batch` を省略して日付から自動判定させる。

```yaml
# cloud-scheduler.yaml
- name: vod-weekly-patch
  schedule: "0 17 * * 0"      # UTC 日曜 17:00 = JST 月曜 02:00
  timeZone: "UTC"
  httpTarget:
    uri: https://<CLOUD_RUN_URL>/weekly-patch
    httpMethod: POST
    body: ""                   # batch は実行日から自動判定
    headers:
      Content-Type: application/json
    oidcToken:
      serviceAccountEmail: <SA_EMAIL>
  retryConfig:
    retryCount: 1              # 失敗時に1回リトライ
    minBackoffDuration: 300s   # 5分後にリトライ
```

### バッチを明示指定する場合

```bash
# 第1週バッチ（batch 0）を手動実行
curl -X POST https://<CLOUD_RUN_URL>/weekly-patch \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  -H "Content-Type: application/json" \
  -d '{"batch": 0, "limit": 100}'

# ドライラン（対象の確認のみ）
curl -X POST https://<CLOUD_RUN_URL>/weekly-patch \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  -H "Content-Type: application/json" \
  -d '{"batch": 0, "dry_run": true}'
```

---

## API パラメータ

`POST /weekly-patch` のリクエストボディ（JSON）:

| パラメータ | 型 | デフォルト | 説明 |
|-----------|------|------------|------|
| `batch` | int (0〜3) | 自動判定 | バッジ番号。省略時は実行日の週から自動判定 |
| `limit` | int | 100 | 最大処理件数（`force=true` のときは無視） |
| `force` | bool | false | 直近7日スキップを解除して強制処理 |
| `dry_run` | bool | false | 対象の確認のみ（WP 更新なし） |
| `slug` | string | — | 特定 slug のみ処理（バッジフィルタ不適用） |

### レスポンス例

```json
{
  "batch": 0,
  "cycle": "2026-05",
  "badge_distribution": {
    "batch0": 120,
    "batch1": 115,
    "batch2": 118,
    "batch3": 122
  },
  "posts": {
    "total": 100,
    "processed": 94,
    "skipped": 3,
    "errors": 3
  },
  "services": {
    "url_checked": 280,
    "jw_searched": 94,
    "urls_registered": 18,
    "status_updated": 262
  },
  "budget": {
    "wp_api_calls": 820,
    "jw_api_calls": 94,
    "scraping_calls": 215,
    "playwright_calls": 65,
    "estimated_minutes": 38.5
  }
}
```

#### レスポンスフィールドの見方

| フィールド | 説明 |
|-----------|------|
| `batch` | 実行されたバッジ番号（0〜3） |
| `cycle` | 実行月（YYYY-MM） |
| `badge_distribution` | 全バッジの投稿数分布（偏り確認用） |
| `posts.processed` | エラーなく完了した投稿数 |
| `posts.skipped` | 言語・カテゴリ制約などでスキップされた件数 |
| `posts.errors` | エラーが発生した投稿数 |
| `services.url_checked` | チェッカーで確認したサービス延べ件数 |
| `services.jw_searched` | JustWatch 検索を実行した投稿数 |
| `services.urls_registered` | JustWatch で新規登録した URL 数 |
| `services.status_updated` | ステータスを書き込んだサービス延べ件数 |
| `budget.estimated_minutes` | 推定処理時間（分）|

---

## スキップ条件

| 条件 | 動作 |
|------|------|
| `scraping_disabled = true` | スキップ（管理者停止） |
| 直近 7 日以内に `updated_at` 更新済み | スキップ（`force=true` で解除） |
| 言語ミスマッチ（lang とサービス対応言語が不一致） | スキップ |
| カテゴリ制約（Crunchyroll はアニメ `term_id=3` のみ） | スキップ |
| `scraping_cooldown_until` 期間中 | **無視**（週次パッチはクールダウンを上書き） |

---

## CLI での操作

```bash
# 今週のバッチを自動判定して実行
python weekly_patch.py

# バッチ 0（第1週）を強制実行（クールダウン無視）
python weekly_patch.py --batch 0 --force

# 最大10件でドライラン（対象確認）
python weekly_patch.py --limit 10 --dry-run

# 特定 slug をデバッグ（バッジ無関係）
python weekly_patch.py --slug john-wick

# バッチ 2 を最大50件で実行
python weekly_patch.py --batch 2 --limit 50
```

---

## 週次カレンダー（例：2026年5月）

| 日付 | 曜日 | バッジ | 処理対象 |
|------|------|-------|---------|
| 5/4（月） | 第1週 | batch 0 | post_id % 4 == 0 の 100件 |
| 5/11（月） | 第2週 | batch 1 | post_id % 4 == 1 の 100件 |
| 5/18（月） | 第3週 | batch 2 | post_id % 4 == 2 の 100件 |
| 5/25（月） | 第4週 | batch 3 | post_id % 4 == 3 の 100件 |

> 5月末時点で全投稿が最新状態になる。6月第1月曜から再び batch 0 が処理される。

---

## 投稿数が 400件を超えた場合

バッジごとに 100件以上の投稿が存在するケース:

- 優先度ソート（Phase1 → Phase2）が適用され、未スクレイピングや最も古い投稿を先に処理する
- 処理しきれなかった投稿は次週以降の同バッジ週で処理される
- 全投稿を1サイクルでカバーする周期は **投稿数 / 400 ヶ月** に伸びる

```
例: 800件の投稿がある場合
  各バッジに ~200件 → 1週に100件ずつ処理 → 1バッジ完了に2週かかる
  全体の周期: 8週（約2ヶ月）
```

必要に応じて `limit` を増やすか、Cloud Scheduler の実行頻度を上げることで調整できる。

---

## 関連ファイル

| ファイル | 役割 |
|---------|------|
| `weekly_patch.py` | 週次パッチ統合ランナー |
| `main.py` | `POST /weekly-patch` エンドポイント |
| `utils/wordpress.py` | `get_all_posts_for_patch()` — 全投稿取得 |
| `utils/justwatch.py` | JustWatch GraphQL クライアント |
| `utils/rate_limit.py` | リクエスト間隔制御 |
| `checkers/` | 各 VOD サービスのチェッカー |
| `docs/weekly-patch-schedule.md` | 技術仕様詳細 |
