# VOD配信情報収集パイプライン 仕様書

作成: 2026-07-22
対象: `news_bot/`

## 1. 目的

劇場公開カレンダー（[theater-release-calendar-spec.md](theater-release-calendar-spec.md)）で確立した
「公開情報を集める施策」（AI Web検索による発見 + Google Sheetsでの情報源・データ管理 + 人間承認）を
**VODの配信情報（配信開始・配信予定）**に応用する。

集めた配信情報は週次で以下の3経路に展開し、Katsumascoreへの流入を増やす。

1. **Slack通知**: 週次まとめを管理者へ通知（承認・確認用）
2. **WordPress CPT投稿**: 「今週配信開始のVOD作品まとめ」をカスタム投稿タイプとして自サイトに投稿
3. **SNS（X）投稿**: x-news-botのスレッドまとめ方式で投稿案を生成し、リプライURLに自サイトのCPT記事URLを使って送客する

外部ニュースの転載ではなく、**自サイトのまとめ記事を受け皿にした送客**を主目的とする点で、
x-news-bot仕様書4.7（Coming Soonソース連携）の狙いと同じ方向の施策である。

さらに単なる配信情報の集約に留まらず、**「配信開始→レビュー→人物→シリーズ→関連記事」という
導線を作り、既存レビュー資産・作品データベースへ読者を送客するハブ**として発展させることで、
Katsumascoreを「レビューサイト」から「映画・アニメ総合メディア」へブランド拡張する狙いも持つ
（[vod-release-calendar-improvements.md](vod-release-calendar-improvements.md)参照）。

## 2. 既存施策との関係

| 既存施策 | 関係 |
|---|---|
| [theater-release-calendar-spec.md](theater-release-calendar-spec.md) | **方式の親**。AI発見（`discover_theater.py`）・正規化/重複キー（`theater_calendar.py`）・シート自動作成（`sheets.py`）・承認待ちフローをそのまま流用する |
| [theater-sources-candidates.md](theater-sources-candidates.md) | **規約判断の前例**。TMDb API・PR TIMES企業別RSS・映画.com RSSはいずれもAdSense収益化サイトでの利用が規約に抵触するリスクで撤回済み。本施策でも同じ判断基準を適用する |
| [coming-soon-pipeline.md](../drop/coming-soon-pipeline.md) | 同じ「VOD配信予定」を扱っていたが、依存していた取得手段（TMDb API・各VODサービス公式サイトのスクレイピング）が両方とも規約上使用不可と判明したため**廃止・`docs/drop/`へ移動**（2026-07-22）。本施策はTMDbにもスクレイピングにも依存しないAI発見方式+X公式アカウントで先行する。将来TMDb商用契約が成立した場合のみComing Soonパイプラインの復活を検討できる |
| [x-news-bot-spec.md](../x-news-bot-spec.md) | **SNS投稿の実行基盤**。スレッドまとめ方式（`compose.compose_headline()` + `compose.pack_thread()` + Slackテンプレート通知→人間が手動投稿）を流用する |
| `vod_bot/weekly_patch.py` | 責務が異なる。weekly_patchは**既存WP投稿の配信状況の更新**（チェッカー巡回）、本施策は**新規に配信開始する作品の発見と発信**。ただしWP REST APIクライアント（`vod_bot/wordpress.py`）の実装パターンは参考にする |
| [vod-release-calendar-improvements.md](vod-release-calendar-improvements.md) | **ブランド価値向上の観点からの改善提案（2026-07-23）**。編集部おすすめ・作品カード統一・Google Discover対策（11.2）、サービス別ページ・年間アーカイブ・ジャンル別まとめ・作品ページ逆リンク（16.将来拡張）、CPT名称の見直し・承認フローのワンクリック化（15.未決定事項）を整理。反映状況は同ドキュメント末尾の対応表を参照 |

## 3. ゴール / 非ゴール

### ゴール

- 対象週に配信開始されるVOD作品を週次で収集する
- 情報源をGoogle Sheetsで管理する（コードへのハードコード禁止）
- 収集結果をGoogle Sheetsに構造化保存し、人間の承認を経てから下流に流す
- 承認済みデータから週次まとめを生成し、Slack通知・WP CPT投稿・X投稿案生成を行う
- Katsumascore内の既存レビュー記事と照合し、内部リンクで回遊を作る

