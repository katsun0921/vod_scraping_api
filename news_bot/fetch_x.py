"""公式Xアカウントの投稿取得（RSSに続く第2のニュースソース）。

仕様書 4.1（RSS優先）に続く追加取得手段。Xの読み取りはPay-Per-Use課金
（$0.005/投稿読み取り）のため、コストを抑える設計とする：

- 1アカウントあたりの取得件数は _MAX_RESULTS で絞る
- since_id（前回取得した最新投稿ID）を渡せば、それ以降の新着分のみ課金対象になる
- ユーザーID（ハンドル→内部ID変換）は account に "user_id" を渡せば解決コールをスキップできる
- user_id・since_idは「公式X一覧」シートにキャッシュする（sheets.update_x_account_state）ため、
  fetch_from_x_account / fetch_all_x は取得結果と併せて更新後の状態を返す

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


def fetch_from_x_account(
    account: dict, client: tweepy.Client | None = None
) -> tuple[list[NewsEntry], dict]:
    """1つの公式Xアカウントから新着投稿を取得する。

    Args:
        account: {"名称": str, "Xハンドル": str, "user_id": str | None, "since_id": str | None}

    Returns:
        (取得したNewsEntry一覧, 更新後の状態 {"user_id": str, "since_id": str | None})
        since_idは今回取得した中で最新の投稿ID（新着が無ければ元のsince_idを維持）。
    """
    client = client or _client()
    handle = account["Xハンドル"].lstrip("@")
    name = account["名称"]
    since_id = str(account["since_id"]) if account.get("since_id") else None
    user_id = account.get("user_id")

    if user_id is None:
        user = client.get_user(username=handle)
        if user.data is None:
            logger.warning("Xアカウントが見つかりません: %s", handle)
            return [], {"user_id": None, "since_id": since_id}
        user_id = user.data.id

    response = client.get_users_tweets(
        id=user_id,
        max_results=_MAX_RESULTS,
        since_id=since_id,
        exclude=["retweets", "replies"],
        tweet_fields=["created_at"],
    )
    tweets = response.data or []
    # 新着が無い場合は元のsince_idを維持する（既に文字列化済みなので再度str()を通す必要はない）。
    state = {"user_id": str(user_id), "since_id": str(tweets[0].id) if tweets else since_id}
    if not tweets:
        return [], state

    entries = [
        NewsEntry(
            title=tweet.text,
            url=f"https://x.com/{handle}/status/{tweet.id}",
            source=name,
            summary=tweet.text,
        )
        for tweet in tweets
    ]
    return entries, state


def fetch_all_x(accounts: list[dict]) -> tuple[list[NewsEntry], dict[str, dict]]:
    """有効な全Xアカウントを巡回して投稿一覧を取得する。1件の失敗は他に伝播させない。

    Returns:
        (全NewsEntry一覧, {Xハンドル: 更新後の状態})
        更新後の状態はsheets.update_x_account_state()でのキャッシュ更新に使う。
    """
    client = _client()
    all_entries: list[NewsEntry] = []
    updated_states: dict[str, dict] = {}
    for account in accounts:
        try:
            entries, state = fetch_from_x_account(account, client=client)
            all_entries.extend(entries)
            updated_states[account["Xハンドル"]] = state
        except Exception:
            logger.exception("Xアカウント取得失敗: %s", account.get("名称"))
    return all_entries, updated_states


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    test_handle = os.environ.get("TEST_X_HANDLE", "anime_jaadugar")
    entries, state = fetch_from_x_account({"名称": test_handle, "Xハンドル": test_handle})
    for item in entries:
        print(item)
    print("state:", state)
