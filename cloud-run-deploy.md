# Cloud Run デプロイ手順書

vod-scraping-api を Google Cloud Run（第2世代）へデプロイする手順。

---

## 現在の状況

| 項目 | 状態 |
|------|------|
| Docker コンテナ化 | ✅ 完了 |
| Cloud Run デプロイ | ✅ 完了 |
| Sheets アクセス権限 | ✅ 完了 |
| GAS Config 設定 | ✅ 完了 |
| CI/CD（GitHub Actions） | ⏳ 未 |

---

## 前提条件

- `gcloud` CLI がインストール済みであること
- `gcloud auth login` で認証済みであること
- GCP プロジェクトが設定済みであること

```bash
gcloud config set project YOUR_PROJECT_ID
gcloud config get project  # 確認
```

---

## Step 1: 必要な API を有効化

```bash
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  sheets.googleapis.com
```

---

## Step 2: Artifact Registry にリポジトリを作成

```bash
gcloud artifacts repositories create vod-scraping-api \
  --repository-format=docker \
  --location=asia-northeast1
```

---

## Step 3: Cloud Build でイメージをビルド＆プッシュ

```bash
cd /path/to/vod_scraping_api

gcloud builds submit \
  --tag asia-northeast1-docker.pkg.dev/YOUR_PROJECT_ID/vod-scraping-api/app:latest \
  .
```

> ⚠ `YOUR_PROJECT_ID` は `gcloud config get project` で確認した値に置き換える

---

## Step 4: Cloud Run にデプロイ（第2世代）

```bash
gcloud run deploy vod-scraping-api \
  --image asia-northeast1-docker.pkg.dev/YOUR_PROJECT_ID/vod-scraping-api/app:latest \
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
|---|---|---|
| `--execution-environment gen2` | 第2世代 | 高速起動・長いタイムアウト対応 |
| `--no-allow-unauthenticated` | 認証必須 | IAM で保護 |
| `--timeout 540` | 9分 | 全VOD確認に時間がかかるため |
| `--max-instances 1` | 最大1インスタンス | シートの同時書き込み競合を防ぐ |
| `--concurrency 1` | 同時リクエスト1 | 同上 |

デプロイ完了後に URL が表示される：

```
Service URL: https://vod-scraping-api-XXXX-an.a.run.app
```

---

## Step 5: ヘルスチェック

```bash
curl -X GET \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  https://vod-scraping-api-XXXX-an.a.run.app/health

# {"status": "ok"} が返れば成功
```

> fish シェルの場合： `(gcloud auth print-identity-token)`（`$()` ではなく `()`）

---

## Step 6: Google Sheets へのアクセス権限付与

Cloud Run はデフォルトのコンピューティングサービスアカウントで動作する。
スプレッドシート側でこのアカウントに編集権限を付与する。

**サービスアカウントのメールアドレス形式：**
```
YOUR_PROJECT_NUMBER-compute@developer.gserviceaccount.com
```

プロジェクト番号の確認：
```bash
gcloud projects describe YOUR_PROJECT_ID --format "value(projectNumber)"
```

**Sheets 側の設定：**
1. 対象のスプレッドシートを開く
2. 右上「共有」をクリック
3. 上記のサービスアカウントのメールアドレスを **編集者** として追加

> ⚠ GCP IAM の `roles/sheets.editor` は存在しない。Sheets の共有設定のみで権限管理する。

---

## Step 7: 動作確認（本番実行）

```bash
curl -X POST \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  https://vod-scraping-api-XXXX-an.a.run.app/

# {"processed": N, "skipped": N, "errors": 0} が返れば成功
```

---

## Step 8: GAS Config シートの更新

Google Sheets の Config シートに以下を設定する：

| key | value |
|---|---|
| `VOD_CHECKER_URL` | `https://vod-scraping-api-XXXX-an.a.run.app` |

設定後、GAS メニュー「VOD管理 → VOD状況を更新（強制実行）」から動作確認できる。

---

## 再デプロイ手順（更新時）

コードを変更した場合は以下を実行：

```bash
# ビルド＆プッシュ
gcloud builds submit \
  --tag asia-northeast1-docker.pkg.dev/YOUR_PROJECT_ID/vod-scraping-api/app:latest \
  .

# デプロイ
gcloud run deploy vod-scraping-api \
  --image asia-northeast1-docker.pkg.dev/YOUR_PROJECT_ID/vod-scraping-api/app:latest \
  --region asia-northeast1
```

---

## ログの確認

```bash
# リアルタイムログ
gcloud run services logs tail vod-scraping-api \
  --region asia-northeast1

# 過去のログ（最新50件）
gcloud logging read \
  "resource.type=cloud_run_revision AND resource.labels.service_name=vod-scraping-api" \
  --limit 50 \
  --format "table(timestamp, textPayload)"
```

---

## トラブルシューティング

| 症状 | 原因 | 対処 |
|------|------|------|
| `gcloud builds submit` が失敗 | API 未有効化 | Step 1 の API 有効化を再確認 |
| Cloud Run が 401 | 認証トークンなし | `-H "Authorization: Bearer $(gcloud auth print-identity-token)"` を追加 |
| Sheets への書き込みが 403 | 権限不足 | Step 6 の Sheets 共有設定を確認 |
| タイムアウト（540秒超） | 対象行が多すぎる | `--timeout 600` に延長またはバッチサイズを減らす |
| Amazon: ロボット検出 | 連続アクセス | 実行頻度を下げる |

---

## 全体フロー

```
GAS メニュー or Cloud Scheduler
         ↓ HTTP POST（IAM 認証）
Cloud Run（第2世代）vod-scraping-api
         ↓ HTTP スクレイピング
各 VOD サービスのページ
         ↓ Google Sheets API で書き込み
VODs シート（status / price / updated_at を更新）
```
