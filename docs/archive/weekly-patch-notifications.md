# 週次パッチ Slack 通知仕様

`vod_bot/weekly_patch.py` が検知した新規配信（新着）を Slack に通知する仕組みの詳細。
スケジューリングやバッチ処理そのものについては [weekly-patch-schedule.md](./weekly-patch-schedule.md) を参照。

---

## 概要

- 通知は **バッチ実行1回につき1通** にまとめて送信する（作品ごとの都度通知は行わない）。
- 実行中に検知した新規配信を `new_streaming_items` に蓄積し、バッチ完了時（または連続エラーによる中断時）に
  `vod_bot/slack.py` の `notify_weekly_new_streaming_summary()` へまとめて渡す。
- 新着が1件もない週は通知を送信しない。
- `SLACK_WEBHOOK_URL` 未設定の場合、または送信失敗時は WARNING ログのみで例外は発生しない。

---

## 発火条件（新規配信の判定）

`vod_bot/wordpress.py` の `update_post()` 内、以下の条件をすべて満たす場合に「新規配信」と判定する。

```
1. 今回の status == "streaming"
2. 直前の status != "streaming"（未取得含む）
3. streaming_started_at が未設定（再配信時は対象外）
```

該当した場合、その投稿・サービスの組み合わせが `new_streaming_items` に追加される。

---

## 通知フォーマット

言語（`acf.lang`）→ VOD サービスの順にグループ化し、1通のテキストメッセージにまとめる。

- **言語グループ**: 日本語（`ja`）→ English（`en`）の順。上記以外の言語コードが将来追加された場合は末尾に表示する。
- **サービスグループ**: 各言語内は `_SERVICE_LABELS`（Amazon Prime Video → Netflix → Hulu → U-NEXT → Disney+ → DMM TV → Apple TV → YouTube → Crunchyroll）の定義順。未知のサービスキーは末尾に表示する。
- 各作品はタイトルにフロントエンド URL をリンクした Slack 形式（`<url|title>`）で表示する。URL が空の場合はタイトルのみ表示する。
- タイトルの後ろに、そのサービスで実際にスクレイピングした配信ページ URL を `｜ <scraping_url|配信ページ>` の形式で添える。`scraping_url` が空の場合は省略する。

### 出力例

```
:clapper: *今週の新着配信一覧* — 全5件

:jp: 日本語（3件）
*Netflix*
  • <https://katsumascore.blog/ja/movie/john-wick|ジョン・ウィック> ｜ <https://www.netflix.com/jp/title/81260280|配信ページ>
  • <https://katsumascore.blog/ja/anime/frieren|葬送のフリーレン> ｜ <https://www.netflix.com/jp/title/81726714|配信ページ>
*U-NEXT*
  • <https://katsumascore.blog/ja/movie/sakuhin-c|作品C> ｜ <https://video.unext.jp/title/SID0012345|配信ページ>

:us: English（2件）
*Amazon Prime Video*
  • <https://katsumascore.blog/en/movie/title-e|Title E> ｜ <https://www.amazon.co.jp/gp/video/detail/B0ABCDEFG|配信ページ>
*Crunchyroll*
  • <https://katsumascore.blog/en/anime/title-d|Title D> ｜ <https://www.crunchyroll.com/series/GABC/title-d|配信ページ>
```

---

## 作品リンク（フロントエンド URL）の組み立て

各作品に付与する URL は **WordPress の投稿リンクではなく、フロントエンド（Next.js）の表示 URL** を使用する。

```
https://katsumascore.blog/{lang}/{category_slug}/{post_slug}

例:
  日本語: https://katsumascore.blog/ja/movie/john-wick
  英語  : https://katsumascore.blog/en/anime/frieren
```

- `lang`: 投稿の `acf.lang` を `ja` / `en` に正規化したもの（`_front_lang_segment()`）。`jp` などの表記ゆれや未設定・未知の言語コードはすべて `ja` に寄せ、`/jp` のような存在しないパスは出力しない。
- `category_slug`: 投稿が属する WordPress カテゴリの slug。複数カテゴリに属する場合は解決できた最初の slug を使用する。
- `post_slug`: 投稿の slug。

### カテゴリ slug の解決

`vod_bot/wordpress.py` の `get_category_slug_map()` が実行開始時に WP REST API（`/categories`）から
`{term_id: slug}` のマッピングを一括取得する（バッチ実行につき1回）。

### フォールバック

**ホストは常にフロント（`https://katsumascore.blog`）を使う。** WordPress の投稿リンク（`post.link`）は
バックエンド側のホストを指すため、フォールバック先には使わない。

- カテゴリ slug が解決できない場合（投稿にカテゴリ term_id がない／マップに存在しない／カテゴリマップの
  取得自体に失敗した場合）は、カテゴリを省いた `https://katsumascore.blog/{lang}/{post_slug}` を返す
- 投稿の slug が空の場合のみ空文字を返す（Slack ではタイトルのみ表示になる）

いずれのフォールバックでも `vod_bot/weekly_patch.py` から WARNING ログが出力される。

---

## 関連コード

| 役割 | ファイル |
|---|---|
| 新規配信の判定 | `vod_bot/wordpress.py` の `update_post()` |
| カテゴリ slug マップ取得 | `vod_bot/wordpress.py` の `get_category_slug_map()` |
| フロントURL組み立て | `vod_bot/weekly_patch.py` の `_build_front_url()` |
| 新着の蓄積・通知呼び出し | `vod_bot/weekly_patch.py` の `run()` |
| Slack 通知本体 | `vod_bot/slack.py` の `notify_weekly_new_streaming_summary()` |

---

## 環境変数

| 変数名 | 用途 | 必須 |
|---|---|---|
| `SLACK_WEBHOOK_URL` | Slack Incoming Webhook URL。未設定時は通知をスキップ | △ |
