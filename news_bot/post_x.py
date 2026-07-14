"""X API v2 投稿。

仕様書 4.5: 本文（URLなし）→ そのツイートへのリプライ（URLあり）の2段投稿。
OAuth1.0aユーザーコンテキストで投稿するため tweepy.Client を使用する。

環境変数:
    X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET
"""

import logging
import os

import tweepy

logger = logging.getLogger(__name__)


def _client() -> tweepy.Client:
    return tweepy.Client(
        consumer_key=os.environ["X_API_KEY"],
        consumer_secret=os.environ["X_API_SECRET"],
        access_token=os.environ["X_ACCESS_TOKEN"],
        access_token_secret=os.environ["X_ACCESS_TOKEN_SECRET"],
    )


def post_with_reply(honbun: str, reply: str) -> tuple[str, str]:
    """本文を投稿し、そのツイートへURL付きリプライを投稿する。

    Returns:
        (本文ツイートID, リプライツイートID)
    """
    client = _client()
    main_resp = client.create_tweet(text=honbun)
    main_id = main_resp.data["id"]

    reply_resp = client.create_tweet(text=reply, in_reply_to_tweet_id=main_id)
    reply_id = reply_resp.data["id"]

    logger.info("X投稿完了: main=%s reply=%s", main_id, reply_id)
    return main_id, reply_id
