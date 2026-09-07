import sys
import types
from unittest.mock import MagicMock

import pytest


class _StubAPIError(Exception):
    """gspread.exceptions.APIError のスタブ（本物と同じく code 属性を持つ）。"""

    def __init__(self, code: int):
        super().__init__(f"APIError: [{code}]")
        self.code = code


class _StubHTTPClient:
    """gspread.http_client.HTTPClient のスタブ。request はテスト側で差し替える。"""

    def request(self, *args, **kwargs):
        raise AssertionError("テストで差し替えられていない")


gspread = types.ModuleType("gspread")
gspread.Client = object
gspread.Worksheet = object
gspread.authorize = lambda credentials, http_client=None: None
gspread_utils = types.ModuleType("gspread.utils")
gspread_utils.rowcol_to_a1 = lambda row, col: f"R{row}C{col}"
gspread_exceptions = types.ModuleType("gspread.exceptions")
gspread_exceptions.APIError = _StubAPIError
gspread_http_client = types.ModuleType("gspread.http_client")
gspread_http_client.HTTPClient = _StubHTTPClient
google = types.ModuleType("google")
google_oauth2 = types.ModuleType("google.oauth2")
google_service_account = types.ModuleType("google.oauth2.service_account")
google_service_account.Credentials = object

sys.modules.setdefault("gspread", gspread)
sys.modules.setdefault("gspread.utils", gspread_utils)
sys.modules.setdefault("gspread.exceptions", gspread_exceptions)
sys.modules.setdefault("gspread.http_client", gspread_http_client)
sys.modules.setdefault("google", google)
sys.modules.setdefault("google.oauth2", google_oauth2)
sys.modules.setdefault("google.oauth2.service_account", google_service_account)

from news_bot import sheets as sheets_module
from news_bot.sheets import NewsBotSheets, _MAX_ATTEMPTS, _RetryingHTTPClient, _is_active, rowcol_to_a1


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


# gspreadが実際にインストールされている環境ではスタブではなく本物のHTTPClientが
# 基底クラスになる（上のスタブ登録は setdefault のため）。どちらでも動くよう、
# 差し替え対象はMRO上の基底クラスから取る。
_BASE_HTTP_CLIENT = _RetryingHTTPClient.__bases__[0]


def _api_error(code: int):
    """本物のAPIErrorはResponseを要求するため、__init__を通さずcodeだけ持たせて作る。"""
    exc = sheets_module.APIError.__new__(sheets_module.APIError)
    Exception.__init__(exc, f"APIError: [{code}]")
    exc.code = code
    return exc


def _retrying_client(monkeypatch, responses):
    """_RetryingHTTPClient を、指定の応答列を順に返す基底クラスに載せて返す。

    responses の要素が例外ならraiseし、それ以外はそのまま返す。呼び出し回数を数える
    ためにリストを共有する。
    """
    calls = []

    def fake_request(self, *args, **kwargs):
        result = responses[len(calls)]
        calls.append(result)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(_BASE_HTTP_CLIENT, "request", fake_request)
    monkeypatch.setattr(sheets_module.time, "sleep", lambda seconds: None)
    return _RetryingHTTPClient.__new__(_RetryingHTTPClient), calls


def test_request_retries_transient_error_then_succeeds(monkeypatch):
    client, calls = _retrying_client(monkeypatch, [_api_error(503), _api_error(500), "OK"])

    assert client.request("get", "https://example.test") == "OK"
    assert len(calls) == 3


def test_request_does_not_retry_permanent_error(monkeypatch):
    client, calls = _retrying_client(monkeypatch, [_api_error(404), "OK"])

    with pytest.raises(sheets_module.APIError):
        client.request("get", "https://example.test")
    assert len(calls) == 1


def test_request_gives_up_after_max_attempts(monkeypatch):
    client, calls = _retrying_client(
        monkeypatch, [_api_error(503) for _ in range(_MAX_ATTEMPTS)]
    )

    with pytest.raises(sheets_module.APIError):
        client.request("get", "https://example.test")
    assert len(calls) == _MAX_ATTEMPTS


def test_request_waits_with_exponential_backoff(monkeypatch):
    waits = []

    def fake_request(self, *args, **kwargs):
        if len(waits) < 3:
            raise _api_error(503)
        return "OK"

    monkeypatch.setattr(_BASE_HTTP_CLIENT, "request", fake_request)
    monkeypatch.setattr(sheets_module.time, "sleep", lambda seconds: waits.append(seconds))
    monkeypatch.setattr(sheets_module.random, "uniform", lambda low, high: 0.0)
    client = _RetryingHTTPClient.__new__(_RetryingHTTPClient)

    assert client.request("get", "https://example.test") == "OK"
    assert waits == [2.0, 4.0, 8.0]
