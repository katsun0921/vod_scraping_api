# news_bot

Katsumascore（映画・アニメ・ドラマレビューメディア）のXニュース通知システム。映画・アニメ関連ニュースをRSSから収集し、Claude APIでランク判定（S/A/B/D）した上で、1回のrunでS/A判定になった記事をまとめて1つのXスレッド（連投）用テンプレートとしてSlackに送信する。投稿は現在手動運用（人間がSlackのテンプレートをコピーして①→②→③…の順にXへ連投する）。

このリポジトリはモノレポ構成で、`vod_bot/`（VOD配信状況スクレイピングAPI）とは独立したサブシステム。依存関係（`requirements.txt`）・実行環境・CIジョブは `news_bot/` 配下で完結し、`vod_bot/` には一切影響しない。データストア（Google Sheets）も `vod_bot/` のWordPressとは別物。

`vod_bot/` と共通化できる汎用コード（レート制御・User-Agent等）は [`../utils/`](../utils/) に置く。`python -m news_bot.main` はリポジトリルートから実行するため追加設定なしで `../utils/` をimportできる。現時点では `news_bot/` から未使用だが、RSS/HTMLスクレイピングの間隔制御が必要になった場合は `utils.rate_limit.RateLimiter` を流用できる。

詳細仕様 → [../docs/x-news-bot-spec.md](../docs/x-news-bot-spec.md)

## 責務

- 登録済みニュースソース（RSS）から映画・アニメ関連ニュースを収集
- URL完全一致による重複チェック（フェーズ1スコープ。タイトル類似度判定はフェーズ2）
- Google Sheets への記事保存
- Claude APIによるS/A/B/D判定
  - 精度比較テストのため、ChatGPT/Grokでも並列に判定させ結果をSheetsに記録できる（`NEWS_BOT_JUDGE_PROVIDERS`）
- 1回のrunでS/A判定になった記事をまとめて1つのXスレッド（連投）用テンプレートを生成し、Slackへ1回だけ送信する（`compose.compose_headline()` + `compose.pack_thread()` + `approval.notify_manual_thread()`）。投稿は人間が手動で行う（自動投稿は行わない）
- 自動投稿（X API v2への投稿、承認リアクションによる承認フロー）は予算状況次第で再開できるようコードは残しているが、現在はパイプラインから呼び出していない

## 実行方式

GitHub Actions（RSS: `.github/workflows/news-bot.yml`、X: `.github/workflows/news-bot-x.yml`）から `python -m news_bot.main`（RSS）/ `python -m news_bot.main x <地域>`（X）を実行する。Cloud Runは使用しない。

cronが有効化されており、`news-bot.yml`は1日1回（0:00 UTC。仕様書 3.は1〜2時間おき推奨だが運用判断で変更）、`news-bot-x.yml`は地域ごとに1日1回（日本 1:00 UTC / アメリカ 13:00 UTC）で自動実行される。Actionsタブからの手動実行（`workflow_dispatch`）も引き続き可能。

ChatGPT/Grokとの精度比較テストのため `NEWS_BOT_JUDGE_PROVIDERS: "claude,openai,grok"` を設定し、3プロバイダーを並列実行している。各プロバイダーの判定結果（rank/reason）は「ニュース取得」シートのClaude/ChatGPT/Grok列にそれぞれ記録される。最終的にどのランクを採用するかは `NEWS_BOT_JUDGE_DECISION`（既定 `primary` = 先頭プロバイダーのランクを採用）で制御し、比較結果を見た上で `majority`（多数決）への切り替えを検討する。本番投入時は `NEWS_BOT_JUDGE_PROVIDERS` を `claude` のみに戻す想定。

投稿は手動運用のため、実行するのは`fetch_cycle()`/`fetch_x_cycle()`のみ：

| 関数 | 処理内容 |
|---|---|
| `fetch_cycle()` | RSS取得 → 重複チェック → 保存 → AI判定 → S/A判定は投稿テンプレートをSlackに送信 |
| `fetch_x_cycle(region)` | 「公式X一覧」の指定地域の有効アカウントから投稿取得 → 上記と同じ処理（重複チェック以降はfetch_cycle()と共通） |
| `process_pending()`（**現在未使用**） | 承認キューを確認し、承認済み分をX投稿。自動投稿を再開する場合に使う（`main.py`の`__main__`でコメントアウト済み） |

