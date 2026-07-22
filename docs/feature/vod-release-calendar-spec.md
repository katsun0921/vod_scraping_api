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

## 2. 既存施策との関係

| 既存施策 | 関係 |
|---|---|
| [theater-release-calendar-spec.md](theater-release-calendar-spec.md) | **方式の親**。AI発見（`discover_theater.py`）・正規化/重複キー（`theater_calendar.py`）・シート自動作成（`sheets.py`）・承認待ちフローをそのまま流用する |
| [theater-sources-candidates.md](theater-sources-candidates.md) | **規約判断の前例**。TMDb API・PR TIMES企業別RSS・映画.com RSSはいずれもAdSense収益化サイトでの利用が規約に抵触するリスクで撤回済み。本施策でも同じ判断基準を適用する |
| [coming-soon-pipeline.md](coming-soon-pipeline.md) | 同じ「VOD配信予定」を扱うが、TMDb API前提のため**保留状態**（TMDb撤回判断の影響を受ける）。本施策はTMDbに依存しないAI発見方式で先行する。将来TMDb商用契約が成立した場合はComing Soonパイプラインを取得レイヤーとして合流できる |
| [x-news-bot-spec.md](../x-news-bot-spec.md) | **SNS投稿の実行基盤**。スレッドまとめ方式（`compose.compose_headline()` + `compose.pack_thread()` + Slackテンプレート通知→人間が手動投稿）を流用する |
| `vod_bot/weekly_patch.py` | 責務が異なる。weekly_patchは**既存WP投稿の配信状況の更新**（チェッカー巡回）、本施策は**新規に配信開始する作品の発見と発信**。ただしWP REST APIクライアント（`vod_bot/wordpress.py`）の実装パターンは参考にする |

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
   ├─ 取得方式=x  → [公式Xアカウントのポスト取得（fetch_x.py流用）→ AI抽出で構造化]
   └─ 取得方式=ai → [discover_vod.py: AI Web検索（Claude/OpenAI併用）]
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
| 各サービス公式サイトのスクレイピング | **不採用**。Netflix（規約4.6）・Amazon・Disney+・Hulu は自動化手段によるアクセス／データ収集を規約で明示禁止。U-NEXT・DMM TV は規約原文未確認のため人間確認まで保留 |
| TMDb（スクレイピング） | **不採用**。規約でサイトのスクレイピングを明示禁止。公認のプログラムアクセスはAPIのみ |
| TMDb（API） | 商用契約（$149/月〜）成立時のみ将来採用（[theater-sources-candidates.md](theater-sources-candidates.md) A.節の判断を継承） |
| PR TIMES企業別RSS / ニュースメディアRSS | theater施策の判断を継承し不採用・保留 |

### 7.3 必須情報源: VODサービス公式Xアカウント（取得方式=x）

配信開始告知はサービス公式Xアカウントが一次情報であり、X API v2という公認の機械取得ルートが
あるため、**本施策の必須情報源**とする。

- 「VOD情報源」シートに `取得方式=x` で各サービスの公式アカウント（Netflix / Prime Video /
  U-NEXT / Disney+ / Hulu / DMM TV。ハンドルは登録前に人間が実在確認）を登録する
- 取得は既存の `news_bot/fetch_x.py`（Bearer Token・`since_id`キャッシュで新着のみ課金）を流用。
  x-news-bot仕様書4.1レイヤー4「配信サービス」として当初から想定されていた枠の実装にあたる
- ポストは非構造化テキストのため、取得後にAI抽出（タイトル / サービス / 配信開始日 / 配信種別）を
  挟んで「VOD配信予定」シートの候補行に変換する。配信開始日が読み取れないポストは破棄する
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

## 11. 週次まとめの展開（要件2: 毎週通知 + WP CPT + SNS）

`vod_publish` は承認済み行から週次まとめを1つ生成し、3経路に展開する。

### 11.1 Slack通知

週次まとめの内容（作品リスト・WP投稿結果・X投稿テンプレート）を管理者チャンネルへ通知する。
`approval.py` の既存テンプレート送信パターンを流用する。

### 11.2 WP CPT投稿

