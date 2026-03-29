"""Google Sheets 読み書きユーティリティ。"""

import os
from datetime import datetime
from typing import Optional

import gspread
from dotenv import load_dotenv
from google.auth import default
from google.oauth2 import service_account

load_dotenv()

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SHEET_NAME = "VODs"

_sheet_cache: Optional[gspread.Worksheet] = None
_col_index: dict = {}  # 列名 → 列番号（1始まり）のキャッシュ


def _get_client() -> gspread.Client:
    """認証済みの gspread クライアントを返す。

    ローカル環境では GOOGLE_APPLICATION_CREDENTIALS の JSON を使用し、
    Cloud Run では Workload Identity を使用する。
    """
    creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if creds_path and os.path.exists(creds_path):
        creds = service_account.Credentials.from_service_account_file(creds_path, scopes=SCOPES)
    else:
        creds, _ = default(scopes=SCOPES)
    return gspread.authorize(creds)


def get_vods_sheet() -> gspread.Worksheet:
    """VODs シートと列インデックスを初期化して返す（キャッシュあり）。"""
    global _sheet_cache, _col_index
    if _sheet_cache is None:
        spreadsheet_id = os.environ["SPREADSHEET_ID"]
        client = _get_client()
        spreadsheet = client.open_by_key(spreadsheet_id)
        _sheet_cache = spreadsheet.worksheet(SHEET_NAME)
        headers = _sheet_cache.row_values(1)
        _col_index = {h: i + 1 for i, h in enumerate(headers)}
    return _sheet_cache


def get_rows() -> list[dict]:
    """VODs シートの url が入力済みの行を辞書のリストとして返す。

    url が空の行はスキップされる。各行には `_row_num` キーでシート上の実際の
    行番号（ヘッダーを1として2始まり）が付与される。
    """
    sheet = get_vods_sheet()
    records = sheet.get_all_records()
    result = []
    for i, record in enumerate(records):
        if record.get("url"):
            record["_row_num"] = i + 2
            result.append(record)
    return result


def update_row(
    row_index: int,
    status: str,
    price: Optional[float],
    updated_at: str,
) -> None:
    """指定行の status / price / updated_at を更新する。

    Args:
        row_index: シート上の行番号（1始まり、ヘッダーを含む）。
        status: 新しいステータス値。
        price: 新しい価格。None の場合は空文字を書き込む。
        updated_at: 更新日時文字列（"YYYY-MM-DD HH:MM:SS" 形式）。
    """
    sheet = get_vods_sheet()
    sheet.update_cell(row_index, _col_index["status"],     status)
    sheet.update_cell(row_index, _col_index["price"],      price if price is not None else "")
    sheet.update_cell(row_index, _col_index["updated_at"], updated_at)
