# YouTube 無料配信 日次チェック仕様

実装: [`vod_bot/youtube_free_patch.py`](../../vod_bot/youtube_free_patch.py)
チェッカー: [`vod_bot/checkers/youtube.py`](../../vod_bot/checkers/youtube.py)
ワークフロー: [`.github/workflows/youtube-free-check.yml`](../../.github/workflows/youtube-free-check.yml)

---

## 目的

映画配給会社の公式 YouTube チャンネル（例:【公式】プレシディオチャンネル）が
本編を**期間限定で無料公開**することがある。「今すぐタダで観られる作品」は
フロントの TOP に出す価値が高い一方、**数日〜数週間で予告なく終わる**。

このチェックは、どの作品がいま無料で観られるかを日次で洗い直し、
WordPress の ACF `youtube` グループを最新の状態に保つ。

フロントの「YouTube で無料配信中」セクションは
`/wp-json/v1/youtube-free-list`（`youtube.status = streaming` かつ
`youtube.price` が 0）を母集団にするため、このチェックが止まると
**終了済みの作品が TOP に残り続ける**。

### 週次パッチ（`weekly_patch.py`）との違い

| | `weekly_patch.py` | 本チェック |
|---|---|---|
| 巡回間隔 | 同じ記事は2ヶ月に1回（`post_id % 8`） | **毎日全件** |
| 対象 | 全サービス | YouTube のみ |
| 母数 | 全記事 | `youtube.scraping_url` 付きの記事だけ |
| 目的 | 配信状況の網羅的な棚卸し | 無料公開の**開始と終了**の即時検知 |

週次パッチのバッチ制では無料公開の開始も終了も取り逃がす。母数が小さく
requests ベースで1件あたり数秒のため、日次で全件を見ても負荷は小さい。

---

## 対象の選び方（重要）

**現在のステータスでは絞らない。** `youtube.scraping_url` が登録された
publish 記事をすべて毎回チェックする。

無料 → 有料の検知だけなら `streaming` の記事だけ見れば足りるが、
**有料・配信終了だった作品が新たに無料公開される**ケース（キャンペーン・
続編公開に合わせた過去作の無料開放など）を拾えなくなる。開始を取り逃がすと
そもそも TOP に載らないため、こちらのほうが損失が大きい。

`scraping_disabled` が立った記事は `get_all_posts_for_patch()` の時点で除外される。

---

## 判定ロジック（`YoutubeChecker`）

watch ページの `ytInitialPlayerResponse` を読み、**上から順に**評価する。

| 条件 | 判定 | price |
|---|---|---|
| HTTP 5xx | **RuntimeError**（据え置き） | — |
| 有料オファー（`ytOfferModuleRenderer`）あり | `rental` / `purchase` | オファーの金額 |
| `playabilityStatus.status == "OK"` | `streaming`（無料公開） | `0` |
| status が `CONTENT_CHECK_REQUIRED` | `streaming`（確認を挟むだけ） | `0` |
| status が `ERROR` / `UNPLAYABLE`（オファーなし） | `ended` | `None` |
| status が `LOGIN_REQUIRED` | **RuntimeError**（据え置き） | — |
| `playabilityStatus` を読めず og:title だけある | **RuntimeError**（据え置き） | — |
| og:title も無い | `ended` | `None` |

有料オファーを最初に見るのは、**有料作品の `playabilityStatus` が `UNPLAYABLE`
になる**ため。順序を逆にするとレンタル映画が `ended` に化ける。

判定に迷う場合は既存の値を据え置き、Slack に「判定不能」として報告する。
無料枠に有料作品が混ざる事故を、取りこぼしより重く見ているためである。

**判定の詳細・正規表現・壊れやすい点・テスト一覧は
[youtube-checker-spec.md](./youtube-checker-spec.md) にまとめてある。**

### `channel_name`

`videoDetails.ownerChannelName` を優先し、無ければ microformat の
`<link itemprop="name">` から取る。どこが無料公開しているかを
フロントに出すために保存する。取れなかった回に既存の値を消さない。

---