### 非ゴール

- 初期MVPではXへの自動投稿は行わない（x-news-botと同じく手動投稿を維持）
- 初期MVPでは全VODサービス・全作品の完全網羅を保証しない
- 初期MVPでは配信終了情報・料金変動は扱わない（`vod_bot`の責務）
- 外部サイトのあらすじ・紹介文など表現は保存・転載しない（事実情報のみ）

## 4. 全体フロー

```text
[VOD情報源シート]
   ├─ 取得方式=x  → [公式Xアカウントのポスト取得（fetch_x.py流用、生テキスト）]
   └─ 取得方式=ai → [discover_vod.py: AI Web検索（Claude/OpenAI併用、生テキスト）]
                              ↓               ↓
                    [extract_vod.py: AI統合レイヤー]
                    Claude/OpenAIで構造化抽出 + 重複キーでマージ
                    （両ソースが同じ作品を挙げたら情報源="X+AI検索"）
                              ↓
                    [正規化・重複判定（vod_calendar.py）]
                              ↓
                    [Google Sheets: VOD配信予定]（投稿状態=承認待ち）
                              ↓
                    [Slack: 発見結果の確認依頼通知]
                              ↓
                    【人間: シートで確認・修正・承認】
                              ↓
              ┌───────────────┼───────────────┐
              ↓               ↓               ↓
      [Slack週次まとめ]  [WP CPT投稿]   [X投稿案生成]
       （管理者通知）   （自サイト記事）  （Slackテンプレ
                              ↑           →手動投稿）
                              └── リプライURLはCPT記事URL
```

## 5. 実行方式

`news_bot.main` にサブコマンドを2つ追加する。

```bash
python -m news_bot.main vod_discover   # 発見・保存（承認待ちにする）
python -m news_bot.main vod_publish    # 承認済み行から週次まとめを展開
```

想定スケジュール（GitHub Actions cron）:

| ジョブ | タイミング | 内容 |
|---|---|---|
| `vod_discover` | 毎週木曜 06:00 JST | **翌週月曜〜日曜**に配信開始する作品を発見し、承認待ちで保存。Slackに確認依頼を通知 |
| `vod_publish` | 毎週月曜 07:00 JST | **当週月曜〜日曜**の承認済み行から週次まとめを生成し、Slack通知・WP CPT投稿・X投稿案送信 |

木曜発見→月曜展開とすることで、人間の承認作業に金〜日の3日間の猶予を持たせる。
承認されなかった行（承認待ちのまま）は `vod_publish` の対象外とし、誤情報の公開を防ぐ
（theater仕様のAI発見方式と同じ人間承認前提）。

## 6. 取得対象期間

- 基準日は実行日とする
- `vod_discover`: 翌週月曜 00:00 〜 翌週日曜 23:59（JST）に配信開始する作品
- `vod_publish`: 当週月曜〜日曜が対象の承認済み行

対象サービス（初期MVP）は Netflix / Prime Video / U-NEXT / Disney+ / Hulu / DMM TV の6サービスとし、
シート運用で増減できるようにする（8.①「対象サービス」列）。

## 7. 情報源の管理方針（要件1: シート管理）

### 7.1 基本方針

取得元はコードにハードコードせず、**「VOD情報源」シート**で管理する
（theater仕様の「劇場情報源」シート、x-news-botの「RSS一覧」と同じ考え方）。

- 「有効/無効」チェックボックスと「規約確認済み」="済" が揃った行のみ取得対象
- `取得方式` は初期MVPでは `x`（公式Xアカウント）と `ai`（AI Web検索）を実装。
  `rss` / `html` は行を登録できるが規約確認済みのソースが現れるまで実装保留

### 7.2 規約調査の結果（2026-07-22）

候補ごとの規約調査は [vod-sources-candidates.md](vod-sources-candidates.md) にまとめた。要点:

