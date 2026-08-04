"""「VOD情報源」シートの読み取り確認スクリプト（セットアップ検証用）。

CSVをimportした直後に実行し、`vod_discover` が実際に使う
`sheets.get_active_vod_x_accounts()` がシートを想定どおり読めているかを確認する。

X APIもClaude APIも呼ばないため課金は発生せず、シートへの書き込みも行わない
（読み取りのみ）。`vod_discover` を実行する前の安全な事前確認に使う。

よくある失敗:
    - 「有効/無効」列がチェックボックスではなく文字列（"有効"等）になっている
    - 「規約確認済み」列が "済" 以外（空欄・"確認済み"等）
    - 対象サービス列がSERVICESのキー（netflix等）と一致しない表記

実行:
    python -m news_bot.check_vod_sources
"""

import logging
import sys

from news_bot.discover_vod import SERVICES
from news_bot.sheets import NewsBotSheets

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def main() -> int:
    sheets = NewsBotSheets()

    logger.info("=== 「VOD情報源」シートの読み取り確認 ===")
    try:
        accounts = sheets.get_active_vod_x_accounts()
    except Exception:
        logger.exception("シートの読み取りに失敗")
        return 1

    if not accounts:
        logger.error(
            "取得対象の行が0件です。以下を確認すること:\n"
            "  - 取得方式列が 'x' か\n"
            "  - 有効/無効列がチェックボックスでONか（文字列の'有効'は無効扱い）\n"
            "  - 規約確認済み列が '済' か"
        )
        return 1

    logger.info("取得対象: %d 件\n", len(accounts))
    for account in accounts:
        cached = "あり" if account.get("user_id") else "なし（初回実行時に解決）"
        logger.info(
            "  %-20s @%-18s 名称=%s  user_idキャッシュ=%s",
            account.get("対象サービス") or "(未設定)",
            account["Xハンドル"],
            account["名称"],
            cached,
        )

    logger.info("")
    found = {a.get("対象サービス") for a in accounts}
    unknown = found - set(SERVICES)
    missing = set(SERVICES) - found

    if unknown:
        logger.error(
            "対象サービス列の値がSERVICESのキーと一致しません: %s\n  有効な値: %s",
            ", ".join(sorted(str(u) for u in unknown)),
            ", ".join(SERVICES),
        )
        return 1

    if missing:
        logger.warning("取得対象に含まれないサービス: %s", ", ".join(sorted(missing)))
        logger.warning("（意図的に無効化しているなら問題なし）")
    else:
        logger.info("対象6サービスすべてが取得対象になっています")

    logger.info("\n=== 確認完了: vod_discover を実行できます ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
