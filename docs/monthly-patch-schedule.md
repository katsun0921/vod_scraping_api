# 月次パッチスケジューリング仕様

## 概要

月次パッチは、VOD 投稿全件を月1サイクルで体系的に確認・更新する仕組み。
通常の日次スクレイピング（`checker.py`）とは独立して動作し、以下を1パスで統合処理する。

- **URL あり投稿** → 既存チェッカーでステータス確認
- **URL なし投稿** → JustWatch API で URL を検索 → 登録 or unavailable 設定

---

## スケジュール構造

```
1ヶ月 = 4バッチ × 100件/バッチ = 最大 400件/月
```

| 週 | バッチ | 実行日 | 対象投稿 |
|----|--------|--------|----------|
| 第1週 | batch 0 | 毎月1〜7日の月曜 | post_id % 4 == 0 |
| 第2週 | batch 1 | 毎月8〜14日の月曜 | post_id % 4 == 1 |
| 第3週 | batch 2 | 毎月15〜21日の月曜 | post_id % 4 == 2 |
| 第4週 | batch 3 | 毎月22〜28日の月曜 | post_id % 4 == 3 |

---

## スケジューリングバッジ

各投稿には `post_id % 4` により **バッジ番号（0-3）** が静的に割り当てられる。

```
post_id = 101 → 101 % 4 = 1 → batch 1（毎月第2週処理）
post_id = 204 → 204 % 4 = 0 → batch 0（毎月第1週処理）
post_id = 307 → 307 % 4 = 3 → batch 3（毎月第4週処理）
```

**バッジの特徴:**
- ACF フィールド追加不要（WordPress 側の変更なし）
- 同一投稿は毎月必ず同じ週に処理される（安定したスケジューリング）
- 投稿の追加・削除があっても他バッチへの影響なし

### バッジ分布レポート

API レスポンスの `badge_distribution` フィールドに各バッチの投稿数が返される。

```json
"badge_distribution": {
  "batch0": 120,
  "batch1": 115,
  "batch2": 118,
  "batch3": 122
}
```

---

## 処理フロー

```
投稿ごとの処理:

┌─ Phase 1: JustWatch 検索（URL なしサービスがある場合）────────────┐
│ 1. post のタイトル/slug で JustWatch GraphQL API を呼ぶ         │
│ 2. 見つかった URL → scraping_url として登録（次フェーズで確認）    │
│ 3. 見つからなかった URL → status=unavailable を書き込み          │
│ 4. 1 回の PATCH で全サービスをまとめて更新                       │
└──────────────────────────────────────────────────────────────┘
         ↓
┌─ Phase 2: URL チェック（scraping_url があるサービス）─────────────┐
│ 1. 各サービスのチェッカーを実行                                  │
│    - requests ベース: Amazon / Netflix / Hulu / Disney+ 等      │
│    - Playwright ベース: U-NEXT / DMM TV / Crunchyroll           │
│ 2. 結果を update_post() で WP に書き込み                        │
│ 3. vod タクソノミーを更新                                       │
│ 4. 新規 streaming 検知 → Slack 通知                            │
└──────────────────────────────────────────────────────────────┘
         ↓
┌─ Phase 3: クールダウン更新───────────────────────────────────────┐
│ URL チェックを1件でも実施した場合のみ:                            │
│ - streaming ありなら 30日後にリセット                            │
│ - 全未配信なら指数バックオフ + 年齢補正                          │
└──────────────────────────────────────────────────────────────┘
```

### 日次スクレイピングとの違い

| 項目 | 日次 checker.py | 月次 monthly_patch.py |
|------|----------------|-----------------------|
| クールダウン | 厳守 | **無視**（直近7日以内のみスキップ） |
| URL なし投稿 | スキップ | **JustWatch で探索** |
| 対象件数 | 30件/日 (DAILY_QUOTA) | **100件/週** |
| 優先度 | 日次クォータ内で優先 | バッジ × updated_at 古い順 |

---

## 月次予算

### 1バッチ（100件）の予算

| 操作 | 件数 | 単位コスト | 合計 |
|------|------|-----------|------|
| WP GET/PATCH | ~700回 | 0.5秒/回 | ~5.8分 |
| JustWatch GraphQL | ~100回 | 3秒/回 | ~5分 |
| URL スクレイピング（requests） | ~210回 | 3秒/回 | ~10.5分 |
| URL スクレイピング（Playwright） | ~60回 | 15秒/回 | ~15分 |
| **合計** | | | **約36〜40分** |

