# vod-scraping-api

Google Sheets の VODs シートに登録された各VODサービスのURLにアクセスし、
配信状況（status / price）を確認・更新する Python スクリプト。

## ローカル実行手順

### 1. 仮想環境の作成

```bash
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium  # U-NEXT 用ブラウザのインストール
```

### 2. 環境変数の設定

```bash
cp .env.example .env
# .env を編集して SPREADSHEET_ID を設定
```

Google サービスアカウントの JSON キーを `service-account.json` として配置する。
（Google Sheets API の編集権限が必要）

### 3. 実行

```bash
# 通常実行（1ヶ月以内に更新済みの行はスキップ）
python checker.py

# 対象行の確認のみ（シートは更新しない）
python checker.py --dry-run

# updated_at に関わらず全行処理
python checker.py --force

# 特定の slug のみ処理
python checker.py --slug john-wick
```

## ステータス値

| status | 意味 |
|---|---|
| `streaming` | 見放題（subscription / free） |
| `rental` | レンタル（price に金額） |
| `purchase` | 購入 |
| `unavailable` | 配信なし |
| `ended` | 配信終了（404等） |

## 対応VODサービス

| サービス名 | URL形式 | 備考 |
|---|---|---|
| `Netflix` | `https://www.netflix.com/jp/title/{id}` | |
| `Amazon Prime Video` | `https://www.amazon.co.jp/gp/video/detail/{id}` | `/dp/{asin}` 形式は Cloud Run からブロックされる場合あり |
| `Hulu` | `https://www.hulu.jp/watch/{id}` | |
| `U-NEXT` | `https://video.unext.jp/title/SID{id}` | SPA のため Playwright で取得 |
| `DMM TV` | （未実装） | |

### Amazon Prime Video URL について

Amazon のURLには2つの形式があるが、**Cloud Run 環境では `/gp/video/detail/` 形式を推奨**する。

| URL形式 | 例 | Cloud Run での動作 |
|---|---|---|
| `/gp/video/detail/{id}` | `https://www.amazon.co.jp/gp/video/detail/0KM0XZX7FZJ3B8DHMAILJXUBGS/` | ✅ 正常に取得できる |
| `/dp/{asin}` | `https://www.amazon.co.jp/dp/B0FY6BPG8L` | ⚠️ ブロックされる場合がある |

VODs シートに URL を登録する際は `/gp/video/detail/` 形式を使用すること。

## デプロイ

- Cloud Run へのデプロイ手順 → [cloud-run-deploy.md](cloud-run-deploy.md)
- Workload Identity Federation の設定 → [workload-identity-setup.md](workload-identity-setup.md)
- CI/CD は GitHub Actions（`.github/workflows/deploy.yml`）で自動化済み
