"""コレクター基底クラス。

各サービスのコレクターは ``BaseCollector`` を継承し ``collect()`` を実装する。
``checkers/`` の ``check(url) -> dict`` と対になる ``collect() -> list[LineupItem]`` 規約。

コーディング規約:
    - robot 検出・サーバーエラー時は ``RuntimeError`` を raise する
      （呼び出し元のランナーが当該サービスをスキップする）。
    - JS レンダリングが必要なサービスは Playwright を使用する。
"""

from abc import ABC, abstractmethod
from typing import Optional

from collectors import LineupItem


class BaseCollector(ABC):
    """ラインナップコレクターの基底クラス。

    サブクラスは ``service`` クラス変数を設定し ``collect()`` を実装する。
    """

    #: サービスキー（サブクラスで設定する）
    service: str = ""

    @abstractmethod
    def collect(self, limit: Optional[int] = None) -> list[LineupItem]:
        """サービスのラインナップ/新着ページから作品一覧を取得する。

        Args:
            limit: 最大取得件数。None の場合は取得可能な全件（デバッグ用に制限可能）。

        Returns:
            ``LineupItem`` のリスト。フィルタ（言語・種別）や差分判定は
            呼び出し元のランナー側で行うため、ここでは収集結果をそのまま返す。

        Raises:
            RuntimeError: robot 検出・サーバーエラー・取得失敗時。
        """
        raise NotImplementedError
