# 劇場公開（上映中フラグ）週次チェック仕様

実装: [`vod_bot/theater_patch.py`](../../vod_bot/theater_patch.py)
ワークフロー: [`.github/workflows/theater-showing-check.yml`](../../.github/workflows/theater-showing-check.yml)

---

## 目的

レビュー記事の ACF `cinema_info_filed.is_cinema_showing`（現在上映中）は人手で
ON/OFF する運用のため、**終映後にチェックを外し忘れると終わった作品がフロントの
「劇場公開中」一覧に残り続ける**。この取りこぼしを週次で自動的に回収する。

対象は「上映中フラグが ON の記事」だけで、フラグを ON にする（＝上映開始を検知する）
処理は行わない。ON にするのは引き続き人間の作業である。

### 週次まとめ記事（`theater_release` CPT）との違い

| | `theater_release` CPT | 本チェック |
|---|---|---|
| 実体 | 週次の劇場公開まとめ記事 | レビュー記事（`post`）の ACF フラグ |
| 担当 | `news_bot`（`theater_publish`） | `vod_bot`（`theater_patch.py`） |
| 向き | これから公開する作品を**載せる** | 公開が終わった作品を**下ろす** |

まとめ記事側の仕様は [theater-release-calendar-spec.md](./theater-release-calendar-spec.md) を参照。

---

## 処理フロー

```
① 上映中フラグ ON の記事一覧を取得
     GET /wp-json/v1/theater-list?lang={ja|en}&per_page=100（全ページ）
     └─ 404（テーマ未デプロイ）のときのみ /wp/v2/posts 全件走査にフォールバック

② 記事ごとに判定
     ├─ 劇場URL（cinema_info_filed.cinema_list_filed）へ GET ← こちらを優先
     └─ 劇場公開日（release.release_date）からの経過日数を計算
        （URLで確認できないときのフォールバック）

③ 上映終了と判定した記事の is_cinema_showing を false に PATCH

④ 結果を Slack に通知（上映終了・判定不能・ロングランの一覧）
```

