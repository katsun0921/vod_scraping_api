# 週次パッチスケジューリング仕様

## 概要

週次パッチは、VOD 投稿全件を体系的に確認・更新する仕組み。
通常の日次スクレイピング（`checker.py`）とは独立して動作し、以下を1パスで統合処理する。

- **URL あり投稿** → 既存チェッカーでステータス確認
- **URL なし投稿** → JustWatch API で URL を検索 → 登録 or unavailable 設定

> **2026-07 変更**: `BATCH_COUNT` を 4（1ヶ月で1周）から 8（2ヶ月で1周）に変更した。
> 投稿数の増加に伴い1回あたりの処理時間が GitHub Actions のジョブタイムアウト
> （`timeout-minutes: 120`）を超過し、バッチ内の後半の投稿が処理されずに
> キャンセルされる事象が複数回発生したため、1回あたりの処理件数を半減させた。

---

## スケジュール構造

```
BATCH_COUNT 週（= 2ヶ月）で全投稿を1周する。
バッチ番号は「基準日からの経過週数 % BATCH_COUNT」で決まるため、
月の日数差（28〜31日）に依存せず、週次実行を続ける限り必ず一巡する。
```

| 経過週数 % 8 | バッチ | 対象投稿 |
|----|--------|----------|
| 0 | batch 0 | post_id % 8 == 0 |
| 1 | batch 1 | post_id % 8 == 1 |
| 2 | batch 2 | post_id % 8 == 2 |
| 3 | batch 3 | post_id % 8 == 3 |
| 4 | batch 4 | post_id % 8 == 4 |
| 5 | batch 5 | post_id % 8 == 5 |
| 6 | batch 6 | post_id % 8 == 6 |
| 7 | batch 7 | post_id % 8 == 7 |

> 以前（BATCH_COUNT=4）は「月の日付 // 7」で月内の第1〜4週にバッチを対応させていたが、
> 月によっては同じバッチが月内に2回実行される歪みがあった。現在は
> `_BATCH_EPOCH_MONDAY`（2024-01-01、既知の月曜日）からの経過週数を使うため、
> 月境界に関係なく常に均等に1バッチずつ進む。

---

## スケジューリングバッジ

各投稿には `post_id % BATCH_COUNT`（BATCH_COUNT=8）により **バッジ番号（0-7）** が静的に割り当てられる。

```
post_id = 101 → 101 % 8 = 5 → batch 5
post_id = 204 → 204 % 8 = 4 → batch 4
post_id = 307 → 307 % 8 = 3 → batch 3
```

**バッジの特徴:**
- ACF フィールド追加不要（WordPress 側の変更なし）
- 同一投稿は BATCH_COUNT 週（=2ヶ月）ごとに必ず同じ週に処理される
- 投稿の追加・削除があっても他バッチへの影響なし

### バッジ分布レポート

API レスポンスの `badge_distribution` フィールドに各バッチの投稿数が返される。

```json
"badge_distribution": {
  "batch0": 62, "batch1": 61, "batch2": 61, "batch3": 61,
  "batch4": 61, "batch5": 61, "batch6": 61, "batch7": 61
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
│ 4. 新規 streaming 検知 → new_streaming_items に蓄積             │
│    （Slack 通知はバッチ完了後に一覧としてまとめて送信）           │
└──────────────────────────────────────────────────────────────┘
         ↓
┌─ Phase 3: クールダウン更新───────────────────────────────────────┐
│ URL チェックを1件でも実施した場合のみ:                            │
│ - streaming ありなら 30日後にリセット                            │
│ - 全未配信なら指数バックオフ + 年齢補正                          │
└──────────────────────────────────────────────────────────────┘
         ↓
┌─ バッチ完了後 ───────────────────────────────────────────────────┐
│ 蓄積した new_streaming_items を Slack に1通で通知                │
│ （詳細フォーマットは docs/weekly-patch-notifications.md 参照）    │
└──────────────────────────────────────────────────────────────┘
```

### 日次スクレイピングとの違い

| 項目 | 日次 checker.py | 週次 weekly_patch.py |
|------|----------------|-----------------------|
| クールダウン | 厳守 | **無視**（直近7日以内のみスキップ） |
| URL なし投稿 | スキップ | **JustWatch で探索** |
| 対象件数 | 30件/日 (DAILY_QUOTA) | **バッジ内全件（~61件/週、投稿数489件時点）** |
| 優先度 | 日次クォータ内で優先 | バッジ × updated_at 古い順 |

---

## 週次予算

### 1バッチ（~61件、投稿数489件時点）の予算

