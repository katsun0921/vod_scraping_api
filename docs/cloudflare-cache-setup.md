# Cloudflare キャッシュ設定手順

WordPress REST API を Cloudflare でキャッシュし、Next.js フロントエンドへ高速配信する。

---

## アーキテクチャ

```
Next.js フロントエンド
    ↓ fetch
Cloudflare（キャッシュレイヤー）
    ↓ MISS 時のみ
WordPress REST API
```

---

## Step 1：ドメインを Cloudflare に追加

1. [https://dash.cloudflare.com](https://dash.cloudflare.com) にログイン
2. 「Domains」→「Onboard domain」
3. ドメイン名を入力 → プラン選択（Free で可）→ DNS スキャン
4. 表示された Cloudflare ネームサーバーを ConoHa Wing に設定

### ConoHa Wing のネームサーバー変更

ConoHa コントロールパネル → WING → ドメイン → 対象ドメイン → ネームサーバー設定 → カスタム

| ネームサーバー | 値（Cloudflare 画面に表示された値） |
|---|---|
| NS1 | `xxxx.ns.cloudflare.com` |
| NS2 | `xxxx.ns.cloudflare.com` |

> DNS 反映まで最大 24〜72 時間かかる。

---

## Step 2：SSL/TLS 設定

Cloudflare → SSL/TLS → Overview → 暗号化モード：**Full** に設定

> ⚠️ 「Flexible」を選ぶと WordPress 管理画面がリダイレクトループになる。

---

## Step 3：Cache Rules（WP REST API キャッシュ）

Cloudflare → Caching → Cache Rules → Create rule

### ルール：VOD API キャッシュ

**条件：**
```
URI Path starts with /wp-json/wp/v2/posts
```

**アクション：**

| 設定 | 値 |
|---|---|
| Cache eligibility | Eligible for cache |
| Edge Cache TTL | 1 hour |
| Browser Cache TTL | 5 minutes |

---

## Step 4：WordPress 管理画面のバイパスルール

Cloudflare → Security → Custom rules → Create rule

**条件（Expression Editor）：**
```
(http.request.uri.path contains "/wp-admin") or
(http.request.uri.path contains "/wp-login.php") or
(http.request.uri.path contains "/wp-cron.php")
```

> SiteGuard のカスタムログイン URL を使っている場合はそのパスも追加する。

**アクション：Skip → All remaining custom rules**

---

## Step 5：Speed 設定（WordPress 互換）

Cloudflare → Speed → Optimization → Content Optimization

| 機能 | 設定 |
|---|---|
| Rocket Loader | OFF |
| Auto Minify | OFF |

---

## Step 6：キャッシュパージ

スクレイパーが ACF を更新した後、対象投稿のキャッシュを削除する。

```bash
curl -X POST "https://api.cloudflare.com/client/v4/zones/YOUR_ZONE_ID/purge_cache" \
  -H "Authorization: Bearer YOUR_API_TOKEN" \
  -H "Content-Type: application/json" \
  --data '{
    "files": [
      "https://example.com/wp-json/wp/v2/posts/12345"
    ]
  }'
```

**API トークンの取得：**
Cloudflare → My Profile → API Tokens → Create Token → Cache Purge テンプレート

---

## DNS レコード注意事項

| Type | Name | Proxy | 注意 |
|---|---|---|---|
| A | mail | DNS only（グレー雲）| Proxied にするとメール送受信が失敗する |
| MX | @ | DNS only | 常に DNS only |

---

## トラブルシューティング

| 症状 | 原因 | 対処 |
|---|---|---|
| 管理画面がリダイレクトループ | SSL が Flexible | Step 2 で Full に変更 |
| Gutenberg が正常動作しない | Rocket Loader が ON | Step 5 で OFF に変更 |
| wp-admin にアクセスできない | バイパスルール未設定 | Step 4 を確認 |
| キャッシュが更新されない | パージ未実行 | Step 6 を実行 |
| メールが届かない | mail レコードが Proxied | DNS only に変更 |

---

## 緊急時

Cloudflare → Overview → Advanced Actions → **Pause Cloudflare on Site**

DNS が直接サーバーに向き、Cloudflare を完全バイパスできる。解決後は必ず再有効化する。
