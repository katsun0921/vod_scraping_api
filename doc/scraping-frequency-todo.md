# TODO

スクレイピング頻度制御の実装タスク一覧。

詳細仕様: [scraping-frequency.md](scraping-frequency.md)

---

## Phase 1: ACF フィールド追加

- [x] ACF JSON に 3 フィールドを追加
  - [x] `scraping_disabled` (true_false)
  - [x] `scraping_cooldown_until` (date_picker)
  - [x] `unavailable_check_count` (number)
- [x] フィールドの配置先を決定（VOD配信状況グループ末尾）
- [x] WordPress 管理画面で ACF JSON をインポート
- [x] 全 publish 投稿に初期値を投入（SQL バッチ、498件）
  - `scraping_disabled = 0`
  - `scraping_cooldown_until = ''`
  - `unavailable_check_count = 0`

---

## Phase 2: 判定ロジック実装

- [x] `utils/wordpress.py` に `should_skip(post, service, today)` 関数を追加
  - [x] `scraping_disabled` チェック
  - [x] `scraping_cooldown_until` チェック
  - [x] `scraping_url` 空チェック
  - [x] 直近30日更新済みチェック
- [x] `utils/wordpress.py` に `update_cooldown(post, today, acf_payload)` 関数を追加
  - [x] 配信中サービスがあれば30日サイクル + カウントリセット
  - [x] 全サービス未配信なら指数バックオフ + 年齢補正
- [x] `utils/wordpress.py` に `patch_cooldown(post_id, acf_payload)` 関数を追加
- [x] ユニットテスト追加（tests/test_wordpress.py、32件）

---

## Phase 3: checker.py 統合

- [x] 投稿ループの最初で `scraping_disabled` / `cooldown` チェックを実行
- [x] サービスループで `should_skip`（scraping_url / updated_at）チェックを実行
- [x] 全サービスチェック完了後に `update_cooldown` + `patch_cooldown` を呼び出し
- [x] ログ出力でスキップ理由を可視化

---

## Phase 4: ドキュメント更新

- [x] `doc/relations.md` に新規 ACF フィールドを追加
- [x] `doc/json-output.md` に新規フィールドを追加
- [x] `doc/operations.md` のスキップ条件表を更新
- [x] `doc/operations.md` の設計思想にクールダウン説明を追加
- [x] ルート `CLAUDE.md` の ACF フィールド定義を更新（apple_tv 追加 + 新規3フィールド）

---

## Phase 5: 動作確認

- [x] ローカルで `checker.py --dry-run` 相当を実行
- [x] スキップカウント・実行カウントをログで確認（processed=50 skipped=3550 errors=0）
- [x] Cloud Run にデプロイ（GitHub Actions CI/CD）
- [ ] 1週間の運用ログを確認
  - [ ] スキップ率
  - [ ] 平均リクエスト数/日
  - [ ] 新規配信検知の遅延

---

## 関連する未着手タスク（別仕様）

### Apple TV チェッカー実装
- [x] `checkers/apple_tv.py` を新規作成
- [x] URL 形式: `https://tv.apple.com/{region}/movie/{slug}/{id}`
- [x] `check(url: str) -> dict` を実装（streaming / purchase / ended / unavailable）
- [x] `checker.py` の `_CHECKER_MAP` に追加

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
