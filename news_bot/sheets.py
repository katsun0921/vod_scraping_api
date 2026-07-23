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
from datetime import date, datetime, timezone

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
    "劇場情報源",
    "劇場公開予定",
    "VOD情報源",
    "VOD配信予定",
]

_NEWS_SOURCES_HEADER = ["ID", "名称", "URL", "カテゴリ", "地域", "取得間隔", "有効/無効", "規約確認済み"]
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
# 劇場公開カレンダー収集パイプライン（docs/feature/theater-release-calendar-spec.md 8.）
_THEATER_ITEMS_HEADER = [
    "取得日時", "公開日", "タイトル", "原題", "カテゴリ", "国", "配給",
    "公式URL", "予告URL", "情報源", "Katsumascore URL", "WP post_id",
    "SNS優先度(S/A/B/C)", "投稿状態", "重複キー", "メモ",
]
# 「劇場情報源」はレイヤー1データソースを未確定のままコード変更なしで追加できるよう、
# RSS一覧と同じ「シートに参照を記述すれば取得対象になる」方式で新設した管理用シート
# （仕様書には無い）。取得方式は現状 "rss" のみ fetch_theater.py が対応する。
_THEATER_SOURCES_HEADER = ["ID", "名称", "URL", "取得方式", "レイヤー", "有効/無効", "規約確認済み", "メモ"]

# VOD配信情報収集パイプライン（docs/feature/vod-release-calendar-spec.md 8.）
# 取得方式=xの行は「公式X一覧」と同じuser_id/since_id/最終取得日時のキャッシュ列を持つ（仕様書8.①）。
_VOD_SOURCES_HEADER = [
    "ID", "名称", "URL", "取得方式", "対象サービス", "有効/無効", "規約確認済み",
    "user_id", "since_id", "最終取得日時", "メモ",
]
_VOD_ITEMS_HEADER = [
    "取得日時", "配信開始日", "タイトル", "原題", "サービス", "カテゴリ", "配信種別",
    "公式URL", "情報源", "Katsumascore URL", "WP post_id", "SNS優先度(S/A/B/C)",
    "投稿状態", "編集部おすすめ", "編集部コメント", "重複キー", "メモ",
]

# main.py が実際に読み書きするシートのみ自動作成する。
# タイトル一覧・YouTube Shorts はMVPスコープ外のため対象外。
_AUTO_CREATED_HEADERS = {
    "RSS一覧": _NEWS_SOURCES_HEADER,
    "ニュース取得": _NEWS_ITEMS_HEADER,
    "X投稿履歴": _POST_HISTORY_HEADER,
    "承認キュー": _APPROVAL_QUEUE_HEADER,
    "公式X一覧": _X_ACCOUNTS_HEADER,
    "劇場情報源": _THEATER_SOURCES_HEADER,
    "劇場公開予定": _THEATER_ITEMS_HEADER,
    "VOD情報源": _VOD_SOURCES_HEADER,
    "VOD配信予定": _VOD_ITEMS_HEADER,
}


def _vod_x_handle_from_url(url: str) -> str:
    """「VOD情報源」シートのURL列（例: https://x.com/NetflixJP）からXハンドルを抽出する。"""
    return url.rstrip("/").rsplit("/", 1)[-1].lstrip("@")


