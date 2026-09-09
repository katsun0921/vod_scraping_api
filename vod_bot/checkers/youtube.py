"""YouTube 配信状況チェッカー。

対象URL形式:
    https://www.youtube.com/watch?v={video_id}
    https://youtu.be/{video_id}

YouTube の「見放題（streaming）」は他サービスと意味が違い、サブスク加入者向けでは
なく**誰でも観られる無料公開**を指す。映画配給会社の公式チャンネル
（例:【公式】プレシディオチャンネル）が期間限定で本編を無料公開するケースが
これにあたる。TOP の「YouTube で無料配信中」セクションはこの判定に依存するため、
レンタル・購入（有料）を無料と取り違えないことがこのチェッカーの主目的である。

判定ロジック（watch ページの `ytInitialPlayerResponse` を読む）:
    - 有料オファー（`ytOfferModuleRenderer`）あり → rental / purchase（price に金額）
    - `playabilityStatus.status == "OK"`           → streaming（無料公開・price 0）
    - status が CONTENT_CHECK_REQUIRED             → streaming（閲覧注意の確認を挟むだけで
      無料で観られる。有料オファーは既に除外済み）
    - status が ERROR / UNPLAYABLE（オファーなし）→ ended（削除・非公開・地域制限）
    - status が LOGIN_REQUIRED                     → RuntimeError（年齢制限か限定公開か
      区別できないため据え置く。誤って ended にすると一覧から落ちる）
    - `playabilityStatus` が読めず og:title だけある → RuntimeError（判定不能）
    - og:title も無い                              → ended
    - サーバーエラー (5xx)                         → RuntimeError

`check()` の戻り値には共通仕様の `status` / `price` に加えて、無料公開している
チャンネル名を `channel_name` として返す。呼び出し元が使わない場合は無視してよい。
"""

import json
import logging
import re
from typing import Optional

import requests
from bs4 import BeautifulSoup

from checkers import HEADERS

logger = logging.getLogger(__name__)

# 有料オファー（レンタル・購入）のパネル。これがあれば無料では観られない
_OFFER_MODULE_MARKER = '"ytOfferModuleRenderer"'

# `"playabilityStatus":{"status":"OK"` の status 値を抜く
_PLAYABILITY_STATUS_RE = re.compile(r'"playabilityStatus"\s*:\s*\{[^{}]*?"status"\s*:\s*"([A-Z_]+)"')

# `"ownerChannelName":"【公式】プレシディオチャンネル"` を抜く（JSON エスケープ込みで取得）
_OWNER_CHANNEL_RE = re.compile(r'"ownerChannelName"\s*:\s*("(?:[^"\\]|\\.)*")')

# 金額表記（`¥407` / `￥1,000` / `407円`）
_PRICE_RE = re.compile(r'[¥￥]\s*([\d,]+)|([\d,]+)\s*円')

# 購入（買い切り）を表す文言。無ければレンタル扱いにする
_PURCHASE_WORDS = ("購入", "で購入", "Buy", "buy")

# 削除・非公開とみなす playabilityStatus
_ENDED_STATUSES = frozenset({"ERROR", "UNPLAYABLE"})

# 再生前に確認を挟むだけで、無料で観られる playabilityStatus。
# 閲覧注意の警告（暴力・自傷など）で出る。ホラー作品の無料公開で踏みやすく、
# ended に倒すと観られる作品が一覧から消えるため streaming として扱う
_FREE_WITH_INTERSTITIAL_STATUSES = frozenset({"CONTENT_CHECK_REQUIRED"})

_TIMEOUT = 30


