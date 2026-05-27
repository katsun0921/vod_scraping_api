# 週次パッチ 運用プラン

> **前提**: 現在の publish 投稿数は **約 500件**。
> 1ヶ月で全投稿を1周するには **1週あたり 125件** を処理する必要がある。

---

## 全体像

```
     第1月曜          第2月曜          第3月曜          第4月曜
      Week 1           Week 2           Week 3           Week 4
   ┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐
   │ batch 0  │     │ batch 1  │     │ batch 2  │     │ batch 3  │
   │  125件   │ ──▶ │  125件   │ ──▶ │  125件   │ ──▶ │  125件   │
   │id % 4 = 0│     │id % 4 = 1│     │id % 4 = 2│     │id % 4 = 3│
   └──────────┘     └──────────┘     └──────────┘     └──────────┘
        ↓                ↓                ↓                ↓
   1ヶ月で 500件 をカバー（翌月また Week 1 から繰り返す）
```

### 投稿数の見積もり

| 指標 | 値 |
|------|---|
| 総投稿数 | 約 500件 |
| 1バッジあたり | ~125件（500 ÷ 4） |
| 1週あたり処理件数 | **125件**（`limit=125`） |
| 1ヶ月でカバーする件数 | 500件（全件） |
| 1周期 | 4週間（1ヶ月） |

---

## スケジューリングバッジ

各投稿は `post_id % 4` で **バッチ番号（0〜3）** に静的割り当てされる。

| バッジ | 割り当て条件 | 想定件数 | 処理タイミング |
|--------|------------|---------|---------------|
| badge 0 | post_id % 4 == 0 | ~125件 | 毎月 1〜7 日の月曜 |
| badge 1 | post_id % 4 == 1 | ~125件 | 毎月 8〜14 日の月曜 |
| badge 2 | post_id % 4 == 2 | ~125件 | 毎月 15〜21 日の月曜 |
| badge 3 | post_id % 4 == 3 | ~125件 | 毎月 22〜31 日の月曜 |

- ACF フィールドの追加は **不要**（WordPress 側の変更なし）
- 投稿を追加・削除しても他バッジへの影響なし
- `batch` パラメータを省略すると実行日から自動判定される

> バッジ分布は API レスポンスの `badge_distribution` で確認できる。
> 偏りが ±15% を超える場合は `limit` を調整するか、Cloud Scheduler の頻度を見直す。

---

## 1週間の処理フロー（125件/回）

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
  └─ 上位 125件を処理
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

### 1回（125件）の予算試算

| 処理 | 件数（目安） | 単位コスト | 小計 |
|------|------------|-----------|------|
| WordPress GET/PATCH | ~875 回 | 0.5秒/回 | ~7.3分 |
| JustWatch GraphQL | ~125 回 | 3秒/回 | ~6.3分 |
| URL スクレイピング（requests） | ~260 回 | 3秒/回 | ~13分 |
| URL スクレイピング（Playwright）| ~80 回 | 15秒/回 | ~20分 |
| **合計** | | | **約 46〜52 分** |

> `budget.estimated_minutes` として API レスポンスに含まれる。
> Cloud Run のタイムアウトは **60分** に設定すること（デフォルトの 5 分では不足）。

### 月次合計（4週 × 125件 = 500件）

| 指標 | 推定値 |
|------|--------|
| 処理投稿数 | 約 500件/月（全件カバー） |
| JustWatch API | 500〜1,000 calls/月 |
| URL スクレイピング | 1,500〜3,000 calls/月 |
| WordPress API | 3,500〜5,000 calls/月 |
| Cloud Run 処理時間 | 約 3〜3.5 時間/月 |

---

## Cloud Scheduler 設定

### 最小構成（推奨）

毎週月曜 02:00 JST に実行し、`limit=125` で500件を 4 週で消化する。

```yaml
# cloud-scheduler.yaml
- name: vod-weekly-patch
  schedule: "0 17 * * 0"      # UTC 日曜 17:00 = JST 月曜 02:00
  timeZone: "UTC"
  httpTarget:
    uri: https://<CLOUD_RUN_URL>/weekly-patch
    httpMethod: POST
    body: '{"limit": 125}'    # 500件 ÷ 4週 = 125件/週
    headers:
      Content-Type: application/json
    oidcToken:
      serviceAccountEmail: <SA_EMAIL>
  retryConfig:
    retryCount: 1              # 失敗時に1回リトライ
    minBackoffDuration: 600s   # 10分後にリトライ
```

