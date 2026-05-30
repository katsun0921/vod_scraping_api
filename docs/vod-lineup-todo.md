# VOD ラインナップ TODO

VOD ラインナップ機能の実装タスク一覧。

詳細仕様: [vod-lineup.md](vod-lineup.md)

---

## Phase 1: 基盤・データモデル

- [ ] `collectors/__init__.py` を作成
  - [ ] `LineupItem` dataclass を定義（title / original_title / url / external_id / release_year / lang / content_type / collected_at）
  - [ ] 共通定数（対象サービス一覧など）を定義
- [ ] `collectors/base.py` を作成
  - [ ] `BaseCollector` 抽象クラスを定義
  - [ ] `collect() -> list[LineupItem]` インターフェースを定義
- [ ] `utils/gcs.py` を作成
  - [ ] `upload_lineup(cycle, items)` — 月別 JSON を GCS に上書きアップロード
  - [ ] `download_overrides(cycle)` — `overrides/{cycle}.json` を取得（なければ空を返す）
  - [ ] `update_index(cycle)` — `index.json` に今月の cycle を追加
  - [ ] 環境変数 `GCS_LINEUP_BUCKET` を参照（ハードコード禁止）
- [ ] `utils/snapshot.py` を作成
  - [ ] `load_snapshot(service)` — 前回の external_id セットを GCS から読み込む
  - [ ] `save_snapshot(service, ids)` — 今回の external_id セットを GCS に保存
  - [ ] `diff(prev, curr)` — 新規追加分を返す
- [ ] `requirements.txt` に `google-cloud-storage` を追加
- [ ] `.env.example` に `GCS_LINEUP_BUCKET` を追加

---

## Phase 2: U-NEXT コレクター（PoC）

> U-NEXT から着手し、DOM セレクタ・スクロール戦略を確定する。

- [ ] U-NEXT 洋画新着ページの公開 URL を実機で確認・確定
- [ ] Playwright でページを開き、タイトルを取得できるセレクタを特定
  - [ ] 無限スクロールの対策方法を決定（スクロール回数 or ページネーション URL）
  - [ ] external_id（作品 ID）の取得方法を確定
- [ ] `collectors/unext.py` を作成
  - [ ] `UnextCollector(BaseCollector)` を実装
  - [ ] `collect() -> list[LineupItem]` を実装（Playwright 使用）
  - [ ] robot 検出・取得失敗時に `RuntimeError` を raise
- [ ] ローカルで動作確認（`dry_run` 相当で取得件数・タイトルをログ出力）

---

## Phase 3: Netflix / Amazon コレクター

- [ ] Netflix 洋画新着ページの公開 URL を実機で確認
  - [ ] ログイン不要で取得可能かを検証
  - [ ] 可能なら `__NEXT_DATA__` / JSON-LD を優先解析
  - [ ] 不可なら代替策を決定（JustWatch API への切り替えなど）
- [ ] `collectors/netflix.py` を作成
  - [ ] `NetflixCollector(BaseCollector)` を実装
  - [ ] requests + BS4 → 不可なら Playwright にフォールバック
  - [ ] robot 検出・取得失敗時に `RuntimeError` を raise
- [ ] Amazon Prime Video 洋画新着ページの公開 URL を実機で確認
  - [ ] robot 検出パターンを既存 `checkers/amazon.py` の `ROBOT_INDICATORS` と照合
- [ ] `collectors/amazon.py` を作成
  - [ ] `AmazonCollector(BaseCollector)` を実装（Playwright 推奨）
  - [ ] robot 検出時は `RuntimeError` を raise（当該サービスをスキップ）

---

## Phase 4: ランナー・エンドポイント

