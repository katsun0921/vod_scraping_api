import sys
import types
from unittest.mock import MagicMock


gspread = types.ModuleType("gspread")
gspread.Client = object
gspread.Worksheet = object
gspread.authorize = lambda credentials: None
gspread_utils = types.ModuleType("gspread.utils")
gspread_utils.rowcol_to_a1 = lambda row, col: f"R{row}C{col}"
google = types.ModuleType("google")
google_oauth2 = types.ModuleType("google.oauth2")
google_service_account = types.ModuleType("google.oauth2.service_account")
google_service_account.Credentials = object

sys.modules.setdefault("gspread", gspread)
sys.modules.setdefault("gspread.utils", gspread_utils)
sys.modules.setdefault("google", google)
sys.modules.setdefault("google.oauth2", google_oauth2)
sys.modules.setdefault("google.oauth2.service_account", google_service_account)

from news_bot.sheets import NewsBotSheets, _is_active, rowcol_to_a1


def test_is_active_accepts_checkbox_boolean_true():
    assert _is_active(True) is True


def test_is_active_accepts_checkbox_formatted_true_string():
    assert _is_active("TRUE") is True


def test_is_active_rejects_false_values():
    assert _is_active(False) is False
    assert _is_active("FALSE") is False
    assert _is_active("") is False


def _sheets_with_worksheet(ws):
    sheets = NewsBotSheets.__new__(NewsBotSheets)
    sheets._worksheet = MagicMock(return_value=ws)
    return sheets


def test_update_vod_item_slack_ref_preserves_ts_as_raw_text():
    ws = MagicMock()
    ws.find.return_value = types.SimpleNamespace(row=7)
    sheets = _sheets_with_worksheet(ws)

    sheets.update_vod_item_slack_ref(
        "2026-08-27|netflix|作品",
        slack_channel="C123",
        slack_ts="1787100950.123456",
    )

    assert ws.update.call_args_list[-1].kwargs == {
        "range_name": rowcol_to_a1(7, 19),
        "values": [["1787100950.123456"]],
        "raw": True,
    }


def test_update_theater_item_slack_ref_preserves_ts_as_raw_text():
    ws = MagicMock()
    ws.find.return_value = types.SimpleNamespace(row=8)
    sheets = _sheets_with_worksheet(ws)

    sheets.update_theater_item_slack_ref(
        "2026-08-28|作品",
        slack_channel="C123",
        slack_ts="1787100951.654321",
    )

    assert ws.update.call_args_list[-1].kwargs == {
        "range_name": rowcol_to_a1(8, 18),
        "values": [["1787100951.654321"]],
        "raw": True,
    }
