# 週次パッチ 運用プラン

> **設計原則**: 投稿数が増えても **全件が定期的にカバー** されることを保証する。
> 1週間に処理する件数は固定ではなく、**バッジ内の全件** を毎週処理する。
> 投稿数の増加に運用変更なしで自動追従する。
>
> **2026-07 更新**: 投稿数増加により1バッチあたりの処理時間が GitHub Actions の
> `timeout-minutes: 120` を超過し、バッチ後半の投稿が処理されずキャンセルされる
> 事象が確認されたため、本ドキュメント下部「選択肢A」を採用し
> `BATCH_COUNT = 4 → 8`（1ヶ月で1周 → 2ヶ月で1周）に変更した。
> 以下の本文は変更前提の記述を含むため、現在の正式仕様は
> [weekly-patch-schedule.md](./weekly-patch-schedule.md)、
> 原因・影響・対応の詳細は [weekly-patch-timeout-incident.md](./weekly-patch-timeout-incident.md) を参照。

---

## 全体像

```
     第1月曜          第2月曜          第3月曜          第4月曜
      Week 1           Week 2           Week 3           Week 4
   ┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐
   │ batch 0  │     │ batch 1  │     │ batch 2  │     │ batch 3  │
   │ 全件処理 │ ──▶ │ 全件処理 │ ──▶ │ 全件処理 │ ──▶ │ 全件処理 │
   │id % 4 = 0│     │id % 4 = 1│     │id % 4 = 2│     │id % 4 = 3│
   └──────────┘     └──────────┘     └──────────┘     └──────────┘
        ↓                ↓                ↓                ↓
   1ヶ月で総投稿数 = N 件 すべてをカバー（N が増えても 4 週で完結）
```

### 規模感（投稿数別の自動スケーリング）

`limit` を指定しなければ、バッジ内の **全件** を毎週処理する。投稿数が増えれば1回の処理時間が伸びる。

| 総投稿数 | 1週あたり処理件数（自動） | 1回の処理時間（推定） |
|---------|------------------------|---------------------|
| 400件 | ~100件 | ~40分 |
| **500件（現在）** | **~125件** | **~50分** |
| 800件 | ~200件 | ~75分 |
| 1,200件 | ~300件 | ~110分 |
| 2,000件 | ~500件 | ~180分 |

> 投稿数が増えても運用設定の変更は不要。Cloud Run のタイムアウト超過に注意。

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
- 投稿の追加・削除があっても他バッジへの影響なし
- `batch` を省略すれば実行日から自動判定される

> バッジ分布は API レスポンスの `badge_distribution` で確認できる。
> 偏りが ±15% を超える場合は、`BATCH_COUNT` を増やすか別の割り当て方を検討する。

---

## 1週間の処理フロー

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
  └─ バッジ内全件を処理（limit 未指定）
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

## 週次予算（500件運用時）

### 1回（~125件）の処理コスト

| 処理 | 件数（目安） | 単位コスト | 小計 |
|------|------------|-----------|------|
| WordPress GET/PATCH | ~875 回 | 0.5秒/回 | ~7.3分 |
| JustWatch GraphQL | ~125 回 | 3秒/回 | ~6.3分 |
| URL スクレイピング（requests） | ~260 回 | 3秒/回 | ~13分 |
| URL スクレイピング（Playwright）| ~80 回 | 15秒/回 | ~20分 |
| **合計** | | | **約 46〜52 分** |

> `budget.estimated_minutes` として API レスポンスに含まれる。
> Cloud Run のタイムアウトは **60分** に設定すること（デフォルト 5 分では不足）。

### 月次合計（4週分）

| 指標 | 推定値 |
|------|--------|
| 処理投稿数 | 500件/月（全件カバー） |
| JustWatch API | 500〜1,000 calls/月 |
| URL スクレイピング | 1,500〜3,000 calls/月 |
| WordPress API | 3,500〜5,000 calls/月 |
| Cloud Run 処理時間 | 約 3〜3.5 時間/月 |

---

## Cloud Scheduler 設定

### 最小構成（推奨）

毎週月曜 02:00 JST に実行。**`limit` は指定不要**（バッジ内全件が自動処理される）。