一覧取得は WordPress テーマの `/v1/theater-list`（[THEATER_LIST_API_SPEC.md](https://github.com/katsun0921/katsumascore_wordpress_theme/blob/main/docs/feature/THEATER_LIST_API_SPEC.md)）に依存する。
`lang` 単位でしか返らないため `ja` / `en` の両方を取得し、`id` で重複排除する。

---

## 判定ロジック

**劇場URLの生存を経過週数より優先する。**

| # | 条件 | 判定 | reason |
|---|---|---|---|
| 1 | 劇場URLが **404 / 410** | 上映終了 | `url_gone` |
| 2 | 劇場URLが 2xx / 3xx・公開から8週間未満 | 上映中 | `url_alive` |
| 3 | 劇場URLが 2xx / 3xx・公開から **8週間以上**経過 | 上映中（ロングラン） | `url_alive_extended` |
| 4 | 8週間以上経過・劇場URL **未登録** | 上映終了 | `expired` |
| 5 | 8週間以上経過・劇場URLを**確認できない** | 判定不能（据え置き） | `url_uncheckable` |
| 6 | 8週間未満・劇場URLを確認できない | 上映中 | `within_period` |
| 7 | 劇場URLも公開日も無い | 判定不能（据え置き） | `no_signal` |

- 判定は 1 → 2/3 → 4/5/6 → 7 の順。劇場URLが消えていれば公開直後でも終了とみなす
- 劇場URLが **5xx / 429 / 403 / 接続失敗**のときは「判定不能」として**上映終了にしない**。
  ボット対策や一時障害で消えたと誤認するのを避けるため
- 公開日が未来（公開前にフラグを立てた場合）は経過日数が負数になるため上映中のまま

### 8週間経過後の扱い（ロングラン作品）

**8週間を過ぎただけではフラグを OFF にしない。** ヒット作は8週間を超えて上映が続くため、
経過週数だけで打ち切ると上映中の作品をフロントの一覧から落としてしまう。

8週間を過ぎた作品は**もう一度劇場URLを見に行き、生きていれば上映中のまま据え置く**
（`url_alive_extended`）。フラグが ON のまま残るので、**8週間以降も毎週の実行で
そのURLを確認し続ける**ことになる。終映して劇場URLが 404 / 410 になった週に初めて
OFF になる（`url_gone`）。

劇場URLを確認できなかった場合（5xx / 403 / 接続失敗）は、ロングランか終映かを
判断できないため据え置き、翌週の再チェックへ回す（`url_uncheckable`）。
劇場URLが未登録で確認する手段が無い場合のみ、従来どおり経過週数だけで終映とみなす
（`expired`）。

### しきい値（8週間）

日本の劇場公開は概ね4〜8週間で終映するため 8週間を既定にした。ただし上記のとおり
このしきい値は**劇場URLで確認できないときのフォールバック**であり、URLが生きている
限り作品を下ろすことはない。`--weeks` / 環境変数 `THEATER_SHOWING_WEEKS` で変更できる。
誤って外した場合は WP 管理画面でチェックを入れ直せば元に戻る（記事本文には一切触れない）。

### 外部サイトを見に行かない方針

映画.com・MOVIE WALKER・配給会社サイト等は利用規約上の判断
（[theater-sources-candidates.md](./theater-sources-candidates.md)）により
**スクレイピングしない**。アクセスするのは記事に登録された劇場URLだけである。

---

## 安全側の設計

- **一覧取得に失敗したら1件も更新しない。** `/v1/theater-list` が 501
  （`showing_meta_key_missing`＝postmeta キー不一致）などを返した場合は
  `TheaterListError` で中断し、`error` を含む結果を返す（CLI は exit 1 / Cloud Run は 502）。
  ここで空一覧として処理を続けると、全記事のフラグを誤って外しかねない
- **PATCH は `cinema_info_filed` のみ更新する。** 同グループの `is_cinema_watched` /
  `cinema_list_filed` は既存値を維持し、必須 ACF フィールドは GET した値をそのまま引き継ぐ
- 判定不能（`no_signal` / `url_uncheckable`）はフラグを変更せず Slack で人間に知らせる
- **経過週数だけでは下ろさない。** 8週間を過ぎてもロングラン上映は続くため、
  劇場URLで確認できる限りURLの生死を優先する。自動では下ろさない以上、実際に
  終映していないかを人間が毎週確認できるよう、ロングラン分も Slack に載せる
- 劇場URLへのアクセスは `RateLimiter`（3〜5秒）で間隔を空ける

---

## 実行方法

### 定期実行（GitHub Actions）

毎週月曜 05:00 JST（cron `0 20 * * 0` UTC）。`weekly-patch`（月曜 02:00 JST）と
時刻をずらしている。`workflow_dispatch` で `weeks` / `dry_run` / `slug` / `post_id` /
`limit` を指定した手動実行もできる。

### CLI

```bash
cd vod_bot
export PYTHONPATH=..

python theater_patch.py              # 上映中の全記事をチェックして自動OFF
python theater_patch.py --dry-run    # 判定のみ（更新・Slack通知なし）
python theater_patch.py --weeks 12   # しきい値を12週間に変更
python theater_patch.py --slug john-wick
python theater_patch.py --post-id 16233
```

### Cloud Run

```bash
curl -X POST https://<cloud-run-url>/theater-check \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  -H "Content-Type: application/json" \
  -d '{"dry_run": true}'
```

---

## 戻り値

```jsonc
{
  "weeks": 8,
  "dry_run": false,
  // showing はロングラン分を含む総数。longrun はそのうち weeks 超えの件数
  "posts": { "total": 12, "showing": 9, "longrun": 1, "ended": 2, "unknown": 1, "errors": 0 },
  "ended": [
    {
      "id": 16233,
      "slug": "example-movie",
      "title": "作品A",
      "reason": "expired",              // expired | url_gone
      "release_date": "2026-06-01",
      "elapsed_days": 99,
      "cinema_url": "",
      "url": "https://katsumascore.blog/ja/movie/example-movie"
    }
  ],
  "unknown": [],                        // reason: no_signal | url_uncheckable
  "longrun": [                          // weeks 超えだが劇場URLが生存（フラグは ON のまま）
    {
      "id": 16240,
      "slug": "long-run-movie",
      "title": "作品B",
      "reason": "url_alive_extended",
      "release_date": "2026-05-01",
      "elapsed_days": 131,
      "cinema_url": "https://...",
      "url": "https://katsumascore.blog/ja/movie/long-run-movie"
    }
  ]
}
```

一覧取得に失敗した場合のみ `error` キーが付き、`posts` は全て 0 になる。

---

## Slack 通知

上映終了・判定不能・ロングランのいずれかがあれば1通送る（すべて空なら送らない）。
`dry_run` では送らない。

```
:performing_arts: 劇場公開の週次チェック — 上映終了 2件 / 判定不能 1件 / ロングラン 1件

上映終了（現在上映中フラグを自動OFF）
  • 作品A ｜ 公開から14週間経過（2026-06-01） ｜ 劇場URL未登録のため確認できず
  • 作品B ｜ 劇場URLが404/410 ｜ 劇場ページ

判定不能（フラグはそのまま）
  • 作品C ｜ 公開日・劇場URLが未入力で判定できず

ロングラン継続中（8週超・フラグはそのまま）
  • 作品D ｜ 公開から14週間経過（2026-05-01） ｜ 劇場URLは生存（ロングラン継続中） ｜ 劇場ページ
```

ロングランの節は自動では下ろさない作品の一覧なので、**実際にはもう終映している作品が
混ざっていないかを毎週ここで確認する**。終映していれば WP 管理画面でチェックを外す。

---

## 環境変数

| 変数名 | 用途 | 必須 |
|---|---|---|
| `WP_API_URL` / `WP_USER` / `WP_APP_PASSWORD` | WordPress REST API | ○ |
| `WP_BASIC_USER` / `WP_BASIC_PASSWORD` | サーバー Basic 認証 | △ |
| `SLACK_WEBHOOK_URL` | Slack 通知 | △ |
| `THEATER_SHOWING_WEEKS` | 上映終了とみなす経過週数（既定 8） | △ |

---

## 関連

| ドキュメント | 内容 |
|---|---|
| [theater-release-calendar-spec.md](./theater-release-calendar-spec.md) | 週次の劇場公開まとめ記事（`news_bot`） |
| [theater-sources-candidates.md](./theater-sources-candidates.md) | 劇場情報源の利用規約調査（外部サイトを使わない根拠） |
| [../vod-scraping-api.md](../vod-scraping-api.md) | vod_bot の API 仕様 |
