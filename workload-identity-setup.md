# Workload Identity Federation 設定手順

GitHub Actions から GCP リソースへ SA キーなしで認証するための設定。

---

## 概要

```
GitHub Actions（katsun0921/vod_scraping_api）
    ↓ OIDC トークン（GitHub 発行）
Workload Identity Pool: github-pool
    └── Provider: github-provider（GitHub を信頼）
            ↓ トークン検証 OK
SA: github-actions-sa
    ↓ 権限借用
Cloud Run デプロイ / Artifact Registry push
```

---

## 前提条件

- `gcloud` CLI がインストール済みであること
- `gcloud auth login` で認証済みであること
- プロジェクトが設定済みであること

```bash
gcloud config set project YOUR_PROJECT_ID
```

---

## Step 1: Workload Identity Pool を作成

```bash
gcloud iam workload-identity-pools create "github-pool" \
  --project=YOUR_PROJECT_ID \
  --location="global" \
  --display-name="GitHub Actions Pool"
```

---

## Step 2: Pool に GitHub の Provider を追加

```bash
gcloud iam workload-identity-pools providers create-oidc "github-provider" \
  --project=YOUR_PROJECT_ID \
  --location="global" \
  --workload-identity-pool="github-pool" \
  --display-name="GitHub Provider" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.actor=assertion.actor" \
  --attribute-condition="assertion.repository=='YOUR_GITHUB_ORG/YOUR_REPO'" \
  --issuer-uri="https://token.actions.githubusercontent.com"
```

**ポイント：**
- `--attribute-condition` で特定リポジトリからのリクエストのみに絞り込む（セキュリティ上重要）
- `YOUR_GITHUB_ORG/YOUR_REPO` は `katsun0921/vod_scraping_api` の形式で指定

---

## Step 3: GitHub Actions 用サービスアカウントを作成

```bash
gcloud iam service-accounts create "github-actions-sa" \
  --project=YOUR_PROJECT_ID \
  --display-name="GitHub Actions Service Account"
```

---

## Step 4: サービスアカウントに必要なロールを付与

```bash
# Cloud Run のデプロイ権限
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:github-actions-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/run.admin"

# Cloud Build の実行権限
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:github-actions-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/cloudbuild.builds.editor"

# Artifact Registry への push 権限
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:github-actions-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/artifactregistry.writer"

# サービスアカウントの借用権限（Cloud Run デプロイ時に必要）
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:github-actions-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/iam.serviceAccountUser"
```

---

## Step 5: Workload Identity Pool と SA を紐付け

```bash
gcloud iam service-accounts add-iam-policy-binding \
  github-actions-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com \
  --project=YOUR_PROJECT_ID \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/YOUR_PROJECT_NUMBER/locations/global/workloadIdentityPools/github-pool/attribute.repository/YOUR_GITHUB_ORG/YOUR_REPO"
```

> `YOUR_PROJECT_NUMBER` は以下で確認：
> ```bash
> gcloud projects describe YOUR_PROJECT_ID --format "value(projectNumber)"
> ```

---

## 設定値の確認

```bash
# Pool の確認
gcloud iam workload-identity-pools list \
  --project=YOUR_PROJECT_ID \
  --location="global"

# Provider の確認
gcloud iam workload-identity-pools providers list \
  --project=YOUR_PROJECT_ID \
  --location="global" \
  --workload-identity-pool="github-pool"

# SA の確認
gcloud iam service-accounts list --project=YOUR_PROJECT_ID
```

---

## GitHub Actions workflow で使用する値

| 項目 | 値 |
|---|---|
| `workload_identity_provider` | `projects/YOUR_PROJECT_NUMBER/locations/global/workloadIdentityPools/github-pool/providers/github-provider` |
| `service_account` | `github-actions-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com` |

---

## トラブルシューティング

| エラー | 原因 | 対処 |
|---|---|---|
| `INVALID_ARGUMENT: The attribute condition must reference one of the provider's claims` | `--attribute-condition` が未指定 | Step 2 に `--attribute-condition` を追加する |
| `Permission denied` on Cloud Run deploy | SA に `roles/run.admin` がない | Step 4 を再確認 |
| `Permission denied` on Artifact Registry | SA に `roles/artifactregistry.writer` がない | Step 4 を再確認 |
