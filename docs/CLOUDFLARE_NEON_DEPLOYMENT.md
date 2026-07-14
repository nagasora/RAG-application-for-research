# Cloudflare Pages + Render + Neon + R2 への移行

PaperPilot を低コストで永続利用するための構成です。

```text
Browser
  -> Cloudflare Pages (Next.js 静的フロントエンド)
  -> Render Web Service (FastAPI)
       -> Neon Postgres (論文・会話・グラフ・ジョブのメタデータ)
       -> Cloudflare R2 (PDF 原本・図表・OCR 由来アセット)
  -> Auth0 (ログイン)
```

## 1. Neon

1. [Neon](https://neon.tech/) でプロジェクトと Postgres データベースを作成します。
2. **Pooled connection string** を取得します。
3. Render API の `DATABASE_URL` に貼り付けます。

`postgresql://` 形式でも API が SQLAlchemy 用の形式へ変換します。接続文字列は秘密情報なので、Git や `render.yaml` に書きません。

## 2. Cloudflare R2

Cloudflare Dashboard で R2 bucket を一つ作成します。例: `paperpilot-data`。

次に R2 の **API Tokens** で、その bucket に限定した Object Read & Write のトークンを作成します。表示される値を Render API に設定します。

```text
PAPER_STORAGE_BACKEND=r2
R2_ACCOUNT_ID=<Cloudflare Account ID>
R2_ACCESS_KEY_ID=<R2 Access Key ID>
R2_SECRET_ACCESS_KEY=<R2 Secret Access Key>
R2_BUCKET=paperpilot-data
R2_PREFIX=paperpilot
```

`R2_SECRET_ACCESS_KEY` は一度しか表示されないため、安全なパスワード管理ツールにも保管してください。R2 bucket は公開設定にせず、PDF の取得は PaperPilot API の認可済みエンドポイント経由にします。

## 3. Render API

既存の `paperpilot-api` サービスで以下を更新します。

- `DATABASE_URL`: Neon の pooled connection string
- 上記 R2 の `PAPER_STORAGE_BACKEND` / `R2_*` 値
- `INGESTION_MODE=background`
- `FRONTEND_ORIGIN`: Cloudflare Pages の本番 URL（例: `https://paperpilot.pages.dev`）

Auth0 の OIDC、OpenAI の設定は現在の値を維持します。保存後、**Manual Deploy → Deploy latest commit** を実行します。

Render Free は休止するため、初回アクセスの起動待ちが発生します。R2 と Neon に保存済みのデータは、その休止で失われません。

## 4. Cloudflare Pages

1. Cloudflare Dashboard → **Workers & Pages** → **Create application** → **Pages** → GitHub を接続します。
2. 対象リポジトリを選びます。
3. Build settings を次のように設定します。

```text
Root directory: frontend
Build command: corepack enable && pnpm install --frozen-lockfile && pnpm build
Build output directory: out
Environment variable: NEXT_DEPLOY_TARGET=cloudflare-pages
Environment variable: NODE_VERSION=22
```

4. Production environment variables を設定します。

```text
NEXT_PUBLIC_API_URL=https://paperpilot-api-fso4.onrender.com
NEXT_PUBLIC_AUTH_MODE=oidc
NEXT_PUBLIC_AUTH0_DOMAIN=dev-chsvrogcw3glxuxt.us.auth0.com
NEXT_PUBLIC_AUTH0_CLIENT_ID=<Auth0 SPA Client ID>
NEXT_PUBLIC_AUTH0_AUDIENCE=https://paperpilot-api-fso4.onrender.com
```

これらの `NEXT_PUBLIC_*` はフロントエンドに埋め込まれる公開値です。OpenAI キー、R2 秘密鍵、Neon 接続文字列は設定しません。

## 5. Auth0

Cloudflare Pages の公開 URL を Auth0 SPA の次の欄すべてに追加します。

- Allowed Callback URLs
- Allowed Logout URLs
- Allowed Web Origins
- Allowed Origins (CORS)

カスタムドメインを追加した場合も、その URL を同じ四箇所へ追加します。

## 動作確認

1. Cloudflare Pages で Auth0 ログインできる。
2. API の `/api/health` が `database: ok` を返す。
3. TXT を一件アップロードし、一覧に `完了` と表示される。
4. PDF をアップロードし、`解析中` → `完了` へ変わる。
5. Render を再デプロイしても、一覧・PDF が残る。

注意: R2 対応前に Render のローカルファイル領域へ保存された PDF は、Render の再デプロイ／再起動で失われている可能性があります。Neon に残る失敗・メタデータのレコードは、必要に応じて削除して PDF を再アップロードしてください。