### 月次合計（4バッチ）

| 指標 | 推定値 |
|------|--------|
| 処理投稿数 | 400件 |
| JustWatch API 呼び出し | 400〜800回 |
| URL スクレイピング | 1,200〜2,400回 |
| WordPress API 呼び出し | 2,800〜4,000回 |
| Cloud Run 処理時間 | 約2〜3時間/月 |

### レスポンスの `budget` フィールド

```json
"budget": {
  "wp_api_calls": 820,        // WordPress PATCH/GET 回数
  "jw_api_calls": 94,         // JustWatch GraphQL 呼び出し回数
  "scraping_calls": 215,      // requests ベースのスクレイピング回数
  "playwright_calls": 65,     // Playwright ベースのスクレイピング回数
  "estimated_minutes": 38.5   // 推定処理時間（分）
}
```

---

## API エンドポイント

### `POST /monthly-patch`

月次パッチを実行する。

**リクエストボディ（JSON）:**

| パラメータ | 型 | デフォルト | 説明 |
|-----------|------|------------|------|
| `batch` | int (0-3) | 自動判定 | バッチ番号。省略時は今日の日付から第1〜4週を判定 |
| `limit` | int | 100 | 最大処理件数 |
| `dry_run` | bool | false | 対象確認のみ（更新なし） |
| `slug` | string | - | 特定 slug のみ処理 |

**レスポンス（例）:**

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

---

## Cloud Scheduler 設定

### 推奨: 毎週月曜に自動実行

```yaml
# cloud-scheduler.yaml（参考）
- name: monthly-patch-weekly
  schedule: "0 2 * * 1"       # 毎週月曜 02:00 JST (UTC+9 → UTC: 日曜 17:00)
  timeZone: "Asia/Tokyo"
  httpTarget:
    uri: https://<CLOUD_RUN_URL>/monthly-patch
    httpMethod: POST
    body: ""                  # batch は日付から自動判定
    headers:
      Content-Type: application/json
    oidcToken:
      serviceAccountEmail: <SA_EMAIL>
```

`batch` を省略すると実行日の月内週番号（1-4週 → 0-3）から自動判定される。

### オプション: バッチを明示指定

```bash
# 第1週バッチを手動実行
curl -X POST https://<CLOUD_RUN_URL>/monthly-patch \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  -H "Content-Type: application/json" \
  -d '{"batch": 0, "limit": 100}'

# ドライラン（対象確認のみ）
curl -X POST https://<CLOUD_RUN_URL>/monthly-patch \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  -H "Content-Type: application/json" \
  -d '{"batch": 0, "dry_run": true}'
```

---

## CLI 実行

```bash
# 今週のバッチを自動判定して実行
python monthly_patch.py

# バッチ0（第1週）を強制実行
python monthly_patch.py --batch 0

# 対象確認のみ（更新なし）
python monthly_patch.py --dry-run

# 最大10件でテスト
python monthly_patch.py --limit 10 --dry-run

# 特定 slug をデバッグ
python monthly_patch.py --slug john-wick
```

---

## スキップ条件

月次パッチでのスキップ条件は通常の日次チェックより緩い。

| 条件 | 日次 checker.py | 月次 monthly_patch.py |
|------|----------------|-----------------------|
| `scraping_disabled=true` | スキップ ✓ | スキップ ✓ |
| `scraping_cooldown_until` 期間中 | スキップ ✓ | **無視** ✗ |
| `scraping_url` が空 | スキップ ✓ | **JustWatch で探索** ✗ |
| 30日以内に updated | スキップ ✓ | **7日以内のみスキップ** ✗ |
| 言語ミスマッチ | スキップ ✓ | スキップ ✓ |
| カテゴリ制約 | スキップ ✓ | スキップ ✓ |

---

## ファイル構成

```
monthly_patch.py          # 月次パッチ統合ランナー（新規）
main.py                   # POST /monthly-patch エンドポイント追加
utils/wordpress.py        # get_all_posts_for_patch() 追加
docs/monthly-patch-schedule.md  # 本ドキュメント
```
