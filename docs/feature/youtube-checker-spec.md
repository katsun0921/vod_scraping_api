# YoutubeChecker 仕様書

実装: [`vod_bot/checkers/youtube.py`](../../vod_bot/checkers/youtube.py)
テスト: [`vod_bot/tests/test_youtube_free_patch.py`](../../vod_bot/tests/test_youtube_free_patch.py)

YouTube の watch ページを取得し、その作品が**いま無料で観られるか**を判定する
チェッカー。`vod_bot/checkers/` の他のチェッカーと同じ `check(url) -> dict`
インターフェースを持つが、判定の意味と難易度が他サービスと違うため独立した
仕様書を置く。

---

## 1. なぜ専用の仕様書が要るのか

### 1.1 YouTube の `streaming` は他サービスと意味が違う

| | Netflix / U-NEXT など | YouTube |
|---|---|---|
| `streaming` の意味 | サブスク加入者なら観られる | **誰でも無料で観られる** |
| 判定の難しさ | 作品ページに載っていれば見放題 | 同じ URL 形式で無料・レンタル・購入が混在する |

Netflix のチェッカーは「そのページに作品があるか」を見れば足りる。YouTube は
**無料公開の動画も、レンタル専用の映画も、まったく同じ `watch?v=` の URL**
で存在する。ページの有無では区別が付かない。

### 1.2 誤判定が直接ユーザー体験を壊す

このチェッカーの結果は、TOP の「YouTube で無料配信中」セクション
（`/wp-json/v1/youtube-free-list`）の掲載可否をそのまま決める。

- **有料を無料と誤る** → 「無料と書いてあったのに課金画面が出た」
- **無料を有料・終了と誤る** → 観られる作品が TOP から消える

前者のほうが体験として重い。したがって**迷ったら「無料」に倒さない**のが
このチェッカーの一貫した方針である（§5 参照）。

---

## 2. 入力

### 2.1 対象 URL

```
https://www.youtube.com/watch?v={video_id}
https://youtu.be/{video_id}
```

ACF `youtube.scraping_url` に登録された値をそのまま渡す。
プレイリスト URL・チャンネル URL は想定しない。

### 2.2 HTTP リクエスト

| 項目 | 値 |
|---|---|
| メソッド | GET |
| ヘッダー | `checkers/__init__.py` の `HEADERS`（Chrome の User-Agent / `Accept-Language: ja-JP,ja;q=0.9,en-US;q=0.8` ほか） |
| タイムアウト | 30秒 |
| リダイレクト | 追う（`allow_redirects=True`） |

`Accept-Language` を日本語にしているのは、有料オファーの文言（「¥407 でレンタル」
「購入」）とチャンネル名を日本語で受け取るためである。英語で返ると
`_PURCHASE_WORDS` の日本語側が効かなくなるが、`Buy` / `buy` も候補に入れて
あるため購入判定自体は成立する。

Playwright は使わない。判定に必要な情報はすべて**サーバーが返す HTML に
インラインで埋まっている** `ytInitialPlayerResponse` から取れるため、
JS 実行なしで足りる。

---

## 3. 出力

```python
{"status": str, "price": float | None, "channel_name": str}
```

| キー | 型 | 説明 |
|---|---|---|
| `status` | str | `streaming` / `rental` / `purchase` / `ended` |
| `price` | float \| None | 金額。無料は `0`、判定できなければ `None` |
| `channel_name` | str | 公開しているチャンネル名。取れなければ空文字 |

`channel_name` は**このチェッカーだけが返す追加キー**である。共通仕様
（`{"status", "price"}`）に対する上乗せで、使わない呼び出し元は無視してよい。
`weekly_patch.py` は実際に無視している（§7）。

`unavailable` は返さない。YouTube には「配信リストに載っていない」という
中間状態が無く、URL が生きているか死んでいるかのどちらかであるため。

---

## 4. 判定フロー

**上から順に評価し、最初に一致したところで確定する。順序そのものが仕様である。**

