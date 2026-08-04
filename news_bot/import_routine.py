"""ルーティン（Claudeのスケジュール実行）が出力したJSONを読み込む。

docs/feature/routine-discovery.md の方式。Claude/OpenAI APIのWeb検索を従量課金で
呼ぶ代わりに、Claudeのルーティンが定期実行で調査してリポジトリにJSONをコミットし、
そのPRを人間がレビュー（＝承認）してマージする。マージ後にGitHub Actionsが
本モジュールでJSONを読み、既存の保存処理（重複判定・期間フィルタ・Katsumascore照合・
Slack通知）へ流す。

discover_theater / discover_vod との違いは「エントリの供給元」だけで、
返す型（TheaterEntry / VodEntry）は同じ。呼び出し元（main.py）は供給元が
AI APIかJSONかを意識せず同じ保存処理を使える。

JSONのスキーマは discover_theater._build_prompt() / discover_vod._build_prompt() が
AIに指示している形式と同一にしてある。ルーティン方式へ完全移行してもプロンプトの
知識が分散しないようにするため。
"""

import json
import logging
from datetime import date
from pathlib import Path

from news_bot.theater_calendar import TheaterEntry
from news_bot.vod_calendar import SERVICES, VodEntry

logger = logging.getLogger(__name__)

# ルーティンが成果物をコミットする場所。日付別に残すのは、あとから
# 「どの週に何を拾ったか」を追跡できるようにするため（シートの情報源列だけでは
# AIが何を根拠にしたか遡れない）。
ROUTINE_DATA_DIR = Path("news_bot/routine_data")

_THEATER_SOURCE = "ルーティン(claude)"
_VOD_SOURCE = "ルーティン(claude)"


def _load_json_array(path: Path) -> list[dict]:
    """JSONファイルを読み、配列であることを検証して返す。

    ルーティンの出力は人間のレビューを経てマージされるが、レビューは内容の正しさ
    （実在する作品か・日付が合っているか）を見るものであり、構造の妥当性まで
    毎回目視で保証はできない。ここで落ちれば Actions が失敗し気付ける。
    """
    if not path.exists():
        raise FileNotFoundError(f"ルーティン成果物が見つかりません: {path}")

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"JSON配列ではありません: {path} ({type(data).__name__})")
    return data


def load_theater_entries(path: Path) -> list[TheaterEntry]:
    """ルーティンが出力した劇場公開JSONを TheaterEntry 一覧にする。

    スキーマ: [{"title", "release_date", "distributor", "official_url"}, ...]

    title / release_date が欠けた要素はスキップする（discover_theater._parse_entries()
    と同じ方針）。公開日が無い作品は重複キーを作れず、シートの並び替えもできないため。
    """
    entries: list[TheaterEntry] = []
    for item in _load_json_array(path):
        title = (item.get("title") or "").strip()
        raw_date = (item.get("release_date") or "").strip()
        if not title or not raw_date:
            logger.warning("title/release_dateが無いためスキップ: %r", item)
            continue
        try:
            release_date = date.fromisoformat(raw_date)
        except ValueError:
            logger.warning("公開日が不正のためスキップ: %s (%r)", title, raw_date)
            continue

        entries.append(
            TheaterEntry(
                title=title,
                url=(item.get("official_url") or "").strip(),
                source=_THEATER_SOURCE,
                release_date=release_date,
                distributor=(item.get("distributor") or "").strip(),
            )
        )
    return entries


def load_vod_entries(path: Path) -> list[VodEntry]:
    """ルーティンが出力したVOD配信JSONを VodEntry 一覧にする。

    スキーマ: [{"title", "title_orig", "service", "available_from",
                "availability_type", "url", "category"}, ...]

    service は SERVICES のキー（netflix / unext 等）でなければスキップする。
    未知のサービス名を通すと compose_vod._service_label() がキーをそのまま
    見出しに出してしまい、記事に生の識別子が露出するため。
    """
    entries: list[VodEntry] = []
    for item in _load_json_array(path):
        title = (item.get("title") or "").strip()
        service = (item.get("service") or "").strip()
        raw_date = (item.get("available_from") or "").strip()
        if not title or not raw_date:
            logger.warning("title/available_fromが無いためスキップ: %r", item)
            continue
        if service not in SERVICES:
            logger.warning("未知のサービスのためスキップ: %s (service=%r)", title, service)
            continue
        try:
            available_from = date.fromisoformat(raw_date)
        except ValueError:
            logger.warning("配信開始日が不正のためスキップ: %s (%r)", title, raw_date)
            continue

        entries.append(
            VodEntry(
                title=title,
                service=service,
                available_from=available_from,
                source=_VOD_SOURCE,
                title_orig=(item.get("title_orig") or "").strip(),
                availability_type=(item.get("availability_type") or "").strip(),
                url=(item.get("url") or "").strip(),
                category=(item.get("category") or "").strip(),
            )
        )
    return entries


def latest_path(kind: str) -> Path:
    """取り込み対象のJSONパスを返す（kind: "theater" / "vod"）。

    ルーティンは常に同じファイル名で上書きコミットする。日付別ファイルにすると
    Actions側が「どれを取り込むべきか」を判断する必要が生じるため、最新分は
    固定名にし、履歴はgitのコミット履歴で追う。
    """
    return ROUTINE_DATA_DIR / f"{kind}_latest.json"
