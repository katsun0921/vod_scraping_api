"""NewsBotSheets._fill_blank_cells の挙動（空欄のみ補完し既存値は保護する）。

gspread への接続は行わず、Worksheet を差し替えて呼び出し内容だけを検証する。
"""

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

from news_bot.sheets import _THEATER_ITEMS_HEADER, NewsBotSheets


class _FakeCell:
    def __init__(self, row):
        self.row = row


class _FakeWorksheet:
    def __init__(self, row_values, found_row=5):
        self._row_values = row_values
        self._found_row = found_row
        self.batched = None

    def find(self, value, in_column=None):
        return _FakeCell(self._found_row)

    def row_values(self, row):
        return self._row_values

    def batch_update(self, updates, value_input_option=None):
        self.batched = updates


def _sheets_with(ws):
    """__init__（Sheets接続）を通さずにインスタンスを作り、_worksheetだけ差し替える。"""
    obj = NewsBotSheets.__new__(NewsBotSheets)
    obj._worksheet = lambda title: ws
    return obj


def _row(**overrides):
    """ヘッダー順に並んだ行データを組み立てる（指定が無い列は空欄）。"""
    values = {name: "" for name in _THEATER_ITEMS_HEADER}
    values.update(overrides)
    return [values[name] for name in _THEATER_ITEMS_HEADER]


def test_fills_only_blank_cells():
    ws = _FakeWorksheet(_row(タイトル="白パンと独裁者", 重複キー="k"))
    filled = _sheets_with(ws).fill_blank_theater_item(
        "k", {"配給": "ビターズ・エンド", "公式URL": "https://example.com/"}
    )

    assert filled == ["配給", "公式URL"]
    assert ws.batched == [
        {"range": f"R5C{_THEATER_ITEMS_HEADER.index('配給') + 1}", "values": [["ビターズ・エンド"]]},
        {"range": f"R5C{_THEATER_ITEMS_HEADER.index('公式URL') + 1}", "values": [["https://example.com/"]]},
    ]


def test_never_overwrites_existing_value():
    """人間がシート上で手修正した値をAIの出力で壊さない。"""
    ws = _FakeWorksheet(_row(配給="オンリー・ハーツ", 重複キー="k"))
    filled = _sheets_with(ws).fill_blank_theater_item("k", {"配給": "ビターズ・エンド"})

    assert filled == []
    assert ws.batched is None


def test_ignores_empty_new_value():
    """新しい値が空なら書き込まない（空欄を空欄で上書きしない）。"""
    ws = _FakeWorksheet(_row(重複キー="k"))
    assert _sheets_with(ws).fill_blank_theater_item("k", {"配給": ""}) == []
    assert ws.batched is None


def test_treats_trailing_missing_columns_as_blank():
    """row_values()は末尾の空セルを詰めて返すため、列数を超える添字も空欄として扱う。"""
    ws = _FakeWorksheet(["2026-08-07", "2026-08-07", "白パンと独裁者"])
    filled = _sheets_with(ws).fill_blank_theater_item("k", {"配給": "ビターズ・エンド"})

    assert filled == ["配給"]


def test_returns_empty_when_key_not_found():
    ws = _FakeWorksheet(_row())
    ws.find = lambda value, in_column=None: None

    assert _sheets_with(ws).fill_blank_theater_item("missing", {"配給": "X"}) == []
    assert ws.batched is None