| # | 条件 | 結果 | 根拠 |
|---|---|---|---|
| 1 | HTTP 5xx | `RuntimeError` | サーバー側の一時障害。値を書き換えない |
| 2 | HTML に `"ytOfferModuleRenderer"` がある | `rental` / `purchase` + 金額 | 有料オファーのパネル。出ている時点で無料では観られない |
| 3 | `playabilityStatus.status == "OK"` | `streaming` / price `0` | 再生可能かつ有料オファー無し＝無料公開 |
| 4 | `playabilityStatus.status == "CONTENT_CHECK_REQUIRED"` | `streaming` / price `0` | 閲覧注意の確認を挟むだけで、無料で観られる |
| 5 | `playabilityStatus.status` が `ERROR` / `UNPLAYABLE` | `ended` | 削除・非公開・地域制限 |
| 6 | `playabilityStatus.status == "LOGIN_REQUIRED"` | `RuntimeError` | 年齢制限か限定公開か区別できない |
| 7 | `playabilityStatus` を読めず `og:title` はある | `RuntimeError` | 同意ページ等。「ページがある」だけで無料と決められない |
| 8 | `og:title` も無い | `ended` | 削除済み・存在しない動画 |

### 4.1 なぜオファー判定（#2）が最初なのか

**有料作品の `playabilityStatus` は `UNPLAYABLE` になる。** 先に
`playabilityStatus` を見ると、レンタル映画が `ended`（配信終了）に化ける。
「有料である」ことと「観られない」ことを取り違えないよう、オファーの有無を
先に確定させる。

### 4.2 `CONTENT_CHECK_REQUIRED` を無料に含める理由（#4）

暴力・自傷などの警告インタースティシャルが挟まる状態で、**確認ボタンを押せば
無料で再生できる**。ホラー作品の無料公開で踏みやすい。ここに到達している時点で
オファー判定（#2）は通過済み＝有料オファーは無いので、無料と断定してよい。

### 4.3 HTTP 4xx を特別扱いしない理由

YouTube は削除済み動画に対しても 200 とエラー用の `playabilityStatus` を返す。
ステータスコードでは判定できないため、4xx もそのまま HTML の中身で判定する。
中身が無ければ #8 で `ended` に落ちる。

---

## 5. 「判定不能（RuntimeError）」の設計

`RuntimeError` を投げると**呼び出し元は既存の ACF 値を書き換えずにスキップ**する
（`vod_bot` 共通の約束）。このチェッカーはそれを積極的に使う。

### 5.1 なぜ `LOGIN_REQUIRED` を `ended` にしないのか

`LOGIN_REQUIRED` は次の2つで同じ値になり、HTML からは区別できない。

| 実態 | 本来あるべき判定 |
|---|---|
| 年齢制限つきの動画（ログインすれば**無料で観られる**） | `streaming` |
| 限定公開・非公開に切り替わった（**もう観られない**） | `ended` |

`ended` に倒すと、R15 のホラー作品などが無料枠から静かに消える。逆に
`streaming` に倒すと、消えた動画が無料枠に残る。どちらも誤りなので、
**判定せず据え置き、Slack の「判定不能」に出して人間に回す。**

### 5.2 なぜ「ページはあるが読めない」を `streaming` にしないのか

**改修前の実装は `og:title` があれば無条件で `streaming` を返していた。**
その結果、レンタル専用の映画まで「無料」として記録されていた。

同意ページ・ボット検出ページが返ると `ytInitialPlayerResponse` は落ちるが
`og:title` は残ることがある。ここで「ページがあるから無料」と決めると、
同じ誤りを繰り返す。よって判定不能として据え置く。

### 5.3 副作用：判定不能が増えると気付けるか

`youtube_free_patch.py` は判定不能を Slack の「判定不能（値はそのまま）」
セクションに理由付きで出す。YouTube 側の HTML 構造が変わって全件が読めなく
なった場合、**この件数が跳ね上がることで検知できる。** 無言で `streaming` を
返し続けるより安全である。