| 操作 | 件数 | 単位コスト | 合計 |
|------|------|-----------|------|
| WP GET/PATCH | ~430回 | 0.5秒/回 | ~3.6分 |
| JustWatch GraphQL | ~61回 | 3秒/回 | ~3分 |
| URL スクレイピング（requests） | ~130回 | 3秒/回 | ~6.5分 |
| URL スクレイピング（Playwright） | ~37回 | 15秒/回 | ~9分 |
| サービス切り替え待機（10秒） | ~160回 | 10秒/回 | ~27分 |
| **合計** | | | **約50〜60分** |

> サービス切り替え待機が全体の半分近くを占める。投稿ごとに対象サービスの
> 組み合わせが変わるため、ほぼ全チェックで切り替え扱いになりやすい。
> BATCH_COUNT=4 だった頃はこの合計が2時間を超え、GitHub Actions の
> `timeout-minutes: 120` を超過して後半の投稿がキャンセルされていた
> （実測: 2026-06-21 batch2 109件中103件着手・101件完了で打ち切り、
> 2026-07-05 batch0 134件中104件着手・102件完了で打ち切り）。

### 2ヶ月合計（8バッチ）

| 指標 | 推定値 |
|------|--------|
| 処理投稿数 | ~489件（2ヶ月で全件） |
| JustWatch API 呼び出し | ~489〜978回 |
| URL スクレイピング | ~1,336〜2,672回 |
| WordPress API 呼び出し | ~3,430〜4,900回 |
| GitHub Actions 処理時間 | 約7〜8時間/2ヶ月 |

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

### `POST /weekly-patch`

週次パッチを実行する。

**リクエストボディ（JSON）:**

| パラメータ | 型 | デフォルト | 説明 |
|-----------|------|------------|------|
| `batch` | int (0-7) | 自動判定 | バッチ番号。省略時は今日の日付から経過週数を判定 |
| `limit` | int | 指定なしで全件 | 最大処理件数 |
| `dry_run` | bool | false | 対象確認のみ（更新なし） |
| `slug` | string | - | 特定 slug のみ処理 |

**レスポンス（例）:**

```json
{
  "batch": 0,
  "cycle": "2026-07",
  "badge_distribution": {
    "batch0": 62, "batch1": 61, "batch2": 61, "batch3": 61,
    "batch4": 61, "batch5": 61, "batch6": 61, "batch7": 61
  },
  "posts": {
    "total": 62,
    "processed": 58,
    "skipped": 3,
    "errors": 1
  },
  "services": {
    "url_checked": 175,
    "jw_searched": 58,
    "urls_registered": 11,
    "status_updated": 163
  },
  "budget": {
    "wp_api_calls": 510,
    "jw_api_calls": 58,
    "scraping_calls": 133,
    "playwright_calls": 40,
    "estimated_minutes": 55.2
  }
}
```

---

## Cloud Scheduler 設定

### 推奨: 毎週月曜に自動実行

```yaml
# cloud-scheduler.yaml（参考）
- name: weekly-patch-weekly
  schedule: "0 2 * * 1"       # 毎週月曜 02:00 JST (UTC+9 → UTC: 日曜 17:00)
  timeZone: "Asia/Tokyo"
  httpTarget:
    uri: https://<CLOUD_RUN_URL>/weekly-patch
    httpMethod: POST
    body: ""                  # batch は日付から自動判定
    headers:
      Content-Type: application/json
    oidcToken:
      serviceAccountEmail: <SA_EMAIL>
```

`batch` を省略すると実行日の基準日からの経過週数 % 8（0-7）から自動判定される。

### オプション: バッチを明示指定

```bash
# batch 0 を手動実行
curl -X POST https://<CLOUD_RUN_URL>/weekly-patch \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  -H "Content-Type: application/json" \
  -d '{"batch": 0}'

# ドライラン（対象確認のみ）
curl -X POST https://<CLOUD_RUN_URL>/weekly-patch \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  -H "Content-Type: application/json" \
  -d '{"batch": 0, "dry_run": true}'
```

---

## CLI 実行

```bash
# 今週のバッチを自動判定して実行
python weekly_patch.py

# バッチ0を強制実行
python weekly_patch.py --batch 0

# 対象確認のみ（更新なし）
python weekly_patch.py --dry-run

# 最大10件でテスト
python weekly_patch.py --limit 10 --dry-run

# 特定 slug をデバッグ
python weekly_patch.py --slug john-wick
```

---

## スキップ条件

週次パッチでのスキップ条件は通常の日次チェックより緩い。

| 条件 | 日次 checker.py | 週次 weekly_patch.py |
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
weekly_patch.py                    # 週次パッチ統合ランナー（新規）
main.py                            # POST /weekly-patch エンドポイント追加
utils/wordpress.py                 # get_all_posts_for_patch() / get_category_slug_map() 追加
utils/slack.py                     # 新着配信サマリー通知（notify_weekly_new_streaming_summary）
docs/weekly-patch-schedule.md      # 本ドキュメント
docs/weekly-patch-notifications.md # Slack 通知フォーマットの詳細
```
