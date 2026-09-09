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

watch ページの `ytInitialPlayerResponse` を読む。

| 条件 | 判定 | price |
|---|---|---|
| 有料オファー（`ytOfferModuleRenderer`）あり | `rental` / `purchase` | オファーの金額 |
| `playabilityStatus.status == "OK"` | `streaming`（無料公開） | `0` |
| status が `ERROR` / `UNPLAYABLE`（オファーなし） | `ended` | `None` |
| status が `LOGIN_REQUIRED` | **RuntimeError**（据え置き） | — |
| `playabilityStatus` を読めず og:title だけある | **RuntimeError**（据え置き） | — |
| og:title も無い | `ended` | `None` |
| HTTP 5xx | **RuntimeError**（据え置き） | — |

購入形態は、オファーパネル周辺に「購入」「Buy」があれば `purchase`、
無ければ `rental` とする。金額は `¥407` / `407円` の表記から拾う。

### なぜ「判定不能」を作るのか

**改修前は og:title があれば無条件で `streaming` を返していた**ため、
レンタル作品まで「無料」と記録されていた。無料枠に有料作品が混ざるのは
「観に行ったら有料だった」という一番避けたい体験を生む。

そのため判定に迷う場合は既存の値を据え置き、Slack に「判定不能」として
報告する。ended に倒さないのも同じ理由で、年齢制限（`LOGIN_REQUIRED`）の
ホラー作品などが無料枠から消えるのを避けている。

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

---

## Slack 通知

無料公開の**開始**・**終了**・**判定不能**を1通にまとめて送る。
どれも空なら通知しない。無料公開は期間限定のため、開始も終了も見逃したくない。

---

## 関連

- `katsumascore_wordpress_theme/docs/feature/YOUTUBE_FREE_LIST_API_SPEC.md` — エンドポイント仕様
- [theater-showing-check-spec.md](./theater-showing-check-spec.md) — 同じ「下ろす」系の自動チェック
- [../vod-scraping-api.md](../vod-scraping-api.md) — チェッカー全体の仕様
