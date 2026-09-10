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


def _routine_theater_entries():
    """ルーティン成果物（#83）相当のエントリ。対象週は 2026-09-18〜2026-09-24。"""
    return [
        main.theater_calendar.TheaterEntry(
            title="八つ墓村",
            url="https://example.com/a",
            source="ルーティン(claude)",
            release_date=date(2026, 9, 18),
            distributor="松竹",
        ),
        main.theater_calendar.TheaterEntry(
            title="ブロークン・ヴォイス",
            url="https://example.com/b",
            source="ルーティン(claude)",
            release_date=date(2026, 9, 19),
            distributor="クレプスキュールフィルム",
        ),
    ]


def test_theater_import_follows_routine_dates_when_merged_on_another_weekday():
    """#83 の回帰テスト。

    木曜マージだと next_week_range() は 09-11〜09-17 を返すが、成果物は 09-18〜09-24 分。
    実行日から逆算していた頃はこれで全件 out_of_range になり、シート保存もSlack通知も
    0件のままジョブは成功していた。
    """
    sheets = MagicMock()
    sheets.get_existing_theater_keys.return_value = set()

    with (
        patch.object(main, "NewsBotSheets", return_value=sheets),
        patch.object(main.import_routine, "load_theater_entries", return_value=_routine_theater_entries()),
        patch.object(
            main.theater_calendar, "next_week_range", return_value=(date(2026, 9, 11), date(2026, 9, 17))
        ),
        patch.object(main.wp_client, "find_post_by_title", return_value=None),
        patch.object(main.approval, "notify_theater_discovered", return_value={}) as notify,
    ):
        stats = main.theater_import_cycle()

    assert stats["out_of_range"] == 0
    assert stats["saved"] == 2
    assert stats["notified"] == 2
    assert sheets.append_theater_item.call_count == 2
    notify.assert_called_once()
    assert notify.call_args[0][0] == date(2026, 9, 18)
    assert notify.call_args[0][1] == date(2026, 9, 24)


def test_theater_import_manual_target_start_still_wins():
    """手動再取り込みは成果物の週より優先される（期間外は落ちる）。"""
    sheets = MagicMock()
    sheets.get_existing_theater_keys.return_value = set()

    with (
        patch.object(main, "NewsBotSheets", return_value=sheets),
        patch.object(main.import_routine, "load_theater_entries", return_value=_routine_theater_entries()),
        patch.object(
            main.theater_calendar, "next_week_range", return_value=(date(2026, 9, 11), date(2026, 9, 17))
        ),
        patch.object(main.wp_client, "find_post_by_title", return_value=None),
        patch.object(main.approval, "notify_theater_discovered", return_value={}) as notify,
    ):
        stats = main.theater_import_cycle(date(2026, 9, 11))

    assert stats == {"discovered": 2, "out_of_range": 2, "duplicate": 0, "saved": 0, "notified": 0}
    sheets.append_theater_item.assert_not_called()
    notify.assert_not_called()


def test_theater_import_logs_each_out_of_range_entry(caplog):
    """どの日付が外れたかを1件ずつ残す。個別のスキップ自体は異常ではないのでINFO。"""
    sheets = MagicMock()
    sheets.get_existing_theater_keys.return_value = set()

    with (
        patch.object(main, "NewsBotSheets", return_value=sheets),
        patch.object(main.import_routine, "load_theater_entries", return_value=_routine_theater_entries()),
        patch.object(
            main.theater_calendar, "next_week_range", return_value=(date(2026, 9, 11), date(2026, 9, 17))
        ),
        patch.object(main.wp_client, "find_post_by_title", return_value=None),
        caplog.at_level(logging.INFO),
    ):
        main.theater_import_cycle(date(2026, 9, 11))

    assert "対象期間外のためスキップ: 八つ墓村 (2026-09-18)" in caplog.text
    assert "対象期間外のためスキップ: ブロークン・ヴォイス (2026-09-19)" in caplog.text


def test_all_out_of_range_is_warned():
    """全件が期間外なら警告する。#76 / #83 はこれが無く成功扱いのまま見逃された。"""
    stats = {"discovered": 7, "out_of_range": 7, "duplicate": 0, "saved": 0, "notified": 0}
    logger = logging.getLogger(main.__name__)

    with patch.object(logger, "warning") as warn:
        main._warn_if_all_out_of_range(stats, date(2026, 9, 11), date(2026, 9, 17), "theater_import_cycle")

    warn.assert_called_once()
    assert "すべてが対象期間" in warn.call_args[0][0]


def test_partial_out_of_range_is_not_warned():
    """一部だけ期間外なのは通常運転（VODのX抽出分）。警告しない。"""
    stats = {"discovered": 10, "out_of_range": 6, "duplicate": 0, "saved": 4, "notified": 4}
    logger = logging.getLogger(main.__name__)

    with patch.object(logger, "warning") as warn:
        main._warn_if_all_out_of_range(stats, date(2026, 9, 14), date(2026, 9, 20), "vod_import_cycle")

    warn.assert_not_called()


def test_zero_discovered_is_not_warned():
    """収集0件は成果物が空だっただけ。警告しない。"""
    stats = {"discovered": 0, "out_of_range": 0, "duplicate": 0, "saved": 0, "notified": 0}
    logger = logging.getLogger(main.__name__)

    with patch.object(logger, "warning") as warn:
        main._warn_if_all_out_of_range(stats, date(2026, 9, 14), date(2026, 9, 20), "vod_import_cycle")

    warn.assert_not_called()


def test_vod_import_follows_routine_dates():
    """VOD側も成果物の日付から対象週を決める（X抽出分は同じ週でフィルタされる）。"""
    sheets = MagicMock()
    sheets.get_existing_vod_keys.return_value = set()
    entries = [
        main.vod_calendar.VodEntry(
            title="作品",
            service="netflix",
            available_from=date(2026, 9, 21),
            source="ルーティン(claude)",
        )
    ]

    with (
        patch.object(main, "NewsBotSheets", return_value=sheets),
        patch.object(main.import_routine, "load_vod_entries", return_value=entries),
        patch.object(
            main.vod_calendar, "next_week_range", return_value=(date(2026, 9, 14), date(2026, 9, 20))
        ),
        patch.object(main, "_fetch_vod_x_entries", return_value=[]),
        patch.object(main.wp_client, "find_post_by_title", return_value=None),
        patch.object(main.approval, "notify_vod_discovered", return_value={}) as notify,
    ):
        stats = main.vod_import_cycle()

    assert stats["out_of_range"] == 0
    assert stats["saved"] == 1
    notify.assert_called_once()
    assert notify.call_args[0][0] == date(2026, 9, 21)
    assert notify.call_args[0][1] == date(2026, 9, 27)