## Xポストのニュースソース化

RSSに続く追加ニュースソースとして、公式Xアカウントの投稿を`fetch_x.py`で取得し、`main.py`の`fetch_x_cycle(region)`から通常のRSSパイプライン（重複チェック→AI判定→S/A判定はSlackテンプレート送信）と同じ処理に流す。

- **取得方針**：「公式X一覧」シートの"地域"列（`日本`/`アメリカ`）で取得元を分け、それぞれ1日1回（合計2回/日）のcronで取得する（`fetch_x_cycle("日本")` / `fetch_x_cycle("アメリカ")`）
- **認証**：投稿用のOAuth1.0aキー（`X_API_KEY`等）とは別に、読み取り専用の`X_BEARER_TOKEN`（OAuth2.0 App-Only）を発行して使う
- **コスト**：Pay-Per-Useで投稿の読み取りは$0.005/件。`since_id`（前回取得した最新投稿ID）を「公式X一覧」シートにキャッシュし、次回はそれ以降の新着分のみ取得することで課金対象を抑える（`sheets.get_active_x_accounts()`/`update_x_account_state()`）
- **「公式X一覧」シートの列**：`ID / アカウント名 / Xハンドル / URL / 種別（作品/配給/制作会社/配信サービス/メディア） / 地域 / 有効/無効（チェックボックス） / user_id / since_id / 最終取得日時`（`user_id`・`since_id`は19桁のsnowflake IDのため、Sheets側の数値変換による桁落ちを避けてraw書き込みしている。読み込み側の`get_active_x_accounts()`も`gspread`の自動数値変換を`numericise_ignore`で無効化し、明示的に文字列化している）。「有効/無効」列は**Sheetsのチェックボックスのみ**対応（gspreadが返す `True` / `"TRUE"` を有効扱い）。テキストで"有効"等と入力しても対象にならない

### 実行方法

`.github/workflows/news-bot-x.yml`が地域ごとに1日1回自動実行する（日本 1:00 UTC / アメリカ 13:00 UTC）。手動実行したい場合はActionsタブから`workflow_dispatch`（`region`入力で`日本`/`アメリカ`を選択、既定は`日本`）で`python -m news_bot.main x <地域>`を実行できる。「公式X一覧」シートに登録済みの有効なアカウントを取得し、AI判定・Slack通知まで行う。

cronのschedule実行では`workflow_dispatch`の`region`入力が存在しないため、どちらのcron式（`0 1 * * *` / `0 13 * * *`）が発火したか（`github.event.schedule`）で地域を判定している（workflow内の`REGION`環境変数の式を参照）。

## 情報収集はルーティン方式（サブスクリプション内）

設計 → [../docs/feature/routine-discovery.md](../docs/feature/routine-discovery.md) / プロンプト → [../docs/feature/routine-prompts.md](../docs/feature/routine-prompts.md)

劇場公開・VOD配信の**発見**（Web検索）は、Claude APIを従量課金で呼ぶ方式から、**Claudeのルーティン（スケジュール実行）**へ移行した。ルーティンはClaudeサブスクリプションの範囲で動くため、都度のAPI課金が定額に収まる。

```
ルーティン（週次）→ JSONをコミットしPR作成
    ↓ routine-pr-notify.yml がSlackへレビュー依頼を通知
    ↓ 人間がレビュー（1次承認）※疑わしい行はPR上で削除
    ↓ マージ
routine-import.yml → theater_import / vod_import
    ↓ 重複判定・Katsumascore照合・シート追記（承認待ち）・Slack通知
人間がシートで承認（2次承認）→ 投稿状態=承認済み + SNS優先度を設定
    ↓
theater_publish / vod_publish → WP CPT投稿 + SNS投稿案をSlackへ
```

