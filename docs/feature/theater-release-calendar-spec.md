# 劇場公開カレンダー収集パイプライン 仕様書

作成: 2026-07-17  
対象: `news_bot/`

## 1. 目的

Katsumascore への流入を増やすため、毎週公開される劇場映画情報を収集し、X投稿・Slack確認・Katsumascore作品ページへの導線作成に利用する。

通常のニュース記事は外部メディアURLへの送客になりやすい。一方で「今週公開の映画」は、Katsumascore の作品ページ・レビュー・公開予定まとめへ直接誘導しやすく、毎週のSNS運用テーマとして再利用性が高い。

## 2. ゴール

- 今週公開される劇場映画を週次で収集する
- 作品情報をGoogle Sheetsに構造化して保存する
- Katsumascore内の既存作品ページと照合する
- X投稿用の「今週公開まとめ」「注目作個別投稿案」をSlackに送る
- 未登録作品をKatsumascore側の追加候補として可視化する

## 3. 非ゴール

- 初期MVPでは劇場チケット販売情報・上映館一覧・上映時間は扱わない
- 初期MVPではXへの自動投稿は行わない
- 初期MVPでは全配給会社・全ミニシアター作品の完全網羅を保証しない
- 初期MVPでは外部サイトから長文の紹介文を転載しない

## 4. 全体フロー

```text
[劇場公開情報ソース]
        ↓
[fetch_theater.py]
        ↓
[正規化・重複判定]
        ↓
[Google Sheets: 劇場公開予定]
        ↓
[Katsumascore投稿照合]
        ↓
[SNS優先度判定]
        ↓
[投稿案生成]
        ↓
[Slackへ手動投稿テンプレート通知]
```

## 5. 実行方式

`news_bot.main` のサブコマンド:

```bash
python -m news_bot.main theater_import        # ルーティン成果物を取り込み（承認待ちで保存）
python -m news_bot.main theater_resolve_approvals  # Slack承認スタンプを反映（ワンクリック承認）
python -m news_bot.main theater_publish       # 承認済み行から週次まとめを展開
python -m news_bot.main theater_add <URL>     # 人間が見つけたURLから1件追記
python -m news_bot.main theater_discover      # [フォールバック] AI Web検索で発見（従量課金）
python -m news_bot.main theater               # [実質未使用] 「劇場情報源」シート巡回
```

### 現行: ルーティン方式

**AI Web検索部分は Claude のルーティンへ移行した**（[routine-discovery.md](./routine-discovery.md)）。
Claude APIを従量課金で呼ぶ代わりに、ルーティンが週次で調査してPRを作り、人間のレビュー（1次承認）を
経てマージされたものを取り込む。

| ジョブ | タイミング | 内容 |
|---|---|---|
| ルーティン | 毎週金曜 09:00 JST | 対象週の公開作品を調査し、`routine_data/theater_latest.json` を更新してPR作成 |
| `theater_import` | PRマージ時（`routine-import.yml`） | 成果物JSONを取り込み、Katsumascore照合の上で承認待ちで保存。Slackに確認依頼を通知 |
| `theater_resolve_approvals` | 毎時（`approval-check.yml`） | 承認待ち行のSlack承認スタンプを確認し、投稿状態を承認済みへ自動更新（8.1） |
| `theater_publish` | 毎週金曜 07:00 JST | 対象週の承認済み行から週次まとめを生成し、WP CPT投稿・SNS投稿案をSlackへ送信 |

`theater_publish` を金曜にしているのは2つの理由による。

1. **日本の劇場公開は金曜が基準日**のため、公開初日の朝に記事が出るのが読者にとって最も有用
2. `week_range()` は**土日に実行すると翌週にずれる**。月〜金なら同じ週（直近の金曜起点）を
   返すため、発見と publish を同じ週に揃えられる

### フォールバック: API方式

`theater_discover`（Claude/OpenAI併用のAI Web検索）は**cronを停止したがコードは残している**。
ルーティンが止まった場合に `theater-calendar.yml` の `workflow_dispatch` から手動実行できる。
従量課金が発生するため、`workflow_dispatch` の既定値は `theater_publish` にしてある。

## 6. 取得対象期間

基準日は実行日とする。

初期MVP:

- 対象開始日: 直近の金曜日
- 対象終了日: その翌週木曜日

例:

- 月曜実行の場合、その週の金曜〜翌週木曜を対象にする
- 金曜実行の場合、当日金曜〜翌週木曜を対象にする