| ソース | 判断 |
|---|---|
| VODサービス公式Xアカウント | **採用（必須）**。X API v2（公式API・有償）経由のためスクレイピング規約問題が生じない |
| AI Web検索 | 採用（既定）。事実情報のみの構造化保存 |
| 各サービス公式サイトのスクレイピング | **不採用（確定）**。Netflix（規約4.6）・Amazon・Disney+・Hulu・U-NEXT・DMM TV（全6サービス、2026-07-22に人間が規約原文確認済み）は自動化手段によるアクセス／データ収集を規約で明示禁止。サービスごとの一次情報はX（7.3）のみで取得する |
| TMDb（スクレイピング） | **不採用**。規約でサイトのスクレイピングを明示禁止。公認のプログラムアクセスはAPIのみ |
| TMDb（API） | 商用契約（$149/月〜）成立時のみ将来採用（[theater-sources-candidates.md](theater-sources-candidates.md) A.節の判断を継承） |
| PR TIMES企業別RSS / ニュースメディアRSS | theater施策の判断を継承し不採用・保留 |

### 7.3 唯一のper-service情報源: VODサービス公式Xアカウント（取得方式=x）

配信開始告知はサービス公式Xアカウントが一次情報であり、X API v2という公認の機械取得ルートが
ある。加えて2026-07-22時点で対象6サービスすべて（Netflix / Prime Video / U-NEXT / Disney+ /
Hulu / DMM TV）の公式サイトスクレイピングが規約上不採用確定した（7.2）ため、**Xは
「本施策の必須情報源」ではなく「サービスごとの一次情報を機械取得できる唯一のルート」**
という位置づけになる。逐一の配信開始告知はXを通じて継続的に取得する。

- 「VOD情報源」シートに `取得方式=x` で各サービスの公式アカウント（Netflix / Prime Video /
  U-NEXT / Disney+ / Hulu / DMM TV。ハンドルは登録前に人間が実在確認）を登録する
- 取得は既存の `news_bot/fetch_x.py`（Bearer Token・`since_id`キャッシュで新着のみ課金）を流用。
  x-news-bot仕様書4.1レイヤー4「配信サービス」として当初から想定されていた枠の実装にあたる
- ポストは非構造化テキストのまま`extract_vod.py`（7.5参照）に渡し、AI統合レイヤーで
  タイトル / サービス / 配信開始日 / 配信種別に構造化する。配信開始日が読み取れないポストは破棄する
- x-news-bot（ニュース→S/A判定→スレッド投稿）とはパイプラインを分ける。同じポストを
  ニュースとしても流したい場合は従来どおり「公式X一覧」シート側に登録する（二重登録は任意）

### 7.4 既定の取得方式: AI Web検索（取得方式=ai）

theater施策の`discover_theater.py`と同じく、**AIのWeb検索
（Claude API `web_search` + OpenAI Responses API `web_search` の併用）**で
対象週の配信開始作品を調べさせ、**事実情報のみ**を構造化して保存する。

- 保存するのは: タイトル / 原題 / サービス / 配信開始日 / 配信種別（見放題・レンタル等）/
  公式URL / 情報源URL。あらすじ・紹介文は保存しない
- 両AIが同じ作品を挙げた場合は情報源=`AI検索(claude+openai)`となり、承認時の実在確度シグナルになる
- AI検索結果は誤り得るため、保存時は必ず投稿状態=`承認待ち`

### 7.5 AI統合レイヤー（要件: 収集情報をまとめるAI API）

X（7.3）とAI Web検索（7.4）はそれぞれ生のテキスト（Xポスト本文 / Web検索結果の応答文）を
返すだけなので、両者を**同じ構造化スキーマに変換し1つのデータセットへ統合するAI層**を
`news_bot/extract_vod.py` として新設する。**新規のAI APIプロバイダーは追加しない**——
既存の `news_bot/ai_clients.py`（Claude Messages API / OpenAI Chat Completions API の
呼び出しラッパー、prompt caching対応）と `discover_theater.py` のWeb検索呼び出しパターンを
再利用するだけで実現できる。

**構成する処理は2種類**:

1. **構造化抽出**（X由来）: `ai_clients.call_claude()` を使い、取得済みのXポスト本文
   （複数件をまとめて1リクエスト。judge.judge_batch()と同じくバッチ化してコスト圧縮）を渡し、
   `{"title", "title_orig", "service", "available_from", "availability_type", "official_url"}`
   のJSON配列を生成させる。Web検索は不要（ポスト本文はすでに取得済みのため）
