"""ブラウザ User-Agent 定数。

バージョンをここで一括管理する。
更新時はこのファイルの定数のみ変更すればよい。
"""

# Chrome バージョン（実際の Chrome に合わせて更新する）
CHROME_VERSION = "148.0.0.0"

# macOS バージョン
MACOS_VERSION = "10_15_7"

# WebKit バージョン
WEBKIT_VERSION = "537.36"

USER_AGENT = (
    f"Mozilla/5.0 (Macintosh; Intel Mac OS X {MACOS_VERSION}) "
    f"AppleWebKit/{WEBKIT_VERSION} (KHTML, like Gecko) "
    f"Chrome/{CHROME_VERSION} Safari/{WEBKIT_VERSION}"
)
