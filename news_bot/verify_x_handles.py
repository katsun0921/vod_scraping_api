"""VOD公式Xアカウントのハンドル実在確認スクリプト（セットアップ検証用）。

「VOD情報源」シートへ登録する前に、候補ハンドルが実在するか・公式認証済みかを
X API で確認する（仕様書15.#7「X公式アカウントのハンドル確定」）。

Webブラウザで x.com/{handle} を開く方法は、存在しないハンドルでもSPAの外枠が
200を返すため実在確認には使えない。X API の users/by/username が唯一の確実な手段。

コスト: User read $0.010/件。候補6件でも $0.06 程度の一度きりの費用。

実行:
    python -m news_bot.verify_x_handles              # 既定の候補リストを確認
    python -m news_bot.verify_x_handles a b c        # 任意のハンドルを確認
"""

import logging
import os
import sys

import tweepy

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# 調査した候補（docs/feature/vod-sources-candidates.md A.節）。
# 実在・公式性はこのスクリプトの実行結果で確定させる。
_CANDIDATES: dict[str, list[str]] = {
    "netflix": ["NetflixJP"],
    "amazon_prime_video": ["PrimeVideo_JP", "PrimeVideoJP"],
    "unext": ["watch_UNEXT", "unext_official"],
    "disney_plus": ["DisneyPlusJP", "disneyplusjp"],
    "hulu": ["hulu_japan", "Hulu_Japan"],
    "dmm_tv": ["DMMTV_official", "dmmtv_info", "DMMTV"],
}


def _client() -> tweepy.Client:
    return tweepy.Client(bearer_token=os.environ["X_BEARER_TOKEN"])


def _describe(user) -> str:
    """確認結果を1行で表す。フォロワー数と認証状態は公式アカウント判定の材料。"""
    verified = getattr(user, "verified", None)
    verified_type = getattr(user, "verified_type", None)
    metrics = getattr(user, "public_metrics", None) or {}
    followers = metrics.get("followers_count")
    parts = [f"id={user.id}", f"name={user.name!r}"]
    if followers is not None:
        parts.append(f"followers={followers:,}")
    if verified is not None:
        parts.append(f"verified={verified}")
    if verified_type:
        parts.append(f"type={verified_type}")
    return "  ".join(parts)


def verify(handles: list[str], client: tweepy.Client | None = None) -> dict[str, dict | None]:
    """ハンドルの実在をX APIで確認する。存在しないものは None を返す。"""
    client = client or _client()
    results: dict[str, dict | None] = {}
    for handle in handles:
        name = handle.lstrip("@")
        try:
            resp = client.get_user(
                username=name,
                user_fields=["public_metrics", "verified", "verified_type", "description"],
            )
        except Exception as exc:  # tweepyは404も例外で返す場合がある
            logger.info("  %-20s NG (%s)", name, type(exc).__name__)
            results[name] = None
            continue

        if resp.data is None:
            logger.info("  %-20s NG (見つかりません)", name)
            results[name] = None
            continue

        logger.info("  %-20s OK  %s", name, _describe(resp.data))
        results[name] = {"user_id": str(resp.data.id), "name": resp.data.name}
    return results


def main(argv: list[str]) -> int:
    if argv:
        logger.info("=== 指定ハンドルの確認 ===")
        verify(argv)
        return 0

    client = _client()
    logger.info("=== VOD公式Xアカウント候補の実在確認 ===")
    logger.info("（OKでもアカウントが公式かは名前・フォロワー数・認証バッジで人間が判断すること）\n")

    confirmed: dict[str, str] = {}
    for service, handles in _CANDIDATES.items():
        logger.info("[%s]", service)
        results = verify(handles, client)
        found = [h for h, v in results.items() if v]
        if found:
            confirmed[service] = found[0]
        else:
            logger.warning("  → %s の候補がすべて見つかりません。手動で正しいハンドルを調べること", service)
        logger.info("")

    logger.info("=== 「VOD情報源」シートへの登録候補 ===")
    logger.info("取得方式=x / 有効/無効=TRUE / 規約確認済み=済 で登録する\n")
    for service, handle in confirmed.items():
        logger.info("  %-20s https://x.com/%s", service, handle)

    missing = [s for s in _CANDIDATES if s not in confirmed]
    if missing:
        logger.warning("\n未確定のサービス: %s", ", ".join(missing))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
