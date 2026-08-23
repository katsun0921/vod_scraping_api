import logging
import sys
import types
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

# Python 3.13では標準ライブラリimghdrが削除されたが、プロジェクトの実行環境
# Python 3.11で使うtweepy 4.14.0がimport時に参照する。ここでは外部通信を行わず
# main.vod_publish_cycleだけをテストするため、importを成立させる最小スタブで十分。
sys.modules.setdefault("imghdr", types.ModuleType("imghdr"))

from news_bot import main


def test_vod_resolve_approvals_updates_post_status_column():
    sheets = MagicMock()
    sheets.get_pending_vod_items_with_slack_ref.return_value = [
        {
            "重複キー": "2026-08-27|netflix|作品",
            "SlackチャンネルID": "C1",
            "Slackメッセージts": "1.2",
        }
    ]

    with (
        patch.object(main, "NewsBotSheets", return_value=sheets),
        patch.object(main.approval, "resolve_approvals", return_value=["2026-08-27|netflix|作品"]),
    ):
        stats = main.vod_resolve_approvals_cycle()

    sheets.update_vod_item_status.assert_called_once_with(
        "2026-08-27|netflix|作品", post_status="承認済み"
    )
    assert stats == {"checked": 1, "approved": 1}


def test_vod_publish_wp_failure_is_logged_and_keeps_items_approved(caplog):
    sheets = MagicMock()
    sheets.get_approved_vod_items.return_value = [{"重複キー": "2026-08-17|unext|作品"}]

    with (
        patch.object(main, "NewsBotSheets", return_value=sheets),
        patch.object(main.compose_vod, "week_label", return_value="2026年8月第3週"),
        patch.object(main.compose_vod, "build_wp_title", return_value="タイトル"),
        patch.object(main.compose_vod, "build_wp_content", return_value="<p>本文</p>"),
        patch.object(main.wp_client, "create_post", side_effect=RuntimeError("WP 404")),
        patch.object(main.approval, "notify_vod_weekly_summary") as notify,
        caplog.at_level(logging.ERROR),
    ):
        with pytest.raises(RuntimeError, match="WP 404"):
            main.vod_publish_cycle(date(2026, 8, 17))

    assert "VOD週次まとめWP投稿失敗" in caplog.text
    assert "WP 404" in caplog.text
    sheets.update_vod_item_status.assert_not_called()
    notify.assert_not_called()


def test_vod_publish_uses_date_based_post_slug():
    sheets = MagicMock()
    sheets.get_approved_vod_items.return_value = [{"重複キー": "2026-08-24|unext|作品"}]

    with (
        patch.object(main, "NewsBotSheets", return_value=sheets),
        patch.object(main.compose_vod, "week_label", return_value="2026年8月第4週"),
        patch.object(main.compose_vod, "build_wp_title", return_value="タイトル"),
        patch.object(main.compose_vod, "build_wp_content", return_value="<p>本文</p>"),
        patch.object(main.compose_vod, "build_x_thread", return_value=[]),
        patch.object(main.wp_client, "create_post", return_value={"link": "https://example.com/vod/"}) as create,
        patch.object(main.approval, "notify_vod_weekly_summary"),
    ):
        main.vod_publish_cycle(date(2026, 8, 24))

    assert create.call_args.kwargs["slug"] == "vod-release-2026-08-24"


def test_theater_publish_uses_date_based_post_slug():
    sheets = MagicMock()
    sheets.get_approved_theater_items.return_value = [{"重複キー": "2026-08-28|作品"}]

    with (
        patch.object(main, "NewsBotSheets", return_value=sheets),
        patch.object(main.compose_theater, "week_label", return_value="2026年8月第4週"),
        patch.object(main.compose_theater, "build_wp_title", return_value="タイトル"),
        patch.object(main.compose_theater, "build_wp_content", return_value="<p>本文</p>"),
        patch.object(main.compose_theater, "build_x_thread", return_value=[]),
        patch.object(main.compose_theater, "build_social_post", return_value=""),
        patch.object(main.compose_theater, "build_featured_posts", return_value=[]),
        patch.object(main.wp_client, "create_post", return_value={"link": "https://example.com/theater/"}) as create,
        patch.object(main.approval, "notify_theater_weekly_summary"),
    ):
        main.theater_publish_cycle(date(2026, 8, 28))

    assert create.call_args.kwargs["slug"] == "theater-release-2026-08-28"