将来拡張:

- 2週間先、1か月先の公開予定も取得し、事前告知投稿に利用する
- 公開延期・公開日変更を差分検知する

## 7. 取得元方針

取得元は3層で考える。

### レイヤー1: カレンダー系ソース

週ごとの公開作品一覧を取得する。初期MVPではこのレイヤーを主軸にする。

候補:

- 映画情報サイトの公開予定一覧
- 配給会社横断の公開カレンダー
- 公式にRSSや構造化データが提供されている一覧

利用条件:

- 利用規約を確認する
- RSSまたは構造化データがある場合はそれを優先する
- HTMLスクレイピングは必要最小限にする

### レイヤー2: 一次ソース

作品公式サイト、配給会社公式サイト、公式Xから情報を補完する。

用途:

- 公式URL
- 予告URL
- 公開日変更
- 配給会社
- 特別上映・先行上映などの注意情報

### レイヤー3: 補完ソース

TMDb、YouTube、既存Katsumascore投稿などでメタ情報を補完する。

用途:

- 原題
- ジャンル
- ポスター
- 概要
- 既存作品ページとの照合

## 8. Google Sheets

新しいワークシート `劇場公開予定` を追加する。

ヘッダー:

```text
取得日時
公開日
タイトル
原題
カテゴリ
国
配給
公式URL
予告URL
情報源
Katsumascore URL
WP post_id
SNS優先度(S/A/B/C)
投稿状態
重複キー
メモ
SlackチャンネルID
Slackメッセージts
```

`SlackチャンネルID`/`Slackメッセージts`はワンクリック承認用の内部管理列（8.1参照）。
「承認キュー」シートの同名列と同じ役割で、発見通知の作品ごとのスレッド返信を指す。

### 8.1 ワンクリック承認（Slack承認スタンプ）

VOD側（[vod-release-calendar-spec.md](vod-release-calendar-spec.md) 8.1）と同型の仕組みを
劇場公開にも用意する。

`theater_import`/`theater_discover` の発見通知（`approval.notify_theater_discovered`）は、
親メッセージ1件+作品ごとのスレッド返信で送る。各スレッド返信の `channel`/`ts` を
「劇場公開予定」シートの `SlackチャンネルID`/`Slackメッセージts` 列に記録し
（`sheets.update_theater_item_slack_ref`）、そのメッセージに人間が :white_check_mark: で
反応すると、`theater_resolve_approvals`（`news_bot.main.theater_resolve_approvals_cycle`、
`.github/workflows/approval-check.yml`で毎時実行）が `reactions.get` でリアクションを確認し、
投稿状態を`承認待ち`→`承認済み`へ自動更新する。

- リアクション判定（`approval.resolve_approvals`）はVODと共用する。両シートで重複キーと
  Slack参照の列名が同じで、判定はリアクションの有無だけのため、実装を分ける理由がない
- キャンセル絵文字は見ない。投稿状態は`承認待ち`/`承認済み`/`投稿済み`の3値運用
  （CLAUDE.md）で「却下」に相当する状態が無く、不要な行はシート上で直接削除する運用のため
- シート上での直接書き換え（`投稿状態`列を手動で`承認済み`にする）も引き続き有効
- **既存シートには列が無いため、手動で2列を追加する必要がある。** 列が無いままだと
  `get_pending_theater_items_with_slack_ref()`が常に空を返し、承認スタンプが反映されない

### 投稿状態

| 値 | 意味 |
|---|---|
| `未判定` | 取得直後 |
| `投稿候補` | SNS優先度が高く、投稿対象 |
| `保存のみ` | 投稿しないが記録する |
| `投稿案送信済み` | Slackへ投稿テンプレート送信済み |
| `手動投稿済み` | 人間がXに投稿済み |
| `除外` | 対象外 |

### SNS優先度

| ランク | 意味 | 扱い |
|---|---|---|
| `S` | 大型新作、話題作、Katsumascore内導線が強い作品 | 週次まとめ + 個別投稿 |
| `A` | ジャンル読者に刺さる注目作 | 週次まとめ対象 |
| `B` | 記録はするが投稿優先度は低い | 保存のみ |
| `C` | 対象外・情報不足・重複 | 除外または保存のみ |

## 9. 重複判定

初期MVPでは以下のキーで重複判定する。

```text
公開日 + 正規化タイトル
```

正規化ルール:

- 前後空白を削除
- 全角・半角スペースを統一
- 記号の揺れを軽く吸収
- 副題区切りの一部表記揺れを吸収

