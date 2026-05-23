# Workload Identity Federation 設定手順

GitHub Actions から GCP リソースへ SA キーなしで認証するための設定。

---

## Workload Identity Federation とは

外部サービス（GitHub Actions など）が GCP のリソースを操作するとき、
**「本当に正規のリクエストか？」を GCP が検証する仕組み**。

### 従来の方法（SA キー）の問題

```
GitHub Actions → SA キー（JSON）を使って GCP に認証
```

- SA キーを GitHub Secrets に保存する必要がある
- キーが漏洩したら即アウト
- キーの有効期限管理が必要

### Workload Identity Federation の仕組み

```
GitHub Actions
    ↓ 「自分は katsun0921/vod_scraping_api の GitHub Actions だ」という
    ↓ OIDC トークン（JWT）を GitHub から発行してもらう
    ↓
GCP（Workload Identity）
    ↓ GitHub の公開鍵でトークンを検証
    ↓ 「本物の GitHub Actions からのリクエスト」と確認
    ↓
サービスアカウントの権限を一時的に借用
    ↓
Cloud Run デプロイ・Artifact Registry push が可能に
```

**キーファイルのやり取りが一切不要**になる。

---

## Workload Identity Pool と Provider

```
Workload Identity Pool（github-pool）
│
│  外部 ID プロバイダーをまとめる「箱」
│  複数の Provider（GitHub・GitLab など）を束ねられる
│
└── Provider（github-provider）
        │
        │  「GitHub の OIDC トークンを信頼する」という設定
        │  - 発行元: https://token.actions.githubusercontent.com
        │  - どのリポジトリを許可するかをここで絞り込む
```

| 用語 | 役割 |
|---|---|
| **Pool** | 外部 ID プロバイダーをグループ化する入れ物 |
| **Provider** | Pool 内で「GitHub を信頼する」と宣言する設定 |
| **SA（サービスアカウント）** | 実際に GCP 操作を行う主体。Pool 経由でのみ借用を許可する |

---

## 全体フロー

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

| パラメーター | 説明 |
|---|---|
| `github-pool` | Pool の ID。後の手順で参照する |
| `--project` | GCP プロジェクト ID |
| `--location` | `global` 固定（Workload Identity は常にグローバル） |
| `--display-name` | GCP Console に表示される名前（任意） |

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

| パラメーター | 説明 |
|---|---|
| `github-provider` | Provider の ID |
| `--workload-identity-pool` | 紐付ける Pool の ID |
| `--issuer-uri` | GitHub の OIDC 発行元 URL（固定値） |
| `--attribute-mapping` | GitHub トークンのクレームを GCP の属性にマッピングする設定 |
| `--attribute-condition` | アクセスを許可する条件。特定リポジトリに絞り込む（セキュリティ上必須） |

**`--attribute-mapping` の詳細：**

| マッピング | 意味 |
|---|---|
| `google.subject=assertion.sub` | GitHub トークンの `sub`（subject）を GCP の主体として使う |
| `attribute.repository=assertion.repository` | GitHub の `repository`（`org/repo` 形式）を属性として取り込む |
| `attribute.actor=assertion.actor` | GitHub の実行ユーザーを属性として取り込む |

**`--attribute-condition` のポイント：**
- `assertion.repository=='YOUR_GITHUB_ORG/YOUR_REPO'` と指定することで、**このリポジトリからのリクエストのみ**に絞り込む
- 指定しないと Pool を知っている任意のリポジトリからアクセスできてしまう

---

## Step 3: GitHub Actions 用サービスアカウントを作成

```bash
gcloud iam service-accounts create "github-actions-sa" \
  --project=YOUR_PROJECT_ID \
  --display-name="GitHub Actions Service Account"
```

| パラメーター | 説明 |
|---|---|
| `github-actions-sa` | SA の ID。メールアドレスの @ 前の部分になる |
| `--display-name` | GCP Console に表示される名前（任意） |

作成後のメールアドレス形式：
```
github-actions-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com
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

| パラメーター | 説明 |
|---|---|
| `--member` | 権限を付与する対象。`serviceAccount:メールアドレス` の形式 |
| `--role` | 付与する IAM ロール |

**付与するロールの説明：**

| ロール | 用途 |
|---|---|
| `roles/run.admin` | Cloud Run サービスのデプロイ・管理 |
| `roles/cloudbuild.builds.editor` | Cloud Build でイメージをビルド・プッシュ |
| `roles/artifactregistry.writer` | Artifact Registry へのイメージ書き込み |
| `roles/iam.serviceAccountUser` | Cloud Run デプロイ時に SA を指定するために必要 |

> ⚠ `roles/sheets.editor` は GCP IAM には存在しない。Sheets へのアクセスは Google Sheets 側の共有設定で管理する。

---

## Step 5: Workload Identity Pool と SA を紐付け

```bash
gcloud iam service-accounts add-iam-policy-binding \
  github-actions-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com \
  --project=YOUR_PROJECT_ID \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/YOUR_PROJECT_NUMBER/locations/global/workloadIdentityPools/github-pool/attribute.repository/YOUR_GITHUB_ORG/YOUR_REPO"
```

| パラメーター | 説明 |
|---|---|
| `--role` | `roles/iam.workloadIdentityUser`：Pool 経由での SA 借用を許可するロール |
| `--member` | `principalSet://` から始まる特殊な形式。Pool の特定属性（リポジトリ）を主体として指定する |

**`--member` の構造：**

```
principalSet://iam.googleapis.com
  /projects/YOUR_PROJECT_NUMBER        ← プロジェクト番号
  /locations/global
  /workloadIdentityPools/github-pool   ← Pool ID
  /attribute.repository                ← Step 2 でマッピングした属性
  /YOUR_GITHUB_ORG/YOUR_REPO           ← 許可するリポジトリ
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
| `Unable to impersonate` | SA と Pool の紐付けが未設定 | Step 5 を再確認 |
