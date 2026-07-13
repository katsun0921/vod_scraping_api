"""Google Sheets I/O クライアント。

仕様書「5. Googleスプレッドシート構成」のシート群を読み書きする。
ワークシート名は仕様書の見出しからマル数字を除いたものと一致させる。

環境変数:
    GOOGLE_SHEETS_SPREADSHEET_ID   : 対象スプレッドシートID
    GOOGLE_SHEETS_CREDENTIALS_JSON : サービスアカウントJSON（1行の文字列）

シート「承認キュー」は仕様書には無いが、S/A判定の承認フロー（4.4）を
cron実行をまたいで追跡するために本実装で追加した内部管理用シート。
列: ニュースURL / ランク / 本文 / リプライ本文 / SlackチャンネルID / Slackメッセージts /
    通知日時 / ステータス（pending/approved/cancelled/posted）
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]

SHEET_TITLES = [
    "タイトル一覧",
    "公式X一覧",
    "ニュースソース",
    "ニュース取得",
    "X投稿履歴",
    "YouTube Shorts",
    "承認キュー",
]

_NEWS_SOURCES_HEADER = ["ID", "名称", "URL", "カテゴリ", "取得方法", "取得間隔", "有効/無効", "規約確認済み"]
_NEWS_ITEMS_HEADER = ["取得日時", "タイトル", "URL", "媒体", "関連タイトル", "概要", "重要度(S/A/B/D)", "投稿状態", "重複フラグ", "AI判定理由"]
_POST_HISTORY_HEADER = ["投稿日時", "ニュースID", "本文", "リプライ本文", "インプレッション", "いいね数", "承認者"]
_APPROVAL_QUEUE_HEADER = [
    "ニュースURL", "ランク", "本文", "リプライ本文",
    "SlackチャンネルID", "Slackメッセージts", "通知日時", "ステータス",
]


def _client() -> gspread.Client:
    creds_json = os.environ["GOOGLE_SHEETS_CREDENTIALS_JSON"]
    info = json.loads(creds_json)
    credentials = Credentials.from_service_account_info(info, scopes=_SCOPES)
    return gspread.authorize(credentials)


class NewsBotSheets:
    """スプレッドシート操作をまとめたクライアント。"""

    def __init__(self) -> None:
        client = _client()
        spreadsheet_id = os.environ["GOOGLE_SHEETS_SPREADSHEET_ID"]
        self._spreadsheet = client.open_by_key(spreadsheet_id)

    def _worksheet(self, title: str) -> gspread.Worksheet:
        return self._spreadsheet.worksheet(title)

    def get_active_sources(self) -> list[dict]:
        """「ニュースソース」シートから有効なRSSソースを取得する。

        取得方法="RSS"、有効/無効="有効"、規約確認済み="済" の行のみ対象。
        """
        rows = self._worksheet("ニュースソース").get_all_records()
        return [
            row for row in rows
            if row.get("取得方法") == "RSS"
            and row.get("有効/無効") == "有効"
            and row.get("規約確認済み") == "済"
        ]

    def get_existing_urls(self) -> set[str]:
        """「ニュース取得」シートに既に保存済みのURL集合を返す（一次重複チェック用）。"""
        rows = self._worksheet("ニュース取得").get_all_records()
        return {row["URL"] for row in rows if row.get("URL")}

    def append_news_item(
        self,
        *,
        title: str,
        url: str,
        source: str,
        related_title: str = "",
        summary: str = "",
        rank: str = "",
        post_status: str = "",
        is_duplicate: bool = False,
        judge_reason: str = "",
    ) -> None:
        """「ニュース取得」シートに1件追記する。"""
        row = [
            datetime.now(timezone.utc).isoformat(),
            title,
            url,
            source,
            related_title,
            summary,
            rank,
            post_status,
            "重複" if is_duplicate else "",
            judge_reason,
        ]
        self._worksheet("ニュース取得").append_row(row, value_input_option="USER_ENTERED")

    def update_news_item_status(self, url: str, *, post_status: str) -> None:
        """URLをキーに「ニュース取得」シートの投稿状態列を更新する。"""
        ws = self._worksheet("ニュース取得")
        cell = ws.find(url, in_column=_NEWS_ITEMS_HEADER.index("URL") + 1)
        if cell is None:
            logger.warning("ニュース取得シートにURLが見つかりません: %s", url)
            return
        status_col = _NEWS_ITEMS_HEADER.index("投稿状態") + 1
        ws.update_cell(cell.row, status_col, post_status)

    def append_post_history(
        self, *, news_id: str, honbun: str, reply: str, approver: str = ""
    ) -> None:
        """「X投稿履歴」シートに投稿結果を記録する。"""
        row = [
            datetime.now(timezone.utc).isoformat(),
            news_id,
            honbun,
            reply,
            "",  # インプレッション（将来）
            "",  # いいね数（将来）
            approver,
        ]
        self._worksheet("X投稿履歴").append_row(row, value_input_option="USER_ENTERED")

    def enqueue_approval(
        self,
        *,
        url: str,
        rank: str,
        honbun: str,
        reply: str,
        slack_channel_id: str,
        slack_ts: str,
    ) -> None:
        """承認待ちアイテムを「承認キュー」に登録する。"""
        row = [
            url, rank, honbun, reply,
            slack_channel_id, slack_ts,
            datetime.now(timezone.utc).isoformat(),
            "pending",
        ]
        self._worksheet("承認キュー").append_row(row, value_input_option="USER_ENTERED")

    def get_pending_approvals(self) -> list[dict]:
        """ステータスが pending の承認待ちアイテムを取得する。"""
        rows = self._worksheet("承認キュー").get_all_records()
        return [row for row in rows if row.get("ステータス") == "pending"]

    def update_approval_status(self, url: str, *, status: str) -> None:
        """URLをキーに「承認キュー」シートのステータス列を更新する。"""
        ws = self._worksheet("承認キュー")
        cell = ws.find(url, in_column=_APPROVAL_QUEUE_HEADER.index("ニュースURL") + 1)
        if cell is None:
            logger.warning("承認キューにURLが見つかりません: %s", url)
            return
        status_col = _APPROVAL_QUEUE_HEADER.index("ステータス") + 1
        ws.update_cell(cell.row, status_col, status)


def ensure_sheets_exist(spreadsheet_id: Optional[str] = None) -> None:
    """初回セットアップ用: 必要なワークシートとヘッダー行が無ければ作成する。"""
    client = _client()
    spreadsheet = client.open_by_key(spreadsheet_id or os.environ["GOOGLE_SHEETS_SPREADSHEET_ID"])
    existing_titles = {ws.title for ws in spreadsheet.worksheets()}

    headers_by_title = {
        "ニュースソース": _NEWS_SOURCES_HEADER,
        "ニュース取得": _NEWS_ITEMS_HEADER,
        "X投稿履歴": _POST_HISTORY_HEADER,
        "承認キュー": _APPROVAL_QUEUE_HEADER,
    }
    for title, header in headers_by_title.items():
        if title not in existing_titles:
            ws = spreadsheet.add_worksheet(title=title, rows=1000, cols=len(header))
            ws.append_row(header, value_input_option="USER_ENTERED")
            logger.info("シート作成: %s", title)