将来拡張:

- `tmdb_id`
- 映画情報サイト固有ID
- 公式URL
- タイトル類似度判定

## 10. Katsumascore照合

初期MVPでは WordPress REST API で既存投稿を検索する。

照合候補:

1. `tmdb_id` が取得できる場合は `tmdb_id`
2. 正規化タイトル
3. 原題
4. 公開年 + タイトル

照合できた場合:

- `WP post_id` を保存する
- `Katsumascore URL` を保存する
- 投稿案のリプライURLに Katsumascore URL を使う

照合できなかった場合:

- `WP post_id` は空欄
- `投稿状態` は `保存のみ` または `未判定`
- 必要に応じて作品追加候補として扱う

## 11. 投稿案

初期MVPでは2種類の投稿案を生成する。

### 11.1 今週公開まとめ

目的:

- 毎週の定番投稿にする
- Katsumascoreへの入口を作る

例:

```text
今週公開の注目映画

・作品A
・作品B
・作品C

週末に観るならどれ？
```

リプライ:

```text
各作品のレビュー・評価はこちら
{Katsumascore URL または公開予定まとめURL}
```

### 11.2 注目作個別投稿

対象:

- SNS優先度 `S`
- Katsumascore URL がある作品

例:

```text
今週公開の注目作。
〇〇がいよいよ劇場公開。

観る前に押さえておきたいポイントは...
```

リプライ:

```text
レビュー・評価はこちら
{Katsumascore URL}
```

## 12. 実装ファイル案

```text
news_bot/
├── fetch_theater.py        # 劇場公開情報の取得（RSS/TMDb。規約上の理由で現在未使用）
├── discover_theater.py     # AI Web検索による発見（フォールバック）+ URLからの個別抽出
├── theater_calendar.py     # 週範囲計算・正規化・重複キー生成 + TheaterEntry のデータ定義
│                            #   （AI SDK非依存にするため fetch_theater.py から移動。
│                            #     fetch_theater.py 側で再エクスポート）
├── import_routine.py       # ルーティン成果物JSONの読み込み（VODと共用）
├── routine_data/
│   └── theater_latest.json # ルーティンが週次で上書きコミットする成果物
├── compose_theater.py      # 週次まとめ本文（WP用HTML）・SNS投稿案生成
├── wp_client.py            # WP REST API クライアント（CPT投稿・既存記事照合。VODと共用）
├── main.py                 # theater_import / theater_publish / theater_discover /
│                            #   theater_add / theater サブコマンド
├── sheets.py               # 劇場公開予定シート対応
└── approval.py             # 発見結果の確認依頼・週次まとめ通知

.github/workflows/
├── routine-import.yml      # ルーティンPRのマージを検知して theater_import を実行
├── theater-calendar.yml    # 金曜=theater_publish（cron 1本）。theater_discover は手動のみ
├── theater-add-url.yml     # URLからの手動追記（workflow_dispatch）
└── approval-check.yml      # 毎時=theater_resolve_approvals（VODと共用。8.1）
```

## 13. 環境変数

既存のGoogle Sheets / Slack / AI関連環境変数を流用する。

追加分:

| 変数名 | 用途 | 必須 |
|---|---|---|
| `WP_API_URL` | WP REST API ベースURL（VOD・`vod_bot`と共用） | ○ |
| `WP_USER` | WP Application Password ユーザー名（同上） | ○ |
| `WP_APP_PASSWORD` | WP Application Password（同上） | ○ |
| `THEATER_NEWS_CPT_SLUG` | CPTのRESTスラッグ（既定 `theater_release`） | 任意 |
| `THEATER_NEWS_WP_STATUS` | 投稿ステータス（既定 `draft`） | 任意 |
| `SLACK_THEATER_CHANNEL_ID` | 劇場公開通知の専用チャンネル（未設定なら承認チャンネル） | 任意 |

> **未登録のGitHub Secretは空文字で渡る**ため、`os.environ.get(k, default)` では既定値に
> フォールバックしない。`wp_client.create_post()` は空文字を未設定として扱うよう修正済み
> （2026-08-05）。

投稿先CPTは VOD（`vod_release`）とは**別**にした。理由は
[THEATER_RELEASE_CPT_SPEC.md](../../../katsumascore_wordpress_theme/docs/feature/THEATER_RELEASE_CPT_SPEC.md)
を参照（「映画館で観たい」と「家で観たい」は別の検索意図であるため）。
個別投稿のスラッグは対象週の開始日を使い、`theater-release-yyyy-mm-dd` とする。

