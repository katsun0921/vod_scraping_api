"""ラインナップ差分判定用スナップショットユーティリティ。

毎月の実行で「今回はじめて現れた作品」だけを通知するため、
前回収集分の external_id セットを GCS に保存し、今回分との差分を取る。

スナップショットはサービスごとに GCS の ``snapshots/{service}.json`` に保存する。
"""

import logging
from typing import Iterable

from utils import gcs

logger = logging.getLogger(__name__)


def load_snapshot(service: str) -> set[str]:
    """前回スナップショット（external_id セット）を GCS から読み込む。

    Args:
        service: サービスキー。

    Returns:
        前回収集分の external_id セット。スナップショットが無ければ空セット。
    """
    data = gcs.download_json(gcs.snapshot_path(service))
    if not data:
        return set()
    return set(data.get("ids", []))


def save_snapshot(service: str, ids: Iterable[str]) -> None:
    """今回の external_id セットを GCS にスナップショットとして保存する。

    Args:
        service: サービスキー。
        ids    : 今回収集分の external_id の集合。
    """
    sorted_ids = sorted(set(ids))
    gcs.upload_json(gcs.snapshot_path(service), {"ids": sorted_ids})
    logger.info("snapshot 保存: service=%s count=%d", service, len(sorted_ids))


def diff(prev: set[str], curr: set[str]) -> set[str]:
    """前回と今回の差分（新規に現れた external_id）を返す。

    Args:
        prev: 前回スナップショットの external_id セット。
        curr: 今回収集分の external_id セット。

    Returns:
        今回はじめて現れた external_id セット（``curr - prev``）。
    """
    return set(curr) - set(prev)
