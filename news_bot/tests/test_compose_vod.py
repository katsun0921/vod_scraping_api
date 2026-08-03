from news_bot import compose_vod


def _item(title: str, service: str, *, pick: bool = False, **extra: object) -> dict:
    item = {
        "タイトル": title,
        "配信開始日": "2026-08-10",
        "サービス": service,
        "配信種別": "見放題",
    }
    if pick:
        item["編集部おすすめ"] = True
    item.update(extra)
    return item


def test_editor_pick_also_appears_in_service_section():
    """編集部おすすめは冒頭で強調しつつ、サービス別の配信一覧にも残す。

    冒頭セクションだけに載せて一覧から除外すると、その週の配信一覧としての
    網羅性が崩れる（改善提案1.は「記事冒頭に追加する」であって置き換えではない）。
    """
    content = compose_vod.build_wp_content(
        [_item("インターステラー", "netflix", pick=True), _item("作品B", "unext")]
    )
    assert content.count("インターステラー") == 2
    assert "編集部おすすめ: インターステラー" in content


def test_editor_pick_section_omitted_when_none_selected():
    content = compose_vod.build_wp_content([_item("作品A", "netflix")])
    assert "編集部おすすめ" not in content
    assert "<h3>Netflix</h3>" in content


def test_items_are_grouped_by_service():
    content = compose_vod.build_wp_content(
        [_item("作品A", "netflix"), _item("作品B", "netflix"), _item("作品C", "unext")]
    )
    assert content.count("<h3>Netflix</h3>") == 1
    assert content.count("<h3>U-NEXT</h3>") == 1


def test_review_link_only_when_katsumascore_url_exists():
    with_review = compose_vod.build_wp_content(
        [_item("作品A", "netflix", **{"Katsumascore URL": "https://example.com/a"})]
    )
    without_review = compose_vod.build_wp_content([_item("作品B", "netflix")])
    assert "レビューを読む" in with_review
    assert "レビューを読む" not in without_review


def test_titles_are_html_escaped():
    content = compose_vod.build_wp_content([_item("<script>x</script>", "netflix")])
    assert "<script>" not in content
    assert "&lt;script&gt;" in content


def test_x_thread_lists_every_item_and_ends_with_wp_url():
    parts = compose_vod.build_x_thread(
        [_item("インターステラー", "netflix", pick=True), _item("作品B", "unext")],
        "https://katsumascore.blog/vod-news/w2",
    )
    assert len(parts) == 2
    assert "インターステラー" in parts[0]
    assert "作品B" in parts[0]
    assert parts[1].endswith("https://katsumascore.blog/vod-news/w2")


def test_week_label_and_title():
    assert compose_vod.week_label(2026, 8, 10) == "2026年8月第2週"
    assert compose_vod.build_wp_title("2026年8月第2週") == "今週配信開始のVOD作品まとめ（2026年8月第2週）"