- **承認は2段階**：PRレビューは「情報が**事実として妥当か**」、シート承認は「**記事に載せるか**」で目的が異なる。PRを通ってもシートには`承認待ち`で入る
- **ルーティンはAIからは設定・起動できない**。Claude Codeの`/schedule`から人間が設定する
- **成果物は固定名**（`routine_data/{theater,vod}_latest.json`）を上書きする。履歴はgitのコミット履歴で追う
- **PR作成通知**：`routine/theater-*` / `routine/vod-*`から成果物JSONを変更するPRが作成されると、`Routine PR Notify`が対応するSlackチャンネルへタイトルとURLを通知する。専用チャンネル未設定時は承認チャンネルへ送る
- **自動マージは行わない**：`GITHUB_TOKEN`によるpushは他のworkflowを起動しないため、Actionsがマージすると`routine-import.yml`が発火せずシート追記もSlack通知も走らない（[routine-discovery.md](../docs/feature/routine-discovery.md)）
- **X抽出はルーティンで代替できない**（X API v2の認証が必要）。`vod_import`内でActionsが実行し、ルーティンのWeb検索結果と統合する
- **`theater_discover` / `vod_discover`（API方式）はコードを残しcronのみ停止**。ルーティンが止まった場合のフォールバックとして手動実行できる。`workflow_dispatch`の既定値は課金の無い`publish`側にしてある
- **過去週の再処理**：Actionsの`Routine Import`と各`Calendar` workflowで`target_start`を指定する。劇場は金曜日、VODは月曜日を`YYYY-MM-DD`で入力し、再取り込み→承認→CPT作成の順に実行する

## 劇場公開カレンダー収集パイプライン

詳細仕様 → [../docs/feature/theater-release-calendar-spec.md](../docs/feature/theater-release-calendar-spec.md)（17.に未実装・未確定事項のTODOをまとめてある）

毎週の劇場公開作品を収集し「劇場公開予定」シートに保存、承認後に週次まとめ記事とSNS投稿案を生成するパイプライン。

- **発見はルーティン方式**（上記）。`theater_import`が成果物JSONを取り込む。フォールバックの`theater_discover`はClaude API（`web_search_20260209`）とOpenAI API（Responses APIの`web_search`）を**併用**する（`discover_theater.py`）。いずれも保存するのは**事実情報のみ**（タイトル・公開日・配給会社名・公式URL）で、あらすじ等の表現はコピーしない
- **人間の承認が必須**：結果は誤り得るため保存時は投稿状態=`承認待ち`。新規保存分は**Slackに親メッセージ+作品ごとのスレッド返信で確認依頼を通知**する（`approval.notify_theater_discovered()`。通知失敗してもシート保存は完了しているためサイクルは失敗しない）。通知先は劇場公開専用チャンネル（`SLACK_THEATER_CHANNEL_ID`、GitHub Secret名`NEWS_BOT_SLACK_THEATER_CHANNEL_ID`。**Botを対象チャンネルに招待しておくこと**）。未設定の場合はニュース通知と同じ承認チャンネルに送る
- **週次まとめの生成（`theater_publish`）**：承認済み行から記事本文とSNS投稿案を作る（`compose_theater.py`）。毎週金曜 07:00 JST（`0 22 * * 4` UTC）に実行。**公開日別**に区切るのがVOD版との違いで、劇場公開は「いつ観に行けるか」が関心事のため。注目作は**SNS優先度=S**の行（劇場シートには「編集部おすすめ」列が無いため）
- **投稿先CPTは`theater_release`**（`vod_release`とは別）。「映画館で観たい」と「家で観たい」は別の検索意図であり、同一CPTに混ぜるとどちらのクエリにも半分ノイズのページを返すことになる。技術的にも`vod_release`が持つ`vod`タクソノミー（配信サービス軸）は劇場公開まとめに付かず、タームが空の記事が混ざる
- **SNS投稿案は3種類**：①Xスレッド（2分割）②Facebook / Threads / Bluesky向け（1投稿完結）③注目作の個別投稿（SNS優先度=S かつレビュー記事がある作品のみ）。**Facebook APIとは連携しない**（Meta開発者アプリの審査とページ権限の取得が週1回の投稿頻度に見合わないため）。Slackに出した完成形テキストを人間が手動投稿する
- **レイヤー1データソース（特定サイトの自動取得）はすべて撤回**：配給会社公式サイト（東宝・東映等）と映画.comRSSは利用規約の複製・転載禁止により除外。**TMDb API**はKatsumascoreのAdSense収益化がPersonal Use申請（非商用・無収益の誓約）に反するため撤回。**PR TIMES企業別RSS**も一般規約第6条④「有償目的で企業コンテンツを利用する行為」の禁止に抵触するリスクが高く撤回（詳細は[theater-sources-candidates.md](../docs/feature/theater-sources-candidates.md)）。`fetch_theater.py`（RSS/TMDb取得）と「劇場情報源」シート巡回の`theater`コマンドはコードとして残っているが、cronからは外した
- **対象期間**：`theater_calendar.week_range()`が実行日から「直近の金曜日〜その翌週木曜日」を計算する（仕様書6.）。**月〜金のどの日に実行しても同じ週を返す**ため、発見とpublishを同じ週に揃えられる（土日は翌週にずれるのでcronは平日に置くこと）
- **重複判定**：「公開日 + 正規化タイトル」（`theater_calendar.normalize_title()` / `dedupe_key()`）の完全一致のみ（仕様書9.）
- **URLからの手動追記（`theater_add`）**：人間が見つけた劇場公開情報のURL（作品公式サイト・ニュース記事等）をActionsタブの`Theater Add URL` workflow（`workflow_dispatch`、GitHubモバイルアプリからも実行可）に入力すると、Claude APIの`web_fetch`ツールでそのページだけを個別取得し、事実情報を抽出してシートに`承認待ち`で追記する（`discover_theater.extract_from_url()` / `main.theater_add_url()`）。週次発見と違い対象期間ではフィルタしない（来月公開の作品も入れられる）。人間が特定した1ページの個別取得のため、撤回した一覧巡回方式とは規約リスクの性質が異なる
- **未実装**：SNS優先度の自動判定（現状は承認時に人間が`S`を手入力）、Googleカレンダー同期、フロント側の`/theater-release/`ページ