2. **統合・重複マージ**（`extract_vod.merge_all()`）: X抽出結果とAI Web検索結果
   （discover_vod.pyの出力）を、9.の重複キー（配信開始日+サービス+正規化タイトル）で
   マージする。`discover_theater.discover_all()`と同じロジック:
   - 両ソースが同じ作品を挙げた場合 → 情報源=`X+AI検索(claude+openai)`（実在確度が最も高い）
   - 片方のみ → 情報源はそのまま（例: `X(netflix公式)` / `AI検索(claude)`）
   - 空のフィールドは他方の値で補完する

**設計上の制約（theater施策と同じ）**:

- 抽出・統合結果はすべて事実情報のみ（あらすじ等の表現は生成させない）
- AI処理は誤り得るため、`merge_all()`の出力はすべて投稿状態=`承認待ち`で保存する
- プロンプトは新規ファイル `news_bot/prompts/vod_extract_system_prompt.md` に切り出し、
  `ai_clients.call_claude()`の`cache_control`でキャッシュを効かせる（news_bot既存パターン踏襲）

### 7.5.1 コスト試算: X抽出のClaude API呼び出し（2026-07-22）

新規AI APIキーの追加は不要（`ANTHROPIC_API_KEY` / `OPENAI_API_KEY`を流用）。ここでは
**構造化抽出**（`ai_clients.call_claude()`、Web検索なしのテキスト生成呼び出し）のコストのみを
試算する（統合・重複マージはAI呼び出しを伴わない純粋なコード処理のため無料）。

**前提**

- モデル: `news_bot/ai_clients.py`の既定 `claude-sonnet-5`（$3.00/$15.00 per 1M tokens。
  2026-08-31まで導入価格 $2.00/$10.00）
- バッチサイズ: `judge.judge_batch()`と同じく15件/リクエストでチャンク化
- 1件あたりの目安トークン数: 入力(Xポスト本文+JSON整形) 約110トークン / 出力(構造化JSON) 約100トークン
- systemプロンプト（抽出指示）は約800トークンで`cache_control`によりバッチ間でキャッシュ（1回目のみフル課金）
- 対象件数はA.1節（[vod-sources-candidates.md](vod-sources-candidates.md)）の週あたり投稿数試算をそのまま流用

**試算（週次実行1回あたり、キャッシュ効果を見込まない保守的な概算）**

| シナリオ | 週間投稿数 | バッチ数(15件/回) | 週額 | 月額(4.3週換算) |
|---|---|---|---|---|
| 少ない週 | 21件 | 2 | 約$0.04 | 約$0.19 |
| 平均的な週 | 84件 | 6 | 約$0.17 | 約$0.72 |
| 多い週 | 210件 | 14 | 約$0.42 | 約$1.80 |

**結論**: X抽出（Claude API）単体では月額 概ね **$0.2〜$1.8程度**。prompt cachingを有効化すれば
（news_bot既存パターンどおり）2バッチ目以降のsystemプロンプト分がさらに圧縮されるため、
上表は上限寄りの保守的な見積もりである。

**X情報源の総コスト**（読み取り[vod-sources-candidates.md A.1] + 抽出[本節]の合算）は
月額 概ね **$0.7〜$7程度**となり、既存news-bot-x予算の範囲内に十分収まる。AI Web検索
（discover_vod.py、Claude/OpenAIのweb_searchツール）側のコストは別途試算が必要
（theater-calendarの週次実行コストと同程度の規模になる見込み）。

## 8. Google Sheets

### ① VOD情報源シート

```text
ID / 名称 / URL / 取得方式(x・ai・rss・html) / 対象サービス / 有効/無効 / 規約確認済み / メモ
```

初期データは `取得方式=ai` の1行（AI Web検索）+ `取得方式=x` の6行
（各サービス公式Xアカウント。[vod-sources-candidates.md](vod-sources-candidates.md) A.節）。
将来、規約確認済みのRSS等が見つかったら行を追加するだけで取得対象を増やせる。
`取得方式=x` の行は「公式X一覧」シートと同じ `user_id` / `since_id` / 最終取得日時の
キャッシュ列を持つ（X snowflake IDの桁落ち対策も同シートの実装に倣う）。

