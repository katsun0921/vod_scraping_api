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
     ├─ 劇場URL（cinema_info_filed.cinema_list_filed）へ GET
     └─ 劇場公開日（release.release_date）からの経過日数を計算

③ 上映終了と判定した記事の is_cinema_showing を false に PATCH

④ 結果を Slack に通知（上映終了・判定不能の一覧）
```

一覧取得は WordPress テーマの `/v1/theater-list`（[THEATER_LIST_API_SPEC.md](https://github.com/katsun0921/katsumascore_wordpress_theme/blob/main/docs/feature/THEATER_LIST_API_SPEC.md)）に依存する。
`lang` 単位でしか返らないため `ja` / `en` の両方を取得し、`id` で重複排除する。

---

## 判定ロジック

| # | 条件 | 判定 | reason |
|---|---|---|---|
| 1 | 劇場URLが **404 / 410** | 上映終了 | `url_gone` |
| 2 | 公開日から **8週間（既定）以上**経過 | 上映終了 | `expired` |
| 3 | 劇場URLが 2xx / 3xx | 上映中 | `url_alive` |
| 4 | 公開日が範囲内（未来日を含む） | 上映中 | `within_period` |
| 5 | 劇場URLも公開日も無い | 判定不能（据え置き） | `no_signal` |

- 判定は 1 → 2 の順。劇場URLが消えていれば公開直後でも終了とみなす
- 劇場URLが **5xx / 429 / 403 / 接続失敗**のときは「判定不能」として**上映終了にしない**。
  ボット対策や一時障害で消えたと誤認するのを避けるため
- 公開日が未来（公開前にフラグを立てた場合）は経過日数が負数になるため上映中のまま

### しきい値（8週間）

日本の劇場公開は概ね4〜8週間で終映するため、ロングラン作品を巻き込みにくい 8週間を既定にした。
`--weeks` / 環境変数 `THEATER_SHOWING_WEEKS` で変更できる。ロングラン作品が誤って
下ろされるようなら 12週間へ引き上げる。誤って外した場合は WP 管理画面でチェックを
入れ直せば元に戻る（記事本文には一切触れない）。

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
- 判定不能（`no_signal`）はフラグを変更せず Slack で人間に知らせる
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
  "posts": { "total": 12, "showing": 9, "ended": 2, "unknown": 1, "errors": 0 },
  "ended": [
    {
      "id": 16233,
      "slug": "example-movie",
      "title": "作品A",
      "reason": "expired",              // expired | url_gone
      "release_date": "2026-06-01",
      "elapsed_days": 99,
      "cinema_url": "https://...",
      "url": "https://katsumascore.blog/ja/movie/example-movie"
    }
  ],
  "unknown": []
}
```

一覧取得に失敗した場合のみ `error` キーが付き、`posts` は全て 0 になる。

---

## Slack 通知

上映終了・判定不能のどちらかがあれば1通送る（どちらも空なら送らない）。
`dry_run` では送らない。

```
:performing_arts: 劇場公開の週次チェック — 上映終了 2件 / 判定不能 1件

上映終了（現在上映中フラグを自動OFF）
  • 作品A ｜ 公開から14週間経過（2026-06-01）
  • 作品B ｜ 劇場URLが404/410 ｜ 劇場ページ

判定不能（フラグはそのまま）
  • 作品C ｜ 公開日・劇場URLが未入力で判定できず
```

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