## VOD配信情報収集パイプライン

詳細仕様 → [../docs/feature/vod-release-calendar-spec.md](../docs/feature/vod-release-calendar-spec.md)

劇場公開カレンダー収集パイプラインと同じ方式（発見 + Google Sheetsでのデータ管理 + 人間承認）をVODの配信開始情報に応用したもの。収集した配信情報を週次でSlack通知・WordPress CPT投稿・X投稿案生成の3経路に展開し、Katsumascoreへの送客を狙う。

- **取得方式は2種類を統合**：①**VOD公式Xアカウント**（Netflix / Prime Video / U-NEXT / Disney+ / Hulu / DMM TVの6サービス、`fetch_vod_x.py`が`fetch_x.py`を流用）②**Web検索**（ルーティン方式。フォールバックは`discover_vod.py`）。両方をAI統合レイヤー（`extract_vod.py`）で同一スキーマに構造化抽出し、「配信開始日+サービス+正規化タイトル」の重複キー（`vod_calendar.py`）でマージする。両ソースが同じ作品を挙げた場合は情報源=`X+AI検索`となり、承認時の実在確度シグナルになる
- **人間の承認が必須**：保存時は投稿状態=`承認待ち`。ルーティンを水曜に置き、月曜の`vod_publish`まで承認猶予を確保する
- **承認後の展開先（3経路）**：①Slack週次まとめ通知 ②WordPress CPT（`vod_release`、下書き投稿。編集部おすすめ + 統一フォーマットの作品カードで構成、`wp_client.py`） ③X投稿案生成（Slackテンプレート→人間が手動投稿、リプライURLは外部媒体ではなくCPT記事URL）
- **実行方式**：`vod_import`（ルーティン成果物のマージ時）/ `python -m news_bot.main vod_publish`（毎週月曜07:00 JST、承認済み分を展開）
- **規約判断は劇場施策と同一基準を継承**：VOD各社公式サイトのスクレイピングは対象6サービス全てで自動化アクセス禁止が規約上確定しているため不採用。TMDb APIも商用契約未成立のため不採用。サービスごとの一次情報を機械取得できるのは公式Xアカウントのみという位置づけ
- 初期セットアップ時に使っていた補助ツール（WP接続確認`check_wp_connection.py` / 公式Xハンドルの実在確認`verify_x_handles.py` / 「VOD情報源」シートの読み取り確認`check_vod_sources.py`）と対応するworkflowは、パイプラインが本番稼働に入り役割を終えたため削除した。再確認が必要になった場合はgit履歴から復元する

## ディレクトリ構成