def _is_active(value) -> bool:
    """「有効/無効」列（Sheetsのチェックボックス）がTRUEかどうかを判定する。

    gspreadの get_all_records() はチェックボックスの値をPythonのbool Trueで返す
    こともあれば、文字列"TRUE"で返すこともある（実運用で確認：後者だった）ため両方許容する。
    """
    if isinstance(value, bool):
        return value is True
    if isinstance(value, str):
        return value.strip().upper() == "TRUE"
    return False


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

        有効/無効列（チェックボックス）がTRUE、規約確認済み="済" の行のみ対象。
        """
        rows = self._worksheet("RSS一覧").get_all_records()
        return [
            row for row in rows
            if _is_active(row.get("有効/無効")) and row.get("規約確認済み") == "済"
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

        有効/無効列（チェックボックス）がTRUEの行のみ対象。

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
            if row.get("地域") == region and _is_active(row.get("有効/無効"))
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

    def get_active_theater_sources(self) -> list[dict]:
        """「劇場情報源」シートから有効な取得元を取得する。

        有効/無効列（チェックボックス）がTRUE、規約確認済み="済" の行のみ対象。
        レイヤー1データソースは未確定のため、コード変更なしにこのシートへ行を
        追加するだけで取得対象を増やせるようにしている（取得方式="rss"のみ対応）。
        """
        rows = self._worksheet("劇場情報源").get_all_records()
        return [
            row for row in rows
            if _is_active(row.get("有効/無効")) and row.get("規約確認済み") == "済"
        ]

    def get_existing_theater_keys(self) -> set[str]:
        """「劇場公開予定」シートに既に保存済みの重複キー集合を返す。"""
        rows = self._worksheet("劇場公開予定").get_all_records()
        return {row["重複キー"] for row in rows if row.get("重複キー")}

    def append_theater_item(
        self,
        *,
        release_date: str,
        title: str,
        dedupe_key: str,
        original_title: str = "",
        category: str = "",
        country: str = "",
        distributor: str = "",
        official_url: str = "",
        trailer_url: str = "",
        source: str = "",
        katsumascore_url: str = "",
        wp_post_id: str = "",
        sns_priority: str = "",
        post_status: str = "未判定",
        memo: str = "",
    ) -> None:
        """「劇場公開予定」シートに1件追記する。"""
        row = [
            datetime.now(timezone.utc).isoformat(),
            release_date,
            title,
            original_title,
            category,
            country,
            distributor,
            official_url,
            trailer_url,
            source,
            katsumascore_url,
            wp_post_id,
            sns_priority,
            post_status,
            dedupe_key,
            memo,
        ]
        self._worksheet("劇場公開予定").append_row(row, value_input_option="USER_ENTERED")

    def get_active_vod_x_accounts(self) -> list[dict]:
        """「VOD情報源」シートから取得方式=xで有効な公式Xアカウントを取得する
        （fetch_vod_x.fetch_all_vod_x()へ渡す形に整形。仕様書7.3）。

        有効/無効列（チェックボックス）がTRUE、規約確認済み="済"の行のみ対象。
        Xハンドルは「公式X一覧」と違い専用列を持たないため、URL列の末尾から導出する
        （仕様書8.①の簡略ヘッダーに合わせるため）。user_id/since_idは公式X一覧と
        同じくXのsnowflake ID（19桁の整数文字列）のため、numericise_ignoreで
        gspreadの自動数値変換を無効化する。
        """
        user_id_col = _VOD_SOURCES_HEADER.index("user_id") + 1
        since_id_col = _VOD_SOURCES_HEADER.index("since_id") + 1
        rows = self._worksheet("VOD情報源").get_all_records(
            numericise_ignore=[user_id_col, since_id_col]
        )
        return [
            {
                "名称": row["名称"],
                "Xハンドル": _vod_x_handle_from_url(row["URL"]),
                "対象サービス": row.get("対象サービス", ""),
                "user_id": str(row["user_id"]) if row.get("user_id") else None,
                "since_id": str(row["since_id"]) if row.get("since_id") else None,
            }
            for row in rows
            if row.get("取得方式") == "x"
            and _is_active(row.get("有効/無効"))
            and row.get("規約確認済み") == "済"
        ]

    def update_vod_x_account_state(self, handle: str, *, user_id: str, since_id: str | None) -> None:
        """Xハンドル（URL列から導出した値）をキーに「VOD情報源」のuser_id/since_id/
        最終取得日時を更新する。

        「公式X一覧」と違いXハンドル専用列が無いため、URL列を1件ずつ走査して
        導出ハンドルが一致する行を探す（`ws.find()`は導出値では検索できないため）。
        user_id・since_idはUSER_ENTEREDで書き込むとSheets側が数値変換し桁落ちするため、
        rawで書き込む（`update_x_account_state()`と同じ対策）。
        """
        ws = self._worksheet("VOD情報源")
        url_col = _VOD_SOURCES_HEADER.index("URL") + 1
        urls = ws.col_values(url_col)
        row_number = None
        for i, url in enumerate(urls[1:], start=2):  # 1行目はヘッダー
            if url and _vod_x_handle_from_url(url) == handle:
                row_number = i
                break
        if row_number is None:
            logger.warning("VOD情報源にXハンドルが見つかりません: %s", handle)
            return

        user_id_col = _VOD_SOURCES_HEADER.index("user_id") + 1
        since_id_col = _VOD_SOURCES_HEADER.index("since_id") + 1
        fetched_at_col = _VOD_SOURCES_HEADER.index("最終取得日時") + 1
        ws.update(range_name=rowcol_to_a1(row_number, user_id_col), values=[[user_id]], raw=True)
        if since_id is not None:
            ws.update(range_name=rowcol_to_a1(row_number, since_id_col), values=[[since_id]], raw=True)
        ws.update_cell(row_number, fetched_at_col, datetime.now(timezone.utc).isoformat())

    def get_existing_vod_keys(self) -> set[str]:
        """「VOD配信予定」シートに既に保存済みの重複キー集合を返す。"""
        rows = self._worksheet("VOD配信予定").get_all_records()
        return {row["重複キー"] for row in rows if row.get("重複キー")}

    def append_vod_item(
        self,
        *,
        release_date: str,
        title: str,
        service: str,
        dedupe_key: str,
        title_orig: str = "",
        category: str = "",
        availability_type: str = "",
        official_url: str = "",
        source: str = "",
        katsumascore_url: str = "",
        wp_post_id: str = "",
        sns_priority: str = "",
        post_status: str = "承認待ち",
        memo: str = "",
    ) -> None:
        """「VOD配信予定」シートに1件追記する（仕様書8.②）。

        「編集部おすすめ」は新規発見時点では常にFalse・「編集部コメント」は空欄とし、
        管理者が承認時に手動で設定する運用とする（仕様書15.未決定事項#12）。
        """
        row = [
            datetime.now(timezone.utc).isoformat(),
            release_date,
            title,
            title_orig,
            service,
            category,
            availability_type,
            official_url,
            source,
            katsumascore_url,
            wp_post_id,
            sns_priority,
            post_status,
            False,
            "",
            dedupe_key,
            memo,
        ]
        self._worksheet("VOD配信予定").append_row(row, value_input_option="USER_ENTERED")

    def get_approved_vod_items(self, start: date, end: date) -> list[dict]:
        """投稿状態=承認済み、かつ配信開始日が対象期間内の行を返す（`vod_publish`対象、仕様書11.）。

        「編集部おすすめ」は公式X一覧の「有効/無効」と同じくチェックボックス列のため、
        `_is_active()`と同じ揺れ（bool True / 文字列"TRUE"）を吸収してPythonのbool型に
        正規化してから返す（compose_vod.py側で素直に`is True`比較できるようにするため）。
        """
        rows = self._worksheet("VOD配信予定").get_all_records()
        result = []
        for row in rows:
            if row.get("投稿状態") != "承認済み":
                continue
            try:
                release_date = date.fromisoformat(str(row.get("配信開始日", "")))
            except ValueError:
                continue
            if not (start <= release_date <= end):
                continue
            row["編集部おすすめ"] = _is_active(row.get("編集部おすすめ"))
            result.append(row)
        return result

    def update_vod_item_status(self, dedupe_key: str, *, post_status: str) -> None:
        """重複キーをキーに「VOD配信予定」シートの投稿状態列を更新する。"""
        ws = self._worksheet("VOD配信予定")
        cell = ws.find(dedupe_key, in_column=_VOD_ITEMS_HEADER.index("重複キー") + 1)
        if cell is None:
            logger.warning("VOD配信予定に重複キーが見つかりません: %s", dedupe_key)
            return
        status_col = _VOD_ITEMS_HEADER.index("投稿状態") + 1
        ws.update_cell(cell.row, status_col, post_status)

    def update_vod_item_katsumascore(self, dedupe_key: str, *, katsumascore_url: str, wp_post_id: str) -> None:
        """Katsumascore照合結果（仕様書10.）を重複キーをキーに書き込む。"""
        ws = self._worksheet("VOD配信予定")
        cell = ws.find(dedupe_key, in_column=_VOD_ITEMS_HEADER.index("重複キー") + 1)
        if cell is None:
            logger.warning("VOD配信予定に重複キーが見つかりません: %s", dedupe_key)
            return
        ws.update_cell(cell.row, _VOD_ITEMS_HEADER.index("Katsumascore URL") + 1, katsumascore_url)
        ws.update_cell(cell.row, _VOD_ITEMS_HEADER.index("WP post_id") + 1, wp_post_id)
