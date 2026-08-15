"""劇場公開まとめ本文（WP用HTML）・SNS投稿案の生成（劇場仕様書11.）。

`theater_publish`（main.py）が「劇場公開予定」シートから取得した承認済み行
（sheets.get_approved_theater_items()が返すdictのリスト。列名は仕様書8.のヘッダーと一致）
を受け取り、WP CPT投稿用の本文とSNS投稿案を生成する。

compose_vod.py と同型だが、区切り方が異なる。VODは「どのサービスで観られるか」が
主要な関心事のためサービス別に区切るのに対し、劇場公開は「いつ観に行けるか」が
主要な関心事のため公開日別に区切る。

「注目作」の扱いもVODと異なる。VODは「編集部おすすめ」列（チェックボックス）を持つが、
劇場公開予定シートにはこの列が無いため、仕様書11.2に従い SNS優先度=S を注目作として扱う。
"""

import html
from collections import OrderedDict
from datetime import date

# 仕様書11.2「注目作個別投稿」の対象。SNS優先度がこの値の作品を記事冒頭とSNS個別投稿に回す。
_FEATURED_PRIORITY = "S"

# 週次まとめ本文に載せる優先度。C（対象外・情報不足・重複）は承認済みでも本文からは省く。
_EXCLUDED_PRIORITIES = {"C"}

_WEEKDAY_JA = ["月", "火", "水", "木", "金", "土", "日"]


def _date_label(release_date: str) -> str:
    """"2026-08-08" -> "8月8日(金)"。パースできない値はそのまま返す。"""
    try:
        d = date.fromisoformat(release_date)
    except (ValueError, TypeError):
        return release_date
    return f"{d.month}月{d.day}日({_WEEKDAY_JA[d.weekday()]})"


def _visible_items(items: list[dict]) -> list[dict]:
    """本文・投稿案に載せる行だけに絞る（SNS優先度=Cを除外）。"""
    return [item for item in items if item.get("SNS優先度(S/A/B/C)") not in _EXCLUDED_PRIORITIES]


def featured_items(items: list[dict]) -> list[dict]:
    """注目作（SNS優先度=S）を返す（仕様書11.2）。"""
    return [item for item in items if item.get("SNS優先度(S/A/B/C)") == _FEATURED_PRIORITY]


def _work_card_html(item: dict) -> str:
    """統一フォーマットの作品カード。compose_vod._work_card_html と対の構成。"""
    title = html.escape(item.get("タイトル", ""))
    lines = [f"<h4>{title}</h4>"]

    if item.get("原題"):
        lines.append(f"<p>原題: {html.escape(item['原題'])}</p>")

    meta = []
    if item.get("国"):
        meta.append(html.escape(item["国"]))
    if item.get("配給"):
        meta.append(f"配給: {html.escape(item['配給'])}")
    if meta:
        lines.append(f"<p>{' / '.join(meta)}</p>")

    if item.get("Katsumascore URL"):
        lines.append(f'<p><a href="{html.escape(item["Katsumascore URL"])}">レビューを読む</a></p>')
    if item.get("公式URL"):
        lines.append(f'<p><a href="{html.escape(item["公式URL"])}">公式サイトを見る</a></p>')
    if item.get("予告URL"):
        lines.append(f'<p><a href="{html.escape(item["予告URL"])}">予告編を観る</a></p>')
    return "\n".join(lines)


def _featured_html(item: dict) -> str:
    """注目作セクション（記事冒頭）。compose_vod._editor_pick_html と対の構成。"""
    title = html.escape(item.get("タイトル", ""))
    lines = [f"<h3>今週の注目作: {title}</h3>"]

    release = item.get("公開日", "")
    if release:
        lines.append(f"<p>{html.escape(_date_label(str(release)))}公開</p>")
    if item.get("メモ"):
        lines.append(f"<p>{html.escape(item['メモ'])}</p>")
    if item.get("Katsumascore URL"):
        lines.append(f'<p><a href="{html.escape(item["Katsumascore URL"])}">レビューはこちら</a></p>')
    return "\n".join(lines)


def _group_by_date(items: list[dict]) -> "OrderedDict[str, list[dict]]":
    """公開日でグルーピングし、日付の昇順で返す。

    dictの挿入順ではなく公開日順に並べるのは、劇場公開が「いつ観に行けるか」を
    軸に読まれるため。シートの行順（取得順）のままだと日付が前後する。
    """
    grouped: dict[str, list[dict]] = {}
    for item in items:
        grouped.setdefault(str(item.get("公開日", "")), []).append(item)
    return OrderedDict(sorted(grouped.items(), key=lambda kv: kv[0]))