---

## 6. 各シグナルの取り出し方

### 6.1 有料オファー（`_parse_offer`）

```python
_OFFER_MODULE_MARKER = '"ytOfferModuleRenderer"'
```

このキーが HTML にあれば有料。オファーパネルは
`playabilityStatus.errorScreen.ytOfferModuleRenderer` の下にあり、
「¥407 でレンタル」のようなボタン文言を含む。

| 判定 | 方法 |
|---|---|
| 購入形態 | マーカー位置から **4000文字以内**に「購入」「Buy」があれば `purchase`、無ければ `rental` |
| 金額 | 同じ範囲を `[¥￥]\s*([\d,]+)` または `([\d,]+)\s*円` で検索し、カンマを除いて float 化 |

**探索範囲を4000文字に限定しているのが要点。** ページ全体を検索すると、
サイドバーの関連動画に出る他作品の価格を拾ってしまう。

金額を読めなかった場合は `price = None` を返す。`status` は有料のままなので、
無料枠に混ざる事故は起きない（無料は `price = 0` が必須のため）。

### 6.2 `playabilityStatus`（`extract_playability_status`）

```python
_PLAYABILITY_STATUS_RE = re.compile(
    r'"playabilityStatus"\s*:\s*\{[^{}]*?"status"\s*:\s*"([A-Z_]+)"'
)
```

`ytInitialPlayerResponse` 全体を JSON パースせず、正規表現で該当箇所だけ抜く。
巨大な JSON（数百KB〜数MB）を毎回パースするコストを避けるため。

`[^{}]*?` は「`playabilityStatus` の直下に、入れ子オブジェクトを跨がずに
`status` が現れる」ことを要求する。実際の YouTube は `status` を先頭付近に
置くため成立するが、**並び順が変わると読めなくなる**（§8）。

読めない場合は空文字を返し、フローは #7 / #8 へ進む。

### 6.3 チャンネル名（`extract_channel_name`）

2段構えで取る。

| 優先 | 取得元 | 形 |
|---|---|---|
| 1 | `ytInitialPlayerResponse.videoDetails.ownerChannelName` | `"ownerChannelName":"【公式】プレシディオチャンネル"` |
| 2 | microformat の `<link itemprop="name" content="...">` | HTML タグ |

1 は JSON 文字列リテラルのまま切り出し、`json.loads()` でエスケープを戻す
（`【` などが素通りしないようにするため）。パースに失敗したら 2 へ落ちる。

どちらも取れなければ空文字を返す。**空文字のときは呼び出し元が既存の値を
維持する**ため、一度取得できたチャンネル名が取得失敗で消えることはない。

---

## 7. 呼び出し元ごとの扱い

| | `youtube_free_patch.py`（日次） | `weekly_patch.py`（週次） |
|---|---|---|
| 対象 | `youtube.scraping_url` 付きの全記事 | バッチ番号に当たった記事の全サービス |
| 頻度 | 毎日 | 同じ記事は2ヶ月に1回 |
| `channel_name` | **保存する** | 無視する（`update_post` は5サブフィールドのみ書く） |
| `streaming_started_at` | 無料に戻るたび入れ直す | 未取得→streaming の初回のみ |
| `RuntimeError` | その記事をスキップし Slack の「判定不能」へ | エラー計上。**3回連続で処理全体を中断** |

無料枠の鮮度は日次の `youtube_free_patch.py` が担保する。週次パッチは
他サービスと同じ流れで YouTube も見るが、無料公開の期間には追従できない。

> **週次パッチ側の注意**: `weekly_patch.py` は `MAX_CONSECUTIVE_ERRORS = 3` で
> 連続エラー時に全体を中断する。判定不能を返す条件が増えたぶん、YouTube が
> 連続で失敗すると中断に寄与しうる。ただしカウンタは他サービスの成功で
> リセットされるため、YouTube だけが3回続けて最後に来ない限り中断はしない。