### ② VOD配信予定シート

```text
取得日時
配信開始日
タイトル
原題
サービス（netflix / amazon_prime_video / unext / disney_plus / hulu / dmm_tv）
カテゴリ（映画・ドラマ・アニメ）
配信種別（見放題・レンタル・独占 等）
公式URL
情報源
Katsumascore URL
WP post_id
SNS優先度(S/A/B/C)
投稿状態
重複キー
メモ
```

サービスのキー名は `vod_bot`（CLAUDE.mdの対応VODサービス表）と揃え、
将来 `vod_bot` 側の配信状況データと突合できるようにする。

### 投稿状態

| 値 | 意味 |
|---|---|
| `承認待ち` | AI発見直後。人間の確認前 |
| `承認済み` | 人間が確認済み。`vod_publish` の対象 |
| `除外` | 誤り・対象外と人間が判断 |
| `投稿済み` | 週次まとめに含めて展開済み |

### SNS優先度

theater仕様8.と同じS/A/B/Cの4段階。Sは週次まとめに加えて個別投稿案も作る。
初期MVPでは判定ロジックは実装せず空欄とし、人間が承認時に手で付ける
（theater側の未決定事項#5と共通。AI判定化は両施策まとめて将来検討）。

## 9. 重複判定

theater仕様9.の正規化ルール（`theater_calendar.py`）を流用し、重複キーは以下とする。

```text
配信開始日 + サービス + 正規化タイトル
```

同一作品が複数サービスで同日配信開始する場合は別行として扱う（サービスごとに1行）。

## 10. Katsumascore照合

WP REST APIで既存レビュー記事を検索し、ヒットしたら `WP post_id` / `Katsumascore URL` を保存する。

- 照合順: `tmdb_id`（ACFに実在すれば）→ 正規化タイトル → 原題
- theater側の未実装事項#4（WP検索関数が無い）と共通の課題。実装する検索関数は
  theater / vod の両方から使える共通関数として `news_bot/` に置く
- 照合できた作品は週次まとめ記事・X投稿の中でレビュー記事へ内部リンクし、回遊を作る

### 10.1 `coming-soon-pipeline.md`（`enrich_events.py`）からの転用

[coming-soon-pipeline.md](../drop/coming-soon-pipeline.md)（`docs/drop/`へ移動・凍結済み）のWP照合実装
（`fetch_wp_post_by_tmdb_id()` + `build_auth()`）は、TMDb依存部分を除けば
そのまま10.の共通WP検索関数の土台として転用できる。

- `build_auth(user, app_pass)` （Basic認証ヘッダー生成）はそのまま流用可能
- `fetch_wp_post_by_tmdb_id()` のクエリパターン（`meta_key=tmdb_id&meta_value=...`）は
  照合順1位（`tmdb_id`）にそのまま使える。ただし`tmdb_id`はACFフィールドの実在が
  未確認（未決定事項#5）のため、**正規化タイトル / 原題での検索にフォールバックする分岐を
  追加する必要がある**（`meta_query`ではなく`search`パラメータまたはタイトル完全一致検索）
- `coming_soon_hidden` ACFフラグのチェックは本施策では不要（Coming Soon固有の非表示制御のため）

TMDb API自体（`tmdb_upcoming.py`・`config.py`の`TMDB_API_KEY`/`PROVIDERS`）は
商用契約が未成立のため使わない（7.2参照）。WP照合部分のみを切り出して再利用する。

## 11. 週次まとめの展開（要件2: 毎週通知 + WP CPT + SNS）

`vod_publish` は承認済み行から週次まとめを1つ生成し、3経路に展開する。

### 11.1 Slack通知

週次まとめの内容（作品リスト・WP投稿結果・X投稿テンプレート）を管理者チャンネルへ通知する。
`approval.py` の既存テンプレート送信パターンを流用する。

### 11.2 WP CPT投稿

WordPressのカスタム投稿タイプ（仮スラッグ: `vod_news`。名称は未決定事項#1参照）へ
「今週配信開始のVOD作品まとめ（YYYY年MM月第N週）」をREST APIで投稿する。

