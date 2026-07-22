import sys
import types


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

from news_bot.sheets import _is_active


def test_is_active_accepts_checkbox_boolean_true():
    assert _is_active(True) is True


def test_is_active_accepts_checkbox_formatted_true_string():
    assert _is_active("TRUE") is True


def test_is_active_rejects_false_values():
    assert _is_active(False) is False
    assert _is_active("FALSE") is False
    assert _is_active("") is False
