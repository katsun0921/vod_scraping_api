# スクレイピング頻度制御

未配信作品のスクレイピング頻度を作品ごとに動的に調整し、ボット検出リスクと Cloud Run のコストを最小化する。

---

## 課題

- 配信開始は予測不能（数か月後 / 数年後 / 永遠に来ない）
- 全件を毎日スクレイピング → 99% は変化なし、料金とリスクの無駄
- 完全停止 → 配信開始の取りこぼし

---

## 設計指針

**「配信される可能性」は時間と共に減衰する** という前提で、作品ごとにチェック間隔を動的に決定する。

| 軸 | 判定 |
|---|---|
| 作品の年齢 | `release_year` からの経過年数 |
| 連続未配信回数 | `unavailable_check_count`（指数バックオフ） |
| 管理者の判断 | `scraping_disabled`（マイナー作品の手動停止） |

---

## ACF フィールド

```
post.acf
├── release_year                  : number     # 既存（作品公開年）
├── scraping_disabled             : true_false # 新規：管理者の探索停止フラグ
├── scraping_cooldown_until       : date       # 新規：次回チェック予定日
└── unavailable_check_count       : number     # 新規：連続未配信カウント
```

### フィールド詳細

| フィールド | 型 | 説明 | 編集 |
|---|---|---|---|
| `scraping_disabled` | true_false | マイナー作品で探索不要の場合 ON | 管理者 |
| `scraping_cooldown_until` | date | 次回スクレイピング予定日（システムが自動更新、手動編集も可） | システム + 管理者 |
| `unavailable_check_count` | number | 連続未配信回数（システム管理用） | システム |

---

## スキップ条件（優先順）

```python
def should_skip(post, service, today):
    # 1. 管理者が探索停止フラグを立てている
    if post.acf.scraping_disabled:
        return True

    # 2. cooldown 期間中
    cooldown = post.acf.scraping_cooldown_until
    if cooldown and cooldown >= today:
        return True

    # 3. scraping_url が空（探索対象外）
    if not post.acf[f"{service}_scraping_url"]:
        return True

    # 4. 直近30日以内に更新済み（既存ロジック）
    updated_at = post.acf[f"{service}_updated_at"]
    if updated_at and within_30_days(updated_at, today):
        return True

    return False
```

---

## Cooldown 更新ロジック

全サービスのチェック完了後に呼び出す。

```python
def update_cooldown(post, today):
    # 配信中サービスが1つでもあれば 30日サイクル + カウントリセット
    has_streaming = any(
        post.acf[f"{s}_status"] == "streaming"
        for s in SERVICES
    )
    if has_streaming:
        post.acf.scraping_cooldown_until = today + 30 days
        post.acf.unavailable_check_count = 0
        return

    # 全サービス未配信 → 指数バックオフ + 年齢補正
    count = post.acf.unavailable_check_count + 1

    # 指数バックオフの基準日数
    base_days = [30, 60, 120, 240, 360][min(count - 1, 4)]

    # 年齢補正
    years_old = today.year - (post.acf.release_year or today.year)
    if years_old >= 5:
        next_days = 360  # 5年以上は12か月固定
    elif years_old >= 3:
        next_days = max(base_days, 180)
    else:
        next_days = base_days

    post.acf.scraping_cooldown_until = today + next_days days
    post.acf.unavailable_check_count = count
```

---

## チェック頻度の早見表

| release_year からの経過 | 配信中 | 連続未配信1回 | 2回 | 3回 | 4回+ |
|---|---|---|---|---|---|
| 0-3年（新作） | 30日 | 30日 | 60日 | 120日 | 240日 |
| 3-5年 | 30日 | 180日 | 180日 | 180日 | 240日 |
| 5年以上 | 30日 | **360日** | 360日 | 360日 | 360日 |
| `scraping_disabled = true` | 永久停止 | - | - | - | - |

---

## コスト試算

仮定：**全1万件**、うち**配信中3000件 / 未配信7000件**

### 現行案（全件30日サイクル）
```
1万件 × 8サービス / 30日 = 約2,700件/日
```

### 提案案（指数バックオフ + 年齢補正）
```
配信中:       3,000件 × 8 / 30日 = 800件/日
未配信新作:     500件 × 8 / 30日 = 130件/日
未配信中年:   2,000件 × 8 / 90日 = 180件/日
未配信古典:   4,500件 × 8 / 360日 = 100件/日
─────────────────────────────────
合計:                       約1,200件/日
```

**約 55% のリクエスト削減** + ボット検出リスク低下。

---

## 取りこぼしリスク

| 作品カテゴリ | 配信告知 | 提案案での検知遅延 |
|---|---|---|
| 配信中作品 | 不要 | - |
| 未配信新作（3年以内） | あり得る | 平均15日 |
| 未配信中年（3-5年） | 稀 | 平均3か月 |
| 未配信旧作（5年以上） | 非常に稀 | 平均6か月 |

新作の見落としリスクは低い（30-60日サイクル = 配信開始から告知期間に十分間に合う）。

---

## エッジケース

### `release_year` が空のとき
→ **新作扱い**（最短サイクル）。後で埋めれば自動最適化される。

### 新規投稿時の `scraping_cooldown_until`
→ 空 → `should_skip` でスキップされない → 即チェック対象。
→ JustWatch（記事作成時）で URL が見つからなかった場合、明示的に1か月先にセットしておくとさらに効率化できる（任意）。

### 既存配信が終了した場合
→ `streaming → unavailable` 遷移時、`unavailable_check_count = 1` から再スタート。

### 管理者が手動で `scraping_disabled` を解除
→ `scraping_cooldown_until` をクリアすれば次回バッチで即チェック対象になる。

---

## JustWatch 月次バッチ仕様（`justwatch_batch.py`）

`scraping_url` が未設定のサービスに対して JustWatch 非公式 API で URL を検索し、
見つかれば `scraping_url` を登録、見つからなければ `status=unavailable` を書き込む。

### 実行タイミング

- **自動**: 毎月1日 02:00 JST（GitHub Actions cron）
- **手動**: GitHub Actions `workflow_dispatch` / Cloud Run `POST /justwatch`

### 処理対象の選定条件

以下をすべて満たす投稿のみ処理する（`get_posts_missing_url` でフィルタリング）。

| 条件 | 内容 |
|---|---|
| `scraping_disabled=false` | 管理者停止フラグが OFF |
| `scraping_cooldown_until` が今日より前 | クールダウン期間外 |
| 対象サービスの `scraping_url` が空 | URL 未登録のサービスが1件以上ある |
| 対象サービスの `updated_at` が1か月以上前または未設定 | 直近1か月以内に確認済みのサービスは再検索しない |

### 処理結果

| 結果 | 書き込み内容 |
|---|---|
| URL 発見 | `scraping_url` に URL を登録（`registered` カウント） |
| URL 未発見 | `status=unavailable` + `updated_at=現在日時`（`unavailable` カウント） |

### Slack 通知

- **開始時**: 処理対象件数・limit を通知
- **投稿ごと**: title / slug・登録 URL または unavailable サービス一覧を通知
- **完了時**: registered / unavailable / skipped / errors のサマリーを通知
- `dry_run=True` のときは通知しない

### 1投稿あたりのリクエスト数

| 処理 | リクエスト数 |
|---|---|
| JustWatch GraphQL 検索 | 1〜2回（title → slug の順で再試行） |
| WordPress GET（既存 ACF 取得） | 1回 |
| WordPress PATCH（全サービス一括更新） | 1回 |

→ 合計 **3〜4リクエスト / 投稿**、投稿間に 3秒のウェイトを挿入。
