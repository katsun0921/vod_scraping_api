# ルーティン方式による情報収集

> 対象: 劇場公開・VOD配信情報の収集を Claude のルーティン（スケジュール実行）へ移す設計
> プロンプト本体: [routine-prompts.md](./routine-prompts.md)
> 最終更新: 2026-08-16

---

## ■ 背景

劇場公開（`theater_discover`）とVOD配信（`vod_discover`）の情報収集は、GitHub Actions から
Claude / OpenAI の Web検索を**従量課金の API** で呼んでいた。

これを Claude のルーティンに置き換える。ルーティンは Claude サブスクリプションの範囲で動くため、
**都度の API 課金がサブスクリプション内の定額に収まり、予算が立てやすくなる。**

### 移行対象と対象外

| 処理 | 移行 | 理由 |
|---|---|---|
| 劇場公開の Web検索 | ✅ ルーティンへ | Anthropic + OpenAI API の従量課金 |
| VOD配信の Web検索 | ✅ ルーティンへ | 同上 |
| **VOD公式Xアカウントの取得・抽出** | ❌ Actions に残す | **X API v2 の認証が必要でルーティンでは代替できない** |
| シート書き込み・Slack通知・WP投稿 | ❌ Actions に残す | 認証情報（サービスアカウント / Bot Token / App Password）を持つのは Actions のみ |

VOD側は「ルーティンの Web検索結果」と「Actions の X抽出結果」を `extract_vod.merge_all()` で
統合するハイブリッドになる。

---

## ■ 方式の選択（C案を採用）

ルーティンからシートやSlackへ直接書き込むには、サービスアカウントJSONやBot Tokenを
ルーティンの実行環境に渡す必要がある。これを避けるため、**リポジトリを経由**する方式を採った。

| 案 | 仕組み | 採否 |
|---|---|---|
| A. 直接書き込み | ルーティンが検索 → シート追記 → Slack通知 | ✗ 認証情報の受け渡しが必要 |
| B. 中間ファイル | ルーティンがJSONをコミット → Actionsが取り込む | △ 承認の置き場所が別途必要 |
| **C. PR経由** | ルーティンがJSONでPRを作る → Actionsが変更範囲とJSONを検証 → 自動マージで取り込み起動 | ✅ 採用 |

### C案を選んだ理由

**成果物の受け渡しを安全に自動化できる。** 変更が `news_bot/routine_data/*.json` だけで、
JSON配列として正常な同一リポジトリのルーティンPRに限ってActionsが自動マージする。
それ以外の変更が混ざったPRは自動マージしない。

**内容の承認はシートに集約する。** マージ後も各行は`承認待ち`で保存されるため、
実在性・日付・URL・記事に載せるかの判断はGoogle Sheets上で行う。

**認証情報が増えない。** ルーティンが触るのはリポジトリだけで、シート・Slack・WPの
認証情報は Actions 側に閉じたままになる。

---

## ■ 人間の承認はシートで行う

PRは機械検証後に自動マージされる。取り込まれた行は **投稿状態=`承認待ち`** で保存され、
人間が内容を確認して`承認済み`へ変更するまでCPT作成には流れない。

| 段階 | 何を見るか | どこで |
|---|---|---|
| **機械検証** | 変更ファイルの範囲・削除でないこと・JSON構文・配列形式 | GitHub Actions |
| **人間の承認** | 実在性・日付・URL・記事に載せるか・SNS優先度 | Google Sheets |

機械検証に失敗したPRはマージされず、シートにも取り込まれない。

---

## ■ 運用フロー

```
┌─ ルーティン（Claudeサブスクリプション内） ────────────────┐
│  週次で Web検索 → news_bot/routine_data/*.json を更新     │
│  → ブランチを切って PR を作成                             │
└────────────────────┬──────────────────────────────────┘
                     │ Actionsが変更範囲・JSONを検証
                     ▼ 自動マージ
┌─ GitHub Actions ──────────────────────────────────────┐
│  theater_import / vod_import                            │
│   ├ JSONを読み込み（import_routine.py）                  │
│   ├ VODのみ: X公式アカウントから抽出して統合              │
│   ├ 対象期間フィルタ・重複判定                            │
│   ├ Katsumascore照合（既存レビュー記事へのリンク）        │
│   ├ シートへ 投稿状態="承認待ち" で追記                   │
│   └ Slack へ確認依頼を通知                               │
└────────────────────┬──────────────────────────────────┘
                     │ 人間がシートで内容を確認・承認
                     │ 投稿状態="承認済み" + SNS優先度を設定
                     ▼
┌─ GitHub Actions（週次 cron） ─────────────────────────┐
│  theater_publish / vod_publish                          │
│   ├ WP CPT へ下書き投稿                                  │
│   ├ SNS投稿案を Slack へ                                 │
│   └ 投稿状態="投稿済み" へ更新                            │
└────────────────────────────────────────────────────────┘
```

---

## ■ 実装