class YoutubeChecker:
    """YouTube の配信状況を確認するチェッカー。

    無料公開は streaming（price 0）、レンタル・購入は rental / purchase、
    削除・非公開は ended を返す。判定できない場合は RuntimeError を投げて
    既存の値を据え置く（誤って「無料」に倒さないため）。
    """

    def check(self, url: str) -> dict:
        """指定URLの配信状況を確認する。

        Args:
            url: チェック対象の YouTube 動画URL。

        Returns:
            {"status": str, "price": float | None, "channel_name": str} の辞書。
            `channel_name` は取得できなければ空文字。

        Raises:
            RuntimeError: ネットワークエラー・サーバーエラー・判定不能時。
        """
        try:
            resp = requests.get(url, headers=HEADERS, timeout=_TIMEOUT, allow_redirects=True)
        except requests.RequestException as e:
            raise RuntimeError(f"YouTube: リクエスト失敗 {e}") from e

        if resp.status_code >= 500:
            raise RuntimeError(f"YouTube: サーバーエラー (HTTP {resp.status_code})")

        html = resp.text
        channel_name = extract_channel_name(html)

        # 有料オファーが出ている時点で無料では観られない。
        # playabilityStatus より先に判定する（オファー時の status は UNPLAYABLE のため）
        if _OFFER_MODULE_MARKER in html:
            status, price = _parse_offer(html)
            logger.info("YouTube: 有料オファー検出 status=%s price=%s url=%s", status, price, url)
            return {"status": status, "price": price, "channel_name": channel_name}

        playability = extract_playability_status(html)

        if playability == "OK" or playability in _FREE_WITH_INTERSTITIAL_STATUSES:
            return {"status": "streaming", "price": 0, "channel_name": channel_name}

        if playability in _ENDED_STATUSES:
            logger.info("YouTube: playabilityStatus=%s → ended url=%s", playability, url)
            return {"status": "ended", "price": None, "channel_name": channel_name}

        if playability == "LOGIN_REQUIRED":
            # 年齢制限（無料のまま）と限定公開（実質終了）を HTML から区別できない。
            # ended に倒すと年齢制限のホラー作品などが一覧から消えるため据え置く
            raise RuntimeError("YouTube: ログインが必要（年齢制限か限定公開か判定できない）")

        soup = BeautifulSoup(html, "html.parser")
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content", "").strip():
            # 同意ページ・ボット検出ページなどで playerResponse が落ちているケース。
            # 「ページはある」だけで無料と決めつけると有料作品を無料枠に載せてしまう
            raise RuntimeError("YouTube: playabilityStatus を読めなかった（判定不能）")

        # og:title も playabilityStatus も無い = 削除・非公開・存在しない動画
        logger.debug("YouTube: og:title なし → ended url=%s", url)
        return {"status": "ended", "price": None, "channel_name": channel_name}


def extract_playability_status(html: str) -> str:
    """`ytInitialPlayerResponse.playabilityStatus.status` を返す。

    Args:
        html: watch ページの HTML。

    Returns:
        `OK` / `ERROR` / `UNPLAYABLE` / `LOGIN_REQUIRED` など。読めなければ空文字。
    """
    m = _PLAYABILITY_STATUS_RE.search(html)
    return m.group(1) if m else ""


def extract_channel_name(html: str) -> str:
    """無料公開しているチャンネル名を返す。

    `ytInitialPlayerResponse.videoDetails.ownerChannelName` を優先し、
    無ければ microformat の `<link itemprop="name" content="...">` を使う。

    Args:
        html: watch ページの HTML。

    Returns:
        チャンネル名。取得できなければ空文字。
    """
    m = _OWNER_CHANNEL_RE.search(html)
    if m:
        try:
            # JSON 文字列リテラルのまま取り出しているのでエスケープを戻す
            name = json.loads(m.group(1))
        except json.JSONDecodeError:
            name = ""
        if isinstance(name, str) and name.strip():
            return name.strip()

    soup = BeautifulSoup(html, "html.parser")
    link = soup.find("link", attrs={"itemprop": "name"})
    if link and link.get("content", "").strip():
        return link["content"].strip()

    return ""


def _parse_offer(html: str) -> tuple[str, Optional[float]]:
    """有料オファーのパネルから購入形態と金額を判定する。

    Args:
        html: watch ページの HTML。

    Returns:
        (status, price) のタプル。status は `purchase` / `rental`。
        金額を読めなかった場合 price は None。
    """
    idx = html.find(_OFFER_MODULE_MARKER)
    # オファーパネル周辺だけを見る。ページ全体を見ると関連動画の価格を拾う
    window = html[idx: idx + 4000]

    status = "purchase" if any(w in window for w in _PURCHASE_WORDS) else "rental"

    m = _PRICE_RE.search(window)
    if not m:
        return status, None

    raw = m.group(1) or m.group(2) or ""
    try:
        return status, float(raw.replace(",", ""))
    except ValueError:
        return status, None
