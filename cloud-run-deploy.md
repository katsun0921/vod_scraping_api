# VOD チェッカー デプロイ手順書
## ローカル動作確認済み → Cloud Run（第2世代）へ

---

## 現在の状況

| 項目 | 状態 |
|------|------|
| ローカルスクレイピング | ✅ 完了 |
| Docker コンテナ化 | ✅ 完了 |
| Cloud Run デプロイ | ⏳ 未 |
| Cloud Scheduler（週次自動実行） | ⏳ 未 |

---

## Step 1：Dockerfile の確認

`Dockerfile`はFlask + gunicorn構成で作成済みです。

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--timeout", "600", "main:app"]
```

---

## Step 2：Cloud Run 用エントリーポイント（main.py）の確認

`main.py`はFlaskアプリとして作成済みです。

- `POST /` → VOD チェック実行（`checker.run()` を呼び出し）
- `GET /health` → ヘルスチェック用

---

## Step 3：.env の確認

`.env` が存在することを確認します。存在しない場合は `.env.example` をコピーして作成してください。

```bash
cp .env.example .env
# .env を編集して実際の値を設定
```

---

## Step 4：Docker でローカル動作確認

### 4-1. Docker イメージをビルド

```bash
docker build -t vod-checker .
```

### 4-2. ローカルで起動

```bash
docker run \
  -e SPREADSHEET_ID=your_spreadsheet_id \
  -e GOOGLE_APPLICATION_CREDENTIALS=/app/service-account.json \
  -v $(pwd)/service-account.json:/app/service-account.json \
  -p 8080:8080 \
  vod-checker
```

### 4-3. 別ターミナルから動作確認

```bash
# ヘルスチェック
curl http://localhost:8080/health

# VODチェック実行
curl -X POST http://localhost:8080/

# 期待されるレスポンス
# {"processed": 12, "skipped": 88, "errors": 0}
```

---

## Step 5：Google Cloud の準備

### 5-1. Google Cloud SDK のインストール確認

```bash
gcloud --version
```

インストールされていない場合：
```bash
# Mac（Homebrew）
brew install google-cloud-sdk

# または公式インストーラー
# https://cloud.google.com/sdk/docs/install
```

### 5-2. ログインとプロジェクト設定

```bash
# ログイン
gcloud auth login

# プロジェクト一覧を確認
gcloud projects list

# プロジェクトを設定
gcloud config set project YOUR_PROJECT_ID

# 設定確認
gcloud config get project
```

### 5-3. 必要な API を有効化

```bash
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  cloudscheduler.googleapis.com \
  sheets.googleapis.com
```

---

## Step 6：Artifact Registry にリポジトリを作成

```bash
gcloud artifacts repositories create vod-checker \
  --repository-format=docker \
  --location=asia-northeast1 \
  --description="VOD チェッカー"
```

---

## Step 7：Cloud Build でイメージをビルド・プッシュ

```bash
# ローカルの Docker ではなく Cloud Build を使う（M1/M2 Mac でのアーキテクチャ問題を回避）
gcloud builds submit \
  --tag asia-northeast1-docker.pkg.dev/YOUR_PROJECT_ID/vod-checker/app:latest \
  .
```

> ⚠ `YOUR_PROJECT_ID` は `gcloud config get project` で確認した値に置き換えてください

---

## Step 8：Cloud Run にデプロイ（第2世代）

```bash
gcloud run deploy vod-checker \
  --image asia-northeast1-docker.pkg.dev/YOUR_PROJECT_ID/vod-checker/app:latest \
  --platform managed \
  --region asia-northeast1 \
  --execution-environment gen2 \
  --no-allow-unauthenticated \
  --set-env-vars SPREADSHEET_ID=YOUR_SPREADSHEET_ID \
  --memory 512Mi \
  --timeout 540 \
  --max-instances 1 \
  --concurrency 1
```

**オプションの説明：**

| オプション | 値 | 理由 |
|-----------|-----|------|
| `--execution-environment gen2` | 第2世代 | 高速起動・長いタイムアウト対応 |
| `--no-allow-unauthenticated` | 認証必須 | IAM で保護 |
| `--timeout 540` | 9分 | 全VOD確認に時間がかかるため |
| `--max-instances 1` | 最大1インスタンス | 同時実行でシートが競合しないよう制限 |
| `--concurrency 1` | 同時リクエスト1 | 同上 |

デプロイ完了後に URL が表示されます：
```
Service URL: https://vod-checker-xxxx-an.a.run.app
```

---

## Step 9：サービスアカウントに Sheets の権限を付与

Cloud Run が Google Sheets にアクセスするための権限を付与します。

```bash
# Cloud Run のデフォルトサービスアカウントを確認
gcloud run services describe vod-checker \
  --region asia-northeast1 \
  --format "value(spec.template.spec.serviceAccountName)"