```
news_bot/
├── main.py              # fetch_cycle() / fetch_x_cycle() / theater_discover_cycle() /
│                         #   theater_import_cycle() / theater_publish_cycle() / theater_cycle() /
│                         #   vod_discover_cycle() / vod_import_cycle() / vod_publish_cycle() /
│                         #   process_pending() エントリーポイント
├── fetch.py             # RSS取得（feedparser）
├── fetch_x.py           # 公式Xアカウントの投稿取得（RSSに続く第2のニュースソース）
├── import_routine.py    # ルーティン成果物JSONの読み込み（TheaterEntry / VodEntry へ変換）
├── routine_data/        # ルーティンが週次で上書きコミットする成果物（PR経由で更新）
│   ├── theater_latest.json
│   └── vod_latest.json
├── fetch_theater.py     # 劇場公開情報の取得（RSS/TMDb。規約上の理由で現在未使用）
├── discover_theater.py  # AI Web検索（Claude/OpenAI併用）による劇場公開作品の発見（フォールバック）
├── theater_calendar.py  # 対象期間計算・タイトル正規化・重複キー生成 + TheaterEntry 定義（劇場）
├── compose_theater.py   # 劇場公開まとめ本文（WP用HTML）・SNS投稿案の生成（公開日別に区切る）
├── fetch_vod_x.py       # VOD公式Xアカウントの投稿取得（fetch_x.py流用、生テキストのまま返す）
├── discover_vod.py      # AI Web検索によるVOD配信開始作品の発見（フォールバック、discover_theater.pyと同型）
├── extract_vod.py       # AI統合レイヤー：Xポストの構造化抽出 + Web検索結果との重複マージ
├── vod_calendar.py      # 週範囲計算・正規化・重複キー生成 + SERVICES / VodEntry 定義（VOD）
├── compose_vod.py       # VOD週次まとめ本文（WP用HTML）・Xスレッド案の生成（サービス別に区切る）
├── wp_client.py         # WordPress REST APIクライアント（CPT投稿・既存記事照合。theater/vod共用）
├── dedupe.py            # URL完全一致の重複チェック（ニュース記事）
├── judge.py             # S/A/B/D判定（複数AIプロバイダーの並列実行・複数記事のバッチ判定に対応）
├── ai_clients.py        # Claude/ChatGPT/Grokへの個別API呼び出しラッパー
├── compose.py           # 投稿文生成（スレッド見出し生成 + パッキング / 単独投稿版は未使用で保持）
├── approval.py          # Slack通知（スレッドまとめ/劇場確認依頼/VOD週次まとめテンプレート送信）
├── post_x.py            # X API v2投稿（tweepy、現在未使用で保持）
├── sheets.py            # Google Sheets I/O（gspread）
├── prompt_loader.py     # prompts/*.md を読み込むローダー
├── prompts/             # プロンプト本文（Markdown）を専用管理
│   ├── judge_system_prompt.md
│   ├── compose_system_prompt.md
│   ├── thread_headline_system_prompt.md
│   └── vod_extract_system_prompt.md
├── tests/               # ユニットテスト
└── requirements.txt
```

プロンプトは`judge.py` / `compose.py`にハードコードせず、`prompts/*.md`で管理する。judge/compose用のfew-shot例やトーンの調整はコードを触らずMarkdownファイルの編集だけで完結する。

データクラス（`TheaterEntry` / `VodEntry`）と定数（`SERVICES`）は`*_calendar.py`に置き、`discover_*.py`には置かない。**これらはAI SDKに依存しない純粋なデータ定義であり、`discover_*`に置くと、ルーティン成果物を読むだけの`import_routine.py`まで`anthropic`/`openai`のインストールを要求してしまうため。** `discover_*.py`側では後方互換のため再エクスポートしている。

## 必要なアカウント

本番投入前に、以下のアカウント・認証情報を用意する必要がある。