def build_wp_content(items: list[dict]) -> str:
    """週次まとめ記事のWP投稿本文（HTML）を生成する。

    構成: 注目作（SNS優先度=S） → 公開日別セクション（統一フォーマットの作品カード）。

    注目作に選ばれた作品も公開日別セクションには通常どおり掲載する。冒頭の注目作は
    「今週の一覧」に対する強調であって置き換えではないため（compose_vod.build_wp_content
    と同じ方針）、ここで除外すると公開一覧としての網羅性が崩れる。
    """
    visible = _visible_items(items)

    sections: list[str] = []
    featured = featured_items(visible)
    if featured:
        featured_html = "\n".join(_featured_html(item) for item in featured)
        sections.append(f"<section>{featured_html}</section>")

    for release_date, date_items in _group_by_date(visible).items():
        cards = "\n".join(_work_card_html(item) for item in date_items)
        label = html.escape(_date_label(release_date))
        sections.append(f"<section><h3>{label}公開</h3>\n{cards}</section>")

    return "\n\n".join(sections)


def build_wp_title(week_label: str) -> str:
    """記事タイトルを生成する（例: "2026年8月第1週公開の映画まとめ"）。

    「劇場公開」ではなく「公開」としているのは、ユーザーの検索語が
    「今週公開 映画」「〇〇 公開日」であり、「劇場公開」は業界の分類語であるため。
    """
    return f"{week_label}公開の映画まとめ"


def week_label(start_year: int, start_month: int, start_day: int) -> str:
    """対象週の開始日から「YYYY年MM月第N週」形式のラベルを生成する。"""
    week_of_month = (start_day - 1) // 7 + 1
    return f"{start_year}年{start_month}月第{week_of_month}週"


def _thread_line(item: dict) -> str:
    return f"・{item.get('タイトル', '')}"


def build_x_thread(items: list[dict], wp_url: str) -> list[str]:
    """週次まとめのXスレッド投稿案を生成する（仕様書11.1）。

    x-news-bot仕様書4.4のスレッドまとめ方式を踏襲: ①作品リスト → ②リプライにWP記事URL。
    自動投稿はせず、Slackへテンプレートとして送るだけ（approval.notify_theater_weekly_summary()）。
    """
    visible = _visible_items(items)

    lines = ["今週公開の注目映画"]
    for release_date, date_items in _group_by_date(visible).items():
        lines.append("")
        lines.append(_date_label(release_date))
        lines.extend(_thread_line(item) for item in date_items)
    lines.append("")
    lines.append("週末に観るならどれ？")

    main_part = "\n".join(lines)
    reply_part = f"各作品のレビュー・評価はこちら\n{wp_url}"
    return [main_part, reply_part]


def build_social_post(items: list[dict], wp_url: str) -> str:
    """Facebook / Threads / Bluesky 等へ手動投稿するための単体テキストを生成する。

    Xはスレッド前提で1投稿あたりの字数制限が厳しいためbuild_x_thread()で分割するが、
    これらのSNSは1投稿に本文とURLをまとめられる。同じ内容を人間が投稿先ごとに
    組み直さなくて済むよう、リンク込みの完成形を別途生成する。

    Facebook APIとの連携は行わない（アプリ審査・ページ権限の取得が必要で、
    投稿頻度に対して運用コストが見合わないため）。Slack経由の手動投稿を前提とする。
    """
    visible = _visible_items(items)

    lines = ["今週公開の映画をまとめました。", ""]
    for release_date, date_items in _group_by_date(visible).items():
        lines.append(f"【{_date_label(release_date)}】")
        lines.extend(_thread_line(item) for item in date_items)
        lines.append("")

    lines.append("あらすじ・レビュー・評価はこちら")
    lines.append(wp_url)
    return "\n".join(lines)


def build_featured_posts(items: list[dict]) -> list[str]:
    """注目作の個別投稿案を生成する（仕様書11.2）。

    対象は SNS優先度=S かつ Katsumascore URL がある作品。URLが無い作品を除くのは、
    個別投稿の目的が単体作品ページへの送客であり、リンク先が無ければ投稿の意味が
    無いため（週次まとめには引き続き掲載される）。
    """
    posts = []
    for item in featured_items(_visible_items(items)):
        url = item.get("Katsumascore URL")
        if not url:
            continue
        title = item.get("タイトル", "")
        release = _date_label(str(item.get("公開日", "")))
        posts.append(
            f"今週公開の注目作。\n"
            f"『{title}』が{release}、いよいよ劇場公開。\n\n"
            f"観る前に押さえておきたいポイントはこちら\n{url}"
        )
    return posts
