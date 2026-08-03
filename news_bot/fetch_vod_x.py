"""VOD公式Xアカウントのポスト取得（fetch_x.py のラッパー、仕様書7.3）。

`news_bot/fetch_x.py`（RSSに続くニュースソース向けの実装）をそのまま流用する。
「VOD情報源」シート（取得方式=x）を対象にする点だけがニュースパイプラインの
「公式X一覧」シートと異なるため、呼び出し口をこのモジュールに分離している。
ポストは生テキストのまま返し、構造化は`extract_vod.py`が担当する。
"""

from news_bot import fetch_x
from news_bot.fetch import NewsEntry


def fetch_all_vod_x(accounts: list[dict]) -> tuple[list[NewsEntry], dict[str, dict]]:
    """有効なVOD公式Xアカウントを巡回し、生テキストのポスト一覧を取得する。

    Args:
        accounts: sheets.NewsBotSheets.get_active_vod_x_accounts() が返す一覧
            （fetch_x.fetch_from_x_account()と同じ形: 名称/Xハンドル/user_id/since_id）

    Returns:
        (NewsEntry一覧, {Xハンドル: 更新後の状態})。NewsEntry.title/summaryは
        ツイート本文そのまま（構造化はextract_vod.extract_from_x_posts()が担当する）。
    """
    return fetch_x.fetch_all_x(accounts)