## 14. MVPスコープ

- [x] `劇場公開予定` シートを自動作成する
- [x] 今週金曜〜翌週木曜の対象期間を計算する
- [x] 劇場公開情報を取得する（ルーティン方式 + AI Web検索フォールバック + URL手動追記）
- [x] タイトル・公開日・公式URL・情報源を保存する
- [x] `公開日 + 正規化タイトル` で重複判定する
- [x] Katsumascore照合（正規化タイトルの完全一致。一致しなければ空欄＝誤リンクを作らない）
- [ ] SNS優先度をAIまたはルールで判定する（**現状は承認時に人間が`S`を手入力**）
- [x] 週次まとめ投稿案をSlackに送る（X / Facebook等 / 注目作個別の3種類）
- [x] WP CPT（`theater_release`）へ下書き投稿する

## 15. 将来拡張

- 複数ソースのマージ
- 公開日変更の差分検知
- TMDb補完
- Katsumascore未登録作品の追加候補通知
- 作品公式Xの自動登録候補化
- 公開週後の「観た人向けレビュー誘導」投稿
- 週末前の再投稿
- Threads / Bluesky / Mastodon向け文面生成
- 投稿後の反応分析と次週投稿へのフィードバック

## 16. 注意点

- 外部サイトの本文・紹介文を長く転載しない
- スクレイピング対象は利用規約を確認する
- 取得元URLを必ず保存し、後から確認できるようにする
- X投稿は初期MVPでは手動運用を維持する
- Katsumascore URLがない作品だけで投稿を作ると送客効率が落ちるため、まとめページなどの受け皿を用意する

## 17. TODO（未実装・未確定事項）

`news_bot/` に着手した現時点の実装状況と、残タスクを記録する。

### 実装済み（発見〜保存の骨組みのみ）

- `theater_calendar.py`: 対象期間計算（6.）・タイトル正規化/重複キー生成（9.）
- `sheets.py`: 「劇場公開予定」シート（8.のヘッダー）の自動作成・追記
- `discover_theater.py`: **AI Web検索（Claude/OpenAI併用）による劇場公開作品の発見**（現行の
  レイヤー1相当。事実情報のみを収集し、両AIの結果を重複キーでマージする。下記「AI発見方式への
  転換」参照）
- `fetch_theater.py`: RSS取得（feedparser）・TMDb discover API取得（`取得方式=tmdb`）。
  **規約上の理由でいずれも現在未使用**（コードは残存）
- `main.py`: `theater_discover_cycle()` — AI発見→対象期間フィルタ→重複チェック→保存
  （投稿状態=`承認待ち`）。旧`theater_cycle()`（劇場情報源シート巡回）も残存するが実質未使用
- `compose_theater.py`: 週次まとめ本文（WP用HTML）・SNS投稿案の生成（11.）。
  公開日別に区切り、SNS優先度=`S`を注目作として冒頭に置く。X（スレッド2分割）/
  Facebook等（1投稿完結）/ 注目作の個別投稿 の3種類を生成する
- `main.py`: `theater_publish_cycle()` — 承認済み行→まとめ生成→`theater_release` CPTへ
  下書き投稿→SNS投稿案をSlack送信→投稿状態を`投稿済み`へ更新（11.）
- `sheets.py`: `get_approved_theater_items()` / `update_theater_item_status()`
- `approval.py`: `notify_theater_weekly_summary()` — 親メッセージ（WP投稿結果+Xスレッド）+
  スレッド返信（他SNS用・注目作個別）
- `.github/workflows/theater-calendar.yml`: 毎週月曜 06:00 JST に`theater_discover`、
  毎週金曜 07:00 JST に`theater_publish`を実行（`ANTHROPIC_API_KEY`/`OPENAI_API_KEY`を渡す）

### 投稿状態の実際の値

仕様書8.の投稿状態テーブル（`未判定`/`投稿候補`/`保存のみ`/`投稿案送信済み`/`手動投稿済み`/`除外`）は
初期設計時のもので、**実装では VOD 側と揃えた3値で運用している**。

| 値 | 意味 | 誰が設定するか |
|---|---|---|
| `承認待ち` | AI発見直後。人間の確認前 | `theater_discover_cycle()` |
| `承認済み` | 人間が実在・公開日を確認した | 人間（シート上で手動） |
| `投稿済み` | 週次まとめに掲載しWP投稿済み | `theater_publish_cycle()` |

