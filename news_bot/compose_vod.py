"""週次まとめ本文（WP用HTML）・Xスレッド案の生成（仕様書11.）。

`vod_publish`（main.py）が「VOD配信予定」シートから取得した承認済み行
（sheets.get_approved_vod_items()が返すdictのリスト。列名は仕様書8.②のヘッダーと一致）
を受け取り、WP CPT投稿用の本文とXスレッド投稿案を生成する。

記事構成はGoogle Discover対策として「編集部おすすめ→サービス別作品カード→関連記事」の
順序に統一する（vod-release-calendar-improvements.md 1./2./9.節）。関連記事セクションは
MVP範囲外（16.将来拡張）のため現時点では生成しない。
"""

import html

_SERVICE_LABELS = {
    "netflix": "Netflix",
    "amazon_prime_video": "Prime Video",
    "unext": "U-NEXT",
    "disney_plus": "Disney+",
    "hulu": "Hulu",
    "dmm_tv": "DMM TV",
}


def _service_label(service_key: str) -> str:
    return _SERVICE_LABELS.get(service_key, service_key)


def _work_card_html(item: dict) -> str:
    """統一フォーマットの作品カード（vod-release-calendar-improvements.md 2.節）。"""
    title = html.escape(item.get("タイトル", ""))
    lines = [f"<h4>{title}</h4>", f"<p>配信開始日: {html.escape(str(item.get('配信開始日', '')))}</p>"]
    if item.get("配信種別"):
        lines.append(f"<p>配信種別: {html.escape(item['配信種別'])}</p>")
    if item.get("Katsumascore URL"):
        lines.append(f'<p><a href="{html.escape(item["Katsumascore URL"])}">レビューを読む</a></p>')
    if item.get("公式URL"):
        lines.append(f'<p><a href="{html.escape(item["公式URL"])}">作品詳細を見る</a></p>')
    return "\n".join(lines)


def _editor_pick_html(item: dict) -> str:
    """編集部おすすめセクション（vod-release-calendar-improvements.md 1.節）。"""
    title = html.escape(item.get("タイトル", ""))
    lines = [f"<h3>編集部おすすめ: {title}</h3>"]
    if item.get("編集部コメント"):
        lines.append(f"<p>{html.escape(item['編集部コメント'])}</p>")
    if item.get("Katsumascore URL"):
        lines.append(f'<p><a href="{html.escape(item["Katsumascore URL"])}">レビューはこちら</a></p>')
    return "\n".join(lines)


def build_wp_content(items: list[dict]) -> str:
    """週次まとめ記事のWP投稿本文（HTML）を生成する。

    構成: 編集部おすすめ → サービス別セクション（統一フォーマットの作品カード）。
    「編集部おすすめ」列（チェックボックス）がTrueの行を冒頭にまとめ、
    それ以外はサービスごとにグルーピングして並べる。
    """
    editor_picks = [item for item in items if item.get("編集部おすすめ") is True]
    regular_items = [item for item in items if item.get("編集部おすすめ") is not True]

    sections: list[str] = []
    if editor_picks:
        picks_html = "\n".join(_editor_pick_html(item) for item in editor_picks)
        sections.append(f"<section>{picks_html}</section>")

    by_service: dict[str, list[dict]] = {}
    for item in regular_items:
        by_service.setdefault(item.get("サービス", ""), []).append(item)

    for service_key, service_items in by_service.items():
        cards = "\n".join(_work_card_html(item) for item in service_items)
        label = html.escape(_service_label(service_key))
        sections.append(f"<section><h3>{label}</h3>\n{cards}</section>")

    return "\n\n".join(sections)


def build_wp_title(week_label: str) -> str:
    """記事タイトルを生成する（例: "今週配信開始のVOD作品まとめ（2026年7月第4週）"）。"""
    return f"今週配信開始のVOD作品まとめ（{week_label}）"


def week_label(start_year: int, start_month: int, start_day: int) -> str:
    """対象週の開始日から「YYYY年MM月第N週」形式のラベルを生成する。"""
    week_of_month = (start_day - 1) // 7 + 1
    return f"{start_year}年{start_month}月第{week_of_month}週"


def _thread_line(item: dict) -> str:
    return f"・{item.get('タイトル', '')}（{item.get('配信開始日', '')}〜）"


def build_x_thread(items: list[dict], wp_url: str) -> list[str]:
    """週次まとめのXスレッド投稿案を生成する（仕様書11.3）。

    x-news-bot仕様書4.4のスレッドまとめ方式を踏襲: ①作品リスト → ②リプライにWP記事URL。
    自動投稿はせず、Slackへテンプレートとして送るだけ（approval.notify_vod_weekly_summary()）。
    """
    by_service: dict[str, list[dict]] = {}
    for item in items:
        by_service.setdefault(item.get("サービス", ""), []).append(item)

    lines = ["今週配信開始の注目作品"]
    for service_key, service_items in by_service.items():
        lines.append("")
        lines.append(_service_label(service_key))
        lines.extend(_thread_line(item) for item in service_items)

    main_part = "\n".join(lines)
    reply_part = f"各作品の詳細・レビューはこちら\n{wp_url}"
    return [main_part, reply_part]