```yaml
# cloud-scheduler.yaml
- name: vod-weekly-patch
  schedule: "0 17 * * 0"      # UTC 日曜 17:00 = JST 月曜 02:00
  timeZone: "UTC"
  httpTarget:
    uri: https://<CLOUD_RUN_URL>/weekly-patch
    httpMethod: POST
    body: "{}"                 # batch / limit はサーバー側で自動判定
    headers:
      Content-Type: application/json
    oidcToken:
      serviceAccountEmail: <SA_EMAIL>
  retryConfig:
    retryCount: 1              # 失敗時に1回リトライ
    minBackoffDuration: 600s   # 10分後にリトライ
```

> Cloud Run の `--timeout=3600s`（60分）を併せて設定すること。
> 投稿数が 1,000件を超えるなら `--timeout=7200s`（120分）まで延長を検討。

### バッチを明示指定する場合

```bash
# 第1週バッチ（batch 0）を全件で手動実行
curl -X POST https://<CLOUD_RUN_URL>/weekly-patch \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  -H "Content-Type: application/json" \
  -d '{"batch": 0}'

# 上限を指定したい場合（デバッグ用）
curl -X POST https://<CLOUD_RUN_URL>/weekly-patch \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  -H "Content-Type: application/json" \
  -d '{"batch": 0, "limit": 50}'

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
| `limit` | int | **指定なしで全件** | 上限を指定する場合のみ使用（デバッグ向け） |
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
| `posts.total` | バッジ内の対象件数（全件処理時 = バッジサイズ） |
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
# 今週のバッチ内全件を処理（推奨）
python weekly_patch.py

# バッチ 0（第1週）を強制実行（クールダウン無視）
python weekly_patch.py --batch 0 --force

# 上限を50件に制限（デバッグ）
python weekly_patch.py --limit 50

# ドライラン（対象確認のみ）
python weekly_patch.py --dry-run --limit 10

# 特定 slug をデバッグ（バッジ無関係）
python weekly_patch.py --slug john-wick
```

---

## 週次カレンダー（例：2026年5月）

| 日付 | 曜日 | バッジ | 処理対象 |
|------|------|-------|---------|
| 5/4（月） | 第1週 | batch 0 | post_id % 4 == 0 の全件 |
| 5/11（月） | 第2週 | batch 1 | post_id % 4 == 1 の全件 |
| 5/18（月） | 第3週 | batch 2 | post_id % 4 == 2 の全件 |
| 5/25（月） | 第4週 | batch 3 | post_id % 4 == 3 の全件 |

> 5月末時点で全投稿が最新状態になる。6月第1月曜から再び batch 0 が処理される。

---

## 投稿数増加への自動追従

### 標準対応（投稿数 〜2,000件）

`limit` 未指定で運用すれば、何もしなくても **常に 1ヶ月で全件カバー** される。

- 500件 → 1週 ~125件処理
- 1,000件 → 1週 ~250件処理
- 2,000件 → 1週 ~500件処理

Cloud Run のタイムアウトだけ、投稿数の倍増ごとに見直す。

| 総投稿数 | 推奨 Cloud Run timeout |
|---------|----------------------|
| 〜 800件 | 60分 |
| 801〜 1,500件 | 90分 |
| 1,501〜 3,000件 | 120分（最大） |

### 大規模対応（投稿数 2,000件超）

1回の処理時間が Cloud Run の最大タイムアウト（60分 第1世代 / 60分 → 緩和申請で延長可）に収まらなくなる場合の選択肢:

#### 選択肢A: BATCH_COUNT を増やす（4 → 8）— ✅ 2026-07 採用済み
```python
# weekly_patch.py
BATCH_COUNT = 8   # 8週で1周（=2ヶ月）に変更
```
1回の処理量は半減するが、1周期が2ヶ月に伸びる。
実際に GitHub Actions のタイムアウト超過が発生したため採用した。
詳細は [weekly-patch-schedule.md](./weekly-patch-schedule.md) 参照。

#### 選択肢B: 週2回実行に変更
```yaml
# 月曜と木曜の両方で実行
schedule: "0 17 * * 0,3"   # UTC 日曜・水曜 17:00
```
処理量は維持したまま月8回実行になる。1ヶ月で2周することになるが、`_PATCH_SKIP_WITHIN_DAYS=7` により直近7日以内の更新済みは自動スキップされるため無駄な処理は発生しない。

#### 選択肢C: 別バッジ方式（細分化）
```python
BATCH_COUNT = 30   # 日次運用に近づける
```
スケジューラを毎日実行に変更し、毎日 全投稿数 / 30 を処理する。Cloud Run のタイムアウト問題を回避できる。

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