| # | アカウント/サービス | 用途 | 必要な認証情報 | GitHub Secret名 |
|---|---|---|---|---|
| 1 | Google Cloud サービスアカウント（**news_bot専用に新規発行**） | Google Sheets APIを有効化し、news_bot専用のサービスアカウントを発行する | サービスアカウントJSON、対象スプレッドシートをそのサービスアカウントのメールアドレスに共有 | `GOOGLE_SHEETS_CREDENTIALS_JSON`, `GOOGLE_SHEETS_SPREADSHEET_ID` |
| 2 | Anthropic Console アカウント | Claude APIでAI判定・投稿文生成を行う | APIキー | `ANTHROPIC_API_KEY` |
| 3 | OpenAI Platform アカウント（**AI判定の精度比較テスト用**） | ChatGPTでのAI判定（`NEWS_BOT_JUDGE_PROVIDERS`に`openai`を含めた場合のみ） | APIキー | `OPENAI_API_KEY` |
| 4 | xAI Developer アカウント（**AI判定の精度比較テスト用**） | Grokでの AI判定（`NEWS_BOT_JUDGE_PROVIDERS`に`grok`を含めた場合のみ） | APIキー | `GROK_API_KEY` |
| 5 | X (Twitter) Developer アカウント + katsumascore運用アカウント | Xへの投稿（Pay-Per-Use課金の有効化・支出上限設定も必要、仕様書4.6） | App の Consumer Key/Secret、投稿アカウントのAccess Token/Secret（OAuth1.0a、Read and Write権限） | `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_TOKEN_SECRET` |
| 6 | 同上のX Developerアカウント（**Xポストのニュースソース化用**） | 公式Xアカウントの投稿読み取り（`fetch_x.py` / `fetch_x_cycle()`） | 同一App内で発行するOAuth2.0 App-Only Bearer Token | `X_BEARER_TOKEN` |
| 7 | Slackワークスペース + Slack App（Bot） | 承認フロー（S/A判定の通知・リアクション検知）・劇場公開/VOD配信の確認依頼通知・週次まとめとSNS投稿案の通知 | Bot Token（`chat:write` / `reactions:read` スコープ）、承認依頼を投稿するチャンネルのID、劇場公開・VOD専用チャンネルのID（任意。未設定なら承認チャンネルに送る） | `NEWS_BOT_SLACK_BOT_TOKEN`, `NEWS_BOT_SLACK_APPROVAL_CHANNEL_ID`, `NEWS_BOT_SLACK_THEATER_CHANNEL_ID`, `NEWS_BOT_SLACK_VOD_CHANNEL_ID` |
| 8 | WordPress（`vod_bot`と共用のApplication Password） | 週次まとめをCPTへ下書き投稿する（`wp_client.py`）。VODは`vod_release`、劇場は`theater_release`の**別CPT** | WP側で両CPTを登録した上でのApplication Password（CPTごとの発行は不要） | `WP_API_URL`, `WP_USER`, `WP_APP_PASSWORD`, `VOD_NEWS_CPT_SLUG`, `THEATER_NEWS_CPT_SLUG` |
| 9 | Claude サブスクリプション（ルーティン用） | 劇場公開・VOD配信の週次Web検索をサブスクリプション内で実行する（従量課金の代替） | Claude Codeの`/schedule`から人間が設定。**AIからは起動・設定できない** | - |
| 10 | GitHubリポジトリの管理権限 | 上記の認証情報をActions Secretsに登録する | - | - |

> **長期有効な認証情報の運用方針**：`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GROK_API_KEY` / `X_API_*` / `NEWS_BOT_SLACK_BOT_TOKEN`は発行元サービスがWorkload Identity Federation等の短期認証に対応していないため、無期限キーとしてGitHub Actions Secretsで管理する。漏洩の通知・兆候（想定外の使用量急増、GitHubのsecret scanningアラート等）を検知した場合は各サービスのコンソールで即座にRevoke（失効）する。

> Slack Botはワークスペースにインストールし、承認を行うチャンネルに招待（`/invite @bot名`）しておく必要がある。招待し忘れると`chat.postMessage`が失敗する。

> **既存スプレッドシートを使っている場合の注意**：「ニュース取得」シートは既に作成済みだとヘッダー行が自動更新されない（`_ensure_sheets_exist()`はシートが無い場合のみ作成する）。ChatGPT/Grok列を使う場合は、既存シートのヘッダー行末尾に手動で `Claude判定` / `Claude理由` / `ChatGPT判定` / `ChatGPT理由` / `Grok判定` / `Grok理由` を追加しておくこと。

## セットアップ

### 1. 依存関係のインストール

