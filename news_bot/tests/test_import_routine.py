import json
import sys
import types
from datetime import date
from pathlib import Path

import pytest

# gspread / requests は本番依存だがテストでは実通信しないためスタブで十分
sys.modules.setdefault("requests", types.ModuleType("requests"))

from news_bot import import_routine


def _write(tmp_path: Path, name: str, data: object) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_load_theater_entries_parses_valid_rows(tmp_path):
    path = _write(
        tmp_path,
        "theater.json",
        [
            {
                "title": "国宝",
                "release_date": "2026-08-07",
                "distributor": "東宝",
                "official_url": "https://example.com/kokuho",
            }
        ],
    )
    entries = import_routine.load_theater_entries(path)
    assert len(entries) == 1
    assert entries[0].title == "国宝"
    assert entries[0].release_date == date(2026, 8, 7)
    assert entries[0].distributor == "東宝"
    assert entries[0].source == "ルーティン(claude)"


def test_load_theater_entries_skips_rows_without_date():
    """公開日が無い行は重複キーを作れずシートの並び替えもできないためスキップする。"""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        path = _write(Path(d), "t.json", [{"title": "日付なし"}, {"title": "正常", "release_date": "2026-08-07"}])
        entries = import_routine.load_theater_entries(path)
        assert [e.title for e in entries] == ["正常"]


def test_load_theater_entries_skips_invalid_date(tmp_path):
    """日付形式が不正な行は落とす（1行の不備で取り込み全体を失敗させない）。"""
    path = _write(tmp_path, "t.json", [{"title": "不正", "release_date": "2026年8月7日"}])
    assert import_routine.load_theater_entries(path) == []


def test_load_vod_entries_parses_valid_rows(tmp_path):
    path = _write(
        tmp_path,
        "vod.json",
        [
            {
                "title": "作品A",
                "title_orig": "Work A",
                "service": "netflix",
                "available_from": "2026-08-10",
                "availability_type": "見放題",
                "url": "https://example.com/a",
                "category": "映画",
            }
        ],
    )
    entries = import_routine.load_vod_entries(path)
    assert len(entries) == 1
    assert entries[0].service == "netflix"
    assert entries[0].available_from == date(2026, 8, 10)
    assert entries[0].availability_type == "見放題"


def test_load_vod_entries_skips_unknown_service(tmp_path):
    """未知のサービスキーは落とす。

    通してしまうと compose_vod._service_label() がキーをそのまま見出しに出力し、
    記事に生の識別子が露出する。
    """
    path = _write(
        tmp_path,
        "vod.json",
        [
            {"title": "未知", "service": "abema", "available_from": "2026-08-10"},
            {"title": "既知", "service": "netflix", "available_from": "2026-08-10"},
        ],
    )
    entries = import_routine.load_vod_entries(path)
    assert [e.title for e in entries] == ["既知"]


def test_missing_file_raises(tmp_path):
    """ファイルが無ければ明示的に失敗させる（0件と区別する）。

    ルーティンがコミットに失敗した場合に「0件だった」と誤認すると、
    取りこぼしに気付けないまま週次サイクルが正常終了してしまう。
    """
    with pytest.raises(FileNotFoundError):
        import_routine.load_theater_entries(tmp_path / "存在しない.json")


def test_non_array_json_raises(tmp_path):
    """JSON配列でなければ失敗させる（Actionsのログで構造の異常に気付けるように）。"""
    path = _write(tmp_path, "t.json", {"title": "配列ではない"})
    with pytest.raises(ValueError):
        import_routine.load_theater_entries(path)


def test_empty_array_is_valid(tmp_path):
    """空配列は正常（その週に該当作品が無い場合があるため）。"""
    path = _write(tmp_path, "t.json", [])
    assert import_routine.load_theater_entries(path) == []


def test_latest_path_is_stable():
    """取り込み対象は固定名。履歴はgitのコミット履歴で追う。"""
    assert import_routine.latest_path("theater").name == "theater_latest.json"
    assert import_routine.latest_path("vod").name == "vod_latest.json"