- [ ] `vod_lineup.py` を作成（月次ランナー）
  - [ ] `_COLLECTOR_MAP` を定義（`service → CollectorClass`）
  - [ ] `run(services, lang, dry_run, limit)` を実装
    - [ ] 各コレクターを呼び出してラインナップを収集
    - [ ] `lang=en` / `content_type=movie` でフィルタ
    - [ ] `snapshot.py` で差分判定（新規のみ抽出）
    - [ ] overrides マージ（`utils/gcs.py` 経由）
    - [ ] GCS に月別 JSON を出力・`index.json` を更新
    - [ ] Slack に月次サマリを通知（タイトルのみ）
    - [ ] 実行結果 dict を返す
  - [ ] CLI エントリーポイント（`--dry-run` / `--services` / `--limit` オプション）
- [ ] `main.py` に `POST /vod-lineup` エンドポイントを追加
  - [ ] リクエストボディ: `services / lang / dry_run / limit`
  - [ ] `vod_lineup.run()` を呼び出してレスポンス返却
- [ ] `utils/slack.py` に `notify_vod_lineup(cycle, titles_by_service)` を追加
  - [ ] フォーマット: サービス別にタイトル一覧を表示（タイトルのみ）

---

## Phase 5: テスト

- [ ] `tests/test_vod_lineup.py` を作成
  - [ ] `LineupItem` dataclass の生成・バリデーション
  - [ ] `snapshot.diff()` の差分判定ロジック
  - [ ] overrides マージロジック（exclude / rename / add）
  - [ ] `run()` の dry_run 動作確認
- [ ] `tests/test_collectors.py` を作成（モックを使用）
  - [ ] 各コレクターが `list[LineupItem]` を返すことを確認
  - [ ] `RuntimeError` が適切に raise されることを確認

---

## Phase 6: インフラ・デプロイ

- [ ] GCS バケット名を確定（例: `vod-lineup`）
- [ ] GCS バケットを作成
  - [ ] `roles/storage.objectAdmin` を Cloud Run SA に付与（WIF、キー不要）
  - [ ] Object Versioning を有効化
    ```bash
    gcloud storage buckets update gs://vod-lineup --versioning
    ```
  - [ ] Cloudflare のオリジンとして設定（サブドメイン `cdn.example.com/vod-lineup/...`）
- [ ] Cloud Run の環境変数に `GCS_LINEUP_BUCKET` を追加
- [ ] Cloud Scheduler を設定
  - [ ] `毎月 1 日 03:00 JST → POST /vod-lineup`
- [ ] `Dockerfile` に変更がある場合は更新（Playwright 追加済みのはずなので確認のみ）
- [ ] GitHub Actions CI に新しい環境変数を追加（必要な場合）

---

## Phase 7: フロントエンド連携

> このリポジトリ（バックエンド）ではなく、Next.js 側の作業。

- [ ] GCS バケットの Cloudflare URL を確定
- [ ] `index.json` を fetch して月セレクタを構築する関数を実装
- [ ] `{cycle}.json` を fetch してタイトル一覧を取得する関数を実装
- [ ] 専用ページ `/vod-lineup` を実装
  - [ ] 月セレクタ（`index.json` → ファイル選択）
  - [ ] サービスタブ（すべて / U-NEXT / Netflix / Amazon）
  - [ ] 種別フィルタ（洋画 ← 将来アニメ・ドラマを追加できる枠を用意）
  - [ ] タイトル一覧表示
- [ ] TOP ティザーセクションを実装
  - [ ] 今月の JSON から最大 N 件を抜粋表示
  - [ ] 「もっと見る →」で `/vod-lineup` に誘導
  - [ ] 既存「新着配信」セクションと別ブロックに配置

---

## Phase 8: 運用確認

- [ ] 初回 Cloud Scheduler 実行ログを確認
  - [ ] 各サービスの収集件数
  - [ ] フィルタ後の件数（lang_en / movie）
  - [ ] GCS にファイルが正常出力されたことを確認
  - [ ] Slack 通知が届いたことを確認
- [ ] フロントの専用ページで表示を確認
- [ ] `gcloud storage cp` で手動修正フローを動作確認
- [ ] （必要になった場合）overrides マージを有効化
  - [ ] `overrides/{cycle}.json` の schema を確定・ドキュメントに記載
  - [ ] `utils/gcs.py` の `download_overrides` → マージロジックを有効化
