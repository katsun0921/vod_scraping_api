# 日次クォータ & 優先順位制御

1日あたりのスクレイピング件数を上限 30件 に抑えつつ、ビジネス価値の高い順（配信終了検知・新規配信検知）にチェック対象を選ぶ。

---

## 背景・課題

- クールダウン制御（`scraping-frequency.md`）によりスキップ率は高い
- スキップを通過した候補が 30件 を超える日は、**どれを選ぶか** が重要になる
- 初回稼働時は未スクレイピング作品が大量にあり、古い作品から `ended/unavailable` を確定させる洗い出し期間が必要

---

## 日次クォータ

| 設定 | 値 | 説明 |
|---|---|---|
| `DAILY_QUOTA` 環境変数 | デフォルト `30` | 1バッチあたりの最大処理件数（post 単位） |

`DAILY_QUOTA` は環境変数で上書き可能。Cloud Run ではシークレットマネージャー経由でも設定可。

---

## 優先順位ロジック（2フェーズ自動切替）

### フェーズ判定

「全 VOD サービスの `updated_at` が空欄の post」が残っている間は **フェーズ1**、  
0件になったら **フェーズ2** へ自動移行（フラグ・環境変数不要）。

---

### フェーズ1：洗い出し期間

| 項目 | 内容 |
|---|---|
| 対象 | 全サービスの `{service}.updated_at` がすべて空欄の post |
| ソートキー | `(release_year ASC, min_updated_at ASC)` |
| 目的 | 古い作品から `ended / unavailable` を確定させる |

```python
# 疑似コード
unscraped = [p for p in candidates if _all_updated_at_empty(p)]
sort_key = lambda p: (
    _release_year_sort(p, phase=1),   # ASC: 空欄は 9999 扱い（最後尾）
    _min_updated_at(p),               # ASC: None は最後尾
)
targets = sorted(unscraped, key=sort_key)[:DAILY_QUOTA]
```

**release_year 空欄の扱い（フェーズ1）**
- `9999` 扱い → ASC ソートで最後尾
- 「古い作品から拾う」意図に忠実。年不明は判断保留として後回し

---

### フェーズ2：通常運用

| 項目 | 内容 |
|---|---|
| 対象 | クールダウン等の既存スキップを通過した全 post |
| ソートキー | `(-has_any_streaming, -release_year, min_updated_at ASC)` |
| 目的 | 配信中作品の終了検知（最優先）+ 新作の新規配信検知 |

```python
# 疑似コード
sort_key = lambda p: (
    -_has_any_streaming(p),           # DESC: 配信中あり = 0, なし = 1
    -_release_year_sort(p, phase=2),  # DESC: 空欄は 0 扱い（最後尾）
    _min_updated_at(p),               # ASC: 古い順
)
targets = sorted(candidates, key=sort_key)[:DAILY_QUOTA]
```

**release_year 空欄の扱い（フェーズ2）**
- `0` 扱い → DESC ソートで最後尾
- 新作優先の意図に忠実

---

## フェーズ切替の詳細

```python
def _all_updated_at_empty(post: dict) -> bool:
    """全サービスの updated_at が空欄なら True（未スクレイピング post の判定）。"""
    acf = post.get("acf") or {}
    return all(
        not (acf.get(svc) or {}).get("updated_at")
        for svc in SERVICES
    )
```

- フェーズ1 対象 post が 0件 → 自動でフェーズ2 の全候補ソートへ移行
- フェーズ1 対象が DAILY_QUOTA 未満の日は、残り枠をフェーズ2 候補で補完する

---

## ログ出力（実装時の目標フォーマット）

```
INFO  QUOTA phase=1 unscraped=87 targets=30
INFO  QUOTA phase=2 streaming_prioritized=12 new_releases=8 others=10
INFO  QUOTA total=30 skipped=457
```

---

## 実装範囲

変更は `checker.py` の `run()` 関数内のみ。`get_posts()` はそのまま全件取得し、  
ソート＆スライスを `run()` 内で行う（WordPress API 側でのフィルタは不要）。

---

## TODO

### Phase A: 日次クォータ + ソート実装

- [ ] `checker.py` に `_all_updated_at_empty(post) -> bool` を追加
- [ ] `checker.py` に `_has_any_streaming(post) -> bool` を追加
- [ ] `checker.py` に `_sort_key_phase1(post) -> tuple` を追加
- [ ] `checker.py` に `_sort_key_phase2(post) -> tuple` を追加
- [ ] `checker.py` の `run()` に `_select_targets(posts, quota) -> list` を追加
  - フェーズ1 対象 post を洗い出し
  - フェーズ1 対象が quota 未満なら残り枠をフェーズ2 で補完
  - ログ出力（phase, 各件数）
- [ ] `run()` の post ループを `_select_targets()` の戻り値に差し替え
- [ ] 環境変数 `DAILY_QUOTA`（デフォルト 30）を `run()` で参照
- [ ] `--force` 時はクォータを無視して全件処理（既存挙動を維持）

### Phase B: ユニットテスト

- [ ] `tests/test_quota.py` を新規作成
  - [ ] `_all_updated_at_empty`：全空欄 / 一部あり / 全あり
  - [ ] `_sort_key_phase1`：release_year ソート順・空欄は最後尾
  - [ ] `_sort_key_phase2`：配信中優先 → 新作優先 → updated_at 古い順
  - [ ] `_select_targets`：フェーズ1のみ / フェーズ2のみ / 混在 / quota 未満

### Phase C: ログ・運用確認

- [ ] `--dry-run` 実行で選択された post と選択理由をログ確認
- [ ] フェーズ1 → フェーズ2 への自動切替をドライランで確認
- [ ] Cloud Run デプロイ後に 1週間のログで件数・フェーズ推移を確認

---

## 関連ドキュメント

- [`scraping-frequency.md`](scraping-frequency.md) — クールダウン・スキップ条件の詳細
- [`scraping-frequency-todo.md`](scraping-frequency-todo.md) — Phase 1〜5 の実装状況
- [`operations.md`](operations.md) — 運用フロー全体
