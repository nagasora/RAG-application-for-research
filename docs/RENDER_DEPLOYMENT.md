# Render デプロイ（公開検証環境）

## この構成の対象

リポジトリ直下の `render.yaml` は、PaperPilot を **公開検証**するための最小構成です。

```text
ブラウザ → PaperPilot Web（Next.js） → PaperPilot API（FastAPI） → Render Postgres
```

PDF 原本と抽出アセットは API コンテナのローカル領域に保存します。したがって、無料 Web Service が停止・再起動・再デプロイされると、アップロード済みの論文と図表アセットは失われます。無料 Render Postgres も 30 日で失効します。研究データを保持する本番運用には使わないでください。

本番化する場合は、S3/R2 等のオブジェクトストレージ対応を実装してから、API を有料プランと persistent disk またはオブジェクトストレージへ移行します。Celery による非同期取込も、原本とアセットを API/worker 間で共有できるストレージへ移してから有効化します。

## 事前準備

1. GitHub にこのリポジトリを push する。
2. Render に GitHub を接続する。
3. 本番用の OIDC プロバイダを用意し、次を確認する。
   - issuer URL
   - API audience
   - JWKS URL
   - フロントエンド URL を redirect/origin として許可できること
4. OpenAI を使う場合は API key を用意する。キーはリポジトリや `NEXT_PUBLIC_*` に保存しない。

## Render での作成順序

`render.yaml` を含むブランチを選んで **New + / Blueprint** を作成します。このBlueprintはAPIとPostgresを作成します。初回作成時、Render は `sync: false` の値を入力するよう促します。

1. `paperpilot-api` と `paperpilot-db` を作成する。
2. API の Environment で、OIDC 値と `OPENAI_API_KEY` を入力する。
3. API が `https://paperpilot-api-<suffix>.onrender.com/api/health` で `{"status":"ok","database":"ok"}` を返すことを確認する。
4. Dashboard の **New + / Web Service** で同じリポジトリからフロントエンドを作成する。Language は Docker、Root Directory は `frontend`、Dockerfile Path は `Dockerfile` にする。
5. `paperpilot-web` の `NEXT_PUBLIC_API_URL` に、上記 API の **HTTPS公開URL（末尾スラッシュなし）** を入れ、`NEXT_PUBLIC_AUTH_MODE=oidc` を設定してデプロイする。Auth0 のSPAログインを使う場合は、さらに `NEXT_PUBLIC_AUTH0_DOMAIN`、`NEXT_PUBLIC_AUTH0_CLIENT_ID`、`NEXT_PUBLIC_AUTH0_AUDIENCE`（API Identifier）を設定する。
6. API の `FRONTEND_ORIGIN` を、作成されたフロントエンドの `https://paperpilot-web-<suffix>.onrender.com` に変更して再デプロイする。
7. OIDC プロバイダにも同じフロントエンド URL を登録する。

`NEXT_PUBLIC_*` は Next.js のビルド時に埋め込まれます。値を変更した際は Web Service で **Save, rebuild, and deploy** を選びます。公開変数へ OpenAI key、OIDC secret、DB URL を入れてはいけません。Auth0 の Domain、SPA Client ID、API Identifier は公開設定値であり、Client Secret は設定しません。

### Auth0 を使う場合の値

API の Environment:

```text
OIDC_ISSUER=https://<tenant>.us.auth0.com/
OIDC_AUDIENCE=<Auth0 API の Identifier>
OIDC_JWKS_URL=https://<tenant>.us.auth0.com/.well-known/jwks.json
```

Web の Environment:

```text
NEXT_PUBLIC_AUTH0_DOMAIN=<tenant>.us.auth0.com
NEXT_PUBLIC_AUTH0_CLIENT_ID=<SPA の Client ID>
NEXT_PUBLIC_AUTH0_AUDIENCE=<Auth0 API の Identifier>
```

Auth0 SPA の Application Settings では、Web の公開URLを `Allowed Callback URLs`、`Allowed Logout URLs`、`Allowed Web Origins`、`Allowed Origins (CORS)` に登録する。

## 最小構成の制約

- `INGESTION_MODE=inline` です。PDF の抽出中は API リクエストが完了するまで待ちます。
- OCR は無効です。必要になったら、まず有料 worker と共有オブジェクトストレージへ移行します。
- Free Web Service は 15 分無アクセスで停止し、次のアクセス時には起動待ちが発生します。
- Free Web Service は persistent disk を利用できません。
- Free Postgres は 1 GB、30 日で失効し、バックアップもありません。
- `AUTH_MODE=oidc` のまま公開します。`AUTH_MODE=dev`、`NEXT_PUBLIC_DEV_USER`、開発トークンを公開環境に設定してはいけません。

## 継続運用へ移行する条件

論文データを残す、複数ユーザーで使う、OCRを使う、または取込を非同期化する前に、次を実施してください。

1. `LocalOriginalStorage` を S3/R2 等に置き換える。
2. API、Celery worker、embedding worker、beat と Render Key Value を分離して再有効化する。
3. Postgres を有料プランへ移し、バックアップと復元を確認する。
4. カスタムドメイン、OIDCの本番redirect URL、CORSの `FRONTEND_ORIGIN` を固定する。

## 公式ドキュメント

- [Render Blueprints](https://render.com/docs/blueprint-spec)
- [Render Docker デプロイ](https://render.com/docs/docker)
- [Render 無料枠の制約](https://render.com/docs/free)
- [Render 環境変数](https://render.com/docs/configure-environment-variables)
