# 週次パッチ Slack 通知仕様

`weekly_patch.py` が検知した新規配信（新着）を Slack に通知する仕組みの詳細。
スケジューリングやバッチ処理そのものについては [weekly-patch-schedule.md](./weekly-patch-schedule.md) を参照。

---

## 概要

- 通知は **バッチ実行1回につき1通** にまとめて送信する（作品ごとの都度通知は行わない）。
- 実行中に検知した新規配信を `new_streaming_items` に蓄積し、バッチ完了時（または連続エラーによる中断時）に
  `utils/slack.py` の `notify_weekly_new_streaming_summary()` へまとめて渡す。
- 新着が1件もない週は通知を送信しない。
- `SLACK_WEBHOOK_URL` 未設定の場合、または送信失敗時は WARNING ログのみで例外は発生しない。

---

## 発火条件（新規配信の判定）

`utils/wordpress.py` の `update_post()` 内、以下の条件をすべて満たす場合に「新規配信」と判定する。

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

### 出力例

```
🎬 今週の新着配信一覧 — 全5件

🇯🇵 日本語（3件）
Netflix
  • 作品A（リンク）
  • 作品B（リンク）
U-NEXT
  • 作品C

🇺🇸 English（2件）
Amazon Prime Video
  • Title E（リンク）
Crunchyroll
  • Title D（リンク）
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

- `lang`: 投稿の `acf.lang`（`ja` / `en`）。
- `category_slug`: 投稿が属する WordPress カテゴリの slug。複数カテゴリに属する場合は解決できた最初の slug を使用する。
- `post_slug`: 投稿の slug。

### カテゴリ slug の解決

`utils/wordpress.py` の `get_category_slug_map()` が実行開始時に WP REST API（`/categories`）から
`{term_id: slug}` のマッピングを一括取得する（バッチ実行につき1回）。

### フォールバック

以下の場合、フロントエンド URL の組み立てを諦め、WordPress の投稿リンク（`post.link`）にフォールバックする。

- 投稿にカテゴリ term_id が設定されていない、またはマップに存在しない
- 投稿の slug が空
- カテゴリマップの取得自体に失敗した場合（全件フォールバック）

フォールバック発生時は `weekly_patch.py` から WARNING ログが出力される。

---

## 関連コード

| 役割 | ファイル |
|---|---|
| 新規配信の判定 | `utils/wordpress.py` の `update_post()` |
| カテゴリ slug マップ取得 | `utils/wordpress.py` の `get_category_slug_map()` |
| フロントURL組み立て | `weekly_patch.py` の `_build_front_url()` |
| 新着の蓄積・通知呼び出し | `weekly_patch.py` の `run()` |
| Slack 通知本体 | `utils/slack.py` の `notify_weekly_new_streaming_summary()` |

---

## 環境変数

| 変数名 | 用途 | 必須 |
|---|---|---|
| `SLACK_WEBHOOK_URL` | Slack Incoming Webhook URL。未設定時は通知をスキップ | △ |
