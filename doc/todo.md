# TODO

スクレイピング頻度制御の実装タスク一覧。

詳細仕様: [scraping-frequency.md](scraping-frequency.md)

---

## Phase 1: ACF フィールド追加

- [ ] ACF JSON に 3 フィールドを追加
  - [ ] `scraping_disabled` (true_false)
  - [ ] `scraping_cooldown_until` (date_picker)
  - [ ] `unavailable_check_count` (number)
- [ ] フィールドの配置先を決定（「作品メタ」グループ推奨）
- [ ] WordPress 管理画面で ACF JSON をインポート
- [ ] 全 publish 投稿に初期値を投入（SQL バッチ）
  - `scraping_disabled = 0`
  - `scraping_cooldown_until = ''`
  - `unavailable_check_count = 0`

---

## Phase 2: 判定ロジック実装

- [ ] `utils/wordpress.py` に `should_skip(post, service, today)` 関数を追加
  - [ ] `scraping_disabled` チェック
  - [ ] `scraping_cooldown_until` チェック
  - [ ] `scraping_url` 空チェック
  - [ ] 直近30日更新済みチェック
- [ ] `utils/wordpress.py` に `update_cooldown(post, today)` 関数を追加
  - [ ] 配信中サービスがあれば30日サイクル + カウントリセット
  - [ ] 全サービス未配信なら指数バックオフ + 年齢補正
- [ ] ユニットテスト追加（任意）

---

## Phase 3: checker.py 統合

- [ ] 投稿ループの最初で `should_skip` チェックを実行
- [ ] 全サービスチェック完了後に `update_cooldown` を呼び出し
- [ ] ACF PATCH 時に `scraping_cooldown_until` / `unavailable_check_count` を含める
- [ ] ログ出力でスキップ理由を可視化

---

## Phase 4: ドキュメント更新

- [ ] `doc/relations.md` に新規 ACF フィールドを追加
- [ ] `doc/json-output.md` に新規フィールドを追加
- [ ] `doc/operations.md` のスキップ条件表を更新
- [ ] `doc/operations.md` にチェック頻度早見表を追加
- [ ] ルート `CLAUDE.md` の ACF フィールド定義を更新

---

## Phase 5: 動作確認

- [ ] ローカルで `checker.py --dry-run` 相当を実行
- [ ] スキップカウント・実行カウントをログで確認
- [ ] Cloud Run にデプロイ
- [ ] 1週間の運用ログを確認
  - [ ] スキップ率
  - [ ] 平均リクエスト数/日
  - [ ] 新規配信検知の遅延

---

## 関連する未着手タスク（別仕様）

### Apple TV チェッカー実装
- [ ] `checkers/apple_tv.py` を新規作成
- [ ] URL 形式: `https://tv.apple.com/jp/movie/{slug}`
- [ ] `check(url: str) -> dict` を実装
- [ ] `checker.py` の `_SERVICE_KEYWORDS` に追加

### Slack 通知
- [ ] `utils/slack.py` を新規作成
- [ ] `streaming_started_at` 新規セット時に Webhook 送信
- [ ] 環境変数 `SLACK_WEBHOOK_URL` 対応
- [ ] 通知フォーマット決定（作品タイトル / サービス名 / URL）

### 独占配信スキップ
- [ ] `is_exclusive` / `exclusive_service` フィールドを ACF に追加
- [ ] `should_skip` に独占判定を追加（対象サービスが exclusive_service と不一致なら true）

### 言語別スキップ
- [ ] `languages` フィールドを ACF に追加（ja / en の checkbox）
- [ ] サービスごとの対応言語マッピングをコードに追加
- [ ] `should_skip` に言語判定を追加

### 月次 JustWatch 再問い合わせ（任意）
- [ ] `scraping_url` 空のサービスに対する月次バッチ
- [ ] URL 見つかれば自動登録 → 通常フロー復帰
- [ ] GAS 側との接続方式を決定