WordPressのカスタム投稿タイプ（仮スラッグ: `vod_news`）へ
「今週配信開始のVOD作品まとめ（YYYY年MM月第N週）」をREST APIで投稿する。

- 本文は構造化データから生成: サービス別セクション → 作品名（配信開始日・配信種別）→
  レビュー記事があれば内部リンク
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
├── fetch_vod_x.py          # 公式Xアカウントのポスト取得（fetch_x.py 流用）+ AI抽出で構造化
├── vod_calendar.py         # 週範囲計算・正規化・重複キー生成（theater_calendar.py を流用/共通化）
├── compose_vod.py          # 週次まとめ本文（WP用HTML）・Xスレッド案の生成
├── wp_client.py            # WP REST API クライアント（CPT投稿・既存記事照合。theaterと共用）
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
- [ ] 公式Xアカウント（取得方式=x）のポストを取得し、AI抽出で構造化する
- [ ] AI Web検索（Claude/OpenAI併用）で対象週の配信開始作品を発見する
- [ ] `配信開始日 + サービス + 正規化タイトル` で重複判定し、承認待ちで保存する
- [ ] 発見結果の確認依頼をSlackに通知する
- [ ] 承認済み行から週次まとめを生成する（`vod_publish`）
- [ ] WP CPTへ下書き投稿する
- [ ] Xスレッド投稿案をSlackへ送信する（手動投稿）
- [ ] GitHub Actions cron（木曜・月曜）を設定する

## 15. 未決定事項（着手前に確認）

| # | 項目 | 内容 |
|---|---|---|
| 1 | CPTスラッグ・WP側登録 | `vod_news`（仮）で良いか。WordPress側のCPT登録（11.2のPHP）とApplication Password発行が前提 |
| 2 | 承認フローの具体化 | theater側の未決定事項#1と共通。承認列のチェックボックス方式か、投稿状態列の手動書き換えか |
| 3 | 対象サービスの範囲 | 初期6サービス（6.）で良いか。Apple TV / YouTube / Crunchyrollを含めるか |
| 4 | CPT記事の公開運用 | 下書き→人間公開の運用をいつまで続けるか。テーマ側のCPTアーカイブ・単体テンプレートの用意 |
| 5 | `tmdb_id` ACFフィールドの実在確認 | coming-soon-pipeline / theater と共通の未決定事項。照合精度に影響 |
| 6 | AI発見の精度検証 | theater側#6と同じく、数週回して網羅性・ハルシネーション・日付誤りの頻度を確認してから公開運用に進む |
| 7 | X公式アカウントのハンドル確定 | 登録候補6サービスのハンドル実在確認（[vod-sources-candidates.md](vod-sources-candidates.md) A.節）と「VOD情報源」シートへの登録 |
| 8 | X API読み取りコスト | 試算済み（[vod-sources-candidates.md](vod-sources-candidates.md) A.1節）。1日1回・6アカウントで月額$0.5〜$5程度と見込み、既存news-bot-x予算内。実行頻度（週1 or 日1）は本番実績を見て確定する |
| 9 | U-NEXT / DMM TV 公式サイトの規約確認 | 人間がブラウザで規約原文を確認。自動化禁止条項が無ければこの2サービスのみ公式サイト取得（`html`）を再検討できる |

## 16. 将来拡張

- Coming Soonパイプライン（TMDb商用契約成立時）を取得レイヤーとして合流
- 規約確認済みRSS/公式ソースの「VOD情報源」シートへの追加
- SNS優先度のAI判定（theaterと共通化）
- 配信開始当日の「本日配信開始」個別投稿案
- `vod_bot` の配信状況データとの突合（まとめ記事内の配信状況表示）
- Threads / Bluesky向け文面生成
- 投稿後の反応分析と次週投稿へのフィードバック

## 17. 注意点

- 外部サイトの本文・あらすじ・紹介文を転載しない（事実情報のみ保存・掲載する）
- 新しい情報源をシートに登録する前に、必ず人間がブラウザで利用規約を確認する
- 情報源URLを必ず保存し、後から確認できるようにする
- X投稿は初期MVPでは手動運用を維持する
- 週次まとめ記事は「配信情報の羅列」で終わらせず、レビュー記事への内部リンクを必ず入れて
  回遊・流入の受け皿として機能させる