`theater_publish` の対象は `承認済み` のみ。SNS優先度=`C` の行は承認済みでも本文・投稿案から
除外する（対象外・情報不足・重複のため）。

### 設計変更: レイヤー1データソースの決め方

仕様書7.時点ではレイヤー1の実際の取得元（サイト/API）が未確定だった。着手にあたり、
**取得元をコードにハードコードせず「劇場情報源」シートで管理する**方式に変更した
（既存の「RSS一覧」と同じ考え方）。

- シート列: `ID / 名称 / URL / 取得方式 / レイヤー / 有効/無効 / 規約確認済み / メモ`
- 「有効/無効」チェックボックスと「規約確認済み」="済" が揃った行のみ取得対象になる
- `取得方式` は `rss`（feedparser）と `tmdb`（TMDb discover API）に対応。html方式は未実装
- 配給会社（東宝・東映・松竹等）公式サイトはRSS/構造化データが無く、確認できた範囲では
  利用規約で無断複製・転載を明示的に禁止していたため、レイヤー1の候補から除外した
  （調査詳細は [theater-sources-candidates.md](theater-sources-candidates.md) E.節）

### データソース決定: TMDb API採用 → 撤回（2026-07-17）

一時、**TMDb discover API**（`/discover/movie`, `region=JP`, `with_release_type=2|3`）を
採用し`fetch_theater.py`に実装した（コードは残存、`取得方式=tmdb`として呼び出し可能）。

**撤回理由**: KatsumascoreはGoogle AdSenseを掲載しており現在収益を得ている。TMDb APIの
「Personal Use」申請フォームには「non-commercial and generates no revenue」
「will not use in any business or commercial environment」という明示的な誓約があり、
虚偽申告には「immediate termination」「revocation」「potential reporting to TMDB」が
明記されている。AdSense掲載はTMDBの定義する商用利用（広告表示によるサイトの収益化）に
該当するため、無償利用前提での採用を撤回した。

**今後の選択肢**（詳細は [theater-sources-candidates.md](theater-sources-candidates.md) A.節）:
1. TMDB公式へ商用利用として問い合わせ、Commercial APIプラン（$149/月〜）を契約する
2. レイヤー1データソースをTMDb以外の候補（RSS/HTML一覧/PR TIMES企業別RSS）から再選定する
   （**着手したが再度撤回。下記参照**）

### レイヤー1データソース再選定 → PR TIMES企業別RSSも撤回（2026-07-20）

TMDb以外の候補（[theater-sources-candidates.md](theater-sources-candidates.md) B/C/E節）を再調査した。

- 映画.com新着情報RSSは利用規約で複製・転載・公衆送信を明示的に禁止しており**見送り確定**
- シネマトゥデイRSS・HTML一覧（MOVIE WALKER PRESS等）は規約未確認またはスクレイパー実装コストが
  高く**保留**
- **PR TIMES企業別RSS**（配給会社が自ら配信するプレスリリースのRSS/RDF、
  `https://prtimes.jp/companyrdf.php?company_id={ID}`）を東宝・東映・松竹・ワーナー ブラザース
  ジャパン・ディズニー・ギャガ・KADOKAWAのcompany_id確認の上で最有力候補としたが、人間から
  共有されたPR TIMES利用規約全文（PDF）を確認した結果、**撤回**した。一般規約第6条④
  「有償目的で企業コンテンツを利用する行為」の禁止規定に、AdSense収益化しているKatsumascore
  での利用が抵触するリスクが高いと判断したため（TMDbの件と同型の問題。詳細は
  [theater-sources-candidates.md](theater-sources-candidates.md) E.節）

### AI発見方式への転換（2026-07-20）

特定サイトの自動取得がすべて撤回されたことを受け、レイヤー1相当の発見ステップを
**AIのWeb検索（Claude API `web_search_20260209` + OpenAI Responses API `web_search` の併用）**
に転換した（`discover_theater.py` / `main.theater_discover_cycle()`）。

- **規約上の整理**: AIに対象週の公開作品を調べさせ、**事実情報のみ**（タイトル・公開日・
  配給会社名・公式URL）を構造化して保存する。事実は著作権の保護対象ではなく、特定サービスの
  フィード/APIを機械巡回してコンテンツを取り込む構造でもないため、撤回した各方式とはリスクの
  性質が異なる。あらすじ・紹介文などの表現は保存しない
