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

import gspread
from gspread.utils import rowcol_to_a1
from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]

SHEET_TITLES = [
    "タイトル一覧",
    "公式X一覧",
    "RSS一覧",
    "ニュース取得",
    "X投稿履歴",
    "YouTube Shorts",
    "承認キュー",
]

_NEWS_SOURCES_HEADER = ["ID", "名称", "URL", "カテゴリ", "取得間隔", "有効/無効", "規約確認済み"]
_X_ACCOUNTS_HEADER = [
    "ID", "アカウント名", "Xハンドル", "URL", "種別", "地域", "有効/無効",
    "user_id", "since_id", "最終取得日時",
]
_NEWS_ITEMS_HEADER = [
    "取得日時", "タイトル", "URL", "媒体", "関連タイトル", "概要",
    "重要度(S/A/B/D)", "投稿状態", "重複フラグ", "AI判定理由",
    "Claude判定", "Claude理由", "ChatGPT判定", "ChatGPT理由", "Grok判定", "Grok理由",
]
# judge.pyのプロバイダーキー → 上記ヘッダーの列名接頭辞
_JUDGE_PROVIDER_COLUMNS = {"claude": "Claude", "openai": "ChatGPT", "grok": "Grok"}
_POST_HISTORY_HEADER = ["投稿日時", "ニュースID", "本文", "リプライ本文", "インプレッション", "いいね数", "承認者"]
_APPROVAL_QUEUE_HEADER = [
    "ニュースURL", "ランク", "本文", "リプライ本文",
    "SlackチャンネルID", "Slackメッセージts", "通知日時", "ステータス",
]

# main.py が実際に読み書きするシートのみ自動作成する。
# タイトル一覧・YouTube Shorts はMVPスコープ外のため対象外。
_AUTO_CREATED_HEADERS = {
    "RSS一覧": _NEWS_SOURCES_HEADER,
    "ニュース取得": _NEWS_ITEMS_HEADER,
    "X投稿履歴": _POST_HISTORY_HEADER,
    "承認キュー": _APPROVAL_QUEUE_HEADER,
    "公式X一覧": _X_ACCOUNTS_HEADER,
}


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
        self._ensure_sheets_exist()

    def _ensure_sheets_exist(self) -> None:
        """必要なワークシートとヘッダー行が無ければ作成する（初回実行時セットアップ）。"""
        existing_titles = {ws.title for ws in self._spreadsheet.worksheets()}
        for title, header in _AUTO_CREATED_HEADERS.items():
            if title not in existing_titles:
                ws = self._spreadsheet.add_worksheet(title=title, rows=1000, cols=len(header))
                ws.append_row(header, value_input_option="USER_ENTERED")
                logger.info("シート作成: %s", title)

    def _worksheet(self, title: str) -> gspread.Worksheet:
        return self._spreadsheet.worksheet(title)

    def get_active_sources(self) -> list[dict]:
        """「RSS一覧」シートから有効なRSSソースを取得する。

        有効/無効="有効"、規約確認済み="済" の行のみ対象。
        """
        rows = self._worksheet("RSS一覧").get_all_records()
        return [
            row for row in rows
            if row.get("有効/無効") == "有効"
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
        provider_results: dict[str, dict] | None = None,
    ) -> None:
        """「ニュース取得」シートに1件追記する。

        provider_results: judge.judge()が返す {プロバイダー名: {rank, reason, ...}}。
        比較テスト用に、判定に使われなかったプロバイダーの列は空欄のまま残す。
        """
        provider_results = provider_results or {}
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
        for provider in _JUDGE_PROVIDER_COLUMNS:
            result = provider_results.get(provider)
            row.append(result["rank"] if result else "")
            row.append(result["reason"] if result else "")
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
        # RAW指定: SlackメッセージtsはUSER_ENTEREDだとSheetsに数値変換され、
        # 浮動小数点の丸めでreactions.get時にmessage_not_foundとなるため文字列のまま保存する。
        self._worksheet("承認キュー").append_row(row, value_input_option="RAW")

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

    def get_active_x_accounts(self, region: str) -> list[dict]:
        """「公式X一覧」から指定地域の有効なアカウントを取得する（fetch_x.fetch_all_xへ渡す形に整形）。

        Args:
            region: "地域"列の値（例: "日本" / "アメリカ"）

        user_id・since_idはTwitterのsnowflake ID（19桁）のため、gspreadのget_all_records()
        が数値に見える文字列を自動でint/floatへ変換してしまう挙動（numericise）を
        numericise_ignoreで無効化する。ここで文字列化を怠ると、新着0件の実行時に
        既存値をそのままSheetsへ書き戻す際に数値として書き込まれ桁落ちする。
        """
        user_id_col = _X_ACCOUNTS_HEADER.index("user_id") + 1
        since_id_col = _X_ACCOUNTS_HEADER.index("since_id") + 1
        rows = self._worksheet("公式X一覧").get_all_records(
            numericise_ignore=[user_id_col, since_id_col]
        )
        return [
            {
                "名称": row["アカウント名"],
                "Xハンドル": row["Xハンドル"],
                "user_id": str(row["user_id"]) if row.get("user_id") else None,
                "since_id": str(row["since_id"]) if row.get("since_id") else None,
            }
            for row in rows
            if row.get("地域") == region and row.get("有効/無効") == "有効"
        ]

    def update_x_account_state(self, handle: str, *, user_id: str, since_id: str | None) -> None:
        """Xハンドルをキーに「公式X一覧」のuser_id/since_id/最終取得日時を更新する。

        user_id・since_idはTwitterのsnowflake ID（19桁の整数文字列）のため、
        USER_ENTEREDで書き込むとSheets側が数値変換して桁落ちする。rawで書き込む。
        """
        ws = self._worksheet("公式X一覧")
        cell = ws.find(handle, in_column=_X_ACCOUNTS_HEADER.index("Xハンドル") + 1)
        if cell is None:
            logger.warning("公式X一覧にハンドルが見つかりません: %s", handle)
            return
        user_id_col = _X_ACCOUNTS_HEADER.index("user_id") + 1
        since_id_col = _X_ACCOUNTS_HEADER.index("since_id") + 1
        fetched_at_col = _X_ACCOUNTS_HEADER.index("最終取得日時") + 1
        ws.update(
            range_name=rowcol_to_a1(cell.row, user_id_col),
            values=[[user_id]],
            raw=True,
        )
        if since_id is not None:
            ws.update(
                range_name=rowcol_to_a1(cell.row, since_id_col),
                values=[[since_id]],
                raw=True,
            )
        ws.update_cell(cell.row, fetched_at_col, datetime.now(timezone.utc).isoformat())