```bash
cd news_bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 環境変数の設定

```bash
cp .env.example .env
# Google Sheetsサービスアカウント / ANTHROPIC_API_KEY（+ 比較テスト時はOPENAI_API_KEY/GROK_API_KEY） / X API資格情報 / Slack Bot Tokenを設定
```

### 3. 実行

```bash
python -m news_bot.main
```

### サブコマンド一覧

| コマンド | 内容 | cron | 従量課金 |
|---|---|---|---|
| （引数なし） | RSS取得→AI判定→Slackスレッド案 | `news-bot.yml` | Claude |
| `x <地域>` | 公式Xアカウント取得→同上 | `news-bot-x.yml` | Claude + X API |
| `theater_import [YYYY-MM-DD]` | ルーティン成果物を取り込み→シート追記→Slack通知。任意引数は対象週の金曜日 | `routine-import.yml`（PRマージ時） | なし |
| `theater_publish [YYYY-MM-DD]` | 承認済み行→WP投稿→SNS投稿案をSlackへ。任意引数は対象週の金曜日 | 毎週金 07:00 JST | なし |
| `theater_add <URL>` | 人間が指定したURLから1件抽出→シート追記 | 手動（`theater-add-url.yml`） | Claude |
| `theater_discover` | AI Web検索で発見（**ルーティンのフォールバック**） | なし（手動のみ） | Claude + OpenAI |
| `vod_import [YYYY-MM-DD]` | ルーティン成果物 + X抽出を統合→シート追記→Slack通知。任意引数は対象週の月曜日 | `routine-import.yml`（PRマージ時） | Claude + X API |
| `vod_publish [YYYY-MM-DD]` | 承認済み行→WP投稿→Xスレッド案をSlackへ。任意引数は対象週の月曜日 | 毎週月 07:00 JST | なし |
| `vod_discover` | AI Web検索 + X抽出で発見（**ルーティンのフォールバック**） | なし（手動のみ） | Claude + OpenAI + X API |
| `theater` | 「劇場情報源」シート巡回（規約上の理由でシート未登録＝実質未使用） | なし | なし |

> `*_discover` はルーティン方式への移行によりcronを停止した。ルーティンが止まった場合の
> フォールバックとして手動実行できるよう残してある。ワークフローの`workflow_dispatch`の
> 既定値は課金の発生しない`*_publish`側にしてあるので、`discover`を実行する場合は明示的に選ぶこと。

### 過去週を再処理する

1. Actionsの`Routine Import`を手動実行し、`command`と`target_start`を指定する
2. Slackで新規行を承認し、`Approval Check`でシートが`承認済み`になったことを確認する
3. 対応する`Theater Calendar`または`VOD Calendar`を手動実行し、同じ`target_start`を指定する

例：劇場の2026-08-14〜2026-08-20は`2026-08-14`、VODの
2026-08-17〜2026-08-23は`2026-08-17`を指定する。CLIからも同じ日付を任意引数として渡せる。

## 実装上の注意（仕様書からの補足）

- Slack通知には **Slack Bot Token**（`chat.postMessage`）を使用する。
- 投稿は手動運用のため、Slackへのスレッドまとめテンプレート送信までがパイプラインの終着点（`approval.notify_manual_thread`）。1回のrunでS/A判定になった記事は個別投稿ではなく1つのXスレッドにまとめる。記事1件だけの単独投稿版（`approval.notify_manual_post` / `compose.compose`）と、承認リアクション（:white_check_mark:=承認 / :x:=取り消し）による自動投稿フロー（`approval.notify_pending` / `approval.resolve` / `process_pending()` / `post_x.post_with_reply`）はいずれもコードを残したまま無効化している。自動化を再開する場合は`main.py`内のコメントを参照。
- 承認キュー・投稿状態をcron実行をまたいで追跡するための内部管理用シート「**承認キュー**」は、自動投稿再開時に備えて仕様書のシート構成（[x-news-bot-spec.md](../docs/x-news-bot-spec.md) 5.）に追加済み。
- タイトル一覧・YouTube Shortsシート（関連タイトル紐付け等）はMVPスコープ外のため未実装。「公式X一覧」は`fetch_x_cycle()`用に実装済み（`sheets.py`の`_AUTO_CREATED_HEADERS`で自動作成対象）。
- Google Sheets/X API/Claude API/Slack Web APIへの実接続はネットワーク制限のある開発環境では未検証。本番投入前に疎通確認が必要。