- **人間の承認**: AI検索結果は誤り得るため、保存時は投稿状態=`承認待ち`。人間がシートを確認・
  修正した上で承認するまで下流（週次サマリー・Slack/WP投稿）には流さない。両AIが同じ作品を
  挙げた場合は情報源=`AI検索(claude+openai)`となり、承認時の実在確度シグナルとして使える
- **今後の拡張**: 「劇場公開予定」シートの確定行をGoogleカレンダーへ自動同期する
  （シート=データストア、カレンダー=管理者向けビューの併用構成）

### 未実装・未確定事項（優先度順）

| # | 項目 | 内容 |
|---|---|---|
| 1 | ~~承認フローの具体化~~（運用方針確定） | **投稿状態列の手動書き換え、またはSlackスレッドへの:white_check_mark:リアクション（ワンクリック承認、8.1参照）の二本立てとする。** Slackボタン（Interactivity）は常時起動サーバーが要るため見送り、X投稿承認フローと同じリアクション+ポーリング方式を採用した（VOD側の未決定事項#2と共通） |
| 2 | Googleカレンダー同期 | 確定行をGoogle Calendar APIでイベント化する（サービスアカウントにスコープ追加・重複防止はextendedPropertiesに重複キーを保持）。未実装 |
| 2b | URL入力ツールの改善 | 現状はActionsタブの`Theater Add URL` workflow（`workflow_dispatch`）でURLを入力する。Slackチャンネル巡回（投稿されたURLをcronで拾う。要`channels:history`スコープ+since管理）やGoogle Form経由は将来の選択肢。いずれも`theater_add_url()`をそのまま流用できる |
| 3 | `tmdb_id` ACFフィールドの実在確認 | `docs/drop/coming-soon-pipeline.md`（2026-07-22に`docs/drop/`へ移動・凍結）の未決定事項#2と共通。10.の照合優先順位1位が前提にしている |
| 4 | Katsumascore照合（10.） | **`theater_publish`の効果を直接制約している最優先項目。** `news_bot/wp_client.py` の `find_post_by_title()`（VOD側で実装済み・正規化タイトル完全一致）を `theater_discover_cycle()` から呼べば流用できる。現状 `Katsumascore URL` は常に空欄のため、週次まとめ記事から自サイトのレビューへの内部リンクが1本も張られず、注目作の個別投稿案も0件になる（`build_featured_posts()` はURLがある作品のみ対象） |
| 5 | SNS優先度(S/A/B/C)判定（8./9.） | AI判定かルールベースか未決定。**現状は常に空欄で保存されるため、`compose_theater.featured_items()` が常に0件を返し、記事冒頭の注目作セクションが出ない。** 当面は人間がシート上で`S`を手入力する運用で回せる（承認作業と同時に行える） |
| 6 | AI発見の精度検証 | `theater_discover_cycle()`を実データで数週回し、取りこぼし（網羅性）・実在しない作品（ハルシネーション）・公開日誤りの頻度を確認する。プロンプトや`_MAX_WEB_SEARCHES`の調整はこの結果を見て行う |
| ~~7~~ | ~~`compose_theater.py`（11.）~~ | **実装済み**（2026-08-04）。公開日別セクション+注目作。X/Facebook等/個別投稿の3種類 |
| ~~8~~ | ~~Slack通知（11.）~~ | **実装済み**（2026-08-04）。`approval.notify_theater_weekly_summary()` |
| 10 | Facebook API連携 | 現状は未対応。`build_social_post()` が生成した完成形テキストをSlackから人間が手動投稿する。API連携にはMeta開発者アプリの審査（`pages_manage_posts`権限）とFacebookページの管理者権限が必要で、週1回の投稿頻度に対して運用コストが見合わないため見送った。Threads / Bluesky も同じテキストを流用できる |
| 11 | フロント側の`/theater-release/`実装 | `katsumascore-front` に単体ページ・アーカイブ・サイトマップ登録が必要。`feature/vod-release-page`ブランチの`/vod-release/`実装を流用できる。**未実装の間はWP投稿しても閲覧できるURLが無い**ため、`THEATER_NEWS_WP_STATUS=draft`（既定値）のまま運用すること |
| 9 | 旧取得方式の再開条件 | `fetch_theater.py`（RSS/TMDb）と「劇場情報源」シート巡回は、PR TIMESパートナーメディア提携またはTMDb商用ライセンス契約が成立した場合のみ再開する（[theater-sources-candidates.md](theater-sources-candidates.md)） |
