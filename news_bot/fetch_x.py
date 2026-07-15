"""公式Xアカウントの投稿取得（試作・ニュースソース化に向けた検証用）。

仕様書 4.1（RSS優先）に続く追加取得手段。Xの読み取りはPay-Per-Use課金
（$0.005/投稿読み取り）のため、コストを抑える設計とする：

- 1アカウントあたりの取得件数は _MAX_RESULTS で絞る
- since_id（前回取得した最新投稿ID）を渡せば、それ以降の新着分のみ課金対象になる
- ユーザーID（ハンドル→内部ID変換）は account に "user_id" を渡せば解決コールをスキップできる
  （本実装時は「公式X一覧」シートにuser_idをキャッシュする想定）

環境変数:
    X_BEARER_TOKEN: 読み取り専用のOAuth 2.0 App-Only Bearer Token
        （投稿用のX_API_KEY等のOAuth1.0aキーとは別に発行する）
"""

import logging
import os

import tweepy

from news_bot.fetch import NewsEntry

logger = logging.getLogger(__name__)

_MAX_RESULTS = 10


def _client() -> tweepy.Client:
    return tweepy.Client(bearer_token=os.environ["X_BEARER_TOKEN"])


def fetch_from_x_account(account: dict, client: tweepy.Client | None = None) -> list[NewsEntry]:
    """1つの公式Xアカウントから新着投稿を取得する。

    Args:
        account: {"名称": str, "Xハンドル": str, "user_id": str | None, "since_id": str | None}
    """
    client = client or _client()
    handle = account["Xハンドル"].lstrip("@")
    name = account["名称"]
    since_id = account.get("since_id") or None
    user_id = account.get("user_id")

    if user_id is None:
        user = client.get_user(username=handle)
        if user.data is None:
            logger.warning("Xアカウントが見つかりません: %s", handle)
            return []
        user_id = user.data.id

    response = client.get_users_tweets(
        id=user_id,
        max_results=_MAX_RESULTS,
        since_id=since_id,
        exclude=["retweets", "replies"],
        tweet_fields=["created_at"],
    )
    if response.data is None:
        return []

    return [
        NewsEntry(
            title=tweet.text,
            url=f"https://x.com/{handle}/status/{tweet.id}",
            source=name,
            summary=tweet.text,
        )
        for tweet in response.data
    ]


def fetch_all_x(accounts: list[dict]) -> list[NewsEntry]:
    """有効な全Xアカウントを巡回して投稿一覧を取得する。1件の失敗は他に伝播させない。"""
    client = _client()
    all_entries: list[NewsEntry] = []
    for account in accounts:
        try:
            all_entries.extend(fetch_from_x_account(account, client=client))
        except Exception:
            logger.exception("Xアカウント取得失敗: %s", account.get("名称"))
    return all_entries


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    test_handle = os.environ.get("TEST_X_HANDLE", "anime_jaadugar")
    for item in fetch_from_x_account({"名称": test_handle, "Xハンドル": test_handle}):
        print(item)