> Cloud Run の `--timeout=3600s`（60分）を併せて設定すること。

### バッチを明示指定する場合

```bash
# 第1週バッチ（batch 0）を 125件 で手動実行
curl -X POST https://<CLOUD_RUN_URL>/weekly-patch \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  -H "Content-Type: application/json" \
  -d '{"batch": 0, "limit": 125}'

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
| `limit` | int | 100 | 最大処理件数。**500件運用では 125 を指定**（`force=true` のとき無視） |
| `force` | bool | false | 直近7日スキップを解除して強制処理 |
| `dry_run` | bool | false | 対象の確認のみ（WP 更新なし） |
| `slug` | string | — | 特定 slug のみ処理（バッジフィルタ不適用） |

### レスポンス例

```json
{
  "batch": 0,
  "cycle": "2026-05",
  "badge_distribution": {
    "batch0": 125,
    "batch1": 124,
    "batch2": 126,
    "batch3": 125
  },
  "posts": {
    "total": 125,
    "processed": 118,
    "skipped": 4,
    "errors": 3
  },
  "services": {
    "url_checked": 340,
    "jw_searched": 115,
    "urls_registered": 22,
    "status_updated": 320
  },
  "budget": {
    "wp_api_calls": 980,
    "jw_api_calls": 115,
    "scraping_calls": 265,
    "playwright_calls": 80,
    "estimated_minutes": 48.2
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
# 今週のバッチを 125件 で実行（推奨）
python weekly_patch.py --limit 125

# バッチ 0（第1週）を強制実行（クールダウン無視）
python weekly_patch.py --batch 0 --limit 125 --force

# 最大10件でドライラン（対象確認）
python weekly_patch.py --limit 10 --dry-run

# 特定 slug をデバッグ（バッジ無関係）
python weekly_patch.py --slug john-wick
```

---

## 週次カレンダー（例：2026年5月）

| 日付 | 曜日 | バッジ | 処理対象 | 件数 |
|------|------|-------|---------|------|
| 5/4（月） | 第1週 | batch 0 | post_id % 4 == 0 | 125件 |
| 5/11（月） | 第2週 | batch 1 | post_id % 4 == 1 | 125件 |
| 5/18（月） | 第3週 | batch 2 | post_id % 4 == 2 | 125件 |
| 5/25（月） | 第4週 | batch 3 | post_id % 4 == 3 | 125件 |

> 5月末時点で全 500件 が最新状態になる。6月第1月曜から再び batch 0 が処理される。

---

## 投稿数のスケーリング

将来的に投稿数が増えた場合の運用指針:

| 総投稿数 | 推奨 `limit` | 1回の処理時間 | 備考 |
|---------|------------|--------------|------|
| 〜 400件 | 100 | ~40分 | 標準構成 |
| 401〜500件 | **125** | ~50分 | **現在の状況** |
| 501〜800件 | 200 | ~75分 | Cloud Run タイムアウトを 90分に延長 |
| 801〜1,200件 | 300 | ~110分 | 週2回実行（月・木）に分割を検討 |
| 1,200件超〜 | — | — | 週2回 × `limit=200` 構成へ移行 |

### スケジューラ頻度を増やすパターン

`limit` だけで賄えない規模になったら、Cloud Scheduler を週2回に増やす:

```yaml
# 月曜と木曜の両方で実行（4バッチ × 週2 = 月8回）
schedule: "0 17 * * 0,3"   # UTC 日曜・水曜 17:00
```

このとき `batch` は実行日から自動判定されるため、ワークロードが均等に分散する。

### バッジ分割の細分化

投稿数が 2,000件 を超えるなら、バッジ方式を `post_id % 8`（8週周期）に変更する。
`weekly_patch.py` の `BATCH_COUNT` を 8 に変更し、Cloud Scheduler の頻度を維持すれば、
**8週で1周** の運用に移行できる。

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
