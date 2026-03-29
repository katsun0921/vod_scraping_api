# vod-checker

Google Sheets の VODs シートに登録された各VODサービスのURLにアクセスし、
配信状況（status / price）を確認・更新する Python スクリプト。

## ローカル実行手順

### 1. 仮想環境の作成

```bash
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
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

## Cloud Run へのデプロイ手順

### 1. 事前準備

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID

gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  sheets.googleapis.com
```

### 2. サービスアカウントへの Sheets 権限付与

```bash
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:YOUR_PROJECT_NUMBER-compute@developer.gserviceaccount.com" \
  --role="roles/sheets.editor"
```

### 3. Artifact Registry へイメージをビルド・プッシュ

```bash
# リポジトリ作成（初回のみ）
gcloud artifacts repositories create vod-checker \
  --repository-format=docker \
  --location=asia-northeast1

# ビルド & プッシュ
gcloud builds submit \
  --tag asia-northeast1-docker.pkg.dev/YOUR_PROJECT_ID/vod-checker/app:latest
```

### 4. Cloud Run にデプロイ（第2世代）

```bash
gcloud run deploy vod-checker \
  --image asia-northeast1-docker.pkg.dev/YOUR_PROJECT_ID/vod-checker/app:latest \
  --platform managed \
  --region asia-northeast1 \
  --generation 2 \
  --no-allow-unauthenticated \
  --set-env-vars SPREADSHEET_ID=YOUR_SPREADSHEET_ID \
  --memory 512Mi \
  --timeout 540 \
  --max-instances 1
```

### 5. 動作確認

```bash
curl -X POST \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  https://vod-checker-XXXX-an.a.run.app/

# レスポンス例
# {"processed": 12, "skipped": 488, "errors": 0}
```

### 6. Cloud Scheduler で週次自動実行（オプション）

```bash
# 毎週日曜 深夜3時（JST）= UTC 18:00
gcloud scheduler jobs create http vod-checker-weekly \
  --location asia-northeast1 \
  --schedule "0 18 * * 0" \
  --uri "https://vod-checker-XXXX-an.a.run.app/" \
  --http-method POST \
  --oidc-service-account-email YOUR_SA@YOUR_PROJECT_ID.iam.gserviceaccount.com \
  --time-zone "UTC"
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

| vod フィールド値 | URL形式 |
|---|---|
| `netflix` | `www.netflix.com/jp/title/{id}` |
| `amazon` | `www.amazon.co.jp/dp/{asin}` |
| `unext` | `video.unext.jp/title/SID{id}` |