**記事構成**（[vod-release-calendar-improvements.md](vod-release-calendar-improvements.md)
1./2./9.節を反映。Google Discover対策として構成順序を統一する）:

```text
編集部おすすめ（Editor's Pick）
  ★評価 + 作品名 + 一言コメント + レビューリンク
  ↓
今週追加作品（サービス別セクション）
  統一フォーマットの「作品カード」を並べる
  ↓
（将来）サービス別まとめへの導線（16.将来拡張）
  ↓
レビューリンク（既出の作品カードに含む）
  ↓
関連記事
```

- **編集部おすすめ**: 記事冒頭に1〜数作品を人間の編集者が選び、★評価・一言コメント・
  レビューリンクを掲載する（AIによる自動選定ではなく編集者が選ぶことに価値を置く。
  選定プロセスの具体化は未決定事項#12）
- **作品カード**（統一フォーマット）: 各作品を「作品タイトル / 配信開始日 / 配信サービス /
  ★評価（レビュー記事があれば） / レビューを読む / 作品詳細を見る」の形式で統一する。
  レビュー記事が無い作品は「レビューを読む」リンクを省略する
- あらすじ等の外部表現は含めない（事実情報 + 自サイトコンテンツへのリンクのみ）
- 初期MVPでは**下書き（`status=draft`）で投稿**し、人間が確認して公開する。
  運用が安定したら `publish` に切り替える
- 認証は既存の WordPress Application Password（`vod_bot` と同じ方式）

**WordPress側で必要な対応（このリポジトリ外）**:

```php
// functions.php（または /inc/ 配下）に CPT を登録
register_post_type( 'vod_news', [
    'label'        => 'VOD配信ニュース',
    'public'       => true,
    'show_in_rest' => true,   // REST API経由の投稿に必須
    'supports'     => [ 'title', 'editor', 'excerpt', 'thumbnail' ],
    'has_archive'  => true,
    'rewrite'      => [ 'slug' => 'vod-news' ],
] );
```

### 11.3 X投稿案（SNS→サイト流入）

x-news-botのスレッドまとめ方式を流用し、**自動投稿はせず**Slackへテンプレートを送る
（人間が手動でXに連投する。x-news-bot仕様書4.4と同じ運用）。

構成:

```text
① 今週配信開始のVOD作品まとめ

Netflix
・作品A（7/28〜）
・作品B（7/30〜）

U-NEXT
・作品C（7/29〜）

② 各作品の詳細・レビューはこちら
{WP CPT記事のURL}
```

- リプライ（最終パーツ）のURLは**外部媒体ではなくWP CPT記事のURL**とする。
  x-news-bot仕様書4.7の「ニュースの転載ではなく自サイトへの送客を主目的とする」方針の実践
- SNS優先度Sの作品は、週次まとめとは別に個別投稿案も生成する（theater仕様11.2と同型）
- 見出し文の生成が必要な場合は `compose.compose_headline()` のトーン制約
  （事実ベース・誇張禁止）に従う

## 12. 実装ファイル案

```text
news_bot/
├── discover_vod.py         # AI Web検索によるVOD配信開始作品の発見（discover_theater.py と同型）
├── fetch_vod_x.py          # 公式Xアカウントのポスト取得（fetch_x.py 流用、生テキストのまま返す）
├── extract_vod.py          # AI統合レイヤー（7.5）: X生テキストの構造化抽出 + AI Web検索結果との
│                            #   重複マージ（merge_all()）。ai_clients.py を再利用、新規APIキー不要
├── prompts/
│   └── vod_extract_system_prompt.md  # X抽出用system prompt（cache_control対象）
├── vod_calendar.py         # 週範囲計算・正規化・重複キー生成（theater_calendar.py を流用/共通化）
├── compose_vod.py          # 週次まとめ本文（WP用HTML）・Xスレッド案の生成
├── wp_client.py            # WP REST API クライアント（CPT投稿・既存記事照合。theaterと共用。
│                            #   照合部分は coming-soon/enrich_events.py の
│                            #   fetch_wp_post_by_tmdb_id()/build_auth() を土台に
│                            #   タイトル検索フォールバックを追加して移植、10.1参照）
├── sheets.py               # 「VOD情報源」「VOD配信予定」シート対応を追加
├── approval.py             # 発見結果の確認依頼・週次まとめ通知を追加
└── main.py                 # vod_discover / vod_publish サブコマンド追加

.github/workflows/
└── vod-calendar.yml        # 木曜=vod_discover / 月曜=vod_publish（cron 2本）
```