| ファイル | 役割 |
|---|---|
| `news_bot/import_routine.py` | 成果物JSONを `TheaterEntry` / `VodEntry` へデシリアライズ |
| `news_bot/routine_data/theater_latest.json` | 劇場公開の最新成果物（ルーティンが上書き） |
| `news_bot/routine_data/vod_latest.json` | VOD配信の最新成果物（同上） |
| `main.py: theater_import_cycle()` | 劇場: JSON読み込み → 保存 |
| `main.py: vod_import_cycle()` | VOD: JSON読み込み + X抽出 → 統合 → 保存 |
| `.github/workflows/routine-data-auto-merge.yml` | 対象PRの変更範囲とJSONを検証し、検証したHEADをsquash merge |
| `.github/workflows/routine-import.yml` | PRマージ後に import を実行 |
| `.github/workflows/routine-pr-notify.yml` | ルーティンPR作成時にSlackへ作成通知 |

### PR作成時のSlack通知

`routine/theater-*`または`routine/vod-*`ブランチから成果物JSONを変更するPRが作成されると、
`Routine PR Notify`がPRタイトル・URL・作成者をSlackへ送信する。劇場/VODの専用チャンネルを
優先し、未設定の場合は承認チャンネルへフォールバックする。通知には既存の
`NEWS_BOT_SLACK_BOT_TOKEN`と各チャンネルIDのSecretsを使用する。

### PRの自動マージ

同一リポジトリの`routine/theater-*`または`routine/vod-*`ブランチから作られたDraftではないPRを
対象にする。変更が`news_bot/routine_data/`直下の`.json`だけで、追加または更新であり、
全成果物がオブジェクト配列のJSONとして正常な場合に限り、検証したHEAD SHAをsquash mergeする。
他のファイル変更・削除・壊れたJSON・forkからのPRは自動マージしない。

### 過去週の手動再処理

`Routine Import`の`workflow_dispatch`には`target_start`を指定できる。劇場は対象週の
金曜日、VODは対象週の月曜日を`YYYY-MM-DD`で入力する。空欄なら通常どおり実行日から
対象期間を計算する。

取り込み後はSlack承認を完了させ、`Theater Calendar`または`VOD Calendar`を手動実行して
同じ`target_start`を指定すると、その過去週を対象にCPTを作成できる。

### 成果物は固定名

`*_latest.json` を上書きする方式にした。日付別ファイルにすると Actions 側が
「どれを取り込むべきか」を判断する必要が生じるため。**履歴は git のコミット履歴で追える。**

### スキーマはプロンプトと同一

JSONのスキーマは `discover_theater._build_prompt()` / `discover_vod._build_prompt()` が
AIに指示している形式と揃えてある。API方式とルーティン方式でスキーマが分岐すると、
プロンプトの知識が2箇所に散るため。

### 取り込み時の検証

自動マージ前のJSON検証に加え、取り込み時にも構造と各項目を機械的に検証する。

| 条件 | 挙動 |
|---|---|
| ファイルが無い | `FileNotFoundError`（0件と区別する。取りこぼしに気付けるように） |
| JSON配列でない | `ValueError` |
| title / 日付が空 | その行をスキップ（ログに警告） |
| 日付形式が不正 | その行をスキップ |
| VODの `service` が未知 | その行をスキップ（生の識別子が記事に露出するのを防ぐ） |
| 空配列 | 正常（その週に該当作品が無い場合がある） |

---

## ■ 既存の discover 系コードの扱い

`discover_theater.py` / `discover_vod.py` と `theater_discover` / `vod_discover` サブコマンドは
**残す**。ルーティンが止まった場合のフォールバックとして手動実行できるようにするため。

cron からは外し、`workflow_dispatch` での手動実行のみとする。

---

## ■ ルーティンの設定

**ルーティンは AI からは起動・設定できない。** Claude Code の `/schedule` から人間が設定する。

プロンプトは [routine-prompts.md](./routine-prompts.md) を参照。

| ルーティン | 推奨スケジュール | 出力先 |
|---|---|---|
| 劇場公開 | 毎週金曜 09:00 JST | `news_bot/routine_data/theater_latest.json` |
| VOD配信 | 毎週水曜 09:00 JST | `news_bot/routine_data/vod_latest.json` |

VOD を水曜にしているのは、`vod_publish` が月曜のため、シート確認と承認の時間を確保するため。

---

## ■ 未確定事項

| # | 項目 | 内容 |
|---|---|---|
| 1 | ルーティンの精度検証 | API方式と比べて取りこぼし・誤情報の頻度がどう変わるか未検証。数週間並行させて比較するのが望ましい |
| 2 | API方式の停止判断 | ルーティンが安定するまで `theater_discover` / `vod_discover` は手動実行で残す。停止するかは精度検証の結果次第 |
| 3 | 自動マージ失敗時の扱い | 機械検証や競合でマージできない場合はPRを残し、Actionsログを確認して修正する |

---

## ■ 関連ドキュメント

| ドキュメント | 内容 |
|---|---|
| [routine-prompts.md](./routine-prompts.md) | ルーティンに登録するプロンプト本体・シート承認時の確認観点 |
| [theater-release-calendar-spec.md](./theater-release-calendar-spec.md) | 劇場公開パイプライン本体の仕様 |
| [vod-release-calendar-spec.md](./vod-release-calendar-spec.md) | VOD配信パイプライン本体の仕様 |
| [strategy-overview.md](./strategy-overview.md) | パイプライン全体の役割分担 |
