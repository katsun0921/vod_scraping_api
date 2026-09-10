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

判定に使うシグナルを生のまま覗く診断用に `probe()` を用意している。
開発環境から YouTube へ到達できないため、実際に何が返ってきているかは
本番（GitHub Actions / Cloud Run）で `youtube_free_patch.py --probe` を
流して確認する。
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

# 同意ページ（EU 向け Cookie 同意など）を示す痕跡。
# ここに落ちると playerResponse が丸ごと無く、判定不能になる
_CONSENT_MARKERS = (
    "consent.youtube.com",
    "Before you continue",
    "ご利用の前に",
)

# ボット検出・レート制限ページを示す痕跡
_BOT_CHECK_MARKERS = (
    "unusual traffic",
    "通常と異なるトラフィック",
    "/sorry/index",
    "captcha",
)

# probe() が返すオファー抜粋の長さ
_OFFER_EXCERPT_CHARS = 300

# ytInitialPlayerResponse が埋まっているかの判定
_PLAYER_RESPONSE_MARKER = "ytInitialPlayerResponse"


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
        resp = _fetch(url)

        if resp.status_code >= 500:
            raise RuntimeError(f"YouTube: サーバーエラー (HTTP {resp.status_code})")

        return judge_html(resp.text, url)


def _fetch(url: str):
    """watch ページを取得する。

    Args:
        url: 取得対象の YouTube 動画URL。

    Returns:
        `requests.Response`。

    Raises:
        RuntimeError: ネットワークエラー時。
    """
    try:
        return requests.get(url, headers=HEADERS, timeout=_TIMEOUT, allow_redirects=True)
    except requests.RequestException as e:
        raise RuntimeError(f"YouTube: リクエスト失敗 {e}") from e


def judge_html(html: str, url: str = "") -> dict:
    """取得済みの HTML から配信状況を判定する（HTTP アクセスを伴わない純粋関数）。

    `check()` と `probe()` が同じ判定を共有するために切り出している。
    診断モードで「実際にどう判定されるか」を再取得せずに出せる。

    Args:
        html: watch ページの HTML。
        url : ログに出す URL（任意）。

    Returns:
        {"status": str, "price": float | None, "channel_name": str} の辞書。

    Raises:
        RuntimeError: 判定不能時。
    """
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

    if extract_og_title(html):
        # 同意ページ・ボット検出ページなどで playerResponse が落ちているケース。
        # 「ページはある」だけで無料と決めつけると有料作品を無料枠に載せてしまう
        raise RuntimeError("YouTube: playabilityStatus を読めなかった（判定不能）")

    # og:title も playabilityStatus も無い = 削除・非公開・存在しない動画
    logger.debug("YouTube: og:title なし → ended url=%s", url)
    return {"status": "ended", "price": None, "channel_name": channel_name}


def extract_og_title(html: str) -> str:
    """`og:title` の内容を返す。無ければ空文字。

    Args:
        html: watch ページの HTML。

    Returns:
        og:title の content。取れなければ空文字。
    """
    soup = BeautifulSoup(html, "html.parser")
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content", "").strip():
        return og_title["content"].strip()
    return ""


def classify_page(html: str, final_url: str = "") -> str:
    """取得したページの種類を判定する（診断用）。

    判定不能が続くとき、原因が「同意ページを掴まされている」のか
    「YouTube の HTML 構造が変わった」のかで対応がまったく違うため、
    probe で切り分けられるようにする。

    Args:
        html     : 取得した HTML。
        final_url: リダイレクト後の URL。

    Returns:
        `consent`（同意ページ）/ `bot_check`（ボット検出）/ `normal`。
    """
    haystack = f"{final_url}\n{html[:200_000]}"
    if any(marker in haystack for marker in _CONSENT_MARKERS):
        return "consent"
    if any(marker in haystack.lower() for marker in _BOT_CHECK_MARKERS):
        return "bot_check"
    return "normal"


def probe(url: str) -> dict:
    """判定に使うシグナルを生のまま取り出す（診断用。WordPress は更新しない）。

    開発環境から YouTube へ到達できないため、判定ロジックが実データに対して
    正しく動くかは本番でしか確かめられない。マーカーや正規表現が期待どおり
    HTML に当たっているかを、1回のアクセスでまとめて可視化する。

    例外は投げない。ネットワークエラーも `error` に載せて返す
    （診断が途中で止まると、複数URLをまとめて見るときに不便なため）。

    Args:
        url: 診断対象の YouTube 動画URL。

    Returns:
        観測したシグナルと、それに基づく判定結果を含む辞書。
    """
    result: dict = {
        "url": url,
        "final_url": "",
        "http_status": 0,
        "html_length": 0,
        "page_type": "",
        "has_player_response": False,
        "playability_status": "",
        "has_offer_marker": False,
        "offer_excerpt": "",
        "channel_name": "",
        "og_title": "",
        "verdict": None,
        "error": "",
    }

    try:
        resp = _fetch(url)
    except RuntimeError as e:
        result["error"] = str(e)
        return result

    html = resp.text
    result["final_url"] = resp.url
    result["http_status"] = resp.status_code
    result["html_length"] = len(html)
    result["page_type"] = classify_page(html, resp.url)
    result["has_player_response"] = _PLAYER_RESPONSE_MARKER in html
    result["playability_status"] = extract_playability_status(html)
    result["has_offer_marker"] = _OFFER_MODULE_MARKER in html
    result["offer_excerpt"] = _offer_excerpt(html)
    result["channel_name"] = extract_channel_name(html)
    result["og_title"] = extract_og_title(html)

    if resp.status_code >= 500:
        result["error"] = f"YouTube: サーバーエラー (HTTP {resp.status_code})"
        return result

    try:
        result["verdict"] = judge_html(html, url)
    except RuntimeError as e:
        result["error"] = str(e)

    return result


def _offer_excerpt(html: str) -> str:
    """有料オファーのパネル周辺を、読める長さに詰めて返す（診断用）。

    Args:
        html: watch ページの HTML。

    Returns:
        空白を潰した抜粋。オファーが無ければ空文字。
    """
    idx = html.find(_OFFER_MODULE_MARKER)
    if idx < 0:
        return ""
    window = html[idx: idx + _OFFER_EXCERPT_CHARS]
    return " ".join(window.split())


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