## 13. 環境変数

既存のGoogle Sheets / Slack / AI関連環境変数（`GOOGLE_SHEETS_*` / `SLACK_WEBHOOK_URL` /
`ANTHROPIC_API_KEY` / `OPENAI_API_KEY`）を流用する。

追加候補:

| 変数名 | 用途 | 必須 |
|---|---|---|
| `VOD_NEWS_WP_API_BASE` | WP REST API ベースURL | ○ |
| `VOD_NEWS_WP_USER` | WP Application Password ユーザー名 | ○ |
| `VOD_NEWS_WP_APP_PASSWORD` | WP Application Password | ○ |
| `VOD_NEWS_CPT_SLUG` | CPTのRESTスラッグ（既定 `vod_news`） | 任意 |
| `VOD_NEWS_WP_STATUS` | 投稿ステータス（既定 `draft`） | 任意 |

## 14. MVPスコープ

- [ ] 「VOD情報源」「VOD配信予定」シートを自動作成する
- [ ] 翌週月曜〜日曜の対象期間を計算する
- [ ] 公式Xアカウント（取得方式=x）のポストを取得する（生テキストのまま）
- [ ] AI Web検索（Claude/OpenAI併用）で対象週の配信開始作品を発見する
- [ ] AI統合レイヤー（`extract_vod.py`）でXポストを構造化抽出し、Web検索結果と重複マージする
- [ ] `配信開始日 + サービス + 正規化タイトル` で重複判定し、承認待ちで保存する
- [ ] 発見結果の確認依頼をSlackに通知する
- [ ] 承認済み行から週次まとめを生成する（`vod_publish`）
- [ ] WP CPTへ下書き投稿する（編集部おすすめ + 統一フォーマットの作品カードを含む記事構成、11.2）
- [ ] Xスレッド投稿案をSlackへ送信する（手動投稿）
- [ ] GitHub Actions cron（木曜・月曜）を設定する

## 15. 未決定事項（着手前に確認）

