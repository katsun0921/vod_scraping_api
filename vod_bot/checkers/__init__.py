"""共通定数。"""

from utils.browser import USER_AGENT

NOT_FOUND_INDICATORS = [
    "404",
    "ページが見つかりません",
    "page not found",
    "not found",
    "this title is not available",
    "お探しのページは見つかりませんでした",
    "お探しのページは見つかりません",
]

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
}
