"""GCS への VOD ラインナップ JSON 読み書きユーティリティ。

VOD ラインナップ機能の出力先である Google Cloud Storage に対して、
月別 JSON / index.json / overrides / snapshot の読み書きを行う。

GCS オブジェクトは部分編集できず「ファイル単位の丸ごと上書き」になる点に注意する
（運用フローは docs/vod-lineup.md の「7. 運用フロー」を参照）。

環境変数:
    GCS_LINEUP_BUCKET: ラインナップ JSON を保存する GCS バケット名（必須）。

認証:
    Cloud Run では Workload Identity Federation により SA キー不要で書き込める。
    ローカル実行時は ``gcloud auth application-default login`` 等で認証する。
"""

import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# ── GCS 上のパス規約 ──────────────────────────────────────────
_INDEX_PATH = "index.json"


def lineup_path(cycle: str) -> str:
    """月別ラインナップ JSON のパスを返す（例: "2026-05.json"）。"""
    return f"{cycle}.json"


def overrides_path(cycle: str) -> str:
    """手動修正 overrides のパスを返す（例: "overrides/2026-05.json"）。"""
    return f"overrides/{cycle}.json"


def snapshot_path(service: str) -> str:
    """差分判定用スナップショットのパスを返す（例: "snapshots/unext.json"）。"""
    return f"snapshots/{service}.json"


# ── GCS クライアント ──────────────────────────────────────────

def _bucket_name() -> str:
    """環境変数からバケット名を取得する。未設定なら RuntimeError。"""
    name = os.environ.get("GCS_LINEUP_BUCKET")
    if not name:
        raise RuntimeError("環境変数 GCS_LINEUP_BUCKET が未設定です")
    return name


def _bucket():
    """GCS バケットオブジェクトを返す。

    google-cloud-storage は重い依存のため、関数内で遅延 import する。
    """
    from google.cloud import storage  # 遅延 import

    client = storage.Client()
    return client.bucket(_bucket_name())


# ── 汎用 JSON 読み書き ───────────────────────────────────────

def download_json(path: str) -> Optional[dict]:
    """GCS の JSON オブジェクトを取得する。

    Args:
        path: バケット内のオブジェクトパス。

    Returns:
        パース済み dict。オブジェクトが存在しなければ None。

    Raises:
        RuntimeError: ダウンロードまたは JSON パースに失敗した場合。
    """
    blob = _bucket().blob(path)
    if not blob.exists():
        return None
    try:
        text = blob.download_as_text()
        return json.loads(text)
    except Exception as e:
        raise RuntimeError(f"GCS download 失敗 path={path}: {e}") from e


def upload_json(path: str, data: dict) -> None:
    """dict を JSON として GCS に上書きアップロードする。

    Args:
        path: バケット内のオブジェクトパス。
        data: 保存する dict。

    Raises:
        RuntimeError: アップロードに失敗した場合。
    """
    blob = _bucket().blob(path)
    try:
        blob.upload_from_string(
            json.dumps(data, ensure_ascii=False, indent=2),
            content_type="application/json; charset=utf-8",
        )
        logger.info("GCS upload: gs://%s/%s", _bucket_name(), path)
    except Exception as e:
        raise RuntimeError(f"GCS upload 失敗 path={path}: {e}") from e


# ── ラインナップ専用操作 ─────────────────────────────────────

def upload_lineup(
    cycle: str,
    titles_by_service: dict[str, list[str]],
    updated_at: str,
) -> str:
    """月別ラインナップ JSON を生成して GCS に上書きアップロードする。

    Args:
        cycle            : 対象サイクル "YYYY-MM"。
        titles_by_service: {service_key: [title, ...]} のタイトル一覧。
        updated_at       : 更新日時 "YYYY-MM-DD HH:MM:SS"。

    Returns:
        書き込んだ GCS オブジェクトパス（例: "2026-05.json"）。
    """
    payload = {
        "cycle": cycle,
        "updated_at": updated_at,
        "services": titles_by_service,
    }
    path = lineup_path(cycle)
    upload_json(path, payload)
    return path


def download_overrides(cycle: str) -> dict:
    """手動修正 overrides を取得する。存在しなければ空 dict を返す。

    overrides の schema（docs/vod-lineup.md 7.3 参照）::

        {
          "exclude": ["service:external_id", ...],
          "rename":  {"service:external_id": "正しいタイトル", ...},
          "add":     {"service": ["手動追加タイトル", ...]}
        }

    Args:
        cycle: 対象サイクル "YYYY-MM"。

    Returns:
        overrides dict。存在しなければ ``{}``。
    """
    return download_json(overrides_path(cycle)) or {}


def update_index(cycle: str) -> list[str]:
    """index.json に対象サイクルを追加し、降順で保存する。

    フロントエンドの月セレクタ用に、利用可能な月リストを管理する。
    既に含まれている場合は重複追加しない。

    Args:
        cycle: 追加するサイクル "YYYY-MM"。

    Returns:
        更新後の月リスト（新しい順）。
    """
    existing = download_json(_INDEX_PATH) or {}
    months = set(existing.get("months", []))
    months.add(cycle)
    sorted_months = sorted(months, reverse=True)
    upload_json(_INDEX_PATH, {"months": sorted_months})
    return sorted_months