## ACF の更新（`patch_youtube_status`）

`update_post()` とは別関数にしている。理由は `streaming_started_at` の
更新条件が違うため。

| 遷移 | `streaming_started_at` |
|---|---|
| 有料・終了・未取得 → `streaming` | **今回の日時で入れ直す** |
| `streaming` 継続（日付が空） | 今回の日時で埋める |
| `streaming` 継続（日付あり） | そのまま |
| `streaming` → 有料・終了 | そのまま（履歴として残す） |

`update_post()` は「未取得 → streaming」の初回だけ日付を入れるが、
YouTube の無料公開は**同じ作品が期間を空けて何度も無料になる**。前回の日付を
残したままだとフロントの「最近無料になった順」の並びと「NEW」表示が壊れる。

`vod` タクソノミーの YouTube ターム（`term_id=973`）は、無料公開のときだけ付ける。

---

## 実行

| 実行環境 | トリガー |
|---|---|
| GitHub Actions | 毎日 06:00 JST（`.github/workflows/youtube-free-check.yml`） |
| Cloud Run | `POST /youtube-free-check` |

```bash
python youtube_free_patch.py                # 全件チェック
python youtube_free_patch.py --dry-run      # 判定のみ（更新・Slack通知なし）
python youtube_free_patch.py --slug one-missed-call-2003
python youtube_free_patch.py --post-id 123
python youtube_free_patch.py --limit 10
```

対象一覧の取得に失敗した場合は WordPress を一切更新せず `error` を返す
（誤って全記事のステータスを書き換えないため）。

### 診断モード（`--probe`）

判定結果ではなく、判定に使ったシグナルそのものを出す。WordPress も Slack も
触らない。

```bash
python youtube_free_patch.py --probe --limit 5               # 登録URLを5件診断
python youtube_free_patch.py --url 'https://youtu.be/xxxx'   # 任意URLを診断
```

**YouTube はアクセス元 IP によって返す HTML を変える**ため、手元で確認しても
本番で同じ HTML が返る保証がない。GitHub Actions の `workflow_dispatch` に
`probe` / `url` 入力を用意してあり、**本番と同じ IP から見た HTML を確認できる。**
判定がおかしいときやマーカーの失効を疑ったときは、まずここから始める。

出力の読み方は [youtube-checker-spec.md](./youtube-checker-spec.md) の
「診断モード」を参照。

---

## Slack 通知

`SLACK_WEBHOOK_URL` 宛に Webhook で送る（未設定なら何もしない）。

### 実行結果（`notify_youtube_free_result`）

無料公開の**開始**・**終了**・**判定不能**・**更新失敗**を1通にまとめて送る。
無料公開は期間限定のため、開始も終了も見逃したくない。

| セクション | 内容 |
|---|---|
| 無料公開が始まった作品 | 記事リンク・チャンネル名・YouTube リンク |
| 無料公開が終わった作品 | 上記＋`streaming → レンタル（407円）` のように遷移と金額 |
| 判定不能（値はそのまま） | 上記＋据え置いた理由 |
| WordPress の更新に失敗 | 上記＋失敗理由。値が古いまま残るので見逃せない |

**4種すべて空の日は通知しない。** 日次実行のため、変化が無い日まで流すと通知が
形骸化して肝心の開始・終了を見落とす。

### 中断（`notify_youtube_free_failure`）

対象記事の一覧を取得できず1件も更新せずに終わった場合は、**無条件で**通知する。
無通知だと「今日は変化が無かった」と見分けが付かず、TOP の無料枠が古いまま
放置されるため。`--dry-run` のときは送らない。

---

## 関連

- [youtube-checker-spec.md](./youtube-checker-spec.md) — `YoutubeChecker` の判定仕様
- `katsumascore_wordpress_theme/docs/feature/YOUTUBE_FREE_LIST_API_SPEC.md` — エンドポイント仕様
- [theater-showing-check-spec.md](./theater-showing-check-spec.md) — 同じ「下ろす」系の自動チェック
- [../vod-scraping-api.md](../vod-scraping-api.md) — チェッカー全体の仕様
