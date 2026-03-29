"""リクエストレート制限ユーティリティ。"""

import random
import time


class RateLimiter:
    """VODサービスへのリクエスト間隔を制御するレートリミッター。

    - リクエストごと: 3〜5秒のランダム待機
    - VODサービス切り替え時: 追加10秒待機
    """

    def __init__(self, min_wait: float = 3.0, max_wait: float = 5.0, service_switch_wait: float = 10.0) -> None:
        self.min_wait = min_wait
        self.max_wait = max_wait
        self.service_switch_wait = service_switch_wait

    def wait(self) -> None:
        """リクエスト間のランダム待機を実行する。"""
        duration = random.uniform(self.min_wait, self.max_wait)
        time.sleep(duration)

    def wait_service_switch(self) -> None:
        """VODサービス切り替え時の追加待機を実行する。"""
        time.sleep(self.service_switch_wait)