# Sheets の編集権限を付与
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:YOUR_PROJECT_NUMBER-compute@developer.gserviceaccount.com" \
  --role="roles/sheets.editor"
```

> `YOUR_PROJECT_NUMBER` は以下で確認できます：
> ```bash
> gcloud projects describe YOUR_PROJECT_ID --format "value(projectNumber)"
> ```

また、Google Sheets 側でもサービスアカウントのメールアドレスに編集権限を付与します：
```
スプレッドシート → 共有 → YOUR_PROJECT_NUMBER-compute@developer.gserviceaccount.com を追加（編集者）
```

---

## Step 10：Cloud Run の動作確認

```bash
# 認証トークンを取得して POST リクエスト
curl -X POST \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  https://vod-checker-xxxx-an.a.run.app/

# 期待されるレスポンス
# {"processed": 12, "skipped": 88, "errors": 0}
```

---

## Step 11：Cloud Scheduler で週次自動実行

```bash
# サービスアカウントを作成（Scheduler 用）
gcloud iam service-accounts create vod-scheduler \
  --display-name "VOD Scheduler"

# Cloud Run の起動権限を付与
gcloud run services add-iam-policy-binding vod-checker \
  --region asia-northeast1 \
  --member "serviceAccount:vod-scheduler@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role "roles/run.invoker"

# 週次スケジュールを作成（毎週日曜 深夜3時 JST = UTC 18:00）
gcloud scheduler jobs create http vod-checker-weekly \
  --location asia-northeast1 \
  --schedule "0 18 * * 0" \
  --uri "https://vod-checker-xxxx-an.a.run.app/" \
  --http-method POST \
  --oidc-service-account-email vod-scheduler@YOUR_PROJECT_ID.iam.gserviceaccount.com \
  --time-zone "UTC" \
  --attempt-deadline 600s
```

---

## Step 12：手動テスト実行（Scheduler から）

```bash
# スケジューラーから手動実行
gcloud scheduler jobs run vod-checker-weekly \
  --location asia-northeast1
```

---

## Step 13：ログの確認

```bash
# リアルタイムログ
gcloud run services logs tail vod-checker \
  --region asia-northeast1

# 過去のログ（最新50件）
gcloud logging read \
  "resource.type=cloud_run_revision \
   AND resource.labels.service_name=vod-checker" \
  --limit 50 \
  --format "table(timestamp, textPayload)"
```

---

## 完成後の全体フロー

```
毎週日曜 深夜3時（JST）
         ↓ Cloud Scheduler が自動トリガー
         ↓
Cloud Run（第2世代）vod-checker
         ↓
         ├─ VODs シートを読み込む
         │
         ├─ updated_at が1ヶ月以内 → スキップ
         │
         └─ 1ヶ月以上 → URL にアクセスして確認
               ├─ Netflix / Amazon / U-NEXT
               ├─ レート制限（3〜5秒 + 10秒待機）
               └─ status / price / updated_at を更新
         ↓
Apps Script の weeklyBatch（別途実行）
         ↓ JSON 再生成 + Cloudflare キャッシュパージ
         ↓
api.katsumascore.blog のキャッシュ更新完了
```

---

## トラブルシューティング

| 症状 | 原因 | 対処 |
|------|------|------|
| `docker build` が失敗 | Dockerfile の記述ミス | エラーメッセージを確認 |
| `gcloud builds submit` が失敗 | API 未有効化 | Step 5-3 の API 有効化を再確認 |
| Cloud Run が 403 | 認証トークンなし | `-H "Authorization: Bearer $(gcloud auth print-identity-token)"` を追加 |
| Sheets への書き込みが 403 | 権限不足 | Step 9 のサービスアカウント設定を確認 |
| タイムアウト（540秒超） | 対象行が多すぎる | `--timeout 600` に延長またはバッチサイズを減らす |
| Amazon: ロボット検出 | 連続アクセス | Cloud Scheduler の実行頻度を下げる |

---

*最終更新：2026-03-27*