---

## 8. 既知の限界と壊れやすい点

| 箇所 | 壊れる条件 | 壊れたときの挙動 |
|---|---|---|
| `_PLAYABILITY_STATUS_RE` | `playabilityStatus` 内のキー順が変わり `status` の前に入れ子が入る | 全件が「判定不能」になる。Slack の判定不能件数で気付ける |
| `_OFFER_MODULE_MARKER` | YouTube がオファーパネルのレンダラー名を変える | **有料作品が `streaming` になる**（最も危険） |
| `_PRICE_RE` | 通貨表記が変わる（ドル建て等） | `price = None`。`status` は有料のままなので無料枠には出ない |
| `_PURCHASE_WORDS` | 表示言語が日本語でも英語でもなくなる | `rental` に倒れる。金額は取れる |
| `ownerChannelName` | キー名の変更 | microformat にフォールバック。両方消えたら空文字 |

**`_OFFER_MODULE_MARKER` の失効だけは自動検知できない。** 有料が
`playabilityStatus: UNPLAYABLE` として `ended` になるなら安全側に倒れるが、
`OK` を返す形に変わると無料枠に有料作品が載る。TOP に見覚えのない有料作品が
出ていないか、たまに目視で確認する運用が要る。

### 検証環境の制約

開発コンテナからは `www.youtube.com` へ到達できない（プロキシが 403）。
そのため**実ページでの疎通確認は行えず、判定ロジックは HTML フィクスチャに
よるユニットテストで検証している。** マーカーや正規表現の妥当性は、本番
（Cloud Run / GitHub Actions）での初回実行結果と Slack 通知で確認すること。

---

## 9. テスト

`vod_bot/tests/test_youtube_free_patch.py` に集約している。HTTP は
`requests.get` をスタブに差し替え、外部アクセスは一切しない。

| テスト | 確認内容 |
|---|---|
| `test_check_free_video_returns_streaming` | 再生可能なら `streaming` / price 0 / チャンネル名 |
| `test_check_rental_offer_is_not_free` | **有料オファーを無料と判定しない**（本改修の要） |
| `test_check_purchase_offer` | 「購入」の文言で `purchase` と金額 |
| `test_check_content_check_required_is_free` | 閲覧注意の確認付きでも無料 |
| `test_check_content_check_required_with_offer_is_paid` | 確認付き＋オファーは有料（順序の検証） |
| `test_check_deleted_video_returns_ended` | 削除済みは `ended` |
| `test_check_login_required_raises` | 年齢制限／限定公開は据え置き |
| `test_check_unreadable_page_raises` | 同意ページは無料に倒さない |
| `test_check_server_error_raises` | 5xx は据え置き |
| `test_extract_channel_name_*` | チャンネル名の2段フォールバック |

---

## 10. 改修履歴

### 旧実装（`og:title` 方式）の問題

```python
# 改修前
og_title = soup.find("meta", property="og:title")
if og_title and og_title.get("content", "").strip():
    return {"status": "streaming", "price": 0}
return {"status": "ended", "price": None}
```

**ページが存在するだけで `streaming`（無料）を返していた。** YouTube では
レンタル専用の映画も同じ URL 形式でページが存在するため、有料作品が無料として
記録される。TOP に無料枠を作るにあたって、この判定では成立しない。

### 現行実装での変更点

1. `ytInitialPlayerResponse` を読み、有料オファーを明示的に検出する
2. 判定できない状態を `RuntimeError`（据え置き）として区別する
3. チャンネル名を返す（無料公開の主体を表示するため）

---

## 11. 関連

- [youtube-free-check-spec.md](./youtube-free-check-spec.md) — このチェッカーを日次で回すジョブの仕様
- [../vod-scraping-api.md](../vod-scraping-api.md) — チェッカー全体の共通仕様
- `katsumascore_wordpress_theme/docs/feature/YOUTUBE_FREE_LIST_API_SPEC.md` — 判定結果を使う REST エンドポイント
