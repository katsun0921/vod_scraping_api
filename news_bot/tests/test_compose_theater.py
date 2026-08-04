from news_bot import compose_theater


def _item(title: str, release_date: str = "2026-08-07", *, priority: str = "A", **extra: object) -> dict:
    item = {
        "タイトル": title,
        "公開日": release_date,
        "SNS優先度(S/A/B/C)": priority,
    }
    item.update(extra)
    return item


def test_featured_also_appears_in_date_section():
    """注目作は冒頭で強調しつつ、公開日別の一覧にも残す。

    冒頭セクションだけに載せて一覧から除外すると、その週の公開一覧としての
    網羅性が崩れる（compose_vod の編集部おすすめと同じ方針）。
    """
    content = compose_theater.build_wp_content(
        [_item("インターステラー", priority="S"), _item("作品B", priority="A")]
    )
    assert content.count("インターステラー") == 2
    assert "今週の注目作: インターステラー" in content


def test_featured_section_omitted_when_none_selected():
    """SNS優先度=Sが無ければ注目作セクション自体を出さない。"""
    content = compose_theater.build_wp_content([_item("作品A"), _item("作品B")])
    assert "今週の注目作" not in content


def test_sections_sorted_by_release_date():
    """公開日別セクションは日付の昇順に並べる。

    シートの行順（取得順）のままだと日付が前後し、「いつ観に行けるか」を
    軸に読む記事として成立しない。
    """
    content = compose_theater.build_wp_content(
        [_item("あとの作品", "2026-08-14"), _item("さきの作品", "2026-08-07")]
    )
    assert content.index("さきの作品") < content.index("あとの作品")


def test_priority_c_excluded_from_content():
    """SNS優先度=C（対象外・情報不足）は承認済みでも本文に載せない。"""
    content = compose_theater.build_wp_content([_item("載る作品"), _item("載らない作品", priority="C")])
    assert "載る作品" in content
    assert "載らない作品" not in content


def test_priority_c_excluded_from_social_posts():
    """本文から除外した作品はSNS投稿案にも載せない（本文とSNSで内容がずれないこと）。"""
    items = [_item("載る作品"), _item("載らない作品", priority="C")]
    x_thread = compose_theater.build_x_thread(items, "https://example.com")
    social = compose_theater.build_social_post(items, "https://example.com")
    assert "載らない作品" not in x_thread[0]
    assert "載らない作品" not in social


def test_date_label_includes_weekday():
    """公開日は曜日つきで表示する（劇場公開は金曜起点のため曜日が判断材料になる）。"""
    content = compose_theater.build_wp_content([_item("作品A", "2026-08-07")])
    assert "8月7日(金)公開" in content


def test_invalid_date_does_not_crash():
    """公開日が不正な行があっても例外にせず、値をそのまま見出しに使う。

    シートは人間が手で編集するため、日付形式が崩れた行が混ざり得る。
    1行の不備で週次まとめ全体が落ちるのは避ける。
    """
    content = compose_theater.build_wp_content([_item("作品A", "未定")])
    assert "作品A" in content


def test_x_thread_has_reply_with_url():
    """Xスレッドは①作品リスト→②リプライにWP記事URLの2部構成にする。"""
    parts = compose_theater.build_x_thread([_item("作品A")], "https://example.com/post")
    assert len(parts) == 2
    assert "作品A" in parts[0]
    assert "https://example.com/post" in parts[1]


def test_social_post_is_single_text_with_url():
    """Facebook等向けは1投稿で完結させる（本文とURLを分割しない）。"""
    social = compose_theater.build_social_post([_item("作品A")], "https://example.com/post")
    assert "作品A" in social
    assert "https://example.com/post" in social


def test_featured_posts_require_katsumascore_url():
    """個別投稿案はレビュー記事があるS作品のみ。リンク先が無ければ投稿の意味が無い。"""
    posts = compose_theater.build_featured_posts(
        [
            _item("リンクあり", priority="S", **{"Katsumascore URL": "https://katsumascore.blog/a"}),
            _item("リンクなし", priority="S"),
            _item("S以外", priority="A", **{"Katsumascore URL": "https://katsumascore.blog/b"}),
        ]
    )
    assert len(posts) == 1
    assert "リンクあり" in posts[0]
    assert "https://katsumascore.blog/a" in posts[0]


def test_html_is_escaped():
    """シート由来の文字列はHTMLエスケープする（人間が手入力するため記号が混ざり得る）。"""
    content = compose_theater.build_wp_content([_item("<script>alert(1)</script>")])
    assert "<script>" not in content
    assert "&lt;script&gt;" in content


def test_wp_title_uses_search_wording():
    """記事タイトルはユーザーの検索語（「今週公開」）に寄せる。

    「劇場公開」は業界の分類語であり検索語ではないため、タイトルには使わない。
    """
    title = compose_theater.build_wp_title(compose_theater.week_label(2026, 8, 7))
    assert title == "今週公開の映画まとめ（2026年8月第1週）"