| # | 項目 | 内容 |
|---|---|---|
| 1 | CPTスラッグ・WP側登録 | `vod_news`（仮）で良いか、または[vod-release-calendar-improvements.md](vod-release-calendar-improvements.md)7.節が提案する`vod_calendar`/`vod_release`/`vod_schedule`のいずれかに変更するか。本CPTは速報ニュースではなく配信予定・配信情報のアーカイブとして使われるため、後者の名称の方が実態に合うという指摘がある。WordPress側のCPT登録（11.2のPHP）とApplication Password発行が前提 |
| 2 | 承認フローの具体化 | theater側の未決定事項#1と共通。承認列のチェックボックス方式か、投稿状態列の手動書き換えか。将来的にはSlack通知に承認ボタンを設置し、Google Sheets更新→WP投稿までワンクリックで完結させる方式も検討する（[vod-release-calendar-improvements.md](vod-release-calendar-improvements.md)8.節） |
| 3 | 対象サービスの範囲 | 初期6サービス（6.）で良いか。Apple TV / YouTube / Crunchyrollを含めるか |
| 4 | CPT記事の公開運用 | 下書き→人間公開の運用をいつまで続けるか。テーマ側のCPTアーカイブ・単体テンプレートの用意 |
| 5 | `tmdb_id` ACFフィールドの実在確認 | coming-soon-pipeline / theater と共通の未決定事項。照合精度に影響 |
| 6 | AI発見の精度検証 | theater側#6と同じく、数週回して網羅性・ハルシネーション・日付誤りの頻度を確認してから公開運用に進む |
| 7 | X公式アカウントのハンドル確定 | 登録候補6サービスのハンドル実在確認（[vod-sources-candidates.md](vod-sources-candidates.md) A.節）と「VOD情報源」シートへの登録 |
| 8 | X API読み取りコスト | 試算済み（[vod-sources-candidates.md](vod-sources-candidates.md) A.1節）。1日1回・6アカウントで月額$0.5〜$5程度と見込み、既存news-bot-x予算内。実行頻度（週1 or 日1）は本番実績を見て確定する |
| 9 | ~~U-NEXT / DMM TV 公式サイトの規約確認~~（解決済み） | **U-NEXT・DMM TVとも2026-07-22に人間が規約原文を確認し、他サービスと同様に自動化手段によるアクセス禁止が判明したため両方とも不採用確定**。これで対象6サービスすべての公式サイトスクレイピングが不採用となり、`取得方式=html`は当面登録対象なし。転用予定だった`coming-soon-pipeline.md`の`scrape_official.py`もこれにより組み込めなくなり、同ファイルは`docs/drop/`へ移動した。**サービスごとの一次情報はXが唯一の機械取得ルートとなる**（7.3、[vod-sources-candidates.md](vod-sources-candidates.md) B節参照） |
| 10 | X抽出プロンプトの精度検証 | `extract_vod.py`のX投稿構造化抽出（7.5）は宣伝文・キャンペーン告知等を配信開始情報と誤抽出する可能性がある。theater側#6のAI発見精度検証と合わせて数週回して確認する |
| 11 | AI Web検索（discover_vod.py）のコスト試算 | X抽出（7.5.1、月額$0.2〜$1.8）は試算済みだが、discover_vod.pyのWeb検索（Claude/OpenAI併用）側のコストは未試算。theater-calendarの週次実行実績を参考に別途試算する |
| 12 | 「編集部おすすめ」の選定プロセス | 誰が（担当者）・どの時点で（`vod_publish`実行前）・何を基準に選ぶか未設計。承認済み行の中からSlack上で選択できるUIが必要か、Google Sheetsに選定列を追加する運用で足りるかを検討する（[vod-release-calendar-improvements.md](vod-release-calendar-improvements.md)1.節） |

## 16. 将来拡張

- Coming Soonパイプライン（TMDb商用契約成立時）を取得レイヤーとして合流
- 規約確認済みRSS/公式ソースの「VOD情報源」シートへの追加
- SNS優先度のAI判定（theaterと共通化）
- 配信開始当日の「本日配信開始」個別投稿案
- `vod_bot` の配信状況データとの突合（まとめ記事内の配信状況表示）
- Threads / Bluesky向け文面生成
- 投稿後の反応分析と次週投稿へのフィードバック

**ブランド価値向上関連**（[vod-release-calendar-improvements.md](vod-release-calendar-improvements.md)参照）:

- **サービス別ページ**: Netflix/Prime Video/Disney+/U-NEXT等のカテゴリページを新設し、
  「今週追加作品」「今月追加作品」「人気レビュー」「関連記事」「最新ニュース」を集約する。
  サービス名検索の獲得・カテゴリページとしてのSEO強化を狙う
- **年間アーカイブの自動生成**: 週次記事を蓄積し「YYYY年 {サービス名}追加作品」まとめを
  自動生成する。ロングテールSEO・過去配信作品検索・コンテンツ資産化が狙い
- **ジャンル別まとめページ**: Movieデータベースを利用して「今週追加されたSF映画」
  「今週追加されたアニメ」等を自動生成する
- **作品ページへの逆リンク**: 作品ページ側（`vod_bot`のWP ACF管理下）に「最新配信情報」
  （サービス名・配信開始日）を表示し、ニュース→作品・作品→ニュースの双方向リンクを形成する。
  `vod_bot`側のACFフィールド追加が前提のため本施策単体では完結しない

## 17. 注意点

- 外部サイトの本文・あらすじ・紹介文を転載しない（事実情報のみ保存・掲載する）
- 新しい情報源をシートに登録する前に、必ず人間がブラウザで利用規約を確認する
- 情報源URLを必ず保存し、後から確認できるようにする
- X投稿は初期MVPでは手動運用を維持する
- 週次まとめ記事は「配信情報の羅列」で終わらせず、レビュー記事への内部リンクを必ず入れて
  回遊・流入の受け皿として機能させる
